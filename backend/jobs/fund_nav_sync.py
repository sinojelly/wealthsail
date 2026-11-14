"""Global trading-day sync for official historical NAVs of held open funds.

The task deliberately shares the existing ``market.market_observations``
``NAV`` persistence boundary.  It does not enqueue tenant workflow jobs or
materialize account values: the data is a reusable market fact, while tenant
reports continue to read and cache their own derived trend results.
"""

from __future__ import annotations

import logging
import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import re
from typing import Awaitable, Callable, Sequence
from weakref import WeakKeyDictionary

import httpx

from backend.common.database import Pool, system_connection
from backend.config import Settings
from backend.funds.nav_window import (
    NAV_HISTORY_FETCH_CALENDAR_DAYS,
    NAV_SYNC_TARGET_SAMPLES,
)
from backend.integrations.market_data import (
    FUNDSOURCE_NAME,
    Observation,
    fetch_fund_nav_history,
)
from backend.jobs.data_tasks import NavPersistenceStats, persist_nav_observations
from backend.investment.theme_graph import collect_theme_nav_histories

logger = logging.getLogger("wealthsail.fund_nav_sync")


class OnDemandNavHistoryCache:
    """Share public provider work across readers, with no DB/user data in tasks.

    The deadline includes the concurrency queue. Even a large personal list
    finishes within the same bound, and omitted funds retry after a short pause.
    Persistence remains on each caller's authenticated connection.
    """

    def __init__(self, *, timeout=18, cooldown_seconds=30):
        self.timeout = timeout
        self.cooldown_seconds = cooldown_seconds
        self.pending, self.recent = {}, {}
        self.semaphores = WeakKeyDictionary()

    async def get(self, code, now, *, fetcher=None, now_provider=None):
        from backend.dependencies import now_shanghai
        from backend.funds.nav_history import local

        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("NAV history requires a six-digit fund code")
        now = local(now)
        key = code, now.date()
        recent = self.recent.get(key)
        if recent and timedelta(0) <= now - recent[0] < timedelta(
            seconds=self.cooldown_seconds
        ):
            if recent[1] is None:
                raise RuntimeError("NAV source sync cooldown")
            return deepcopy(recent[1])
        task = self.pending.get(key)
        if task is None:
            loop = asyncio.get_running_loop()
            semaphore = self.semaphores.setdefault(loop, asyncio.Semaphore(4))

            async def load():
                try:

                    async def bounded():
                        async with semaphore:
                            return await self._fetch(
                                code, now, fetcher, now_provider or now_shanghai
                            )

                    result = await asyncio.wait_for(bounded(), timeout=self.timeout)
                    self.recent[key] = local((now_provider or now_shanghai)()), result
                    return result
                except Exception:
                    self.recent[key] = local((now_provider or now_shanghai)()), None
                    raise
                finally:
                    self.pending.pop(key, None)
                    if len(self.recent) > 256:
                        oldest = sorted(
                            self.recent, key=lambda item: self.recent[item][0]
                        )
                        for item in oldest[: len(self.recent) - 256]:
                            self.recent.pop(item, None)

            task = asyncio.create_task(load())
            # Retrieving an exception also covers a reader disconnecting while
            # the shared, bounded public fetch is still finishing.
            task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
            self.pending[key] = task
        return deepcopy(await asyncio.shield(task))

    async def _fetch(self, code, now, fetcher, clock):
        from backend.funds.nav_history import local, valid_points
        from backend.integrations.fund_nav_fallback import (
            fetch_fund_nav_history_with_fallback,
        )
        from backend.integrations.nav_history_proof import nav_points_digest

        start = now.date() - timedelta(days=NAV_HISTORY_FETCH_CALENDAR_DAYS)
        async with httpx.AsyncClient(timeout=6, follow_redirects=False) as client:
            observations = await (fetcher or fetch_fund_nav_history_with_fallback)(
                code,
                start,
                now.date(),
                client,
                page_size=100,
                max_pages=32,
                minimum_points=NAV_SYNC_TARGET_SAMPLES,
                timeout_seconds=8,
            )
        points, by_day, proofs = [], {}, []
        for observation in observations:
            value = observation.value
            metadata = observation.metadata or {}
            day = local(observation.as_of).date()
            if (
                observation.symbol != code
                or observation.subject_type != "FUND"
                or observation.field_code != "NAV"
                or observation.currency != "CNY"
                or metadata.get("officialUnitNav") is not True
                or observation.quality_status not in {"FRESH", "STALE", "REVISED"}
                or observation.unit
                not in {"CNY/share", "CNY", "CNY/份", "元/份", "CNY/unit"}
                or not isinstance(value, Decimal)
                or not value.is_finite()
                or value <= 0
                or local(observation.as_of) > now
                or not start <= day <= now.date()
                or day in by_day
                and by_day[day]["close"] != str(value)
            ):
                raise ValueError("invalid or conflicting official unit NAV history")
            point = {"date": day.isoformat(), "close": str(value)}
            change = metadata.get("returnPct")
            if change is not None:
                try:
                    change = Decimal(str(change))
                    if change.is_finite() and change > -100:
                        point["returnPct"] = str(change)
                except ArithmeticError:
                    pass
            by_day[day] = point
            proofs.append(metadata.get("providerHistoryProof"))
        points = [by_day[day] for day in sorted(by_day)][-NAV_SYNC_TARGET_SAMPLES:]
        if not points:
            raise ValueError("public sources returned no official unit NAV history")
        finished = local(clock())
        if finished < now:
            raise ValueError("NAV retrieval clock regressed")
        history = {
            "fundCode": code,
            "priceBasis": "UNIT_NAV",
            "currency": "CNY",
            "unit": "CNY/份",
            "sourceUri": f"https://fund.eastmoney.com/{code}.html",
            "asOf": points[-1]["date"],
            "retrievedAt": finished.isoformat(),
            "availableAt": finished.isoformat(),
            "points": points,
        }
        if (
            proofs
            and isinstance(proofs[0], dict)
            and all(proof == proofs[0] for proof in proofs)
        ):
            proof = proofs[0]
            if (
                proof.get("complete") is True
                and proof.get("fundCode") == code
                and proof.get("pointCount") == len(points)
                and proof.get("pointsDigest") == nav_points_digest(points)
            ):
                history["providerHistoryProof"] = deepcopy(proof)
        if "providerHistoryProof" not in history:
            history["coverageReason"] = "SOURCE_HISTORY_INCOMPLETE"
        if not valid_points(history, code, finished):
            raise ValueError("invalid normalized unit NAV history")
        return history


