"""Relative weakness and discounted left entries use only observable prices."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.indicators.actionable_zones import compute_actionable_price_plan


def observations(closes, *, ohlc=True):
    return [
        {
            "date": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
            "close": close,
            "high": close + 1 if ohlc else None,
            "low": close - 1 if ohlc else None,
        }
        for index, close in enumerate(closes)
    ]


def plan(rows, **kwargs):
    return compute_actionable_price_plan(
        rows, subject_kind="index", subject_id="robotics", **kwargs
    )


def left(result):
    return result["tracks"][0]


def test_relative_strength_uses_same_day_benchmark_percentage_points_and_high_drawdown():
    rows = observations([100] * 65 + list(range(99, 79, -1)))
    benchmark = observations([100] * 85)
    result = plan(rows, benchmark_points=benchmark)
    strength = result["relativeStrength"]
    assert strength["excessReturn20d"] == pytest.approx(-20)
    assert strength["excessReturn60d"] == pytest.approx(-20)
    assert strength["excessReturnUnit"] == "PERCENTAGE_POINTS"
    assert strength["drawdown60d"] == pytest.approx(20)
    assert strength["drawdownUnit"] == "PERCENT"
    assert strength["weakening"] is True
    assert strength["breakdownDate"] == "2025-03-07"
    assert result["indicators"]["excessReturn60d"] == pytest.approx(-20)
    assert any("2025-03-07" in reason for reason in left(result)["reasons"])


def test_relative_strength_extracts_latest_ma60_cross_of_current_decline():
    rows = observations([100] * 65 + [90] * 10 + [120] * 10 + [110, 100, 90, 80])
    strength = plan(
        rows, benchmark_points=observations([100 + index for index in range(len(rows))])
    )["relativeStrength"]
    assert strength["weakening"] is True
    assert strength["breakdownDate"] == "2025-03-28"


def test_one_weak_day_cannot_claim_a_medium_term_breakdown():
    rows = observations([100] * 80 + [80])
    strength = plan(rows, benchmark_points=observations([100] * len(rows)))[
        "relativeStrength"
    ]
    assert strength["weakening"] is False
    assert strength["breakdownDate"] is None


@pytest.mark.parametrize("missing", ["absent", "latest", "middle", "invalid"])
def test_incomplete_benchmark_cannot_create_aligned_returns_or_breakdown(missing):
    rows = observations([100] * 65 + list(range(99, 79, -1)))
    benchmark = observations([100] * len(rows))
    if missing == "absent":
        benchmark = None
    elif missing == "latest":
        benchmark.pop()
    elif missing == "middle":
        benchmark.pop(-10)
    else:
        benchmark[-10]["close"] = None
    strength = plan(rows, benchmark_points=benchmark)["relativeStrength"]
    assert strength["state"] == "INSUFFICIENT"
    assert strength["excessReturn20d"] is None
    assert strength["excessReturn60d"] is None
    assert strength["weakening"] is None
    assert strength["breakdownDate"] is None
    assert strength["drawdown60d"] == pytest.approx(20)
    assert strength["missingEvidence"]


def test_as_of_applies_to_both_series_without_borrowing_a_future_benchmark():
    rows = observations([100] * 65 + list(range(99, 78, -1)))
    benchmark = observations([100] * 85 + [10000])
    result = plan(rows, benchmark_points=benchmark, as_of="2025-03-26")
    assert result["relativeStrength"]["asOf"] == "2025-03-26"
    assert result["relativeStrength"]["excessReturn60d"] == pytest.approx(-20)


@pytest.mark.parametrize(
    "cycle",
    [
        {"stage": "EARLY_COMMERCIALIZATION"},
        {"stage": "POLICY_CATALYST", "riskFlags": ["FINANCING_COOLING"]},
    ],
)
def test_cooling_moves_only_left_entries_one_scale_and_recomputes_readiness(cycle):
    rows = observations([100] * 200)
    before = plan(rows, pe_percentile=35)
    result = plan(rows, pe_percentile=35, industry_cycle=cycle)
    adjusted = left(result)
    assert left(before)["entryReady"] is True
    assert adjusted["entryZoneMin"] == "96.40"
    assert adjusted["entryZoneMax"] == "98.00"
    assert adjusted["stopLossPrice"] == "92.40"
    assert adjusted["takeProfitT1"] == "104.40"
    assert adjusted["takeProfitT2"] == "108.72"
    assert adjusted["inBuyZone"] is False
    assert adjusted["entryReady"] is False
    assert result["tracks"][1] == before["tracks"][1]
    assert (
        "商业化早期"
        if cycle["stage"] == "EARLY_COMMERCIALIZATION"
        else "一级市场融资降温"
    ) in result["industryRiskWarning"]
    assert adjusted["entryStatus"] == result["industryRiskWarning"]


def test_existing_plan_discount_is_idempotent_and_preserves_input():
    from backend.indicators.actionable_zones import apply_industry_cycle

    original = plan(observations([100] * 200), pe_percentile=35)
    saved = deepcopy(original)
    cycle = {"stage": "EARLY_COMMERCIALIZATION", "riskFlags": ["DEBUBBLING"]}
    once = apply_industry_cycle(original, cycle)
    twice = apply_industry_cycle(once, cycle)
    assert original == saved
    assert once == twice
    assert left(once)["entryZoneMax"] == "98.00"
    assert once["industryAdjustmentBase"]["scale"] == "2.0"
    restored = apply_industry_cycle(once, {"stage": "EARNINGS_DELIVERY"})
    assert left(restored) == left(original)


def test_discount_recalculates_historical_nav_profit_from_displayed_prices():
    rows = observations(
        [1 + index / 500 for index in range(140)] + [1.12] * 80, ohlc=False
    )
    result = compute_actionable_price_plan(
        rows,
        subject_kind="nav",
        subject_id="robotics-nav",
        price_basis="UNIT_NAV",
        industry_cycle={"stage": "EARLY_COMMERCIALIZATION"},
    )
    adjusted = left(result)
    minimum, maximum, sell_minimum, sell_maximum = [
        Decimal(adjusted[key])
        for key in ("entryZoneMin", "entryZoneMax", "sellZoneMin", "sellZoneMax")
    ]
    assert (
        Decimal(adjusted["stopLossPrice"])
        < minimum
        <= maximum
        < sell_minimum
        < sell_maximum
    )
    assert Decimal(adjusted["estimatedProfitMinPct"]) == (
        (sell_minimum / maximum - 1) * 100
    ).quantize(Decimal("0.01"))
    assert Decimal(adjusted["estimatedProfitMaxPct"]) == (
        (sell_maximum / minimum - 1) * 100
    ).quantize(Decimal("0.01"))


def test_no_scale_suppresses_undiscounted_left_references_but_keeps_right_track():
    rows = observations([100] * 200, ohlc=False)
    before = plan(rows, pe_percentile=35)
    result = plan(
        rows, pe_percentile=35, industry_cycle={"stage": "EARLY_COMMERCIALIZATION"}
    )
    adjusted = left(result)
    assert adjusted["entryZoneMin"] is adjusted["entryZoneMax"] is None
    assert adjusted["entryReady"] is adjusted["inBuyZone"] is False
    assert adjusted["priceState"] == "PARTIAL"
    assert result["tracks"][1] == before["tracks"][1]


def test_short_nav_history_stays_partial_after_cycle_discount():
    result = compute_actionable_price_plan(
        observations([1] * 20, ohlc=False),
        subject_kind="nav",
        subject_id="robotics-nav",
        price_basis="UNIT_NAV",
        industry_cycle={"stage": "EARLY_COMMERCIALIZATION"},
    )
    assert left(result)["entryZoneMin"] == "0.9784"
    assert left(result)["entryZoneMax"] == "0.9880"
    assert left(result)["priceState"] == "PARTIAL"
    assert left(result)["entryReady"] is False


def test_discount_updates_scanner_distance_and_selected_coordinates_together():
    from backend.indicators.actionable_zones import apply_industry_cycle

    original = plan(observations([100] * 200), pe_percentile=35)
    original.update(
        selectedTrack="LEFT_VALUE",
        **{key: value for key, value in left(original).items() if key != "track"},
    )
    original["signals"] = [
        {
            "track": "LEFT_VALUE",
            "triggerState": "CANDIDATE",
            "candidate": True,
            "inEntryZone": True,
            "entryReady": True,
            "distanceToEntryPct": 0,
        }
    ]
    result = apply_industry_cycle(original, {"stage": "EARLY_COMMERCIALIZATION"})
    signal = result["signals"][0]
    assert signal["inEntryZone"] is False
    assert signal["entryReady"] is False
    assert signal["candidate"] is True
    assert signal["distanceToEntryPct"] == 2
    assert result["entryZoneMin"] == "96.40"
    assert result["entryZoneMax"] == "98.00"
    assert result["entryReady"] is False
    assert result["entryStatus"] == result["industryRiskWarning"]


def test_crossing_date_stays_unknown_when_series_starts_inside_the_downtrend():
    rows = observations(list(range(200, 100, -1)))
    strength = plan(rows, benchmark_points=observations([100] * len(rows)))[
        "relativeStrength"
    ]
    assert strength["weakening"] is True
    assert strength["breakdownDate"] is None
    assert any("交叉" in reason for reason in strength["missingEvidence"])


def test_more_recent_benchmark_is_clipped_to_disclosed_fund_date():
    rows = observations([100] * 65 + list(range(99, 79, -1)))
    benchmark = observations([100] * 85 + [10000])
    strength = plan(rows, benchmark_points=benchmark)["relativeStrength"]
    assert strength["asOf"] == "2025-03-26"
    assert strength["excessReturn20d"] == pytest.approx(-20)
    assert strength["weakening"] is True


def test_benchmark_omissions_never_compress_sixty_days_into_a_longer_window():
    rows = observations([100] * 65 + list(range(99, 79, -1)))
    benchmark = observations([100] * len(rows))
    benchmark.pop(-40)
    strength = plan(rows, benchmark_points=benchmark)["relativeStrength"]
    assert strength["excessReturn20d"] == pytest.approx(-20)
    assert strength["excessReturn60d"] is None
    assert strength["weakening"] is None
    assert strength["breakdownDate"] is None


def test_unrepresentable_positive_decimal_does_not_create_zero_price_indicators():
    result = plan(observations([Decimal("1e-1000")] * 100, ohlc=False))
    assert result["currentPrice"] is None
    assert result["relativeStrength"]["drawdown60d"] is None


def test_reapplying_cycle_preserves_later_qdii_and_subscription_guards():
    from backend.indicators.actionable_zones import apply_industry_cycle

    cycle = {"stage": "EARLY_COMMERCIALIZATION"}
    original = plan(observations([100] * 200), pe_percentile=35, industry_cycle=cycle)
    original["selectedTrack"] = "LEFT_VALUE"
    for row in (original, left(original)):
        row.update(
            entryBlocked=True,
            entryBlockReason="DELAYED_NAV_MARKET_DROP",
            entryReady=False,
            inBuyZone=False,
            preferredEntryEligible=False,
            entryStatus="⚠️ 净值滞后1天·当晚外盘急跌暂缓入场",
        )
    original["subscriptionRestricted"] = True
    original["signals"] = [{"track": "LEFT_VALUE", "entryReady": False}]
    result = apply_industry_cycle(original, cycle)
    assert left(result)["entryBlocked"] is True
    assert left(result)["entryBlockReason"] == "DELAYED_NAV_MARKET_DROP"
    assert result["entryBlocked"] is True
    assert result["entryBlockReason"] == "DELAYED_NAV_MARKET_DROP"
    assert result["signals"][0]["entryReady"] is False
    assert result["signals"][0]["entryBlocked"] is True


def test_first_cycle_adjustment_preserves_preexisting_external_guard():
    from backend.indicators.actionable_zones import apply_industry_cycle

    original = plan(observations([100] * 200), pe_percentile=35)
    left(original).update(
        entryBlocked=True,
        entryBlockReason="SUBSCRIPTION_RESTRICTED",
        entryReady=False,
        inBuyZone=False,
        priceState="PARTIAL",
    )
    result = apply_industry_cycle(original, {"stage": "EARLY_COMMERCIALIZATION"})
    assert left(result)["entryBlocked"] is True
    assert left(result)["entryBlockReason"] == "SUBSCRIPTION_RESTRICTED"
    assert left(result)["entryReady"] is False
    assert left(result)["priceState"] == "PARTIAL"


def test_scanner_signals_obey_plan_level_blocker_when_prices_are_inside_zone():
    from backend.indicators.actionable_zones import apply_industry_cycle

    original = plan(observations([100] * 200), pe_percentile=35)
    original["entryBlocked"] = True
    original["entryReady"] = False
    original["entryBlockReason"] = "SUBSCRIPTION_RESTRICTED"
    original["signals"] = [{"track": "LEFT_VALUE", "entryReady": True}]
    result = apply_industry_cycle(original, {"stage": "EARNINGS_DELIVERY"})
    assert result["signals"][0]["inEntryZone"] is True
    assert result["signals"][0]["entryReady"] is False


def test_legacy_plan_without_original_anchors_is_blocked_idempotently():
    from backend.indicators.actionable_zones import apply_industry_cycle

    original = plan(observations([100] * 200), pe_percentile=35)
    original.pop("industryAdjustmentBase")
    cycle = {"stage": "EARLY_COMMERCIALIZATION"}
    once = apply_industry_cycle(original, cycle)
    twice = apply_industry_cycle(once, cycle)
    assert once == twice
    assert left(once)["entryReady"] is False
    assert left(once)["entryZoneMin"] is None


def test_excess_returns_never_publish_nonfinite_numbers_on_extreme_real_prices():
    import json

    rows = observations([1e-300] * 80 + [1e308], ohlc=False)
    result = plan(rows, benchmark_points=observations([100] * len(rows)))
    json.dumps(result, allow_nan=False)
    assert result["relativeStrength"]["excessReturn20d"] is None
    assert result["relativeStrength"]["weakening"] is None
