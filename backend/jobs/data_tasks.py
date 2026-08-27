"""Durable historical-data jobs for source-bound fund positions.

Claims and finalization are short compare-and-set database operations. Provider
network calls happen only after the claim/load connections have been released.
Official unit NAV observations are shared market facts; derived holding metrics
remain tenant/account scoped and can be rebuilt without replaying ledger events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx

from backend.common.database import Pool, system_connection
from backend.config import Settings
from backend.integrations.market_data import (
    FUNDSOURCE_NAME,
    Observation,
    fetch_fund_nav_history,
)
from backend.market import repository as market_repository
from backend.portfolio.imports.analytics import (
    NAV_RANGE_ALGORITHM_VERSION,
    NavObservation,
    calculate_range_metrics,
)

logger = logging.getLogger("wealthsail.data_tasks")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SUPPORTED_JOB_TYPES = ("FUND_NAV_BACKFILL", "POSITION_PERIOD_METRICS_REBUILD")


@dataclass(frozen=True, slots=True)
class DataTaskLease:
    run_id: str
    tenant_id: str
    job_type: str
    payload: dict[str, Any]
    attempt_count: int
    lease_token: str


@dataclass(frozen=True, slots=True)
class OpenFundPosition:
    tenant_id: str
    account_id: str
    instrument_id: str
    source_binding_id: str
    symbol: str
    holding_started_on: date | None
    holding_start_method: str
    history_complete: bool
    source_as_of: date | None = None


def _payload_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def data_task_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:data-task"


async def claim_data_task(
    pool: Pool,
    *,
    owner: str,
    lease_seconds: int,
) -> DataTaskLease | None:
    """Atomically claim one due task, including an expired RUNNING lease."""
    async with system_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            WITH candidate AS (
                SELECT id
                  FROM workflow.job_runs
                 WHERE job_type IN ('FUND_NAV_BACKFILL',
                                    'POSITION_PERIOD_METRICS_REBUILD')
                   AND (
                       (status IN ('PENDING', 'FAILED') AND
                           CASE WHEN status = 'PENDING'
                                THEN available_at <= now()
                                ELSE next_retry_at IS NOT NULL
                                     AND next_retry_at <= now()
                           END)
                       OR (status = 'RUNNING' AND lease_until < now())
                   )
                 ORDER BY available_at, id
                 LIMIT 1
                 FOR UPDATE SKIP LOCKED
            )
            UPDATE workflow.job_runs AS run
               SET status = 'RUNNING', started_at = now(), finished_at = NULL,
                   error_class = NULL, lease_token = gen_random_uuid(),
                   lease_owner = $1,
                   lease_until = now() + make_interval(secs => $2),
                   heartbeat_at = now(), attempt_count = attempt_count + 1,
                   next_retry_at = NULL
              FROM candidate
             WHERE run.id = candidate.id
            RETURNING run.id, run.tenant_id, run.job_type, run.payload,
                      run.attempt_count, run.lease_token
            """,
            owner,
            lease_seconds,
        )
    if row is None:
        return None
    return DataTaskLease(
        run_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        job_type=str(row["job_type"]),
        payload=_payload_object(row["payload"]),
        attempt_count=int(row["attempt_count"]),
        lease_token=str(row["lease_token"]),
    )


async def renew_data_task(pool: Pool, lease: DataTaskLease, lease_seconds: int) -> bool:
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.job_runs
               SET heartbeat_at = now(),
                   lease_until = now() + make_interval(secs => $3)
             WHERE id = $1 AND lease_token = $2 AND status = 'RUNNING'
            """,
            lease.run_id,
            lease.lease_token,
            lease_seconds,
        )
    return result.endswith("1")


async def complete_data_task(pool: Pool, lease: DataTaskLease) -> bool:
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.job_runs
               SET status = 'COMPLETED', finished_at = now(), error_class = NULL,
                   lease_token = NULL, lease_owner = NULL, lease_until = NULL,
                   heartbeat_at = now(), next_retry_at = NULL
             WHERE id = $1 AND lease_token = $2 AND status = 'RUNNING'
            """,
            lease.run_id,
            lease.lease_token,
        )
    return result.endswith("1")