on_demand_nav_history_cache = OnDemandNavHistoryCache()


async def fetch_on_demand_nav_history(code, now):
    return await on_demand_nav_history_cache.get(code, now)


_RESEARCH_NAV_GAP_BATCH = 24


def _report_nav_target_codes(report):
    from backend.investment.live_fund_plans import _plans
    from backend.macro.research_mentions import mentioned_fund_codes

    return list(
        dict.fromkeys(
            row["fundCode"]
            for row in (*_plans(report), *mentioned_fund_codes(report))
            if isinstance(row.get("fundCode"), str)
            and re.fullmatch(r"[0-9]{6}", row["fundCode"])
            and row.get("market") in (None, "FUND_CN")
        )
    )


async def _load_gap_fill_targets(conn, now):
    from backend.funds.nav_history import (
        NAV_GAP_FILL_MIN_SAMPLES,
        obj,
        read_nav_fund_identities,
        representative_nav_codes,
    )

    payload = await conn.fetchval(
        """SELECT t.payload FROM research.daily_research_runs r
          JOIN research.daily_research_results t ON t.run_id=r.run_id
          WHERE t.status='READY' AND r.dispatched_at<=$1 AND t.recorded_at<=$1
            AND r.slot_date<=($1 AT TIME ZONE 'Asia/Shanghai')::date
          ORDER BY r.dispatched_at DESC,t.recorded_at DESC,r.run_id DESC LIMIT 1""",
        now,
    )
    codes = list(
        dict.fromkeys(
            [
                *representative_nav_codes(),
                *_report_nav_target_codes(obj(payload)),
            ]
        )
    )
    # Use retrieval time for rechecks, as NAV publication may lag across markets.
    # Inspect counts in PostgreSQL so recurring polls do not transfer chart data.
    rows = await conn.fetch(
        """SELECT c.fund_code FROM unnest($1::text[]) WITH ORDINALITY c(fund_code, priority)
          WHERE NOT EXISTS(SELECT 1 FROM market.fund_nav_sync_attempts a
            WHERE a.fund_code=c.fund_code AND a.last_sync_attempt_at>$2::timestamptz-interval '12 hours')
          AND NOT EXISTS(SELECT 1 FROM (
              SELECT payload
              FROM market.fund_research_nav_histories WHERE fund_code=c.fund_code
                AND retrieved_at BETWEEN $2-interval '30 hours' AND $2 AND available_at<=$2
              UNION ALL
              SELECT payload
              FROM market.fund_nav_chart_histories WHERE fund_code=c.fund_code
                AND retrieved_at BETWEEN $2-interval '30 hours' AND $2 AND available_at<=$2
            ) h WHERE h.payload->>'priceBasis'='UNIT_NAV' AND h.payload->>'fundCode'=c.fund_code
              AND COALESCE(h.payload->>'currency','CNY')='CNY'
              AND h.payload->>'sourceUri' IN (
                'https://fund.eastmoney.com/'||c.fund_code||'.html',
                'https://fund.eastmoney.com/pingzhongdata/'||c.fund_code||'.js',
                'https://api.fund.eastmoney.com/f10/lsjz')
              AND h.payload->>'asOf' BETWEEN $3 AND $4
              AND CASE WHEN jsonb_typeof(h.payload->'points')='array'
                THEN jsonb_array_length(h.payload->'points') >= $5 ELSE false END)
          ORDER BY c.priority""",
        codes,
        now,
        (now.date() - timedelta(days=10)).isoformat(),
        now.date().isoformat(),
        NAV_GAP_FILL_MIN_SAMPLES,
    )
    return await read_nav_fund_identities(conn, [row["fund_code"] for row in rows], now)


