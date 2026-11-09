"""A retained pipeline cannot replace a newer completed report's identity."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from backend.macro.daily_repository import read_daily_research
from test_daily_repository import NOW, run


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["SCHEDULED", "DEBUG", "MANUAL"])
async def test_newer_today_success_supersedes_an_older_failed_pipeline(
    monkeypatch, run_mode
):
    pipeline = {
        "state": "FAILED",
        "researchRunId": "old-failed",
        "startedAt": NOW - timedelta(hours=1),
        "finishedAt": NOW - timedelta(minutes=30),
        "errorClass": "ValueError",
    }
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=pipeline),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = run(
        status="READY",
        recorded_at=NOW,
        payload={"summary": "today complete"},
        run_mode=run_mode,
    )
    result = await read_daily_research(conn, NOW)
    assert result["state"] == "READY"
    assert result["pipeline"] is None
    assert result["runId"] == result["reportRunId"] == "run-1"
    assert result["completedAt"] == NOW
    assert result["errorClass"] is None and result["failureReasonCode"] is None


@pytest.mark.asyncio
async def test_a_newer_running_pipeline_keeps_ownership_over_an_older_today_report(
    monkeypatch,
):
    pipeline = {
        "state": "RUNNING",
        "researchRunId": "new-running",
        "startedAt": NOW - timedelta(minutes=1),
        "finishedAt": None,
        "errorClass": None,
    }
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=pipeline),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = run(
        status="READY",
        recorded_at=NOW - timedelta(minutes=2),
        payload={"summary": "earlier today"},
    )
    result = await read_daily_research(conn, NOW)
    assert result["state"] == "RUNNING"
    assert result["runId"] == "new-running" and result["reportRunId"] == "run-1"
