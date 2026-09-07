"""Strict-date POST_MARKET performance and contribution read contracts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.reports import repository


@pytest.mark.asyncio
async def test_post_market_performance_requires_complete_current_and_previous_nav() -> (
    None
):
    captured: dict[str, object] = {}

    class Connection:
        async def fetchrow(self, sql: str, *args):
            captured["sql"] = " ".join(sql.split())
            captured["args"] = args
            return {
                "daily_pnl": Decimal("50.00"),
                "daily_pnl_rate": Decimal("5.00"),
                "quality": "FRESH",
                "account_count": 1,
            }

    result = await repository.get_post_market_performance(
        Connection(),
        "tenant-1",
        date(2026, 9, 7),
        owner_user_id="user-1",
    )

    assert result == {
        "dailyPnl": "50.00",
        "dailyPnlRate": "5.00",
        "quality": "FRESH",
        "asOf": "2026-09-07",
    }
    sql = str(captured["sql"])
    assert "current_nav.as_of = $3" in sql
    assert "previous_nav.total_nav" in sql
    assert "previous_nav.data_quality_status" in sql
    assert "calendar.calendar_date < $3" in sql
    assert "daily_external_flow" in sql
    assert "COUNT(current_nav.daily_pnl) = COUNT(*)" in sql
    assert captured["args"] == ("tenant-1", "user-1", date(2026, 9, 7))


@pytest.mark.asyncio
async def test_post_market_contributions_use_same_day_quotes_and_skip_changed_positions() -> (
    None
):
    captured: dict[str, object] = {}

    class Connection:
        async def fetch(self, sql: str, *args):
            captured["sql"] = " ".join(sql.split())
            captured["args"] = args
            return [
                {
                    "instrument_id": "instrument-1",
                    "symbol": "600036",
                    "name": "招商银行",
                    "market_value": Decimal("1020"),
                    "change_pct": Decimal("2"),
                    "quality": "FRESH",
                    "changed_today": False,
                },
                {
                    "instrument_id": "instrument-2",
                    "symbol": "510300",
                    "name": "沪深300ETF",
                    "market_value": Decimal("990"),
                    "change_pct": Decimal("-1"),
                    "quality": "FRESH",
                    "changed_today": False,
                },
            ]

    result = await repository.get_post_market_contributions(
        Connection(),
        "tenant-1",
        date(2026, 9, 7),
        owner_user_id="user-1",
    )

    assert [row["contributionAmount"] for row in result["items"]] == [
        "20.00",
        "-10.00",
    ]
    assert result["complete"] is True
    assert result["positionCount"] == result["attributedCount"] == 2
    sql = str(captured["sql"])
    assert "AT TIME ZONE 'Asia/Shanghai'" in sql
    assert "= $3" in sql
    assert "changed_today" in sql
    assert "changed_today.instrument_id IS NOT NULL AS changed_today" in sql
    assert "reversal.reverses_event_id = original.id" in sql
    assert "COUNT(snapshot.market_value)" in sql
    assert "COUNT(snapshot.market_price)" in sql
    assert "quotes.value = positions.market_price" in sql
    assert "changed_account.id = event.account_id" in sql
    assert "account.status = 'ACTIVE'" in sql
    assert "owner.effective_from <= $3" in sql
    assert "o.quality_status IN ('FRESH', 'ESTIMATED')" in sql
    assert captured["args"] == ("tenant-1", "user-1", date(2026, 9, 7))


@pytest.mark.asyncio
async def test_post_market_contribution_coverage_marks_unattributed_holdings() -> None:
    class Connection:
        async def fetch(self, _sql: str, *_args):
            return [
                {
                    "instrument_id": "instrument-1",
                    "symbol": "600036",
                    "name": "招商银行",
                    "market_value": Decimal("1020"),
                    "change_pct": Decimal("2"),
                    "quality": "FRESH",
                    "changed_today": False,
                },
                {
                    "instrument_id": "instrument-2",
                    "symbol": "510300",
                    "name": "沪深300ETF",
                    "market_value": Decimal("990"),
                    "change_pct": None,
                    "quality": None,
                    "changed_today": True,
                },
            ]

    result = await repository.get_post_market_contributions(
        Connection(), "tenant-1", date(2026, 9, 7)
    )

    assert result["complete"] is False
    assert result["positionCount"] == 2
    assert result["attributedCount"] == 1
