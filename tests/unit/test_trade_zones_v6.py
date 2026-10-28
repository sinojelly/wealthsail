"""Two-year price-span anchors keep NAV trade bands inside real history."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import json

import pytest

from backend.indicators.actionable_zones import compute_actionable_price_plan


def calculate(prices, **kwargs):
    return compute_actionable_price_plan(
        [
            {
                "date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
                "close": str(p),
            }
            for i, p in enumerate(prices)
        ],
        subject_kind="nav",
        subject_id="008298",
        price_basis="UNIT_NAV",
        **kwargs,
    )


def decimal(value):
    return Decimal(str(value))


def money(value):
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@pytest.mark.parametrize(
    "count,ready", [(119, False), (120, True), (219, True), (500, True), (550, True)]
)
def test_v6_history_requires_120_and_uses_at_most_latest_500_observations(count, ready):
    result = calculate([Decimal(1) + Decimal(i) / 1000 for i in range(count)])
    assert result["methodVersion"] == "actionable-zones-v6"
    assert result["historicalPriceWindow"]["sampleCount"] == min(count, 500)
    assert result["historicalPriceWindow"]["requiredSamples"] == 120
    assert result["historicalPriceWindow"]["state"] == (
        "READY" if ready else "INSUFFICIENT"
    )
    for percentile in (30, 45, 75, 92):
        if ready:
            first = max(0, count - 500)
            expected = (
                Decimal(1)
                + (Decimal(first) + Decimal(min(count, 500) - 1) * percentile / 100)
                / 1000
            )
            assert result["indicators"][f"p{percentile}"] == pytest.approx(
                float(expected)
            )
        else:
            assert result["indicators"][f"p{percentile}"] is None
    if count < 220:
        assert result["indicators"]["navPercentile1y"] is None
    if ready:
        assert result["indicators"]["troughLow"] == pytest.approx(
            1 + max(0, count - 500) / 1000
        )
        assert result["indicators"]["priceSpan"] == pytest.approx(
            (min(count, 500) - 1) / 1000
        )


def test_high_position_uses_annual_and_lower_half_support_and_peak_cap():
    result = calculate([Decimal(1) + Decimal(i) / 500 for i in range(500)])
    values = result["indicators"]
    scale = decimal(values["navVolatility14"])
    trough, span, peak = (
        decimal(values[key]) for key in ("troughLow", "priceSpan", "peakHigh")
    )
    high = min(
        decimal(values["ma60"]),
        decimal(values["ma200"]),
        decimal(values["p45"]),
        trough + Decimal("0.45") * span,
    )
    low = max(
        trough,
        min(
            high - Decimal("0.8") * scale,
            decimal(values["p30"]),
            trough + Decimal("0.32") * span,
        ),
    )
    target = max(
        high * Decimal("1.12"),
        decimal(values["ma200"]),
        decimal(values["p75"]),
        trough + Decimal("0.6") * span,
    )
    maximum = min(
        peak * Decimal("0.992"),
        max(
            target + Decimal("1.2") * scale,
            decimal(values["p92"]),
            trough + Decimal("0.82") * span,
        ),
    )
    minimum = min(target, maximum - Decimal("0.8") * scale)
    for item in result["tracks"]:
        assert decimal(item["entryZoneMin"]) == money(low)
        assert decimal(item["entryZoneMax"]) == money(high)
        assert decimal(item["sellZoneMin"]) == money(minimum)
        assert decimal(item["sellZoneMax"]) == money(maximum)
        assert decimal(item["sellZoneMax"]) <= peak * Decimal("0.992")
        assert item["inSellZone"] is True
        assert item["entryReady"] is False


def test_low_position_sell_band_has_real_upside_instead_of_a_quarter_range_exit():
    # A long recovery cycle followed by a trough has a genuine broad range.
    result = calculate(
        [Decimal(1) + Decimal(i) / 499 for i in range(499)] + [Decimal("1.001")]
    )
    values = result["indicators"]
    scale = decimal(values["navVolatility14"])
    high = min(
        decimal(values["ma20"]),
        Decimal("1.001") + Decimal("0.3") * scale,
        decimal(values["p40"]),
        Decimal(1) + Decimal("0.35") * decimal(values["priceSpan"]),
    )
    for item in result["tracks"]:
        assert decimal(item["entryZoneMax"]) == money(high)
        assert decimal(item["sellZoneMin"]) >= decimal(item["entryZoneMax"]) * Decimal(
            "1.12"
        )
        assert decimal(item["sellZoneMin"]) >= Decimal(1) + Decimal("0.6") * decimal(
            values["priceSpan"]
        )
        assert decimal(item["estimatedProfitMinPct"]) >= 12
        expected_min = (
            (decimal(item["sellZoneMin"]) - decimal(item["entryZoneMax"]))
            / decimal(item["entryZoneMax"])
            * 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        expected_max = (
            (decimal(item["sellZoneMax"]) - decimal(item["entryZoneMin"]))
            / decimal(item["entryZoneMin"])
            * 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert decimal(item["estimatedProfitMinPct"]) == expected_min
        assert decimal(item["estimatedProfitMaxPct"]) == expected_max
        expected_current = (
            (decimal(item["sellZoneMax"]) - Decimal("1.001")) / Decimal("1.001") * 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert decimal(item["currentToSellMaxPct"]) == expected_current


def test_120_samples_use_real_ma60_when_ma200_is_missing():
    result = calculate([Decimal(1) + Decimal(i) / 100 for i in range(120)])
    assert result["indicators"]["ma200"] is None
    assert result["historicalPriceWindow"]["state"] == "READY"
    assert all(item["priceState"] == "READY" for item in result["tracks"])
    assert all(
        item["buyZoneAnchor"] == "TWO_YEAR_LOWER_SUPPORT" for item in result["tracks"]
    )


@pytest.mark.parametrize("price", ["1", "0.0001", "100000"])
def test_flat_history_does_not_fabricate_profitable_sell_band_above_peak(price):
    result = calculate([Decimal(price)] * 220)
    for item in result["tracks"]:
        assert item["priceState"] in {"PARTIAL", "INVALID"}
        assert item["entryReady"] is False
        assert item["takeProfitT1"] is item["takeProfitT2"] is None
        assert item["estimatedProfitMinPct"] is item["estimatedProfitMaxPct"] is None


def test_peak_cap_is_rounded_down_so_display_cannot_exceed_ceiling():
    result = calculate(
        [Decimal("0.95") + Decimal(i) / 100000 for i in range(499)]
        + [Decimal("1.00999")]
    )
    cap = Decimal("1.00999") * Decimal("0.992")
    for item in result["tracks"]:
        if item["sellZoneMax"]:
            assert decimal(item["sellZoneMax"]) <= cap
            assert decimal(item["sellZoneMax"]) <= cap.quantize(
                Decimal("0.0001"), rounding=ROUND_FLOOR
            )


def test_cap_conflicting_with_twelve_percent_floor_retains_reference_only():
    result = calculate([Decimal(1) + Decimal(i) / 1000 for i in range(200)])
    for item in result["tracks"]:
        assert item["sellZoneMin"] is not None
        assert decimal(item["estimatedProfitMinPct"]) < 12
        assert item["priceState"] == "PARTIAL"
        assert item["entryReady"] is False
        assert any("无法同时满足12%" in reason for reason in item["reasons"])


def test_cutoff_excludes_future_peak_and_anchors_do_not_mutate_input():
    result = calculate(
        [Decimal(1) + Decimal(i) / 1000 for i in range(120)] + [Decimal(100)],
        as_of=(date(2024, 1, 1) + timedelta(days=119)).isoformat(),
    )
    assert result["historicalPriceWindow"]["sampleCount"] == 120
    assert result["indicators"]["peakHigh"] == pytest.approx(1.119)
    assert result["currentPrice"] == "1.1190"


def test_current_to_sell_profit_is_absent_at_or_above_buy_upper_bound():
    result = calculate([Decimal(1) + Decimal(i) / 1000 for i in range(500)])
    assert all(item["currentToSellMaxPct"] is None for item in result["tracks"])


def test_extreme_finite_price_span_keeps_percentage_serializable():
    result = calculate(
        [Decimal("0.0001")] * 200 + [Decimal("1e100")] * 299 + [Decimal("0.0001")]
    )
    json.dumps(result, allow_nan=False)
