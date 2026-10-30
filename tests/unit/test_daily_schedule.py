"""The API and execution slot share the configured Beijing schedule."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.macro import daily_repository as repo


SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    "stamp,mode,time,want",
    [
        ("2026-10-30T07:00:00+08:00", "DAILY", "07:00", "2026-10-31T07:00:00+08:00"),
        ("2026-10-31T06:59:00+08:00", "DAILY", "07:00", "2026-10-31T07:00:00+08:00"),
        ("2026-10-30T07:00:00+08:00", "WEEKDAYS", "07:00", "2026-11-02T07:00:00+08:00"),
        ("2026-10-31T08:00:00+08:00", "WEEKDAYS", "08:15", "2026-11-02T08:15:00+08:00"),
        ("2026-10-30T23:30:00+00:00", "DAILY", "08:15", "2026-10-31T08:15:00+08:00"),
        ("2026-12-31T23:59:00+08:00", "DAILY", "00:00", "2027-01-01T00:00:00+08:00"),
    ],
)
def test_next_run_respects_frequency_time_and_beijing_date(stamp, mode, time, want):
    result = repo.next_run(
        datetime.fromisoformat(stamp), schedule_mode=mode, schedule_time=time
    )
    assert result == datetime.fromisoformat(want)


@pytest.mark.asyncio
async def test_default_daily_schedule_has_a_saturday_slot_before_seven():
    stamp = datetime(2026, 10, 31, 6, 30, tzinfo=SHANGHAI)
    conn = AsyncMock()
    conn.fetchval.return_value = None
    conn.fetchrow.return_value = None
    result = await repo.read_daily_research(conn, stamp)
    assert result["scheduleState"] == "NOT_DUE"
    assert result["todayScheduledFor"] == datetime(2026, 10, 31, 7, tzinfo=SHANGHAI)
    assert result["nextRunAt"] == datetime(2026, 10, 31, 7, tzinfo=SHANGHAI)
    assert result["schedule"] == {
        "mode": "DAILY",
        "time": "07:00",
        "timezone": "Asia/Shanghai",
        "startWindowMinutes": 120,
    }


@pytest.mark.asyncio
async def test_weekday_schedule_has_no_saturday_trigger_and_keeps_configured_time():
    stamp = datetime(2026, 10, 31, 8, 30, tzinfo=SHANGHAI)
    conn = AsyncMock()
    conn.fetchval.return_value = None
    conn.fetchrow.return_value = None
    result = await repo.read_daily_research(
        conn, stamp, schedule_mode="WEEKDAYS", schedule_time="08:15"
    )
    assert result["scheduleState"] == "NON_WORKDAY"
    assert result["todayScheduledFor"] is None
    assert result["nextRunAt"] == datetime(2026, 11, 2, 8, 15, tzinfo=SHANGHAI)


@pytest.mark.asyncio
async def test_configured_time_drives_start_window_in_api():
    stamp = datetime(2026, 10, 31, 9, 40, tzinfo=SHANGHAI)
    conn = AsyncMock()
    conn.fetchval.return_value = None
    conn.fetchrow.return_value = None
    result = await repo.read_daily_research(
        conn, stamp, schedule_time="08:15", start_window_minutes=90
    )
    assert result["scheduleState"] == "DUE_NOT_DISPATCHED"
    result = await repo.read_daily_research(
        conn,
        stamp + timedelta(minutes=5),
        schedule_time="08:15",
        start_window_minutes=90,
    )
    assert result["scheduleState"] == "MISSED"


@pytest.mark.asyncio
async def test_model_slot_uses_configured_time_instead_of_hardcoded_seven():
    stamp = datetime(2026, 10, 31, 8, 16, tzinfo=SHANGHAI)
    conn = AsyncMock()
    conn.fetchrow.return_value = {"run_id": "one-run"}
    result = await repo.claim_daily_run(
        conn,
        stamp,
        {"asOf": stamp.isoformat()},
        input_hash="hash",
        prompt_version="test",
        schedule_time="08:15",
    )
    assert result["run_id"] == "one-run"
    assert conn.fetchrow.call_args.args[2] == datetime(
        2026, 10, 31, 8, 15, tzinfo=SHANGHAI
    )


@pytest.mark.parametrize("value", ["7:00", "24:00", "08:60", "08:15:00", "", "7am"])
def test_invalid_schedule_time_is_rejected_before_service_start(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, daily_research_schedule_time=value)


def test_unsupported_calendar_mode_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, daily_research_schedule_mode="CN_WORKDAYS")


def test_environment_can_select_weekdays_at_eight_fifteen(monkeypatch):
    monkeypatch.setenv("DAILY_RESEARCH_SCHEDULE_MODE", "WEEKDAYS")
    monkeypatch.setenv("DAILY_RESEARCH_SCHEDULE_TIME", "08:15")
    settings = Settings(_env_file=None)
    assert repo.next_run(
        datetime(2026, 10, 30, 23, 30, tzinfo=timezone.utc),
        schedule_mode=settings.daily_research_schedule_mode,
        schedule_time=settings.daily_research_schedule_time,
    ) == datetime(2026, 11, 2, 8, 15, tzinfo=SHANGHAI)


@pytest.mark.asyncio
async def test_api_resume_window_uses_existing_plan_after_environment_time_change(
    monkeypatch,
):
    from backend.macro import daily_pipeline_repository

    now = datetime(2026, 10, 31, 9, 10, tzinfo=SHANGHAI)
    original_slot = now.replace(hour=8, minute=15)
    pipeline = {
        "id": "pipeline",
        "state": "RUNNING",
        "stage": "WAITING_SOURCES",
        "scheduledFor": original_slot,
        "startedAt": original_slot,
        "finishedAt": None,
        "researchRunId": None,
        "errorClass": None,
    }
    monkeypatch.setattr(
        daily_pipeline_repository,
        "read_pipeline",
        AsyncMock(return_value=pipeline),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    result = await repo.read_daily_research(conn, now, schedule_time="07:00")
    assert result["todayScheduledFor"] == original_slot
    assert result["state"] == "RUNNING"
    assert result["scheduleState"] == "DISPATCHED"
    assert result["nextRunAt"] == datetime(2026, 11, 1, 7, tzinfo=SHANGHAI)
