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
    assert result["state"] == "NOT_RUN"
    assert result["report"] is None
    assert result["nextRunAt"] == NOW.replace(hour=8, minute=30) + timedelta(days=1)
    conn.execute.assert_not_awaited()


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
    stamp = datetime(2026, 10, 15, 23, tzinfo=timezone.utc)
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(status="READY", recorded_at=stamp, payload={"summary": "public"}),
        None,
    ]
    result = await repository().read_daily_research(conn, stamp, enabled=False)
    assert result["state"] == "DISABLED"
    assert result["nextRunAt"] == stamp.astimezone(SHANGHAI).replace(hour=8, minute=30)
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
