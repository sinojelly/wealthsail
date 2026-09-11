"""Contracts for the global daily fund-screener range synchronizer."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.config import Settings
from backend.integrations.fund_nav_ranges import FundNavRange
from backend.jobs.fund_nav_range_sync import (
    fetch_and_persist_fund_nav_range,
    sync_fund_nav_ranges,
)


class _SyncConnection:
    async def fetch(self, query: str, *_args):
        if "FROM ledger.position_snapshots" in query:
            # The same held fund can appear in different tenant portfolios, but
            # the cache must perform one shared provider refresh only.
            return [
                {
                    "instrument_id": "fund-1",
                    "symbol": "110022",
                    "fund_name": "易方达消费行业",
                    "fund_type": "OFF_EXCHANGE_FUND",
                },
                {
                    "instrument_id": "fund-1",
                    "symbol": "110022",
                    "fund_name": "易方达消费行业",
                    "fund_type": "OFF_EXCHANGE_FUND",
                },
            ]
        raise AssertionError(f"unexpected query: {query}")


@asynccontextmanager
async def _connection(conn):
    yield conn


@pytest.mark.asyncio
async def test_daily_range_sync_deduplicates_held_funds_and_persists_unavailable() -> None:
    """A failed code still gets a visible UNAVAILABLE cache row, once only."""

    conn = _SyncConnection()
    fetched_codes: list[str] = []
    persisted: list[FundNavRange] = []

    async def fetcher(fund_code: str, *, fund_name: str, fund_type: str):
        fetched_codes.append(fund_code)
        return FundNavRange(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_type=fund_type,
            latest_nav=None,
            latest_nav_date=None,
            highest_nav=None,
            highest_nav_date=None,
            lowest_nav=None,
            lowest_nav_date=None,
            drawdown_from_high=None,
            rise_from_low=None,
            current_percentile=None,
            inception_date=None,
            data_source="UNAVAILABLE",
        )

    async def persister(_pool, result: FundNavRange):
        persisted.append(result)

    with patch(
        "backend.jobs.fund_nav_range_sync.system_connection",
        return_value=_connection(conn),
    ):
        stats = await sync_fund_nav_ranges(
            MagicMock(),
            Settings(_env_file=None),
            datetime(2026, 9, 11, 2, 0),
            fetcher=fetcher,
            persister=persister,
        )

    assert fetched_codes == ["110022"]
    assert [item.data_source for item in persisted] == ["UNAVAILABLE"]
    assert (stats.scanned_funds, stats.unavailable_funds, stats.failed_funds) == (
        1,
        1,
        0,
    )


@pytest.mark.asyncio
async def test_on_demand_range_fetch_resolves_identity_then_persists_shared_fact() -> None:
    """A requested unknown-to-cache code must be resolved and persisted by the Worker."""

    persisted: list[FundNavRange] = []

    async def resolve(fund_code: str):
        assert fund_code == "110022"
        return ("易方达消费行业", "OFF_EXCHANGE_FUND")

    async def fetch(fund_code: str, *, fund_name: str, fund_type: str):
        return FundNavRange(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_type=fund_type,
            latest_nav=None,
            latest_nav_date=None,
            highest_nav=None,
            highest_nav_date=None,
            lowest_nav=None,
            lowest_nav_date=None,
            drawdown_from_high=None,
            rise_from_low=None,
            current_percentile=None,
            inception_date=None,
            data_source="UNAVAILABLE",
        )

    async def persist(_pool, result: FundNavRange):
        persisted.append(result)

    result = await fetch_and_persist_fund_nav_range(
        MagicMock(),
        "110022",
        identity_resolver=resolve,
        range_fetcher=fetch,
        persister=persist,
    )

    assert result.data_source == "UNAVAILABLE"
    assert [(item.fund_code, item.fund_name) for item in persisted] == [
        ("110022", "易方达消费行业")
    ]