async def backfill_research_nav_gaps(pool, now):
    """Warm local unit NAV cohorts used by Today and the interactive chart."""
    from backend.dependencies import now_shanghai
    from backend.funds.nav_history import local, stamp, valid_points
    from backend.integrations.fund_nav_fallback import fetch_research_nav_gap_history

    now = local(now)
    claimed = {}
    async with system_connection(pool) as conn:
        for fund in await _load_gap_fill_targets(conn, now):
            code = fund["fundCode"]
            try:
                claim = await conn.fetchrow(
                    "SELECT * FROM market.claim_fund_nav_sync_attempt($1)", code
                )
            except Exception as exc:  # one changing fund identity cannot block others
                logger.warning(
                    "fund_nav_gap_claim_failed symbol=%s error_class=%s",
                    code,
                    type(exc).__name__,
                )
                continue
            if claim and claim.get("claimed") is True:
                claimed[code] = fund
                if len(claimed) == _RESEARCH_NAV_GAP_BATCH:
                    break
    if not claimed:
        return 0
    result = await collect_theme_nav_histories(
        pool,
        now,
        asset_rows=[
            {
                "asset_code": code,
                "overseas_calendar_required": fund["overseasCalendarRequired"],
            }
            for code, fund in claimed.items()
        ],
        fetcher=fetch_research_nav_gap_history,
    )
    finished = max(now, local(now_shanghai()))
    payloads = {
        code: history
        for history in result["fundHistories"]
        if (code := history.get("fundCode")) in claimed
        and valid_points(history, code, finished)
    }
    if not payloads:
        return 0
    inserted = 0
    async with system_connection(pool) as conn:
        for code, history in payloads.items():
            try:
                await conn.execute(
                    """INSERT INTO market.fund_research_nav_histories
                    (fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$3,$4::jsonb)""",
                    code,
                    stamp(history["retrievedAt"]),
                    stamp(history["availableAt"]),
                    json.dumps(history, ensure_ascii=False, allow_nan=False),
                )
                inserted += 1
            except (
                Exception
            ) as exc:  # preserve other verified cohorts when one save fails
                logger.warning(
                    "fund_nav_gap_persist_failed symbol=%s error_class=%s",
                    code,
                    type(exc).__name__,
                )
    return inserted


# Query past the storage target so a not-yet-disclosed current NAV (or a
# delayed overseas disclosure) does not leave the first backfill at 549 rows.
_DISCLOSURE_BUFFER_TRADING_DAYS = 10
_INITIAL_HISTORY_TRADING_DAYS = (
    NAV_SYNC_TARGET_SAMPLES + _DISCLOSURE_BUFFER_TRADING_DAYS
)
_INCREMENTAL_OVERLAP_DAYS = 7
_CALENDAR_FALLBACK_DAYS = NAV_HISTORY_FETCH_CALENDAR_DAYS


@dataclass(frozen=True, slots=True)
class HeldFund:
    """One globally deduplicated held off-exchange fund."""

    instrument_id: str
    symbol: str
    latest_nav_on: date | None
    history_sample_count: int = 0


