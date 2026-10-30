"""Daily dispatches are consumed once; API projections never write."""

from datetime import datetime, timedelta, timezone
from importlib import import_module, util
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 16, 9, tzinfo=SHANGHAI)


def repository():
    assert util.find_spec("backend.macro.daily_repository"), "daily repository missing"
    return import_module("backend.macro.daily_repository")


def run(**changes):
    result = {
        "run_id": "run-1",
        "slot_date": NOW.date(),
        "scheduled_for": NOW.replace(hour=8, minute=30),
        "dispatched_at": NOW - timedelta(minutes=5),
        "status": None,
        "recorded_at": None,
        "payload": None,
        "error_class": None,
    }
    return {**result, **changes}


@pytest.mark.asyncio
async def test_read_empty_is_not_run_with_next_shanghai_slot():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [None, None]
    result = await repository().read_daily_research(conn, NOW)
    assert result["state"] == "MISSED"
    assert result["report"] is None
    assert result["nextRunAt"] == NOW.replace(hour=7, minute=0) + timedelta(days=1)
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_latest_ready_report_is_not_downloaded_twice():
    latest = run(status="READY", recorded_at=NOW, payload={"summary": "ready"})
    conn = AsyncMock()
    conn.fetchrow.return_value = latest
    result = await repository().read_daily_research(conn, NOW)
    assert result["report"] == {"summary": "ready"}
    assert result["reportRunId"] == "run-1"
    assert conn.fetchrow.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("age,state", [(5, "RUNNING"), (21, "UNCERTAIN")])
async def test_dispatched_run_without_terminal_is_never_assumed_completed(age, state):
    conn = AsyncMock()
    conn.fetchrow.side_effect = [run(dispatched_at=NOW - timedelta(minutes=age)), None]
    result = await repository().read_daily_research(conn, NOW)
    assert result["state"] == state
    assert result["completedAt"] is None


@pytest.mark.asyncio
async def test_current_failure_preserves_previous_success_with_its_actual_asof():
    yesterday = NOW - timedelta(days=1)
    old = run(
        run_id="old",
        slot_date=yesterday.date(),
        status="READY",
        recorded_at=yesterday,
        payload={"summary": "yesterday"},
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(status="FAILED", recorded_at=NOW, error_class="CodexUnavailableError"),
        old,
    ]
    result = await repository().read_daily_research(conn, NOW)
    assert result["state"] == "FAILED"
    assert result["errorClass"] == "CodexUnavailableError"
    assert result["report"] == {"summary": "yesterday"}
    assert result["asOf"] == yesterday


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["FAILED", "UNCERTAIN", "STALE"])
async def test_effective_report_identity_never_uses_latest_failed_or_pending_run(state):
    yesterday = NOW - timedelta(days=1)
    old = run(
        run_id="old-ready",
        slot_date=yesterday.date(),
        status="READY",
        recorded_at=yesterday,
        payload={"summary": "old"},
    )
    latest = run(status=state if state != "STALE" else "READY", recorded_at=NOW)
    stamp = NOW
    if state == "STALE":
        latest = old
        stamp = NOW.replace(hour=7)
    conn = AsyncMock()
    conn.fetchrow.side_effect = [latest, old]
    result = await repository().read_daily_research(conn, stamp)
    assert result["reportRunId"] == "old-ready"
    assert result["reportCompletedAt"] == yesterday
    assert result["reportSlotDate"] == yesterday.date()
    if state != "STALE":
        assert result["runId"] != result["reportRunId"]
        assert result["completedAt"] == NOW


@pytest.mark.asyncio
async def test_utc_input_uses_shanghai_calendar_and_disabled_does_not_hide_report():
    stamp = datetime(2026, 10, 15, 22, tzinfo=timezone.utc)
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(status="READY", recorded_at=stamp, payload={"summary": "public"}),
        None,
    ]
    result = await repository().read_daily_research(conn, stamp, enabled=False)
    assert result["state"] == "DISABLED"
    assert result["nextRunAt"] == stamp.astimezone(SHANGHAI).replace(hour=7, minute=0)
    assert result["report"] == {"summary": "public"}


