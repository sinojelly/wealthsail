"""Regression tests for four-session scheduling and persistent business keys."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings
from backend.jobs import runner
from backend.jobs.runner import business_job_key, claim_job_run, finish_job_run
from backend.jobs.scheduler import (
    SCHEDULES,
    Scheduler,
    SESSION_LABELS,
    due_at,
)


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


def test_intraday_business_key_is_idempotent_per_five_minute_bucket() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    first = datetime(2026, 8, 26, 9, 30, 1, tzinfo=shanghai)
    same_bucket = datetime(2026, 8, 26, 9, 34, 59, tzinfo=shanghai)
    next_bucket = datetime(2026, 8, 26, 9, 35, tzinfo=shanghai)

    assert business_job_key("job-7", "INTRADAY", first) == business_job_key(
        "job-7", "INTRADAY", same_bucket
    )
    assert business_job_key("job-7", "INTRADAY", first) != business_job_key(
        "job-7", "INTRADAY", next_bucket
    )
    assert business_job_key("job-7", "INTRADAY", first).endswith(
        ":INTRADAY:0930"
    )


def test_fixed_session_has_bounded_restart_catch_up_window() -> None:
    pre_market = next(s for s in SCHEDULES if s.session == "PRE_MARKET")
    assert pre_market.catch_up_minutes == 15
    assert pre_market.hour == 8 and pre_market.minute == 30

    from backend.jobs.scheduler import is_due

    assert is_due(pre_market, datetime(2026, 8, 27, 8, 40)) is True
    assert is_due(pre_market, datetime(2026, 8, 27, 8, 44)) is True
    assert is_due(pre_market, datetime(2026, 8, 27, 8, 45)) is False


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


@pytest.mark.asyncio
async def test_scheduler_dispatches_only_the_four_mvp_sessions_at_documented_times() -> None:
    """Temporary sessions and an 18:00 drift must not enter production scheduling."""
    shanghai = ZoneInfo("Asia/Shanghai")
    dispatched: list[str] = []

    async def execute(session: str, _at: datetime) -> None:
        dispatched.append(session)

    async def trading_day(_now: datetime) -> bool:
        return True

    assert {schedule.session for schedule in SCHEDULES} == {
        "PRE_MARKET",
        "INTRADAY",
        "FUND_1430",
        "POST_MARKET",
    }

    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=trading_day,
        now_provider=lambda: datetime(2026, 8, 27, 15, 30, tzinfo=shanghai),
    )
    await scheduler._tick()
    assert dispatched == ["POST_MARKET"]

    dispatched.clear()
    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=trading_day,
        now_provider=lambda: datetime(2026, 8, 27, 18, 0, tzinfo=shanghai),
    )
    await scheduler._tick()
    assert dispatched == []


@pytest.mark.asyncio
async def test_simultaneous_sessions_start_independently() -> None:
    """A slow intraday report must not delay the time-sensitive 14:30 report."""
    shanghai = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 8, 27, 14, 30, tzinfo=shanghai)
    fund_started = asyncio.Event()
    started: list[str] = []

    async def execute(session: str, _at: datetime) -> None:
        started.append(session)
        if session == "INTRADAY":
            await fund_started.wait()
        else:
            fund_started.set()

    async def trading_day(_now: datetime) -> bool:
        return True

    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=trading_day,
        now_provider=lambda: now,
    )

    await asyncio.wait_for(scheduler._tick(), timeout=0.5)

    assert set(started) == {"INTRADAY", "FUND_1430"}


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


@pytest.mark.asyncio
async def test_recovery_scan_executes_due_report_run_with_original_key(
    monkeypatch,
) -> None:
    class Conn:
        def __init__(self):
            self.sql = ""
            self.args = ()

        async def fetch(self, sql, *args):
            self.sql = sql
            self.args = args
            return [
                {
                    "id": "job-1",
                    "tenant_id": "tenant-1",
                    "job_name": "pre-market",
                    "report_session": "PRE_MARKET",
                    "job_key": "job:job-1:2026-08-26:PRE_MARKET",
                }
            ]

    conn = Conn()
    executed: list[tuple[str, str]] = []

    async def execute(_pool, _settings, job, key, session):
        executed.append((key, session))
        assert job["id"] == "job-1"
        return "COMPLETED:report=report-1"

    monkeypatch.setattr(runner, "_execute_with_lease", execute)
    outcomes = await runner.run_recoverable_jobs(
        _Pool(conn), Settings(_env_file=None), limit=10
    )

    assert "jr.status IN ('PENDING', 'FAILED')" in conn.sql
    assert "jr.attempt_count <" in conn.sql
    assert "make_interval(mins =>" in conn.sql
    assert executed == [("job:job-1:2026-08-26:PRE_MARKET", "PRE_MARKET")]
    assert outcomes == {"job:job-1:2026-08-26:PRE_MARKET": "COMPLETED:report=report-1"}


def test_pre_market_phases_fire_at_exact_shanghai_slots() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    assert due_at("PRE_MARKET_MACRO", datetime(2026, 9, 2, 8, 30, 5, tzinfo=shanghai))
    assert due_at(
        "PRE_MARKET_PERSONALIZED", datetime(2026, 9, 2, 8, 35, 5, tzinfo=shanghai)
    )
    assert not due_at(
        "PRE_MARKET_PERSONALIZED", datetime(2026, 9, 2, 8, 34, 59, tzinfo=shanghai)
    )
    # Macro keeps its legacy bounded catch-up window (…08:45).
    assert due_at("PRE_MARKET_MACRO", datetime(2026, 9, 2, 8, 40, tzinfo=shanghai))


def test_four_public_session_labels_remain_unchanged() -> None:
    assert dict(SESSION_LABELS) == {
        "PRE_MARKET": "盘前简报",
        "INTRADAY": "盘中跟踪",
        "FUND_1430": "基金 2:30 报告",
        "POST_MARKET": "盘后交互复盘",
    }


@pytest.mark.asyncio
async def test_both_pre_market_phases_skip_exchange_holidays() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 10, 1, 8, 30, tzinfo=shanghai)

    dispatched: list[str] = []

    async def execute(phase: str, _at: datetime) -> None:
        dispatched.append(phase)

    async def trading_day(_now: datetime) -> bool:
        return False

    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=trading_day,
        now_provider=lambda: now,
    )
    # 08:30 macro is due by the clock, but the holiday gate must still block
    # every phase before any dispatch.
    assert due_at("PRE_MARKET_MACRO", now) is True
    await scheduler._tick()
    assert dispatched == []