@dataclass(frozen=True, slots=True)
class FundNavHistorySyncStats:
    """Bounded, non-sensitive run outcome for Worker logs and monitoring."""

    scanned_instruments: int
    fetched_observations: int
    persisted_observations: int
    skipped_observations: int
    empty_instruments: int
    failed_instruments: int


NavFetcher = Callable[
    [str, date, date, httpx.AsyncClient], Awaitable[list[Observation]]
]
NavPersister = Callable[[Pool, str, list[Observation]], Awaitable[NavPersistenceStats]]


def nav_history_window(
    *,
    latest_nav_on: date | None,
    trading_days: Sequence[date],
    as_of: date,
    history_sample_count: int = 0,
) -> tuple[date, date]:
    """Return an inclusive fetch window for one fund.

    A new or shallow fund requests 560 exchange sessions to leave disclosure
    room beyond the 550 stored-sample target. Complete histories only re-read
    seven calendar days so a late provider correction can replace a revision.
    """

    if latest_nav_on is not None and history_sample_count >= NAV_SYNC_TARGET_SAMPLES:
        return latest_nav_on - timedelta(days=_INCREMENTAL_OVERLAP_DAYS), as_of

    normalized_days = sorted(
        {item for item in trading_days if item <= as_of}, reverse=True
    )
    if len(normalized_days) >= _INITIAL_HISTORY_TRADING_DAYS:
        return normalized_days[_INITIAL_HISTORY_TRADING_DAYS - 1], as_of
    # The Worker bootstraps the exchange calendar before the scheduler starts.
    # This fallback keeps a cold or temporarily incomplete calendar from
    # preventing the first truthful NAV import; 900 days includes holiday room.
    return as_of - timedelta(days=_CALENDAR_FALLBACK_DAYS), as_of


async def sync_fund_nav_history(
    pool: Pool,
    settings: Settings,
    now: datetime,
    *,
    fetcher: NavFetcher | None = None,
    persister: NavPersister | None = None,
) -> FundNavHistorySyncStats:
    """Synchronise all currently held off-exchange funds once at 22:00.

    Position scanning is global and deduplicated by the reference instrument,
    so one fund held by several users produces one provider request and one
    shared NAV history.  A provider failure is isolated to that fund and must
    never stop later funds or the Worker scheduler.
    """

    as_of = now.date()
    async with system_connection(pool) as conn:
        # SQL already selects distinct instruments, while this second boundary
        # protects provider load if an adapter or future query regresses.
        funds = _deduplicate_held_funds(await _load_held_funds(conn))
        trading_days = await _load_recent_trading_days(conn, as_of)
    if not funds:
        return FundNavHistorySyncStats(0, 0, 0, 0, 0, 0)

    if fetcher is None:

        async def nav_fetcher(
            symbol: str, start: date, end: date, client: httpx.AsyncClient
        ) -> list[Observation]:
            # Keep the global job bounded by the same provider pagination
            # settings as tenant-triggered historical NAV backfills.
            return await fetch_fund_nav_history(
                symbol,
                start,
                end,
                client,
                page_size=settings.fund_nav_history_page_size,
                max_pages=settings.fund_nav_history_max_pages,
            )
    else:
        nav_fetcher = fetcher
    nav_persister = persister or persist_nav_observations
    fetched = persisted = skipped = empty = failed = 0
    async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
        for fund in funds:
            start, end = nav_history_window(
                latest_nav_on=fund.latest_nav_on,
                history_sample_count=fund.history_sample_count,
                trading_days=trading_days,
                as_of=as_of,
            )
            try:
                observations = await nav_fetcher(fund.symbol, start, end, client)
                fetched += len(observations)
                if not observations:
                    empty += 1
                    logger.info(
                        "fund_nav_history_sync_empty symbol=%s start=%s end=%s",
                        fund.symbol,
                        start.isoformat(),
                        end.isoformat(),
                    )
                    continue
                result = await nav_persister(pool, fund.instrument_id, observations)
                persisted += result.inserted_count
                skipped += result.skipped_count
                logger.info(
                    "fund_nav_history_sync_persisted symbol=%s observations=%d inserted=%d skipped=%d",
                    fund.symbol,
                    len(observations),
                    result.inserted_count,
                    result.skipped_count,
                )
            except Exception as exc:  # noqa: BLE001 -- one vendor symbol must not stop all funds
                failed += 1
                logger.warning(
                    "fund_nav_history_sync_failed symbol=%s error_class=%s",
                    fund.symbol,
                    type(exc).__name__,
                )

    stats = FundNavHistorySyncStats(
        scanned_instruments=len(funds),
        fetched_observations=fetched,
        persisted_observations=persisted,
        skipped_observations=skipped,
        empty_instruments=empty,
        failed_instruments=failed,
    )
    logger.info(
        "fund_nav_history_sync_complete scanned=%d fetched=%d persisted=%d skipped=%d empty=%d failed=%d",
        stats.scanned_instruments,
        stats.fetched_observations,
        stats.persisted_observations,
        stats.skipped_observations,
        stats.empty_instruments,
        stats.failed_instruments,
    )
    return stats


