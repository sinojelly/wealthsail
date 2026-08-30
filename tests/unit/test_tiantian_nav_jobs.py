"""Durable lease and materialization contracts for Tiantian NAV data tasks."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import asyncpg
import pytest

from backend.config import Settings
from backend.jobs.data_tasks import (
    DataTaskLease,
    OpenFundPosition,
    _holding_method,
    claim_data_task,
    complete_data_task,
    fail_data_task,
    materialize_position_metrics,
    persist_nav_observations,
    process_data_task,
    refresh_fund_position_projections,
)
from backend.integrations.market_data import (
    FUNDSOURCE_NAME,
    F_NAV,
    Observation,
    Q_STALE,
)


class _Connection:
    def __init__(
        self, *, row=None, rows=None, result: str = "UPDATE 1"
    ) -> None:
        self.row = row
        self.rows = rows or []
        self.result = result
        self.sql: list[str] = []
        self.args: tuple = ()
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list[tuple]]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        self.sql.append(sql)
        self.args = args
        return self.row

    async def execute(self, sql: str, *args):
        self.sql.append(sql)
        self.args = args
        self.execute_calls.append((sql, args))
        return self.result

    async def fetch(self, sql: str, *args):
        self.sql.append(sql)
        self.args = args
        self.fetch_calls.append((sql, args))
        return self.rows

    async def executemany(self, sql: str, args):
        rows = list(args)
        self.sql.append(sql)
        self.executemany_calls.append((sql, rows))

    async def fetchval(self, sql: str, *args):
        self.sql.append(sql)
        self.args = args
        self.fetchval_calls.append((sql, args))
        if "SELECT id FROM reference.data_sources" in sql:
            return "source-1"
        if "INSERT INTO market.source_snapshots" in sql:
            return "snapshot-1"
        return None

    def transaction(self):
        return _connection(self)


@asynccontextmanager
async def _connection(conn):
    yield conn


@pytest.mark.asyncio
async def test_claim_is_atomic_skip_locked_and_second_consumer_gets_nothing() -> None:
    row = {
        "id": "run-1",
        "tenant_id": "tenant-1",
        "job_type": "FUND_NAV_BACKFILL",
        "payload": {"batchId": "batch-1"},
        "attempt_count": 1,
        "lease_token": "lease-1",
    }
    first = _Connection(row=row)
    second = _Connection(row=None)
    with patch(
        "backend.jobs.data_tasks.system_connection",
        side_effect=[_connection(first), _connection(second)],
    ):
        first_lease = await claim_data_task(
            MagicMock(), owner="worker-a", lease_seconds=60
        )
        second_lease = await claim_data_task(
            MagicMock(), owner="worker-b", lease_seconds=60
        )

    assert first_lease is not None and first_lease.lease_token == "lease-1"
    assert second_lease is None
    assert "FOR UPDATE SKIP LOCKED" in first.sql[0]
    assert "lease_until < now()" in first.sql[0]
    assert "status IN ('PENDING', 'FAILED')" in first.sql[0]


@pytest.mark.asyncio
async def test_completion_and_failure_are_lease_token_compare_and_set() -> None:
    completed_conn = _Connection()
    retry_conn = _Connection()
    terminal_conn = _Connection()
    lease = DataTaskLease(
        run_id="run-1",
        tenant_id="tenant-1",
        job_type="FUND_NAV_BACKFILL",
        payload={"batchId": "batch-1"},
        attempt_count=2,
        lease_token="lease-1",
    )
    settings = Settings(
        _env_file=None,
        data_task_max_attempts=3,
        data_task_base_backoff_seconds=10,
    )
    with patch(
        "backend.jobs.data_tasks.system_connection",
        side_effect=[
            _connection(completed_conn),
            _connection(retry_conn),
            _connection(terminal_conn),
        ],
    ):
        assert await complete_data_task(MagicMock(), lease) is True
        assert (
            await fail_data_task(MagicMock(), lease, settings, RuntimeError()) is True
        )
        terminal = replace(lease, attempt_count=3)
        assert (
            await fail_data_task(MagicMock(), terminal, settings, RuntimeError())
            is True
        )

    assert "lease_token = $2" in completed_conn.sql[0]
    assert retry_conn.args[2] == "FAILED"
    assert retry_conn.args[4] == 20
    assert retry_conn.args[5] is False
    assert terminal_conn.args[5] is True


@pytest.mark.asyncio
async def test_failure_logging_distinguishes_automatic_retry_from_terminal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    retry_conn = _Connection()
    terminal_conn = _Connection()
    lease = DataTaskLease(
        run_id="run-1",
        tenant_id="tenant-1",
        job_type="FUND_NAV_BACKFILL",
        payload={"batchId": "batch-1"},
        attempt_count=2,
        lease_token="lease-1",
    )
    settings = Settings(
        _env_file=None,
        data_task_max_attempts=3,
        data_task_base_backoff_seconds=10,
    )

    with (
        patch(
            "backend.jobs.data_tasks.system_connection",
            side_effect=[_connection(retry_conn), _connection(terminal_conn)],
        ),
        caplog.at_level(logging.WARNING, logger="wealthsail.data_tasks"),
    ):
        await fail_data_task(
            MagicMock(), lease, settings, RuntimeError("provider temporarily unavailable")
        )
        await fail_data_task(
            MagicMock(),
            replace(lease, attempt_count=3),
            settings,
            RuntimeError("provider still unavailable"),
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "data_task_retry_scheduled" in messages
    assert "run_id=run-1" in messages
    assert "batch_id=batch-1" in messages
    assert "attempt=2" in messages
    assert "retry_in_seconds=20" in messages
    assert "data_task_failed_terminal" in messages
    assert "attempt=3" in messages
    assert "error_class=RuntimeError" in messages


@pytest.mark.asyncio
async def test_database_check_violation_is_terminal_on_the_first_attempt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn = _Connection()
    lease = DataTaskLease(
        run_id="run-1",
        tenant_id="tenant-1",
        job_type="FUND_NAV_BACKFILL",
        payload={"batchId": "batch-1"},
        attempt_count=1,
        lease_token="lease-1",
    )
    settings = Settings(
        _env_file=None,
        data_task_max_attempts=5,
        data_task_base_backoff_seconds=10,
    )

    with (
        patch(
            "backend.jobs.data_tasks.system_connection",
            return_value=_connection(conn),
        ),
        caplog.at_level(logging.WARNING, logger="wealthsail.data_tasks"),
    ):
        await fail_data_task(
            MagicMock(),
            lease,
            settings,
            asyncpg.CheckViolationError("invalid source type"),
        )

    assert conn.args[5] is True
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "data_task_failed_terminal" in messages
    assert "data_task_retry_scheduled" not in messages


def test_effective_holding_date_defaults_to_portfolio_estimate_until_user_overrides() -> (
    None
):
    estimated = _holding_method(
        {
            "user_holding_started_on": None,
            "candidate_holding_started_on": date(2021, 1, 26),
            "holding_start_status": "PARTIAL_HISTORY",
            "holding_start_method": "PORTFOLIO_ACTIVITY_ESTIMATE",
            "holding_start_confidence": "LOW",
        }
    )
    overridden = _holding_method(
        {
            "user_holding_started_on": date(2021, 2, 23),
            "candidate_holding_started_on": date(2021, 1, 26),
            "holding_start_status": "PARTIAL_HISTORY",
            "holding_start_method": "PORTFOLIO_ACTIVITY_ESTIMATE",
            "holding_start_confidence": "LOW",
        }
    )
    post_import_decision = _holding_method(
        {
            "confirmed_holding_started_on": date(2021, 3, 15),
            "user_holding_started_on": None,
            "candidate_holding_started_on": date(2021, 1, 26),
            "holding_start_method": "PORTFOLIO_ACTIVITY_ESTIMATE",
            "holding_start_confidence": "LOW",
        }
    )

    assert estimated == (
        date(2021, 1, 26),
        "PORTFOLIO_ACTIVITY_ESTIMATE",
        "LOW",
        False,
    )
    assert overridden == (date(2021, 2, 23), "USER_CONFIRMED", "HIGH", True)
    assert post_import_decision == (
        date(2021, 3, 15),
        "USER_CONFIRMED",
        "HIGH",
        True,
    )


@pytest.mark.asyncio
async def test_backfill_fetches_outside_db_and_materializes_only_open_source_positions() -> (
    None
):
    position = OpenFundPosition(
        tenant_id="tenant-1",
        account_id="account-1",
        instrument_id="fund-1",
        source_binding_id="binding-1",
        symbol="110022",
        holding_started_on=date(2024, 1, 2),
        holding_start_method="TRANSACTION_RECONCILED",
        holding_start_confidence="HIGH",
        history_complete=True,
    )
    observations = [
        Observation(
            symbol="110022",
            subject_type="FUND",
            field_code=F_NAV,
            value=Decimal("1.20"),
            as_of=datetime.fromisoformat("2024-01-02T00:00:00+00:00"),
            quality_status=Q_STALE,
        ),
        Observation(
            symbol="110022",
            subject_type="FUND",
            field_code=F_NAV,
            value=Decimal("1.50"),
            as_of=datetime.fromisoformat("2026-08-28T00:00:00+00:00"),
            quality_status=Q_STALE,
        ),
    ]
    lease = DataTaskLease(
        run_id="run-1",
        tenant_id="tenant-1",
        job_type="FUND_NAV_BACKFILL",
        payload={"sourceBindingId": "binding-1"},
        attempt_count=1,
        lease_token="lease-1",
    )
    order: list[str] = []

    async def load(*_args, **_kwargs):
        order.append("load-and-release-db")
        return [position]

    async def fetch(*_args, **_kwargs):
        order.append("network-without-db")
        return observations

    async def persist(*_args, **_kwargs):
        order.append("persist")
        return SimpleNamespace(inserted_count=2, skipped_count=0)

    materialize = AsyncMock()
    with (
        patch("backend.jobs.data_tasks.load_open_fund_positions", side_effect=load),
        patch("backend.jobs.data_tasks.fetch_fund_nav_history", side_effect=fetch),
        patch("backend.jobs.data_tasks.persist_nav_observations", side_effect=persist),
        patch("backend.jobs.data_tasks.materialize_position_metrics", materialize),
        patch(
            "backend.jobs.data_tasks.refresh_fund_position_projections",
            new=AsyncMock(return_value=(1, 1)),
        ),
        patch("backend.jobs.data_tasks.httpx.AsyncClient") as client_type,
    ):
        client_type.return_value.__aenter__.return_value = MagicMock()
        await process_data_task(
            MagicMock(),
            Settings(_env_file=None),
            lease,
            as_of=date(2026, 8, 28),
        )

    assert order == ["load-and-release-db", "network-without-db", "persist"]
    materialize.assert_awaited_once()
    assert materialize.await_args.args[2] == [position]
    assert materialize.await_args.args[3] == {"fund-1": observations}


@pytest.mark.asyncio
async def test_nav_persistence_registers_tiantian_as_a_fund_nav_source() -> None:
    conn = _Connection()
    observation = Observation(
        symbol="012733",
        subject_type="FUND",
        field_code=F_NAV,
        value=Decimal("1.20"),
        as_of=datetime.fromisoformat("2026-08-29T00:00:00+00:00"),
        quality_status=Q_STALE,
    )

    with patch(
        "backend.jobs.data_tasks.system_connection",
        return_value=_connection(conn),
    ):
        await persist_nav_observations(MagicMock(), "fund-1", [observation])

    source_registration = next(
        args
        for sql, args in conn.execute_calls
        if "INSERT INTO reference.data_sources" in sql
    )
    assert source_registration == (FUNDSOURCE_NAME, "FUND_NAV")


@pytest.mark.asyncio
async def test_nav_persistence_reads_existing_revisions_once_and_bulk_inserts_changes() -> None:
    existing_as_of = datetime.fromisoformat("2026-08-28T00:00:00+00:00")
    changed_as_of = datetime.fromisoformat("2026-08-29T00:00:00+00:00")
    new_as_of = datetime.fromisoformat("2026-08-30T00:00:00+00:00")
    conn = _Connection(
        rows=[
            {"as_of": existing_as_of, "value": Decimal("1.20"), "revision": 3},
            {"as_of": changed_as_of, "value": Decimal("1.30"), "revision": 2},
        ]
    )
    observations = [
        Observation(
            symbol="012733",
            subject_type="FUND",
            field_code=F_NAV,
            value=value,
            as_of=as_of,
            quality_status=Q_STALE,
        )
        for as_of, value in (
            (existing_as_of, Decimal("1.20")),
            (changed_as_of, Decimal("1.31")),
            (new_as_of, Decimal("1.40")),
        )
    ]

    with patch(
        "backend.jobs.data_tasks.system_connection",
        return_value=_connection(conn),
    ):
        result = await persist_nav_observations(
            MagicMock(), "fund-1", observations
        )

    assert len(conn.fetch_calls) == 1
    assert "as_of = ANY" in conn.fetch_calls[0][0]
    assert len(conn.executemany_calls) == 1
    inserted_rows = conn.executemany_calls[0][1]
    assert [row[-1] for row in inserted_rows] == [3, 1]
    assert result.inserted_count == 2
    assert result.skipped_count == 1


@pytest.mark.asyncio
async def test_projection_refresh_revalues_the_full_tenant_quote_set() -> None:
    conn = _Connection()
    holding_ids = AsyncMock(return_value=["fund-1", "stock-1"])
    quotes = {
        "fund-1": {"price": Decimal("1.40"), "quality": "FRESH"},
        "stock-1": {"price": Decimal("12.00"), "quality": "FRESH"},
    }
    load_quotes = AsyncMock(return_value=quotes)
    refresh_positions = AsyncMock(return_value=2)
    refresh_account_nav = AsyncMock(return_value=1)

    with (
        patch(
            "backend.jobs.data_tasks.system_connection",
            return_value=_connection(conn),
        ),
        patch(
            "backend.jobs.data_tasks.portfolio_repository.holding_instrument_ids",
            new=holding_ids,
        ),
        patch(
            "backend.jobs.data_tasks.portfolio_repository.latest_observation_quotes",
            new=load_quotes,
        ),
        patch(
            "backend.jobs.data_tasks.market_repository.refresh_positions",
            new=refresh_positions,
        ),
        patch(
            "backend.jobs.data_tasks.market_repository.refresh_account_nav",
            new=refresh_account_nav,
        ),
    ):
        result = await refresh_fund_position_projections(
            MagicMock(), tenant_id="tenant-1", as_of=date(2026, 8, 30)
        )

    assert result == (2, 1)
    holding_ids.assert_awaited_once_with(conn, "tenant-1")
    load_quotes.assert_awaited_once_with(conn, ["fund-1", "stock-1"])
    refresh_positions.assert_awaited_once_with(
        conn,
        tenant_id="tenant-1",
        quotes=quotes,
        as_of_date=date(2026, 8, 30),
    )
    refresh_account_nav.assert_awaited_once_with(
        conn,
        tenant_id="tenant-1",
        as_of_date=date(2026, 8, 30),
    )


@pytest.mark.asyncio
async def test_backfill_logs_persistence_progress_and_refreshes_valuation_projection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    position = OpenFundPosition(
        tenant_id="tenant-1",
        account_id="account-1",
        instrument_id="fund-1",
        source_binding_id="binding-1",
        symbol="110022",
        holding_started_on=date(2024, 1, 2),
        holding_start_method="TRANSACTION_RECONCILED",
        holding_start_confidence="HIGH",
        history_complete=True,
    )
    observations = [
        Observation(
            symbol="110022",
            subject_type="FUND",
            field_code=F_NAV,
            value=Decimal("1.50"),
            as_of=datetime.fromisoformat("2026-08-28T00:00:00+00:00"),
            quality_status=Q_STALE,
        )
    ]
    lease = DataTaskLease(
        run_id="run-progress",
        tenant_id="tenant-1",
        job_type="FUND_NAV_BACKFILL",
        payload={"batchId": "batch-1", "sourceBindingId": "binding-1"},
        attempt_count=1,
        lease_token="lease-1",
    )
    projection = AsyncMock(return_value=(1, 1))

    with (
        patch(
            "backend.jobs.data_tasks.load_open_fund_positions",
            new=AsyncMock(return_value=[position]),
        ),
        patch(
            "backend.jobs.data_tasks.fetch_fund_nav_history",
            new=AsyncMock(return_value=observations),
        ),
        patch(
            "backend.jobs.data_tasks.persist_nav_observations",
            new=AsyncMock(
                return_value=SimpleNamespace(inserted_count=1, skipped_count=0)
            ),
        ),
        patch(
            "backend.jobs.data_tasks.materialize_position_metrics",
            new=AsyncMock(),
        ),
        patch(
            "backend.jobs.data_tasks.refresh_fund_position_projections",
            new=projection,
            create=True,
        ),
        patch("backend.jobs.data_tasks.httpx.AsyncClient") as client_type,
        caplog.at_level(logging.INFO, logger="wealthsail.data_tasks"),
    ):
        client_type.return_value.__aenter__.return_value = MagicMock()
        await process_data_task(
            MagicMock(),
            Settings(_env_file=None),
            lease,
            as_of=date(2026, 8, 30),
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "fund_nav_backfill_persisted" in messages
    assert "position_index=1" in messages
    assert "position_count=1" in messages
    assert "inserted_count=1" in messages
    assert "skipped_count=0" in messages
    assert "duration_ms=" in messages
    projection.assert_awaited_once_with(
        ANY,
        tenant_id="tenant-1",
        as_of=date(2026, 8, 30),
    )


@pytest.mark.asyncio
async def test_provider_failure_logs_task_stage_and_fund_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    position = OpenFundPosition(
        tenant_id="tenant-1",
        account_id="account-1",
        instrument_id="fund-1",
        source_binding_id="binding-1",
        symbol="110022",
        holding_started_on=date(2024, 1, 2),
        holding_start_method="TRANSACTION_RECONCILED",
        holding_start_confidence="HIGH",
        history_complete=True,
    )
    lease = DataTaskLease(
        run_id="run-1",
        tenant_id="tenant-1",
        job_type="FUND_NAV_BACKFILL",
        payload={"batchId": "batch-1", "sourceBindingId": "binding-1"},
        attempt_count=1,
        lease_token="lease-1",
    )

    with (
        patch(
            "backend.jobs.data_tasks.load_open_fund_positions",
            new=AsyncMock(return_value=[position]),
        ),
        patch(
            "backend.jobs.data_tasks.fetch_fund_nav_history",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "GET https://api.example/fund?token=secret-value failed "
                    "password=hunter2"
                )
            ),
        ),
        patch("backend.jobs.data_tasks.httpx.AsyncClient") as client_type,
        caplog.at_level(logging.ERROR, logger="wealthsail.data_tasks"),
    ):
        client_type.return_value.__aenter__.return_value = MagicMock()
        with pytest.raises(RuntimeError, match="secret-value"):
            await process_data_task(
                MagicMock(),
                Settings(_env_file=None),
                lease,
                as_of=date(2026, 8, 30),
            )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "data_task_stage_failed" in messages
    assert "run_id=run-1" in messages
    assert "batch_id=batch-1" in messages
    assert "stage=fetch_fund_nav_history" in messages
    assert "symbol=110022" in messages
    assert "start=2024-01-02" in messages
    assert "as_of=2026-08-30" in messages
    assert "error_class=RuntimeError" in messages
    assert "error=GET https://api.example/fund?<redacted> failed" in messages
    assert "trace=" in messages
    assert "account-1" not in messages
    assert "secret-value" not in messages
    assert "hunter2" not in messages


@pytest.mark.asyncio
async def test_materialization_writes_literal_decimal_range_and_holding_days() -> None:
    conn = _Connection()
    position = OpenFundPosition(
        tenant_id="tenant-1",
        account_id="account-1",
        instrument_id="fund-1",
        source_binding_id="binding-1",
        symbol="110022",
        holding_started_on=date(2026, 8, 26),
        holding_start_method="TRANSACTION_RECONCILED",
        holding_start_confidence="HIGH",
        history_complete=True,
    )
    observations = [
        Observation(
            symbol="110022",
            subject_type="FUND",
            field_code=F_NAV,
            value=Decimal("1.00"),
            as_of=datetime.fromisoformat("2026-08-26T00:00:00+00:00"),
            quality_status=Q_STALE,
        ),
        Observation(
            symbol="110022",
            subject_type="FUND",
            field_code=F_NAV,
            value=Decimal("2.00"),
            as_of=datetime.fromisoformat("2026-08-27T00:00:00+00:00"),
            quality_status=Q_STALE,
        ),
        Observation(
            symbol="110022",
            subject_type="FUND",
            field_code=F_NAV,
            value=Decimal("1.50"),
            as_of=datetime.fromisoformat("2026-08-28T00:00:00+00:00"),
            quality_status=Q_STALE,
        ),
    ]
    with patch(
        "backend.jobs.data_tasks.system_connection",
        return_value=_connection(conn),
    ):
        await materialize_position_metrics(
            MagicMock(), date(2026, 8, 28), [position], {"fund-1": observations}
        )

    args = conn.args
    assert args[6] == 3
    assert args[8] == "HIGH"
    assert args[9] == Decimal("1.50")
    assert args[11] == Decimal("2.00")
    assert args[13] == Decimal("1.00")
    assert args[15] == Decimal("-25.00")
    assert args[16] == Decimal("50.00")
    assert args[17] == Decimal("50.00")
    assert args[18] == "READY"
