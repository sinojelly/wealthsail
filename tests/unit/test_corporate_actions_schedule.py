"""Tests for trading-day 06:00 corporate-action scheduling."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.config import Settings
from backend.jobs.scheduler import SCHEDULES, Scheduler, due_at
from backend import worker


def test_corporate_action_refresh_is_a_trading_day_six_am_phase() -> None:
    schedule = next(
        schedule
        for schedule in SCHEDULES
        if schedule.phase == "CORPORATE_ACTIONS_REFRESH"
    )
    shanghai = ZoneInfo("Asia/Shanghai")

    assert schedule.hour == 6 and schedule.minute == 0
    assert due_at(
        "CORPORATE_ACTIONS_REFRESH", datetime(2026, 9, 8, 6, 0, tzinfo=shanghai)
    )
    assert not due_at(
        "CORPORATE_ACTIONS_REFRESH", datetime(2026, 9, 6, 6, 0, tzinfo=shanghai)
    )


def test_fund_nav_history_refresh_is_a_trading_day_ten_pm_phase() -> None:
    schedule = next(
        schedule
        for schedule in SCHEDULES
        if schedule.phase == "FUND_NAV_HISTORY_REFRESH"
    )
    shanghai = ZoneInfo("Asia/Shanghai")

    assert schedule.hour == 22 and schedule.minute == 0
    assert due_at(
        "FUND_NAV_HISTORY_REFRESH",
        datetime(2026, 9, 8, 22, 0, tzinfo=shanghai),
    )
    assert not due_at(
        "FUND_NAV_HISTORY_REFRESH",
        datetime(2026, 9, 6, 22, 0, tzinfo=shanghai),
    )


@pytest.mark.asyncio
async def test_scheduler_skips_corporate_action_refresh_on_calendar_holiday() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    dispatched: list[str] = []

    async def execute(session: str, now: datetime) -> None:
        dispatched.append(session)

    async def non_trading_day(now: datetime) -> bool:
        return False

    scheduler = Scheduler(
        Settings(_env_file=None),
        execute,
        trading_day_check=non_trading_day,
        now_provider=lambda: datetime(2026, 9, 8, 6, 0, tzinfo=shanghai),
    )

    await scheduler._tick()

    assert dispatched == []


@pytest.mark.asyncio
async def test_worker_runs_global_sync_without_creating_report_jobs(monkeypatch) -> None:
    sync = AsyncMock()
    monkeypatch.setattr(worker, "sync_corporate_actions", sync)
    monkeypatch.setattr(worker.market_service, "refresh_market", AsyncMock())
    monkeypatch.setattr(worker.runner, "run_session", AsyncMock())

    now = datetime(2026, 9, 8, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker._execute_session(object(), Settings(_env_file=None), "CORPORATE_ACTIONS_REFRESH", now)

    sync.assert_awaited_once()
    worker.market_service.refresh_market.assert_not_awaited()
    worker.runner.run_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_runs_global_nav_sync_without_creating_report_jobs(monkeypatch) -> None:
    sync = AsyncMock()
    monkeypatch.setattr(worker, "sync_fund_nav_history", sync)
    monkeypatch.setattr(worker.market_service, "refresh_market", AsyncMock())
    monkeypatch.setattr(worker.runner, "run_session", AsyncMock())

    now = datetime(2026, 9, 8, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker._execute_session(object(), Settings(_env_file=None), "FUND_NAV_HISTORY_REFRESH", now)

    sync.assert_awaited_once()
    worker.market_service.refresh_market.assert_not_awaited()
    worker.runner.run_session.assert_not_awaited()