def _deduplicate_held_funds(funds: Sequence[HeldFund]) -> list[HeldFund]:
    """Keep one global sync target per reference instrument."""

    by_instrument: dict[str, HeldFund] = {}
    for fund in funds:
        by_instrument.setdefault(fund.instrument_id, fund)
    return list(by_instrument.values())


async def _load_held_funds(conn) -> list[HeldFund]:
    """Read active holdings across every tenant, once per reference fund."""

    rows = await conn.fetch(
        """
        WITH latest_positions AS (
            SELECT DISTINCT ON (
                       snapshot.tenant_id, snapshot.account_id, snapshot.instrument_id
                   )
                   snapshot.tenant_id, snapshot.account_id, snapshot.instrument_id,
                   snapshot.quantity
              FROM ledger.position_snapshots snapshot
             ORDER BY snapshot.tenant_id, snapshot.account_id,
                      snapshot.instrument_id, snapshot.as_of DESC, snapshot.created_at DESC
        ), held_funds AS (
            SELECT DISTINCT position.instrument_id
              FROM latest_positions position
             WHERE position.quantity > 0
        )
        SELECT instrument.id AS instrument_id,
               alias.alias_value AS symbol,
               latest_nav.latest_nav_on, latest_nav.history_sample_count
          FROM held_funds held
          JOIN reference.instruments instrument ON instrument.id = held.instrument_id
          JOIN LATERAL (
              SELECT instrument_alias.alias_value
                FROM reference.instrument_aliases instrument_alias
               WHERE instrument_alias.instrument_id = instrument.id
                 AND instrument_alias.alias_type = 'TICKER'
                 AND instrument_alias.effective_from <= CURRENT_DATE
                 AND (
                     instrument_alias.effective_to IS NULL
                     OR instrument_alias.effective_to >= CURRENT_DATE
                 )
               ORDER BY instrument_alias.effective_from DESC
               LIMIT 1
          ) alias ON TRUE
          LEFT JOIN LATERAL (
              SELECT max((observation.as_of AT TIME ZONE 'Asia/Shanghai')::date)
                         AS latest_nav_on,
                     count(DISTINCT (observation.as_of AT TIME ZONE 'Asia/Shanghai')::date)
                         AS history_sample_count
                FROM market.market_observations observation
                JOIN reference.data_sources source ON source.id = observation.source_id
               WHERE observation.subject_type = 'FUND'
                 AND observation.subject_id = instrument.id
                 AND observation.field_code = 'NAV'
                 AND observation.value IS NOT NULL
                 AND observation.value > 0
                 AND source.name = $1
          ) latest_nav ON TRUE
         WHERE instrument.asset_type = 'OFF_EXCHANGE_FUND'
           AND instrument.lifecycle_status = 'ACTIVE'
         ORDER BY alias.alias_value
        """,
        FUNDSOURCE_NAME,
    )
    return [
        HeldFund(
            instrument_id=str(row["instrument_id"]),
            symbol=str(row["symbol"]).strip().zfill(6),
            latest_nav_on=row["latest_nav_on"],
            history_sample_count=int(row.get("history_sample_count") or 0),
        )
        for row in rows
    ]


async def _load_recent_trading_days(conn, as_of: date) -> list[date]:
    rows = await conn.fetch(
        """
        SELECT calendar_date
          FROM reference.trading_calendars
         WHERE market = 'SSE'
           AND is_trading_day = TRUE
           AND calendar_date <= $1
         ORDER BY calendar_date DESC
         LIMIT $2
        """,
        as_of,
        _INITIAL_HISTORY_TRADING_DAYS,
    )
    days = [row["calendar_date"] for row in rows]
    if len(days) >= _INITIAL_HISTORY_TRADING_DAYS:
        return days

    # Synthesizing 550 weekdays misses exchange holidays and would still yield
    # fewer than 550 NAV samples. Let the window use the 900-day safety margin.
    return []
