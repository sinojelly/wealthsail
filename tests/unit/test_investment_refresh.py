from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_refresh_status_is_tenant_scoped():
    from backend.investment.service import refresh_status
    from backend.errors import NotFoundError

    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with pytest.raises(NotFoundError):
        await refresh_status(conn, "tenant-a", "00000000-0000-0000-0000-000000000099")
    sql, *args = conn.fetchrow.await_args.args
    assert "tenant_id" in sql
    assert "tenant-a" in args


@pytest.mark.asyncio
async def test_diagnosis_uses_stored_snapshot_and_never_calls_provider():
    from backend.investment.service import analysis

    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "result": {"score": None, "state": "UNSUPPORTED"},
        "as_of": "2025-01-01",
        "input_hash": "hash",
        "source_uri": "fixture://history",
    }
    result = await analysis(conn, "index", "hs300")
    assert result["score"] is None
    assert result["inputHash"] == "hash"
    assert result["state"] == "UNSUPPORTED"


def test_worker_claims_and_dispatches_investment_task():
    from pathlib import Path
    from backend.jobs.data_tasks import _SUPPORTED_JOB_TYPES

    assert "INVESTMENT_RESEARCH_REFRESH" in _SUPPORTED_JOB_TYPES
    content = Path("backend/jobs/data_tasks.py").read_text("utf-8")
    assert "'INVESTMENT_RESEARCH_REFRESH'" in content


@pytest.mark.asyncio
async def test_old_stored_score_is_suppressed_on_read():
    from datetime import date
    from backend.investment.service import analysis
    conn = AsyncMock()
    conn.fetchrow.return_value = {"result": {"score": 85, "state": "STRONG", "market": "SSE"},
                                 "as_of": date(2025, 1, 1), "input_hash": "hash", "source_uri": "fixture://history"}
    conn.fetchval.return_value = date(2026, 10, 12)
    result = await analysis(conn, "index", "hs300")
    assert result["score"] is None
    assert result["state"] == "STALE"


@pytest.mark.asyncio
async def test_unregistered_fund_refresh_reuses_existing_nav_job(monkeypatch):
    from backend.investment.service import enqueue_refresh
    from backend.investment.schemas import RefreshRequest
    from backend.funds import service as funds
    enqueue = AsyncMock(return_value={"runId": "nav-job", "created": True, "status": "QUEUED"})
    monkeypatch.setattr(funds, "enqueue_fetch", enqueue)
    result = await enqueue_refresh(object(), "tenant-a", RefreshRequest(kind="nav", id="110022"))
    assert result["runId"] == "nav-job"
    enqueue.assert_awaited_once()