def test_daily_settings_default_off_and_migration_has_unique_dispatch_and_no_runtime_update():
    from pathlib import Path
    from backend.config import Settings

    settings = Settings(_env_file=None)
    assert getattr(settings, "daily_research_enabled", None) is False
    path = Path(__file__).resolve().parents[2] / "migrations/081_daily_research.sql"
    assert path.exists(), "daily migration missing"
    sql = path.read_text("utf-8")
    assert "slot_date DATE NOT NULL UNIQUE" in sql
    assert "run_id UUID PRIMARY KEY" in sql
    assert "TO wealthsail_api,wealthsail_worker" in sql
    assert "GRANT INSERT" in sql
    assert "GRANT UPDATE" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hour,minute,latest,enabled,want",
    [
        (6, 59, None, True, "NOT_DUE"),
        (7, 0, None, True, "DUE_NOT_DISPATCHED"),
        (9, 0, None, True, "MISSED"),
        (9, 0, run(), True, "DISPATCHED"),
        (9, 0, run(status="FAILED", recorded_at=NOW), True, "DISPATCHED"),
        (9, 0, run(status="UNCERTAIN", recorded_at=NOW), True, "DISPATCHED"),
        (9, 0, None, False, "DISABLED"),
    ],
)
async def test_today_schedule_distinguishes_due_time_dispatch_and_disabled(
    hour, minute, latest, enabled, want
):
    conn = AsyncMock()
    conn.fetchrow.side_effect = [latest, None]
    result = await repository().read_daily_research(
        conn, NOW.replace(hour=hour, minute=minute), enabled=enabled
    )
    assert result["scheduleState"] == want
    assert result["todayScheduledFor"] == NOW.replace(hour=7, minute=0)
    assert result["report"] is None
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hour,want", [(6, "NOT_DUE"), (8, "DUE_NOT_DISPATCHED"), (9, "MISSED")]
)
async def test_old_success_does_not_hide_missing_today_dispatch(hour, want):
    yesterday = NOW - timedelta(days=1)
    old = run(
        slot_date=yesterday.date(),
        scheduled_for=yesterday.replace(hour=8, minute=30),
        dispatched_at=yesterday,
        recorded_at=yesterday,
        status="READY",
        payload={"summary": "previous"},
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [old, old]
    result = await repository().read_daily_research(conn, NOW.replace(hour=hour))
    assert (
        result["state"] == ("MISSED" if hour == 9 else "STALE")
        and result["scheduleState"] == want
    )
    assert result["todayScheduledFor"] == NOW.replace(hour=7, minute=0)
    assert result["scheduledFor"] == yesterday.replace(hour=8, minute=30)
    assert result["reportCompletedAt"] == yesterday
    assert result["reportSlotDate"] == yesterday.date()


@pytest.mark.asyncio
async def test_schedule_uses_shanghai_day_after_utc_midnight_and_preserves_failed_run():
    stamp = datetime(2026, 10, 16, 0, 30, tzinfo=timezone.utc)
    failed = run(
        scheduled_for=stamp,
        dispatched_at=stamp,
        status="FAILED",
        recorded_at=stamp,
        error_class="ValueError",
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [failed, None]
    result = await repository().read_daily_research(conn, stamp)
    assert result["scheduleState"] == "DISPATCHED"
    assert result["todayScheduledFor"] == stamp.astimezone(SHANGHAI).replace(
        hour=7, minute=0
    )
    assert result["state"] == "FAILED" and result["errorClass"] == "ValueError"
    assert result["reportRunId"] is None and result["reportCompletedAt"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("day", [31, 1])
async def test_non_workday_has_no_today_trigger_and_next_plan_is_monday(day):
    stamp = datetime(2026, 10 if day == 31 else 11, day, 8, tzinfo=SHANGHAI)
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    result = await repository().read_daily_research(conn, stamp, schedule_mode="WEEKDAYS")
    assert result["scheduleState"] == "NON_WORKDAY"
    assert result["todayScheduledFor"] is None
    assert result["nextRunAt"] == datetime(2026, 11, 2, 7, tzinfo=SHANGHAI)
