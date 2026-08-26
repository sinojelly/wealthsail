"""Portfolio projection/read-model invariants from Review 09."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.portfolio import repository, service


class _QueryConnection:
    def __init__(self) -> None:
        self.sql = ""

    async def fetch(self, sql: str, *_args):
        self.sql = " ".join(sql.split())
        return []


@pytest.mark.asyncio
async def test_latest_position_identity_includes_account_and_instrument() -> None:
    """The same instrument held in two accounts must produce two rows."""
    conn = _QueryConnection()

    await repository.latest_positions(conn, "tenant-1")

    assert "DISTINCT ON (ps.account_id, ps.instrument_id)" in conn.sql
    assert "ORDER BY ps.account_id, ps.instrument_id, ps.as_of DESC" in conn.sql


@pytest.mark.asyncio
@pytest.mark.parametrize("since", [None, date(2026, 8, 1)])
async def test_nav_history_does_not_sum_partial_account_valuations(since) -> None:
    """One unpriced account must make the portfolio-day total unavailable."""
    conn = _QueryConnection()

    await repository.nav_history(conn, "tenant-1", since=since)

    sql = conn.sql.upper()
    assert "COUNT(*) = COUNT(TOTAL_NAV)" in sql
    assert "COUNT(*) = COUNT(DAILY_PNL)" in sql


@pytest.mark.asyncio
async def test_missing_quote_is_not_rendered_as_zero_and_sort_is_numeric(monkeypatch) -> None:
    rows = [
        {
            "instrument_id": "instrument-unpriced",
            "account_id": "account-1",
            "ticker": "NOQUOTE",
            "canonical_name": "未估值基金",
            "asset_type": "OFF_EXCHANGE_FUND",
            "quantity": Decimal("10"),
            "avg_cost": Decimal("1"),
            "market_price": None,
            "market_value": None,
            "unrealized_pnl": None,
            "data_quality_status": "UNAVAILABLE",
        },
        {
            "instrument_id": "instrument-9",
            "account_id": "account-1",
            "ticker": "SMALL",
            "canonical_name": "小市值",
            "asset_type": "CN_STOCK",
            "quantity": Decimal("1"),
            "avg_cost": Decimal("1"),
            "market_price": Decimal("9"),
            "market_value": Decimal("9"),
            "unrealized_pnl": Decimal("8"),
            "data_quality_status": "FRESH",
        },
        {
            "instrument_id": "instrument-100",
            "account_id": "account-2",
            "ticker": "LARGE",
            "canonical_name": "大市值",
            "asset_type": "CN_STOCK",
            "quantity": Decimal("1"),
            "avg_cost": Decimal("1"),
            "market_price": Decimal("100"),
            "market_value": Decimal("100"),
            "unrealized_pnl": Decimal("99"),
            "data_quality_status": "FRESH",
        },
    ]

    async def latest_positions(_conn, _tenant):
        return rows

    async def latest_nav(_conn, _tenant):
        return [{"total_nav": Decimal("109"), "as_of": date(2026, 8, 26)}]

    monkeypatch.setattr(repository, "latest_positions", latest_positions)
    monkeypatch.setattr(repository, "latest_nav_per_account", latest_nav)

    result = await service.get_positions(object(), "tenant-1")

    assert [row["symbol"] for row in result] == ["LARGE", "SMALL", "NOQUOTE"]
    unpriced = result[-1]
    assert unpriced["marketValue"] is None
    assert unpriced["pnl"] is None
    assert unpriced["weight"] is None
    assert unpriced["quality"] == "MISSING"


@pytest.mark.asyncio
async def test_summary_as_of_is_oldest_account_snapshot_not_request_clock(monkeypatch) -> None:
    async def latest_nav(_conn, _tenant):
        return [
            {
                "total_nav": Decimal("50"),
                "cash_balance": Decimal("50"),
                "positions_market_value": Decimal("0"),
                "daily_pnl": Decimal("0"),
                "cumulative_pnl": Decimal("0"),
                "data_quality_status": "FRESH",
                "as_of": date(2026, 8, 25),
            },
            {
                "total_nav": Decimal("50"),
                "cash_balance": Decimal("50"),
                "positions_market_value": Decimal("0"),
                "daily_pnl": Decimal("0"),
                "cumulative_pnl": Decimal("0"),
                "data_quality_status": "FRESH",
                "as_of": date(2026, 8, 26),
            },
        ]

    async def no_positions(_conn, _tenant):
        return []

    monkeypatch.setattr(repository, "latest_nav_per_account", latest_nav)
    monkeypatch.setattr(repository, "latest_positions", no_positions)

    result = await service.get_summary(
        object(), "tenant-1", as_of=date(2030, 1, 1)  # type: ignore[arg-type]
    )

    assert result["asOf"] == date(2026, 8, 25)


@pytest.mark.asyncio
async def test_summary_exposes_known_value_and_coverage_without_fake_total(monkeypatch) -> None:
    async def latest_nav(_conn, _tenant):
        return [
            {
                "total_nav": None,
                "known_value": Decimal("150"),
                "valuation_coverage_pct": Decimal("50"),
                "unpriced_position_count": 1,
                "cash_balance": Decimal("100"),
                "positions_market_value": None,
                "daily_pnl": None,
                "cumulative_pnl": None,
                "data_quality_status": "UNAVAILABLE",
                "as_of": date(2026, 8, 26),
            }
        ]

    async def no_positions(_conn, _tenant):
        return []

    monkeypatch.setattr(repository, "latest_nav_per_account", latest_nav)
    monkeypatch.setattr(repository, "latest_positions", no_positions)

    result = await service.get_summary(object(), "tenant-1", as_of=date(2026, 8, 26))  # type: ignore[arg-type]

    assert result["totalAsset"] is None
    assert result["knownValue"] == "150.00"
    assert result["valuationCoveragePct"] == "50.00"
    assert result["unpricedPositionCount"] == 1
    assert result["qualityStatus"] == "MISSING"
