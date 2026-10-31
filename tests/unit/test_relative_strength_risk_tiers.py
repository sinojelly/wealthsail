"""Sector evidence strength and relative weakness protect left entries in tiers."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from backend.indicators.actionable_zones import (
    apply_industry_cycle,
    compute_actionable_price_plan,
)
from backend.investment.opportunity_scoring import score_plan
from test_daily_research import NOW
from test_opportunity_priority47 import evidence, plan as score_anchor


def points(prices):
    return [
        {
            "date": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
            "close": price,
            "high": price + 1,
            "low": price - 1,
        }
        for index, price in enumerate(prices)
    ]


def calculate(prices, **kwargs):
    return compute_actionable_price_plan(
        points(prices), subject_kind="index", subject_id="robotics", **kwargs
    )


def cycle(level):
    if level == "UNPROVEN":
        return {
            "stage": "UNKNOWN",
            "riskLevel": level,
            "forwardPlansOnly": True,
            "riskFlags": ["FORWARD_PLANS_ONLY"],
            "confirmedRiskFlags": [],
            "suspectedRiskFlags": [],
            "leftDiscountScale": 0.5,
        }
    return {
        "stage": "UNKNOWN",
        "riskLevel": level,
        "riskFlags": ["FINANCING_COOLING"],
        "confirmedRiskFlags": ["FINANCING_COOLING"] if level == "CONFIRMED" else [],
        "suspectedRiskFlags": ["FINANCING_COOLING"] if level == "SUSPECTED" else [],
        "leftDiscountScale": 1.0 if level == "CONFIRMED" else 0.5,
    }


def test_lower_highs_uses_three_ordered_twenty_day_peaks_and_enters_trend_risk():
    prices = [140] * 20 + [130] * 20 + [120] * 20 + [110] * 18 + [100, 105]
    result = calculate(prices, benchmark_points=points([100] * len(prices)))
    strength = result["relativeStrength"]
    assert strength["lowerHighs"] is True
    assert strength["weakening"] is False
    assert strength["breakdownDate"] is None
    assert strength["trendWeakening"] is True
    assert result["trendWeakening"] is True
    assert any("最高价" in reason for reason in strength["reasons"])


@pytest.mark.parametrize(
    "prices,expected",
    [([100] * 59, None), ([130] * 20 + [120] * 20 + [120] * 20, False)],
)
def test_lower_highs_requires_sixty_samples_and_strictly_declining_peaks(
    prices, expected
):
    strength = calculate(prices)["relativeStrength"]
    assert strength["lowerHighs"] is expected
    assert strength["trendWeakening"] is None


def test_lower_highs_without_matching_benchmark_does_not_invent_relative_weakness():
    strength = calculate([130] * 20 + [120] * 20 + [110] * 20)["relativeStrength"]
    assert strength["lowerHighs"] is True
    assert strength["trendWeakening"] is None
    assert strength["weakening"] is None


@pytest.mark.parametrize("level", ["SUSPECTED", "UNPROVEN"])
def test_half_scale_discount_recalculates_stop_and_preserves_right_track(level):
    before = calculate([100] * 200, pe_percentile=35)
    result = apply_industry_cycle(before, cycle(level))
    left = result["tracks"][0]
    assert left["entryZoneMin"] == "97.40"
    assert left["entryZoneMax"] == "99.00"
    assert left["stopLossPrice"] == "93.40"
    assert left["takeProfitT1"] == "105.40"
    assert left["takeProfitT2"] == "109.72"
    assert result["tracks"][1] == before["tracks"][1]
    assert result["industryRiskAdjustment"]["entryShiftScale"] == 0.5
    assert result["industryRiskAdjustment"]["riskLevel"] == level
    assert result == apply_industry_cycle(result, cycle(level))


def test_ma60_relative_breakdown_upgrades_half_scale_protection_and_signals():
    before = calculate([100] * 200, pe_percentile=35)
    before["relativeStrength"]["weakening"] = True
    before["selectedTrack"] = "LEFT_VALUE"
    before["signals"] = [
        {
            "track": "LEFT_VALUE",
            "candidate": True,
            "inEntryZone": True,
            "entryReady": True,
            "distanceToEntryPct": 0,
        }
    ]
    result = apply_industry_cycle(before, cycle("SUSPECTED"))
    assert result["industryRiskAdjustment"]["entryShiftScale"] == 1.0
    assert result["industryRiskAdjustment"]["baseEntryShiftScale"] == 0.5
    assert result["industryRiskAdjustment"]["relativeStrengthUpgrade"] is True
    assert result["entryZoneMax"] == "98.00"
    assert result["signals"][0]["distanceToEntryPct"] == 2
    assert result["signals"][0]["entryReady"] is False
    assert any("共振" in reason for reason in result["tracks"][0]["reasons"])


def test_dynamic_warning_explains_unproven_forward_plan_instead_of_claiming_debubbling():
    result = apply_industry_cycle(calculate([100] * 200), cycle("UNPROVEN"))
    assert "远期" in result["industryRiskWarning"]
    assert "去泡沫" not in result["industryRiskWarning"]
    assert result["tracks"][0]["entryStatus"] == result["industryRiskWarning"]


@pytest.mark.parametrize(
    "level,weakening,industry_penalty,relative_penalty,total",
    [
        ("CONFIRMED", False, -20, 0, -20),
        ("SUSPECTED", False, -10, 0, -10),
        ("UNPROVEN", False, -10, 0, -10),
        ("CONFIRMED", True, -20, -5, -25),
        ("SUSPECTED", True, -10, -8, -18),
    ],
)
def test_risk_penalties_are_tiered_capped_and_explain_actual_and_nominal_effects(
    level, weakening, industry_penalty, relative_penalty, total
):
    original = score_anchor()
    ordinary = score_plan(original, evidence(), NOW)
    risky = {
        **deepcopy(original),
        "industryCycle": cycle(level),
        "relativeStrength": {"weakening": weakening},
    }
    scored = score_plan(risky, evidence(), NOW)
    breakdown = scored["scoreBreakdown"]
    assert scored["opportunityScore"] == ordinary["opportunityScore"] + total
    assert breakdown["industryCyclePenalty"] == industry_penalty
    assert breakdown["relativeStrengthPenalty"] == relative_penalty
    assert breakdown["nominalRelativeStrengthPenalty"] == (-8 if weakening else 0)
    assert breakdown["riskPenaltyTotal"] == total
    assert breakdown["riskPenaltyCap"] == 25
    assert breakdown["evidenceScope"] == "SECTOR_PROXY"
    assert (
        score_plan(scored, evidence(), NOW)["opportunityScore"]
        == scored["opportunityScore"]
    )


def test_lower_highs_relative_risk_also_reduces_score_without_claiming_ma60_cross():
    original = score_anchor()
    ordinary = score_plan(original, evidence(), NOW)
    risky = {
        **deepcopy(original),
        "relativeStrength": {
            "weakening": False,
            "trendWeakening": True,
            "lowerHighs": True,
        },
    }
    scored = score_plan(risky, evidence(), NOW)
    assert scored["opportunityScore"] == ordinary["opportunityScore"] - 8
    assert scored["scoreBreakdown"]["relativeStrengthPenalty"] == -8


def test_actual_penalty_breakdown_never_exceeds_available_score():
    original = score_anchor(candidate=False, current="1.2", rsi=80)
    original["industryCycle"] = cycle("CONFIRMED")
    original["relativeStrength"] = {"weakening": True}
    result = score_plan(original, {}, NOW)
    assert result["opportunityScore"] == 0
    assert result["scoreBreakdown"]["nominalIndustryCyclePenalty"] == -20
    assert result["scoreBreakdown"]["nominalRelativeStrengthPenalty"] == -8
    assert result["scoreBreakdown"]["industryCyclePenalty"] == -7.5
    assert result["scoreBreakdown"]["relativeStrengthPenalty"] == 0
    assert result["scoreBreakdown"]["riskPenaltyTotal"] == -7.5
