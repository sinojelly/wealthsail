"""Persistence contracts for on-demand fund-screener jobs."""

from __future__ import annotations

import pytest

from backend.funds import repository


class _ConflictConnection:
    """Models the API role, which may insert a job but cannot rewrite its lease."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetchrow(self, query: str, *_args):
        self.queries.append(query)
        if "INSERT INTO workflow.job_runs" in query:
            return None
        if "FROM workflow.job_runs" in query:
            return {
                "id": "existing-run",
                "status": "PENDING",
                "job_type": "FUND_NAV_RANGE_FETCH",
            }
        raise AssertionError("unexpected query")


@pytest.mark.asyncio
async def test_enqueue_fetch_reuses_conflicting_job_without_updating_it() -> None:
    """Concurrent requests must return the existing task without altering its lease state."""

    conn = _ConflictConnection()
    result = await repository.enqueue_nav_range_fetch(
        conn, tenant_id="tenant-1", fund_code="110022"
    )

    assert result == {
        "id": "existing-run",
        "status": "PENDING",
        "job_type": "FUND_NAV_RANGE_FETCH",
        "created": False,
    }
    assert len(conn.queries) == 2
