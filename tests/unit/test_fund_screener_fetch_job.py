"""Durable Worker contract for an on-demand fund-screener refresh."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import Settings
from backend.jobs.data_tasks import DataTaskLease, process_data_task


@pytest.mark.asyncio
async def test_on_demand_fund_range_task_refreshes_one_code_without_loading_positions() -> None:
    """A browser request must refresh its code in the Worker, not a tenant portfolio."""

    lease = DataTaskLease(
        run_id="range-run-1",
        tenant_id="tenant-1",
        job_type="FUND_NAV_RANGE_FETCH",
        payload={"fundCode": "110022"},
        attempt_count=1,
        lease_token="lease-1",
    )
    refresh = AsyncMock()
    pool = MagicMock()
    with patch(
        "backend.jobs.data_tasks.fetch_and_persist_fund_nav_range",
        new=refresh,
        create=True,
    ), patch(
        "backend.jobs.data_tasks.load_open_fund_positions",
        new=AsyncMock(side_effect=AssertionError("must not load tenant positions")),
    ):
        await process_data_task(pool, Settings(_env_file=None), lease)

    refresh.assert_awaited_once_with(pool, "110022")