async def fail_data_task(
    pool: Pool,
    lease: DataTaskLease,
    settings: Settings,
    exc: Exception,
) -> bool:
    terminal = lease.attempt_count >= settings.data_task_max_attempts
    backoff = settings.data_task_base_backoff_seconds * (
        2 ** max(lease.attempt_count - 1, 0)
    )
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.job_runs
               SET status = $3, error_class = $4,
                   finished_at = CASE WHEN $6 THEN now() ELSE NULL END,
                   next_retry_at = CASE WHEN $6 THEN NULL
                       ELSE now() + make_interval(secs => $5) END,
                   lease_token = NULL, lease_owner = NULL, lease_until = NULL,
                   heartbeat_at = now()
             WHERE id = $1 AND lease_token = $2 AND status = 'RUNNING'
            """,
            lease.run_id,
            lease.lease_token,
            "FAILED",
            exc.__class__.__name__,
            backoff,
            terminal,
        )
    return result.endswith("1")


def _holding_method(row: Mapping[str, Any]) -> tuple[date | None, str, bool]:
    user_started = row.get("user_holding_started_on")
    if user_started is not None:
        return user_started, "USER_CONFIRMED", True
    status = str(row.get("holding_start_status") or "INSUFFICIENT_DATA")
    method = "TRANSACTION_RECONCILED" if status == "RECONCILED" else status
    if method not in {
        "TRANSACTION_RECONCILED",
        "PARTIAL_HISTORY",
        "MISMATCH",
        "INSUFFICIENT_DATA",
    }:
        method = "INSUFFICIENT_DATA"
    return row.get("candidate_holding_started_on"), method, method != "PARTIAL_HISTORY"


async def load_open_fund_positions(
    pool: Pool,
    *,
    tenant_id: str,
    source_binding_id: str | None,
) -> list[OpenFundPosition]:
    """Load only unreversed source baselines; closed/manual positions are excluded."""
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (item.instrument_id)
                   event.account_id, item.instrument_id,
                   batch.source_binding_id, item.source_symbol,
                   item.user_holding_started_on,
                   item.candidate_holding_started_on,
                   item.holding_start_status, batch.source_as_of::date AS source_as_of
              FROM ledger.ledger_events event
              JOIN ledger.portfolio_import_batches batch
                ON batch.tenant_id = event.tenant_id
               AND batch.id = event.import_batch_id
              JOIN ledger.portfolio_import_items item
                ON item.tenant_id = event.tenant_id
               AND item.batch_id = event.import_batch_id
               AND item.id = event.import_item_id
              JOIN ledger.portfolio_source_bindings binding
                ON binding.tenant_id = batch.tenant_id
               AND binding.id = batch.source_binding_id
             WHERE event.tenant_id = $1
               AND ($2::uuid IS NULL OR batch.source_binding_id = $2::uuid)
               AND batch.source_type = 'TIANTIAN_FUND_ZIP'
               AND batch.status = 'CONFIRMED' AND binding.status = 'ACTIVE'
               AND item.item_type = 'POSITION_SNAPSHOT' AND item.quantity > 0
               AND event.status = 'CONFIRMED' AND event.event_type = 'ADJUSTMENT'
               AND event.reverses_event_id IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM ledger.ledger_events reversal
                    WHERE reversal.tenant_id = event.tenant_id
                      AND reversal.reverses_event_id = event.id
                      AND reversal.status = 'CONFIRMED'
               )
             ORDER BY item.instrument_id, event.created_at DESC, event.id DESC
            """,
            tenant_id,
            source_binding_id,
        )
    positions: list[OpenFundPosition] = []
    for row in rows:
        started_on, method, history_complete = _holding_method(row)
        positions.append(
            OpenFundPosition(
                tenant_id=tenant_id,
                account_id=str(row["account_id"]),
                instrument_id=str(row["instrument_id"]),
                source_binding_id=str(row["source_binding_id"]),
                symbol=str(row["source_symbol"]),
                holding_started_on=started_on,
                holding_start_method=method,
                history_complete=history_complete,
                source_as_of=row["source_as_of"],
            )
        )
    return positions


