"""Durable lease and materialization contracts for Tiantian NAV data tasks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import Settings
from backend.jobs.data_tasks import (
    DataTaskLease,
    OpenFundPosition,
    claim_data_task,
    complete_data_task,
    fail_data_task,
    materialize_position_metrics,
    process_data_task,
)
from backend.integrations.market_data import F_NAV, Observation, Q_STALE


class _Connection:
    def __init__(self, *, row=None, result: str = "UPDATE 1") -> None:
        self.row = row
        self.result = result
        self.sql: list[str] = []
        self.args: tuple = ()

    async def fetchrow(self, sql: str, *args):
        self.sql.append(sql)
        self.args = args
        return self.row

    async def execute(self, sql: str, *args):
        self.sql.append(sql)
        self.args = args
        return self.result

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
        first_lease = await claim_data_task(MagicMock(), owner="worker-a", lease_seconds=60)
        second_lease = await claim_data_task(MagicMock(), owner="worker-b", lease_seconds=60)

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
        assert await fail_data_task(MagicMock(), lease, settings, RuntimeError()) is True
        terminal = replace(lease, attempt_count=3)
        assert await fail_data_task(MagicMock(), terminal, settings, RuntimeError()) is True

    assert "lease_token = $2" in completed_conn.sql[0]
    assert retry_conn.args[2] == "FAILED"
    assert retry_conn.args[4] == 20
    assert retry_conn.args[5] is False
    assert terminal_conn.args[5] is True


@pytest.mark.asyncio
async def test_backfill_fetches_outside_db_and_materializes_only_open_source_positions() -> None:
    position = OpenFundPosition(
        tenant_id="tenant-1",
        account_id="account-1",
        instrument_id="fund-1",
        source_binding_id="binding-1",
        symbol="110022",
        holding_started_on=date(2024, 1, 2),
        holding_start_method="TRANSACTION_RECONCILED",
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

    materialize = AsyncMock()
    with (
        patch("backend.jobs.data_tasks.load_open_fund_positions", side_effect=load),
        patch("backend.jobs.data_tasks.fetch_fund_nav_history", side_effect=fetch),
        patch("backend.jobs.data_tasks.persist_nav_observations", side_effect=persist),
        patch("backend.jobs.data_tasks.materialize_position_metrics", materialize),
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
    assert args[8] == Decimal("1.50")
    assert args[10] == Decimal("2.00")
    assert args[12] == Decimal("1.00")
    assert args[14] == Decimal("-25.00")
    assert args[15] == Decimal("50.00")
    assert args[16] == Decimal("50.00")
    assert args[17] == "READY"
