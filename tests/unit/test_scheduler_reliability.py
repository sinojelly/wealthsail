"""Regression tests for four-session scheduling and persistent business keys."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings
from backend.jobs.runner import business_job_key, claim_job_run, finish_job_run
from backend.jobs.scheduler import Scheduler


def test_business_job_key_ignores_tick_seconds_and_worker_restart() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    first = datetime(2026, 8, 26, 14, 30, 1, tzinfo=shanghai)
    replay = datetime(2026, 8, 26, 14, 30, 59, tzinfo=shanghai)

    assert business_job_key("job-7", "FUND_1430", first) == business_job_key(
        "job-7", "FUND_1430", replay
    )
    assert business_job_key("job-7", "FUND_1430", first) == (
        "job:job-7:2026-08-26:FUND_1430"
    )


@pytest.mark.asyncio
async def test_scheduler_skips_a_calendar_holiday_even_when_cron_is_due() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 10, 1, 14, 30, tzinfo=shanghai)
    dispatched: list[str] = []

    async def execute(session: str, _now: datetime) -> None:
        dispatched.append(session)

    async def trading_day(_now: datetime) -> bool:
        return False

    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=trading_day,
        now_provider=lambda: now,
    )

    await scheduler._tick()

    assert dispatched == []


@pytest.mark.asyncio
async def test_scheduler_dispatches_timezone_aware_shanghai_timestamp() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 8, 26, 8, 30, tzinfo=shanghai)
    seen: list[datetime] = []

    async def execute(_session: str, at: datetime) -> None:
        seen.append(at)

    async def trading_day(_now: datetime) -> bool:
        return True

    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=trading_day,
        now_provider=lambda: now,
    )
    await scheduler._tick()

    assert seen == [now]
    assert seen[0].utcoffset().total_seconds() == 8 * 60 * 60


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_job_claim_can_recover_only_expired_lease_with_stable_key() -> None:
    class Conn:
        def __init__(self):
            self.sql = ""

        async def fetchrow(self, sql, *_args):
            self.sql = sql
            return {"id": "run-1", "lease_token": "lease-1"}

    conn = Conn()
    lease = await claim_job_run(
        _Pool(conn),
        {"id": "job-1", "tenant_id": "tenant-1"},
        "job:job-1:2026-08-26:FUND_1430",
        owner="worker-a",
        lease_seconds=60,
    )

    assert lease is not None and lease.run_id == "run-1"
    assert "ON CONFLICT (tenant_id, job_key) DO UPDATE" in conn.sql
    assert "lease_until < now()" in conn.sql
    assert "status NOT IN ('COMPLETED', 'ABSTAINED', 'SKIPPED')" in conn.sql


@pytest.mark.asyncio
async def test_job_completion_uses_lease_token_compare_and_set() -> None:
    class Conn:
        def __init__(self):
            self.sql = ""

        async def execute(self, sql, *_args):
            self.sql = sql
            return "UPDATE 1"

    conn = Conn()
    updated = await finish_job_run(
        _Pool(conn), "run-1", "lease-1", status="COMPLETED"
    )

    assert updated is True
    assert "lease_token = $2" in conn.sql
    assert "status = 'RUNNING'" in conn.sql
