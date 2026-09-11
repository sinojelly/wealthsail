"""Contracts for accumulated-NAV range facts used by the fund screener."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.integrations.fund_nav_ranges import (
    build_fund_nav_range,
    fetch_fund_identity,
    fetch_fund_nav_range,
)


def test_accumulated_nav_range_uses_full_history_for_extremes_and_percentile() -> None:
    """Using unit NAV or a short window would understate the true drawdown."""

    result = build_fund_nav_range(
        fund_code="110022",
        fund_name="易方达消费行业",
        fund_type="OFF_EXCHANGE_FUND",
        history=[
            (date(2020, 1, 2), Decimal("1.00")),
            (date(2021, 6, 1), Decimal("1.50")),
            (date(2022, 10, 31), Decimal("0.90")),
            (date(2026, 9, 10), Decimal("1.10")),
        ],
    )

    assert result.data_source == "AKSHARE"
    assert result.inception_date == date(2020, 1, 2)
    assert result.latest_nav == Decimal("1.10")
    assert result.latest_nav_date == date(2026, 9, 10)
    assert (result.highest_nav, result.highest_nav_date) == (
        Decimal("1.50"),
        date(2021, 6, 1),
    )
    assert (result.lowest_nav, result.lowest_nav_date) == (
        Decimal("0.90"),
        date(2022, 10, 31),
    )
    assert result.drawdown_from_high == Decimal("26.6667")
    assert result.rise_from_low == Decimal("22.2222")
    assert result.current_percentile == Decimal("33.3333")


@pytest.mark.asyncio
async def test_fetch_failure_returns_unavailable_without_fabricating_range_values() -> None:
    """A provider outage must not leave stale numbers mislabeled as current."""

    class FailingProvider:
        def fund_open_fund_info_em(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = await fetch_fund_nav_range(
        "110022",
        fund_name="易方达消费行业",
        fund_type="OFF_EXCHANGE_FUND",
        provider=FailingProvider(),
    )

    assert result.data_source == "UNAVAILABLE"
    assert result.fund_code == "110022"
    assert result.fund_name == "易方达消费行业"
    assert result.latest_nav is None
    assert result.highest_nav is None
    assert result.lowest_nav is None
    assert result.current_percentile is None


@pytest.mark.asyncio
async def test_fetch_parses_akshare_accumulated_nav_history() -> None:
    """A column rename or unit-NAV substitution must not silently create a range."""

    class Frame:
        empty = False
        columns = ("净值日期", "累计净值")

        def iterrows(self):
            yield 0, {"净值日期": "2024-01-02", "累计净值": "1.00"}
            yield 1, {"净值日期": "2024-06-03", "累计净值": "1.25"}
            yield 2, {"净值日期": "2024-12-31", "累计净值": "1.10"}

    class Provider:
        def fund_open_fund_info_em(self, **_kwargs):
            return Frame()

    result = await fetch_fund_nav_range(
        "110022",
        fund_name="易方达消费行业",
        fund_type="OFF_EXCHANGE_FUND",
        provider=Provider(),
    )

    assert result.data_source == "AKSHARE"
    assert result.latest_nav == Decimal("1.10")
    assert result.highest_nav == Decimal("1.25")
    assert result.lowest_nav == Decimal("1.00")
    assert result.drawdown_from_high == Decimal("12.0000")


@pytest.mark.asyncio
async def test_on_demand_identity_resolution_uses_provider_name_without_guessing() -> None:
    """A range cache row needs a provider-confirmed name, not a code-derived label."""

    class Frame:
        empty = False
        columns = ("item", "value")

        def iterrows(self):
            yield 0, {"item": "基金代码", "value": "110022"}
            yield 1, {"item": "基金名称", "value": "易方达消费行业"}

    class Provider:
        def fund_individual_basic_info_xq(self, **_kwargs):
            return Frame()

    assert await fetch_fund_identity("110022", provider=Provider()) == (
        "易方达消费行业",
        "OFF_EXCHANGE_FUND",
    )