async def persist_nav_observations(
    pool: Pool,
    instrument_id: str,
    observations: list[Observation],
) -> None:
    """Persist a source snapshot and append a revision only when NAV changed."""
    if not observations:
        return
    retrieved_at = datetime.now(_SHANGHAI)
    async with system_connection(pool) as conn:
        async with conn.transaction():
            await market_repository.upsert_data_source(
                conn, name=FUNDSOURCE_NAME, source_type="PUBLIC_API"
            )
            source_id = await market_repository.get_source_id(conn, FUNDSOURCE_NAME)
            if source_id is None:
                raise RuntimeError("fund NAV source could not be resolved")
            snapshot_id = await market_repository.insert_source_snapshot(
                conn,
                source_id=source_id,
                retrieval_key=(
                    f"fund-nav-history:{instrument_id}:"
                    f"{observations[0].as_of.date()}:{observations[-1].as_of.date()}"
                ),
                observations=[item.to_payload() for item in observations],
                quality_status="FRESH",
                retrieved_at=retrieved_at,
            )
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('fund-nav:' || $1, 0))",
                instrument_id,
            )
            for item in observations:
                latest = await conn.fetchrow(
                    """
                    SELECT value, revision
                      FROM market.market_observations
                     WHERE subject_type = 'FUND' AND subject_id = $1
                       AND field_code = 'NAV' AND as_of = $2 AND source_id = $3
                     ORDER BY revision DESC LIMIT 1
                    """,
                    instrument_id,
                    item.as_of,
                    source_id,
                )
                if latest is not None and Decimal(latest["value"]) == item.value:
                    continue
                await conn.execute(
                    """
                    INSERT INTO market.market_observations (
                        subject_type, subject_id, field_code, value, unit, currency,
                        as_of, observed_at, snapshot_id, source_id, quality_status,
                        method_version, metadata, revision
                    ) VALUES (
                        'FUND', $1, 'NAV', $2, $3, 'CNY', $4, $5, $6, $7,
                        $8, $9, $10::jsonb, $11
                    )
                    """,
                    instrument_id,
                    item.value,
                    item.unit,
                    item.as_of,
                    retrieved_at,
                    snapshot_id,
                    source_id,
                    item.quality_status,
                    item.method_version,
                    json.dumps(item.metadata or {}, ensure_ascii=False),
                    int(latest["revision"]) + 1 if latest is not None else 1,
                )


