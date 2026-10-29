"""Test zombie AI usage call recovery and aggregation."""

import pytest
from unittest.mock import AsyncMock

from backend.ai_usage.calculation import aggregate_task
from backend.ai_usage.repository import reconcile_stale_calls


def test_aggregate_task_reconciles_zombie_running_calls_when_task_finished():
    """When a task has finished, any leftover RUNNING call must be normalized to UNCERTAIN in calls list."""
    calls = [
        {
            "id": "call-1",
            "model": "codex",
            "status": "RUNNING",
            "tokens": {"totalTokens": None},
            "quota": {"windows": [], "credits": {}},
        }
    ]
    # Task has finished with FAILED status
    task = {
        "id": "task-1",
        "finishedAt": "2026-10-29T08:36:24Z",
        "businessStatus": "FAILED",
    }
    result = aggregate_task(task, calls)
    assert result["inferenceStatus"] == "UNCERTAIN"
    # Individual call in result['calls'] must also reflect UNCERTAIN and ZombieCallRecovered
    assert len(result["calls"]) == 1
    call_res = result["calls"][0]
    assert call_res["status"] == "UNCERTAIN"
    assert call_res["errorClass"] == "ZombieCallRecovered"


def test_aggregate_task_keeps_running_when_task_is_still_running():
    """When task is still RUNNING without finishedAt, RUNNING status is preserved."""
    calls = [
        {
            "id": "call-1",
            "model": "codex",
            "status": "RUNNING",
            "tokens": {"totalTokens": None},
            "quota": {"windows": [], "credits": {}},
        }
    ]
    task = {
        "id": "task-1",
        "finishedAt": None,
        "businessStatus": "RUNNING",
    }
    result = aggregate_task(task, calls)
    assert result["inferenceStatus"] == "RUNNING"
    assert result["calls"][0]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_reconcile_stale_calls_executes_expected_sql():
    """reconcile_stale_calls executes update targeting RUNNING calls with grace period and batch limit."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 2")
    res = await reconcile_stale_calls(conn, limit=50)
    assert res == "UPDATE 2"
    sql = conn.execute.call_args[0][0]
    assert "UPDATE research.ai_usage_calls" in sql
    assert "status = 'UNCERTAIN'" in sql
    assert "ZombieCallRecovered" in sql
    assert "c.status = 'RUNNING'" in sql
    assert "INTERVAL '30 seconds'" in sql
    assert "LIMIT $1" in sql
    assert conn.execute.call_args[0][1] == 50
