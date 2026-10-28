"""NAV buy/sell coordinates use disclosed historical support and resistance."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json

import pytest

from backend.indicators.actionable_zones import compute_actionable_price_plan


def rows(prices):
    start = date(2024, 1, 1)
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "close": str(value)}
        for i, value in enumerate(prices)
    ]


def nav(prices, **kwargs):
    return compute_actionable_price_plan(
        rows(prices),
        subject_kind="nav",
        subject_id="008298",
        price_basis="UNIT_NAV",
        **kwargs,
    )


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def test_historical_percentile_prices_use_linear_interpolation_and_latest_500_samples():
    result = nav(
        [Decimal("100")] * 30 + [Decimal(1) + Decimal(i) / 1000 for i in range(500)]
    )
    assert result["methodVersion"] == "actionable-zones-v6"
    assert result["historicalPriceWindow"]["sampleCount"] == 500
    assert result["historicalPriceWindow"]["state"] == "READY"
    for percentile in (30, 35, 40, 45, 50, 55, 60, 65, 75, 85, 92, 95):
        expected = Decimal(1) + Decimal(499) * Decimal(percentile) / 100000
        assert result["indicators"][f"p{percentile}"] == pytest.approx(float(expected))
        assert result["indicatorStates"][f"p{percentile}"]["sampleCount"] == 500
    assert result["indicators"]["peakHigh"] == 1.499


def test_high_position_anchors_both_tracks_to_medium_term_historical_support():
    result = nav([Decimal(1) + Decimal(i) / 1000 for i in range(500)])
    values = result["indicators"]
    scale = Decimal(str(values["navVolatility14"]))
    high = min(
        Decimal(str(values["ma60"])),
        Decimal(str(values["ma200"])),
        Decimal(str(values["p45"])),
        Decimal("1.22455"),
    )
    low = max(
        Decimal(str(values["troughLow"])),
        min(
            high - Decimal("0.8") * scale,
            Decimal(str(values["p30"])),
            Decimal("1.15968"),
        ),
    )
    assert values["navPercentile1y"] == 100
    for track in result["tracks"]:
        assert Decimal(track["entryZoneMax"]) == money(high)
        assert Decimal(track["entryZoneMin"]) == money(low)
        assert Decimal(track["entryZoneMax"]) < Decimal(str(values["ma20"]))
        assert Decimal(track["stopLossPrice"]) < Decimal(track["entryZoneMin"])
        assert track["buyZoneAnchor"] == "TWO_YEAR_LOWER_SUPPORT"


def test_span_position_can_select_history_anchor_even_below_65th_year_percentile():
    # Older observations are low; the latest year has a higher range.
    prices = [Decimal("0.5")] * 340 + [Decimal("1.4")] * 159 + [Decimal("1.2")]
    result = nav(prices)
    assert result["indicators"]["navPercentile1y"] < 65
    assert 1.2 > 0.5 + 0.6 * (1.4 - 0.5)
    assert all(
        track["buyZoneAnchor"] == "TWO_YEAR_LOWER_SUPPORT" for track in result["tracks"]
    )


def test_low_position_both_tracks_are_capped_at_historical_p40():
    prices = (
        [Decimal("1")] * 300
        + [Decimal("1.8")] * 180
        + [Decimal("2")] * 19
        + [Decimal("1.3")]
    )
    result = nav(prices)
    values = result["indicators"]
    right = result["tracks"][1]
    expected = min(
        Decimal(str(values["ma20"])),
        Decimal("1.3") + Decimal("0.3") * Decimal(str(values["navVolatility14"])),
        Decimal(str(values["p40"])),
        Decimal("1.35"),
    )
    assert values["navPercentile1y"] < 70
    assert Decimal(right["entryZoneMax"]) == money(expected)
    assert Decimal(right["entryZoneMin"]) <= Decimal(right["entryZoneMax"])


def test_sell_zone_is_historical_resistance_and_can_already_be_reached():
    result = nav([Decimal(1) + Decimal(i) / 1000 for i in range(500)])
    values = result["indicators"]
    scale = Decimal(str(values["navVolatility14"]))
    for track in result["tracks"]:
        first = max(
            Decimal("1.22455") * Decimal("1.12"),
            Decimal(str(values["ma200"])),
            Decimal(str(values["p75"])),
            Decimal("1.2994"),
        )
        second = min(
            Decimal(str(values["peakHigh"])) * Decimal("0.992"),
            max(
                first + Decimal("1.2") * scale,
                Decimal(str(values["p92"])),
                Decimal("1.40918"),
            ),
        )
        first = min(first, second - Decimal("0.8") * scale)
        assert Decimal(track["sellZoneMin"]) == money(first)
        assert Decimal(track["sellZoneMax"]) == money(second)
        assert track["takeProfitT1"] == track["sellZoneMin"]
        assert track["takeProfitT2"] == track["sellZoneMax"]
        assert Decimal(result["currentPrice"]) >= Decimal(track["sellZoneMin"])
        assert track["inSellZone"] is True
        assert track["entryBlocked"] is True
        assert track["entryReady"] is False
        assert track["inBuyZone"] is False
        assert (
            track["entryStatus"]
            == "🔥 已入建议卖出区间(100%分位)·持仓宜止盈/空仓勿追高"
        )
        assert track["entryStatusTone"] == "rose"


def test_high_percentile_alone_is_sell_signal_without_chasing_current_price():
    result = nav([Decimal("1")] * 220)
    for track in result["tracks"]:
        assert track["sellZoneMin"] is track["sellZoneMax"] is None
        assert track["priceState"] == "PARTIAL"
        assert track["inSellZone"] is True
        assert track["entryReady"] is False


def test_insufficient_historical_window_does_not_invent_percentile_anchors():
    result = nav([Decimal("1")] * 119)
    assert result["historicalPriceWindow"]["state"] == "INSUFFICIENT"
    assert result["historicalPriceWindow"]["sampleCount"] == 119
    assert result["indicators"]["p85"] is None
    assert result["indicatorStates"]["p85"]["state"] == "INSUFFICIENT"
    for track in result["tracks"]:
        assert track["priceState"] == "PARTIAL"
        assert track["entryReady"] is False
        assert any("120" in reason for reason in track["reasons"])


def test_cutoff_invalid_observation_and_ordering_apply_to_historical_anchors():
    prices = [Decimal(1) + Decimal(i) / 1000 for i in range(300)]
    data = rows(prices + [Decimal("1000")])
    before = deepcopy(data)
    result = compute_actionable_price_plan(
        data,
        subject_kind="nav",
        subject_id="008298",
        price_basis="UNIT_NAV",
        as_of=data[-2]["date"],
    )
    assert result["indicators"]["peakHigh"] == 1.299
    assert result["historicalPriceWindow"]["sampleCount"] == 300
    assert data == before
    data[-120]["close"] = "NaN"
    result = compute_actionable_price_plan(
        data, subject_kind="nav", subject_id="008298", price_basis="UNIT_NAV"
    )
    assert result["indicators"]["p85"] is None
    assert result["historicalPriceWindow"]["sampleCount"] == 119
    data[-1]["date"] = data[-2]["date"]
    result = compute_actionable_price_plan(
        data, subject_kind="nav", subject_id="008298", price_basis="UNIT_NAV"
    )
    assert result["historicalPriceWindow"]["state"] == "CONFLICT"
    assert result["historicalPriceWindow"]["sampleCount"] == 0
    json.dumps(result, allow_nan=False)


def test_index_ohlc_keeps_legacy_coordinates_without_nav_history_anchors():
    data = rows([100] * 220)
    for row in data:
        row.update(high=101, low=99)
    result = compute_actionable_price_plan(
        data, subject_kind="index", subject_id="bank"
    )
    right = result["tracks"][1]
    assert right["entryZoneMin"] == "98.90"
    assert right["entryZoneMax"] == "100.50"
    assert right["takeProfitT1"] == "106.90"
    assert result["indicatorStates"]["p85"]["state"] == "NOT_APPLICABLE"


def test_high_position_with_conflicting_ohlc_keeps_historical_buy_reference_only():
    data = rows([Decimal(1) + Decimal(i) / 1000 for i in range(220)])
    data[-1].update(high="0.5", low="0.6")
    result = compute_actionable_price_plan(
        data, subject_kind="nav", subject_id="008298", price_basis="UNIT_NAV"
    )
    assert result["indicators"]["navVolatility14"] is None
    assert result["indicatorStates"]["atr14"]["state"] == "CONFLICT"
    for track in result["tracks"]:
        assert Decimal(track["entryZoneMax"]) <= money(result["indicators"]["p55"])
        assert track["priceState"] == "PARTIAL"
        assert track["entryReady"] is False
        assert track["stopLossPrice"] is None


def test_extreme_nav_prices_never_leak_nan_or_infinity():
    result = nav([Decimal("1e308")] * 220)
    json.dumps(result, allow_nan=False)
