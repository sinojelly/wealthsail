"""List values preserve NAV versus return-series and adjusted-price semantics."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.investment.repository import screen
from backend.investment.schemas import ScreenQuery
from backend.investment.service import fund_dto


def _fund(**changes):
    row = {
        "instrument_id": "fund-id",
        "fund_code": "000001",
        "fund_name": "基金",
        "fund_type": "INDEX",
        "trade_form": "FUND",
        "market": "FUND_CN",
        "currency": "CNY",
    }
    row.update(changes)
    return row


@pytest.mark.parametrize(
    "series,point,want,basis",
    [
        (
            "PROVIDER_RETURN_INDEX",
            {"close": 130, "unitNav": 1.3},
            Decimal("1.3"),
            "UNIT_NAV",
        ),
        ("UNIT_NAV", {"close": 1.4}, Decimal("1.4"), "UNIT_NAV"),
        ("ETF_HFQ", {"close": 2.5}, Decimal("2.5"), "ADJUSTED_PRICE"),
        ("ACCUMULATED_NAV", {"close": 3.2}, Decimal("3.2"), "ACCUMULATED_NAV"),
    ],
)
def test_latest_research_value_uses_correct_series_basis(series, point, want, basis):
    item = fund_dto(
        _fund(
            latest_series_kind=series,
            latest_point=point,
            latest_series_date=date(2026, 10, 13),
            latest_nav=9.9,
            latest_nav_date=date(2025, 1, 1),
        )
    )
    assert item["latestValue"] == want
    assert item["latestValueBasis"] == basis
    assert item["latestDate"] == "2026-10-13"


def test_provider_return_close_never_becomes_nav_when_unit_nav_is_missing():
    item = fund_dto(
        _fund(
            latest_series_kind="PROVIDER_RETURN_INDEX",
            latest_point={"close": 130},
            latest_series_date=date(2026, 10, 13),
        )
    )
    assert item["latestValue"] is None and item["latestValueBasis"] is None
    assert item["latestDate"] is None


@pytest.mark.parametrize(
    "point",
    [
        {},
        {"unitNav": float("nan"), "close": 130},
        {"unitNav": -1, "close": 130},
        {"unitNav": True},
    ],
)
def test_unusable_research_value_falls_back_to_old_accumulated_nav_range(point):
    item = fund_dto(
        _fund(
            latest_series_kind="PROVIDER_RETURN_INDEX",
            latest_point=point,
            latest_series_date=date(2026, 10, 13),
            latest_nav=Decimal("2.2"),
            latest_nav_date=date(2026, 10, 12),
        )
    )
    assert item["latestValue"] == Decimal("2.2")
    assert item["latestValueBasis"] == "ACCUMULATED_NAV"
    assert item["latestDate"] == "2026-10-12"


def test_json_point_and_old_fixture_shapes_remain_supported():
    item = fund_dto(
        _fund(
            latest_series_kind="PROVIDER_RETURN_INDEX",
            latest_point='{"unitNav":1.3,"close":130}',
            latest_series_date=date(2026, 10, 13),
        )
    )
    assert item["latestValue"] == Decimal("1.3")
    old = fund_dto(_fund(latest_nav=Decimal("2.2"), latest_nav_date=date(2026, 10, 12)))
    assert old["latestValueBasis"] == "ACCUMULATED_NAV"
    assert old["latestDate"] == "2026-10-12"
    empty = fund_dto(_fund())
    assert (
        empty["latestValue"] is None
        and empty["latestValueBasis"] is None
        and empty["latestDate"] is None
    )


@pytest.mark.asyncio
async def test_screener_reads_last_point_and_its_series_date_from_latest_snapshot():
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchval.return_value = 0
    await screen(conn, ScreenQuery())
    sql = conn.fetch.await_args.args[0]
    assert "n.latest_point AS latest_point" in sql
    assert "s.series_kind AS latest_series_kind" in sql
    assert "COALESCE((n.latest_point->>'date')::date,s.as_of) AS latest_series_date" in sql
