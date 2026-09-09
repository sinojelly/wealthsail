"""Unit tests for deterministic pre-market holding trend calculation."""

from datetime import date
from decimal import Decimal

import pytest


def _trend_module():
    from backend.reports import holding_trends

    return holding_trends


def test_prior_same_direction_streak_excludes_today() -> None:
    trends = _trend_module()
    history = [
        trends.PricePoint(date(2026, 9, 4), Decimal("100"), "CLOSE", "FRESH"),
        trends.PricePoint(date(2026, 9, 7), Decimal("102"), "CLOSE", "FRESH"),
        trends.PricePoint(date(2026, 9, 8), Decimal("103"), "CLOSE", "FRESH"),
    ]

    assert (
        trends.calculate_holding_trend(Decimal("0.5"), history).display
        == "📈 连涨2天 (+3.00%)"
    )


def test_opposite_today_direction_is_reversal() -> None:
    trends = _trend_module()
    history = [
        trends.PricePoint(date(2026, 9, 7), Decimal("102"), "CLOSE", "FRESH"),
        trends.PricePoint(date(2026, 9, 8), Decimal("100"), "CLOSE", "FRESH"),
    ]

    assert trends.calculate_holding_trend(Decimal("0.5"), history).display == "转涨"


def test_missing_history_is_unavailable() -> None:
    trends = _trend_module()

    trend = trends.calculate_holding_trend(Decimal("1"), [])

    assert (trend.display, trend.quality) == ("--", "UNAVAILABLE")


class _CachedTrendConnection:
    def __init__(self) -> None:
        self.history_queries = 0

    async def fetch(self, query: str, *_args):
        if "market.instrument_trend_snapshots" in query:
            return [
                {
                    "instrument_id": "instrument-1",
                    "previous_direction": "UP",
                    "streak_direction": "UP",
                    "streak_days": 2,
                    "streak_change_pct": Decimal("3"),
                    "quality_status": "FRESH",
                }
            ]
        if "market.market_observations" in query:
            self.history_queries += 1
            return []
        raise AssertionError(f"unexpected query: {query}")

    async def fetchval(self, query: str, *_args):
        assert "market.upsert_instrument_trend_snapshot" in query
        return False


@pytest.mark.asyncio
async def test_same_day_cache_hit_skips_history_lookup() -> None:
    from backend.reports import repository

    conn = _CachedTrendConnection()
    trends = await repository.resolve_instrument_trends(
        conn,
        ["instrument-1"],
        date(2026, 9, 9),
        {"instrument-1": Decimal("1")},
    )

    assert trends["instrument-1"].display == "📈 连涨2天 (+3.00%)"
    assert conn.history_queries == 0
