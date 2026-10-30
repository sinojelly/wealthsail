"""Research sources refresh daily after NAV disclosure, before report sessions."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from backend.config import Settings
from backend.jobs.scheduler import Scheduler, due_at


@pytest.mark.asyncio
async def test_daily_research_checks_calendar_before_collecting_facts(monkeypatch):
    from backend.jobs import investment_research as worker, investment_macro
    from unittest.mock import AsyncMock
    from contextlib import asynccontextmanager

    events = []

    async def calendar(pool):
        events.append("calendar")
        return {"status": "READY"}

    async def macro(pool, now):
        events.append("macro")
        return {"observations": 0}

    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "ensure_trading_calendar", calendar, raising=False)
    monkeypatch.setattr(investment_macro, "sync_macro", macro)
    monkeypatch.setattr(worker, "system_connection", connection)
    await worker.sync_research(
        object(),
        object(),
        datetime(2026, 10, 14, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert events == ["calendar", "macro"]


SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    "day,hour,minute,second,expected",
    [
        (18, 6, 30, 0, True),  # Sunday; macro releases are independent of trading.
        (1, 7, 30, 0, True),  # National Day restart within the catch-up window.
        (13, 7, 0, 0, True),
        (13, 6, 29, 59, False),
        (13, 8, 29, 59, True),
        (13, 8, 30, 0, False),
        (13, 22, 30, 0, False),
    ],
)
def test_research_source_refresh_respects_daily_disclosure_and_restart_window(
    day, hour, minute, second, expected
):
    now = datetime(2026, 10, day, hour, minute, second, tzinfo=SHANGHAI)
    assert due_at("INVESTMENT_RESEARCH_REFRESH", now) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("day,hour,minute", [(18, 6, 30), (1, 7, 30)])
async def test_research_refresh_dispatches_when_exchange_calendar_is_closed(
    day, hour, minute
):
    now = datetime(2026, 10, day, hour, minute, tzinfo=SHANGHAI)
    dispatched = []

    async def execute(session, at):
        dispatched.append((session, at))

    async def holiday(at):
        return False

    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=holiday,
        now_provider=lambda: now,
    )
    await scheduler._tick()
    assert dispatched == [("INVESTMENT_RESEARCH_REFRESH", now)]


@pytest.mark.asyncio
async def test_research_catch_up_ticks_dispatch_once_per_calendar_day():
    now = datetime(2026, 10, 18, 6, 30, tzinfo=SHANGHAI)
    dispatched = []

    async def execute(session, at):
        dispatched.append((session, at.date()))

    async def holiday(at):
        return False

    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=holiday,
        now_provider=lambda: now,
    )
    await scheduler._tick()
    now = datetime(2026, 10, 18, 7, 30, tzinfo=SHANGHAI)
    await scheduler._tick()
    now = datetime(2026, 10, 19, 7, 0, tzinfo=SHANGHAI)
    await scheduler._tick()
    assert dispatched == [
        ("INVESTMENT_RESEARCH_REFRESH", datetime(2026, 10, 18).date()),
        ("INVESTMENT_RESEARCH_REFRESH", datetime(2026, 10, 19).date()),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("pre_market_enabled", [False, True])
async def test_source_refresh_catch_up_ends_before_independently_enabled_pre_market_phases(
    pre_market_enabled,
):
    now = datetime(2026, 10, 13, 8, 30, tzinfo=SHANGHAI)
    dispatched = []

    async def execute(session, at):
        dispatched.append(session)

    async def trading_day(at):
        return True

    scheduler = Scheduler(
        Settings(
            _env_file=None,
            daily_research_enabled=True,
            pre_market_reports_enabled=pre_market_enabled,
        ),
        execute,
        trading_day_check=trading_day,
        now_provider=lambda: now,
    )
    await scheduler._tick()
    now = datetime(2026, 10, 13, 8, 35, tzinfo=SHANGHAI)
    await scheduler._tick()
    assert dispatched == (
        ["PRE_MARKET_MACRO", "PRE_MARKET_PERSONALIZED"] if pre_market_enabled else []
    )


@pytest.mark.asyncio
async def test_slow_research_at_0829_does_not_block_0830_pre_market_tick():
    now = datetime(2026, 10, 13, 8, 29, tzinfo=SHANGHAI)
    research_started, release_research, report_started = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    dispatched = []

    async def execute(session, at):
        dispatched.append((session, at))
        if session == "INVESTMENT_RESEARCH_REFRESH":
            research_started.set()
            await release_research.wait()
        elif session == "PRE_MARKET_MACRO":
            report_started.set()

    async def trading_day(at):
        return True

    scheduler = Scheduler(
        Settings(
            _env_file=None,
            scheduler_tick_seconds=0.001,
            daily_research_enabled=True,
            pre_market_reports_enabled=True,
        ),
        execute,
        trading_day_check=trading_day,
        now_provider=lambda: now,
    )
    scheduler.start()
    try:
        await asyncio.wait_for(research_started.wait(), timeout=0.5)
        now = datetime(2026, 10, 13, 8, 30, tzinfo=SHANGHAI)
        await asyncio.wait_for(report_started.wait(), timeout=0.5)
        assert [phase for phase, _ in dispatched] == [
            "INVESTMENT_RESEARCH_REFRESH",
            "PRE_MARKET_MACRO",
        ]
        assert not release_research.is_set()
    finally:
        release_research.set()
        await scheduler.stop()


@pytest.mark.asyncio
async def test_stop_cancels_research_and_waits_for_its_async_cleanup():
    started, cancelled, release_cleanup, cleaned = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    running = []

    async def execute(session, at):
        running.append(asyncio.current_task())
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cancelled.set()
            await release_cleanup.wait()
            cleaned.set()

    scheduler = Scheduler(
        Settings(_env_file=None, scheduler_tick_seconds=0.001),
        execute,
        now_provider=lambda: datetime(2026, 10, 13, 6, 30, tzinfo=SHANGHAI),
    )
    scheduler.start()
    await asyncio.wait_for(started.wait(), timeout=0.5)
    stopping = asyncio.create_task(scheduler.stop())
    try:
        await asyncio.wait_for(cancelled.wait(), timeout=0.5)
        assert not stopping.done()
        release_cleanup.set()
        await asyncio.wait_for(stopping, timeout=0.5)
        assert cleaned.is_set()
        assert running[0].done()
        await scheduler.stop()  # Repeated shutdown is harmless.
    finally:
        release_cleanup.set()
        if not cancelled.is_set():
            for task in running:
                task.cancel()
        await asyncio.gather(stopping, *running, return_exceptions=True)


@pytest.mark.asyncio
async def test_busy_research_does_not_overlap_or_consume_next_days_retry_slot():
    now = datetime(2026, 10, 13, 6, 30, tzinfo=SHANGHAI)
    release = asyncio.Event()
    running, starts = [], []

    async def execute(session, at):
        if session == "INVESTMENT_RESEARCH_REFRESH":
            running.append(asyncio.current_task())
            starts.append(at.date())
            await release.wait()

    scheduler = Scheduler(Settings(_env_file=None), execute, now_provider=lambda: now)
    try:
        await scheduler._tick()
        await scheduler._tick()
        now = datetime(2026, 10, 14, 6, 30, tzinfo=SHANGHAI)
        await scheduler._tick()
        assert starts == [datetime(2026, 10, 13).date()]
        release.set()
        await asyncio.gather(*running)
        await scheduler._tick()
        await scheduler._tick()
        assert starts == [datetime(2026, 10, 13).date(), datetime(2026, 10, 14).date()]
    finally:
        release.set()
        await asyncio.gather(*running, return_exceptions=True)
        await scheduler.stop()


@pytest.mark.asyncio
async def test_report_dispatch_keeps_existing_await_completion_semantics():
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(session, at):
        assert session == "PRE_MARKET_MACRO"
        started.set()
        await release.wait()

    scheduler = Scheduler(
        Settings(_env_file=None, pre_market_reports_enabled=True),
        execute,
        now_provider=lambda: datetime(2026, 10, 13, 8, 30, tzinfo=SHANGHAI),
    )
    tick = asyncio.create_task(scheduler._tick())
    try:
        await asyncio.wait_for(started.wait(), timeout=0.5)
        assert not tick.done()
        release.set()
        await asyncio.wait_for(tick, timeout=0.5)
    finally:
        release.set()
        await asyncio.gather(tick, return_exceptions=True)
        await scheduler.stop()


@pytest.mark.asyncio
async def test_cross_day_research_finished_after_retry_window_waits_until_next_day():
    now = datetime(2026, 10, 13, 6, 30, tzinfo=SHANGHAI)
    release = asyncio.Event()
    running, starts = [], []

    async def execute(session, at):
        if session == "INVESTMENT_RESEARCH_REFRESH":
            running.append(asyncio.current_task())
            starts.append(at.date())
            await release.wait()

    scheduler = Scheduler(Settings(_env_file=None), execute, now_provider=lambda: now)
    try:
        await scheduler._tick()
        now = datetime(2026, 10, 14, 6, 30, tzinfo=SHANGHAI)
        await scheduler._tick()
        now = datetime(2026, 10, 14, 8, 30, tzinfo=SHANGHAI)
        release.set()
        await asyncio.gather(*running)
        await scheduler._tick()
        assert starts == [datetime(2026, 10, 13).date()]
        now = datetime(2026, 10, 15, 6, 30, tzinfo=SHANGHAI)
        await scheduler._tick()
        assert starts == [datetime(2026, 10, 13).date(), datetime(2026, 10, 15).date()]
    finally:
        release.set()
        await asyncio.gather(*running, return_exceptions=True)
        await scheduler.stop()


@pytest.mark.asyncio
async def test_failed_background_research_does_not_repeat_or_block_reports(caplog):
    now = datetime(2026, 10, 13, 8, 29, tzinfo=SHANGHAI)
    dispatched = []

    async def execute(session, at):
        dispatched.append(session)
        if session == "INVESTMENT_RESEARCH_REFRESH":
            raise RuntimeError("fixture provider unavailable")

    scheduler = Scheduler(
        Settings(_env_file=None, pre_market_reports_enabled=True),
        execute,
        now_provider=lambda: now,
    )
    try:
        await scheduler._tick()
        await scheduler._tick()
        now = datetime(2026, 10, 13, 8, 30, tzinfo=SHANGHAI)
        await scheduler._tick()
        assert dispatched == ["INVESTMENT_RESEARCH_REFRESH", "PRE_MARKET_MACRO"]
        assert (
            "scheduler execution failed for INVESTMENT_RESEARCH_REFRESH" in caplog.text
        )
    finally:
        await scheduler.stop()
