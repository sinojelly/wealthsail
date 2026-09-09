"""Contracts for the global 22:00 official fund-NAV history synchronizer."""

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.config import Settings
from backend.integrations.market_data import Observation
from backend.jobs.data_tasks import NavPersistenceStats


def test_initial_history_window_starts_at_the_sixtieth_latest_trading_day() -> None:
    """A new held fund receives the requested 60 trading-day official NAV backfill."""
    from backend.jobs.fund_nav_sync import nav_history_window

    end = date(2026, 9, 9)
    trading_days = [end - timedelta(days=index) for index in range(60)]

    start, resolved_end = nav_history_window(
        latest_nav_on=None,
        trading_days=trading_days,
        as_of=end,
    )

    assert start == date(2026, 7, 12)
    assert resolved_end == end


def test_incremental_history_window_rechecks_a_short_overlap_for_revisions() -> None:
    """Existing facts are refreshed incrementally without downloading all history."""
    from backend.jobs.fund_nav_sync import nav_history_window

    start, end = nav_history_window(
        latest_nav_on=date(2026, 9, 7),
        trading_days=[date(2026, 9, 9)],
        as_of=date(2026, 9, 9),
    )

    assert (start, end) == (date(2026, 8, 31), date(2026, 9, 9))


class _SyncConnection:
    def __init__(self) -> None:
        self.today = date(2026, 9, 9)

    async def fetch(self, query: str, *_args):
        if "FROM ledger.position_snapshots" in query:
            # This represents the same reference fund held by two tenants.
            # The defensive task boundary must still issue one provider call.
            return [
                {
                    "instrument_id": "fund-1",
                    "symbol": "110022",
                    "latest_nav_on": None,
                },
                {
                    "instrument_id": "fund-1",
                    "symbol": "110022",
                    "latest_nav_on": None,
                },
            ]
        if "FROM reference.trading_calendars" in query:
            return [
                {"calendar_date": self.today - timedelta(days=index)}
                for index in range(60)
            ]
        raise AssertionError(f"unexpected query: {query}")


@asynccontextmanager
async def _connection(conn):
    yield conn


@pytest.mark.asyncio
async def test_global_sync_deduplicates_shared_fund_before_fetching_and_persisting() -> None:
    """One globally shared fund creates one provider request and one NAV write."""
    from backend.jobs.fund_nav_sync import sync_fund_nav_history

    conn = _SyncConnection()
    fetched_symbols: list[str] = []
    persisted_ids: list[str] = []

    async def fetcher(symbol, _start, _end, _client):
        fetched_symbols.append(symbol)
        return [
            Observation(
                symbol=symbol,
                subject_type="FUND",
                field_code="NAV",
                value=Decimal("1.2345"),
                as_of=datetime.combine(conn.today, time.min, tzinfo=timezone.utc),
                quality_status="FRESH",
            )
        ]

    async def persister(_pool, instrument_id, observations):
        persisted_ids.append(instrument_id)
        assert len(observations) == 1
        return NavPersistenceStats(inserted_count=1, skipped_count=0)

    with patch(
        "backend.jobs.fund_nav_sync.system_connection",
        return_value=_connection(conn),
    ):
        stats = await sync_fund_nav_history(
            MagicMock(),
            Settings(_env_file=None),
            datetime(2026, 9, 9, 22, 0, tzinfo=timezone.utc),
            fetcher=fetcher,
            persister=persister,
        )

    assert fetched_symbols == ["110022"]
    assert persisted_ids == ["fund-1"]
    assert (stats.scanned_instruments, stats.persisted_observations) == (1, 1)
