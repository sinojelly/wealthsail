from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from backend.integrations.market_data import F_NAV, Q_FRESH, Q_STALE
from backend.portfolio.repository import latest_observation_quotes
from backend.portfolio.service import _position_display_quality


@pytest.mark.asyncio
async def test_latest_trading_day_official_fund_nav_is_current_on_weekend() -> None:
    class Connection:
        async def fetch(self, _sql: str, _instrument_ids: list[str]):
            return [
                {
                    "subject_id": "fund-1",
                    "field_code": F_NAV,
                    "value": Decimal("1.2345"),
                    "quality_status": Q_STALE,
                    "as_of": datetime(2026, 8, 28, tzinfo=timezone.utc),
                }
            ]

        async def fetchval(self, _sql: str):
            return date(2026, 8, 28)

    quotes = await latest_observation_quotes(Connection(), ["fund-1"])

    assert quotes == {
        "fund-1": {"price": Decimal("1.2345"), "quality": Q_FRESH}
    }


@pytest.mark.asyncio
async def test_older_official_fund_nav_remains_stale() -> None:
    class Connection:
        async def fetch(self, _sql: str, _instrument_ids: list[str]):
            return [
                {
                    "subject_id": "fund-1",
                    "field_code": F_NAV,
                    "value": Decimal("1.2000"),
                    "quality_status": Q_STALE,
                    "as_of": datetime(2026, 8, 27, tzinfo=timezone.utc),
                }
            ]

        async def fetchval(self, _sql: str):
            return date(2026, 8, 28)

    quotes = await latest_observation_quotes(Connection(), ["fund-1"])

    assert quotes == {
        "fund-1": {"price": Decimal("1.2000"), "quality": Q_STALE}
    }


def test_position_read_model_treats_latest_trading_day_fund_nav_as_current() -> None:
    assert (
        _position_display_quality(
            {
                "asset_type": "OFF_EXCHANGE_FUND",
                "data_quality_status": Q_STALE,
                "current_nav_date": date(2026, 8, 28),
                "latest_trading_date": date(2026, 8, 28),
            }
        )
        == Q_FRESH
    )


def test_position_read_model_does_not_hide_a_genuinely_old_fund_nav() -> None:
    assert (
        _position_display_quality(
            {
                "asset_type": "OFF_EXCHANGE_FUND",
                "data_quality_status": Q_STALE,
                "current_nav_date": date(2026, 8, 27),
                "latest_trading_date": date(2026, 8, 28),
            }
        )
        == Q_STALE
    )
