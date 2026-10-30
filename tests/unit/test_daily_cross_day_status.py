"""An unfinished previous-day pipeline does not replace today's schedule or result."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from backend.macro import daily_repository, daily_pipeline_repository
from test_daily_research_job import NOW
from test_daily_repository import run


def yesterday_pipeline(state="RUNNING"):
    started = NOW.replace(hour=23, minute=59)
    return {
        "id": "yesterday",
        "state": state,
        "stage": "VALIDATING_INPUT",
        "scheduledFor": started,
        "startWindowEndsAt": started + timedelta(hours=2),
        "runtimeDeadlineAt": started + timedelta(hours=4),
        "startedAt": started,
        "finishedAt": None,
        "researchRunId": None,
        "errorClass": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("old_state", ["RUNNING", "MISSED"])
async def test_prior_execution_keeps_its_actual_time_without_marking_today_missed(
    monkeypatch, old_state
):
    old = yesterday_pipeline(old_state)
    now = old["startedAt"] + timedelta(minutes=11)
    monkeypatch.setattr(
        daily_pipeline_repository, "read_pipeline", AsyncMock(return_value=old)
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    result = await daily_repository.read_daily_research(
        conn, now, schedule_time="23:59"
    )
    assert result["state"] == old_state
    assert result["scheduleState"] == "NOT_DUE"
    assert result["scheduledFor"] == old["scheduledFor"]
    assert result["startedAt"] == old["startedAt"]
    assert result["todayScheduledFor"] == now.replace(hour=23, minute=59)
    assert result["nextRunAt"] == now.replace(hour=23, minute=59)
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_day_debug_result_is_not_hidden_by_yesterdays_pipeline(
    monkeypatch,
):
    old = yesterday_pipeline()
    now = old["startedAt"] + timedelta(minutes=11)
    monkeypatch.setattr(
        daily_pipeline_repository, "read_pipeline", AsyncMock(return_value=old)
    )
    latest = run(
        run_id="today-debug",
        slot_date=now.date(),
        dispatched_at=now - timedelta(minutes=1),
        scheduled_for=now - timedelta(minutes=1),
        recorded_at=now,
        status="FAILED",
        error_class="FixtureFailure",
        run_mode="DEBUG",
        scheduled_consumed=False,
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [latest, None]
    result = await daily_repository.read_daily_research(
        conn, now, schedule_time="23:59"
    )
    assert result["state"] == "FAILED"
    assert result["runId"] == "today-debug"
    assert result["pipeline"] is None
    assert result["todayScheduledFor"] == now.replace(hour=23, minute=59)