async def load_persisted_nav(
    pool: Pool,
    positions: list[OpenFundPosition],
    *,
    as_of: date,
) -> dict[str, list[Observation]]:
    result: dict[str, list[Observation]] = {}
    async with system_connection(pool) as conn:
        for position in positions:
            start = position.holding_started_on or position.source_as_of or as_of
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (observation.as_of::date)
                       observation.value, observation.as_of,
                       observation.quality_status, observation.method_version
                  FROM market.market_observations observation
                  JOIN reference.data_sources source ON source.id = observation.source_id
                 WHERE observation.subject_type = 'FUND'
                   AND observation.subject_id = $1 AND observation.field_code = 'NAV'
                   AND observation.as_of::date BETWEEN $2 AND $3
                   AND source.name = $4 AND observation.value > 0
                 ORDER BY observation.as_of::date, observation.revision DESC,
                          observation.observed_at DESC
                """,
                position.instrument_id,
                start,
                as_of,
                FUNDSOURCE_NAME,
            )
            result[position.instrument_id] = [
                Observation(
                    symbol=position.symbol,
                    subject_type="FUND",
                    field_code="NAV",
                    value=Decimal(row["value"]),
                    as_of=row["as_of"],
                    quality_status=str(row["quality_status"]),
                    method_version=str(row["method_version"]),
                )
                for row in rows
            ]
    return result


async def materialize_position_metrics(
    pool: Pool,
    as_of: date,
    positions: list[OpenFundPosition],
    observations_by_instrument: Mapping[str, list[Observation]],
) -> None:
    async with system_connection(pool) as conn:
        async with conn.transaction():
            for position in positions:
                observations = [
                    NavObservation(item.as_of.date(), item.value)
                    for item in observations_by_instrument.get(position.instrument_id, [])
                    if item.value is not None
                ]
                metric = calculate_range_metrics(
                    observations,
                    holding_started_on=position.holding_started_on,
                    history_complete=position.history_complete,
                )
                holding_days = (
                    (as_of - position.holding_started_on).days + 1
                    if position.holding_started_on is not None
                    and position.holding_started_on <= as_of
                    else None
                )
                await conn.execute(
                    """
                    INSERT INTO ledger.position_period_metrics (
                        tenant_id, account_id, instrument_id, source_binding_id,
                        as_of, holding_started_on, holding_days, holding_start_method,
                        current_nav, current_nav_date, period_high_nav, period_high_date,
                        period_low_nav, period_low_date, from_high_pct, from_low_pct,
                        range_position_pct, calculation_status, quality_reasons,
                        calculation_version, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13, $14, $15, $16, $17, $18, $19::text[], $20, now()
                    )
                    ON CONFLICT (tenant_id, account_id, instrument_id, as_of)
                    DO UPDATE SET
                        source_binding_id = EXCLUDED.source_binding_id,
                        holding_started_on = EXCLUDED.holding_started_on,
                        holding_days = EXCLUDED.holding_days,
                        holding_start_method = EXCLUDED.holding_start_method,
                        current_nav = EXCLUDED.current_nav,
                        current_nav_date = EXCLUDED.current_nav_date,
                        period_high_nav = EXCLUDED.period_high_nav,
                        period_high_date = EXCLUDED.period_high_date,
                        period_low_nav = EXCLUDED.period_low_nav,
                        period_low_date = EXCLUDED.period_low_date,
                        from_high_pct = EXCLUDED.from_high_pct,
                        from_low_pct = EXCLUDED.from_low_pct,
                        range_position_pct = EXCLUDED.range_position_pct,
                        calculation_status = EXCLUDED.calculation_status,
                        quality_reasons = EXCLUDED.quality_reasons,
                        calculation_version = EXCLUDED.calculation_version,
                        updated_at = now()
                    """,
                    position.tenant_id,
                    position.account_id,
                    position.instrument_id,
                    position.source_binding_id,
                    as_of,
                    position.holding_started_on,
                    holding_days,
                    position.holding_start_method,
                    metric.current_nav,
                    metric.as_of,
                    metric.high_nav,
                    metric.high_on,
                    metric.low_nav,
                    metric.low_on,
                    metric.from_high_pct,
                    metric.from_low_pct,
                    metric.range_position_pct,
                    metric.status,
                    list(metric.quality_reasons),
                    NAV_RANGE_ALGORITHM_VERSION,
                )


async def process_data_task(
    pool: Pool,
    settings: Settings,
    lease: DataTaskLease,
    *,
    as_of: date | None = None,
) -> None:
    """Run one claimed job with all provider I/O outside DB transactions."""
    if lease.job_type not in _SUPPORTED_JOB_TYPES:
        raise ValueError(f"unsupported data task: {lease.job_type}")
    as_of = as_of or datetime.now(_SHANGHAI).date()
    source_binding_id = lease.payload.get("sourceBindingId")
    positions = await load_open_fund_positions(
        pool,
        tenant_id=lease.tenant_id,
        source_binding_id=str(source_binding_id) if source_binding_id else None,
    )
    observations_by_instrument: dict[str, list[Observation]] = {}
    if lease.job_type == "FUND_NAV_BACKFILL":
        async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
            for position in positions:
                start = position.holding_started_on or position.source_as_of or as_of
                observations = await fetch_fund_nav_history(
                    position.symbol,
                    start,
                    as_of,
                    client,
                    page_size=settings.fund_nav_history_page_size,
                    max_pages=settings.fund_nav_history_max_pages,
                )
                observations_by_instrument[position.instrument_id] = observations
                await persist_nav_observations(pool, position.instrument_id, observations)
    else:
        observations_by_instrument = await load_persisted_nav(
            pool, positions, as_of=as_of
        )
    await materialize_position_metrics(
        pool, as_of, positions, observations_by_instrument
    )


async def _heartbeat_loop(
    pool: Pool, lease: DataTaskLease, lease_seconds: int
) -> None:
    while True:
        await asyncio.sleep(max(1.0, lease_seconds / 3))
        if not await renew_data_task(pool, lease, lease_seconds):
            raise RuntimeError("data-task lease ownership lost")


class DataTaskConsumer:
    """A bounded set of durable workers; each loop owns at most one lease."""

    def __init__(self, pool: Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings
        self._tasks: list[asyncio.Task] = []

    async def poll_once(self) -> bool:
        lease = await claim_data_task(
            self._pool,
            owner=data_task_owner(),
            lease_seconds=self._settings.data_task_lease_seconds,
        )
        if lease is None:
            return False
        heartbeat = asyncio.create_task(
            _heartbeat_loop(
                self._pool, lease, self._settings.data_task_lease_seconds
            ),
            name=f"data-task-heartbeat:{lease.run_id}",
        )
        try:
            await process_data_task(self._pool, self._settings, lease)
            if not await complete_data_task(self._pool, lease):
                raise RuntimeError("data-task lease lost before completion")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # durable retry state is part of the contract
            await fail_data_task(self._pool, lease, self._settings, exc)
            logger.warning(
                "data task %s failed (%s), attempt=%d",
                lease.run_id,
                exc.__class__.__name__,
                lease.attempt_count,
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True

    def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        self._tasks = [
            asyncio.create_task(self._loop(), name=f"wealthsail-data-task-{index}")
            for index in range(self._settings.data_task_concurrency)
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _loop(self) -> None:
        while True:
            handled = await self.poll_once()
            if not handled:
                await asyncio.sleep(self._settings.data_task_poll_interval_seconds)
