from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from backend.operations.repository import (
    enqueue_manual_fund_estimate,
    fund_estimate_status,
)


@pytest.mark.asyncio
async def test_fund_estimate_status_exposes_calendar_run_and_coverage() -> None:
    class Connection:
        async def fetchrow(self, sql: str, *_args):
            if "reference.trading_calendars" in sql:
                return {
                    "today": date(2026, 8, 31),
                    "calendar_configured": True,
                    "today_configured": True,
                    "is_trading_day": True,
                    "latest_trading_date": date(2026, 8, 31),
                }
            if "manual:FUND_1430" in sql:
                return {
                    "id": "run-1",
                    "status": "RUNNING",
                    "started_at": datetime(2026, 8, 31, 6, 30, tzinfo=timezone.utc),
                    "finished_at": None,
                    "error_class": None,
                }
            if "ESTIMATED_NAV" in sql:
                assert "AT TIME ZONE 'Asia/Shanghai'" in sql
                return {
                    "held_fund_count": 12,
                    "estimated_count": 8,
                    "unavailable_count": 4,
                    "latest_as_of": datetime(2026, 8, 31, 6, 31, tzinfo=timezone.utc),
                }
            raise AssertionError(sql)

    result = await fund_estimate_status(Connection(), "tenant-1")

    assert result["canRun"] is True
    assert result["calendar"]["status"] == "READY"
    assert result["run"]["status"] == "RUNNING"
    assert result["coverage"] == {
        "heldFundCount": 12,
        "estimatedCount": 8,
        "unavailableCount": 4,
        "latestAsOf": datetime(2026, 8, 31, 6, 31, tzinfo=timezone.utc),
    }


@pytest.mark.asyncio
async def test_manual_fund_estimate_request_is_deduplicated_while_active() -> None:
    class Connection:
        inserted = False

        async def execute(self, _sql: str, *_args):
            return "SELECT 1"

        async def fetchrow(self, sql: str, *_args):
            if "status IN ('PENDING', 'RUNNING')" in sql:
                return {
                    "id": "run-existing",
                    "status": "PENDING",
                    "started_at": datetime.now(timezone.utc),
                    "finished_at": None,
                    "error_class": None,
                }
            if "INSERT INTO workflow.job_runs" in sql:
                self.inserted = True
            raise AssertionError(sql)

    conn = Connection()
    result = await enqueue_manual_fund_estimate(conn, "tenant-1")

    assert result["created"] is False
    assert result["run"]["id"] == "run-existing"
    assert conn.inserted is False


@pytest.mark.asyncio
async def test_manual_fund_estimate_request_creates_persistent_job_run() -> None:
    class Connection:
        async def execute(self, sql: str, *_args):
            assert "pg_advisory_xact_lock" in sql
            return "SELECT 1"

        async def fetchrow(self, sql: str, *_args):
            if "status IN ('PENDING', 'RUNNING')" in sql:
                return None
            if "FROM workflow.jobs" in sql:
                return {"id": "job-fund"}
            if "INSERT INTO workflow.job_runs" in sql:
                assert "manual:FUND_1430:" in sql
                assert "'PENDING'" in sql
                return {
                    "id": "run-new",
                    "status": "PENDING",
                    "started_at": datetime.now(timezone.utc),
                    "finished_at": None,
                    "error_class": None,
                }
            raise AssertionError(sql)

    result = await enqueue_manual_fund_estimate(Connection(), "tenant-1")

    assert result["created"] is True
    assert result["run"]["id"] == "run-new"


@pytest.mark.asyncio
async def test_debug_fund_estimate_request_encodes_authenticated_recipient() -> None:
    seen: dict[str, object] = {}

    class Connection:
        async def execute(self, sql: str, *args):
            assert "pg_advisory_xact_lock" in sql
            seen["lock_args"] = args
            return "SELECT 1"

        async def fetchrow(self, sql: str, *args):
            if "status IN ('PENDING', 'RUNNING')" in sql:
                seen["dedupe_args"] = args
                return None
            if "FROM workflow.jobs" in sql:
                return {"id": "job-fund"}
            if "INSERT INTO workflow.job_runs" in sql:
                seen["insert_sql"] = sql
                seen["insert_args"] = args
                return {
                    "id": "run-debug",
                    "status": "PENDING",
                    "started_at": datetime.now(timezone.utc),
                    "finished_at": None,
                    "error_class": None,
                }
            raise AssertionError(sql)

    target_user_id = "00000000-0000-0000-0000-000000000001"
    result = await enqueue_manual_fund_estimate(
        Connection(), "tenant-1", target_user_id=target_user_id
    )

    assert result["run"]["id"] == "run-debug"
    assert f"debug:FUND_1430:{target_user_id}:" in str(seen["insert_args"])
    assert "manual:FUND_1430:" not in str(seen["insert_args"])
    assert seen["dedupe_args"][-1] is True
    assert seen["insert_args"][-1] == f'{{"targetUserId":"{target_user_id}"}}'
