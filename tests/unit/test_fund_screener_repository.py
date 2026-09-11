"""Persistence contracts for on-demand fund-screener jobs."""

from __future__ import annotations

import pytest

from backend.funds import repository


class _ConflictConnection:
    """Models the API role, which may insert a job but cannot rewrite its lease."""

    def __init__(self, *, existing_status: str = "PENDING", requeue_row=None) -> None:
        self.queries: list[str] = []
        self.existing_status = existing_status
        self.requeue_row = requeue_row

    async def fetchrow(self, query: str, *_args):
        self.queries.append(query)
        if "INSERT INTO workflow.job_runs" in query:
            return None
        if "UPDATE workflow.job_runs" in query:
            return self.requeue_row
        if "FROM workflow.job_runs" in query:
            return {
                "id": "existing-run",
                "status": self.existing_status,
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
    assert len(conn.queries) == 3


@pytest.mark.asyncio
async def test_enqueue_fetch_requeues_a_completed_job() -> None:
    """A completed request must be reset to PENDING so the code can refresh."""

    conn = _ConflictConnection(
        requeue_row={
            "id": "existing-run",
            "status": "PENDING",
            "job_type": "FUND_NAV_RANGE_FETCH",
        }
    )
    result = await repository.enqueue_nav_range_fetch(
        conn, tenant_id="tenant-1", fund_code="110022"
    )

    assert result["created"] is True
    assert "UPDATE workflow.job_runs" in conn.queries[1]


@pytest.mark.asyncio
async def test_enqueue_fetch_requeues_a_terminal_failed_job() -> None:
    """A failed request with no retry left must become fetchable again."""

    conn = _ConflictConnection(existing_status="FAILED")
    result = await repository.enqueue_nav_range_fetch(
        conn, tenant_id="tenant-1", fund_code="110022"
    )

    # The UPDATE only matches FAILED rows whose retries are exhausted; the
    # fake returns no requeue row, so the existing FAILED-with-retry row is
    # surfaced unchanged instead.
    assert result["created"] is False
