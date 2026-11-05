"""Recheck known risks before new themes, and protect lower-high downtrends."""

from copy import deepcopy
from datetime import date, timedelta
import pytest

from backend.indicators.actionable_zones import (
    apply_industry_cycle,
    compute_actionable_price_plan,
)
from backend.integrations.daily_sources import industry_cycle_research_tasks
from test_daily_sources import NOW


def prices(values):
    return [
        {
            "date": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
            "close": value,
            "high": value + 1,
            "low": value - 1,
        }
        for index, value in enumerate(values)
    ]


def risk_state(industry, field="riskFlags"):
    return {"industry": industry, "industryCycle": {field: ["FINANCING_COOLING"]}}


def test_lower_highs_trend_weakness_upgrades_half_scale_without_claiming_ma60_breakdown():
    rows = prices([140] * 20 + [130] * 20 + [120] * 20 + [110] * 18 + [100, 105])
    baseline = compute_actionable_price_plan(
        rows,
        subject_kind="index",
        subject_id="robotics",
        benchmark_points=prices([100] * len(rows)),
    )
    cycle = {
        "stage": "UNKNOWN",
        "riskLevel": "SUSPECTED",
        "riskFlags": ["FINANCING_COOLING"],
        "confirmedRiskFlags": [],
        "suspectedRiskFlags": ["FINANCING_COOLING"],
    }
    result = apply_industry_cycle(baseline, cycle)
    strength = result["relativeStrength"]
    assert strength["weakening"] is False
    assert strength["trendWeakening"] is True
    assert strength["breakdownDate"] is None
    assert result["industryRiskAdjustment"]["entryShiftScale"] == 1
    assert result["industryRiskAdjustment"]["baseEntryShiftScale"] == 0.5
    assert result["industryRiskAdjustment"]["relativeStrengthUpgrade"] is True
    assert result["tracks"][1] == baseline["tracks"][1]
    assert result == apply_industry_cycle(result, cycle)


def test_lower_highs_reason_describes_window_maxima_without_claiming_rebound_pivots():
    rows = prices([140] * 20 + [130] * 20 + [120] * 20 + [110] * 18 + [100, 105])
    result = compute_actionable_price_plan(
        rows,
        subject_kind="index",
        subject_id="robotics",
        benchmark_points=prices([100] * len(rows)),
    )
    assert (
        "近60个交易日三个连续20日窗口最高价依次下移，且20日/60日均跑输沪深300，触发技术趋势风控。"
        in result["relativeStrength"]["reasons"]
    )


@pytest.mark.parametrize(
    "field",
    ["riskFlags", "confirmedRiskFlags", "suspectedRiskFlags", "persistedRiskFlags"],
)
def test_prior_risky_industry_absent_from_current_universe_is_checked_before_news_and_nearest(
    field,
):
    packet = {
        "previousIndustryRiskStates": [risk_state("ROBOT", field)],
        "alphaPublic": {"sectorCatalysts": {"FINANCE_POLICY": ["today"]}},
        "quantitativeScan": {
            "items": [
                {
                    "discoverySector": "BROKER",
                    "entryReady": True,
                    "distanceToEntryPct": 0,
                }
            ]
        },
        "discoveryUniverse": [
            {"discoverySector": industry} for industry in [
                "BANK", "AI", "RARE_EARTH", "POWER", "GOLD", "DRUG", "COAL",
                "PCB", "DEFENCE", "COMMUNICATION", "MEMORY", "FINTECH", "BROAD",
                "NONFERROUS", "GAMING", "DIVIDEND", "HK_TECH", "AEROSPACE",
                "SATELLITE", "SOLID_BATTERY",
            ]
        ],
    }
    saved = deepcopy(packet)
    result = industry_cycle_research_tasks(packet, NOW)
    industries = [row["industry"] for row in result["candidates"]]
    assert industries[:3] == ["ROBOT", "FINANCE_POLICY", "BROKER"]
    assert len(industries) == 10
    assert len(result["candidates"][0]["queries"]) == 5
    assert packet == saved


def test_prior_risks_share_hard_top_ten_cap_and_do_not_duplicate_industries():
    industries = [
        "ROBOT", "BANK", "BROKER", "AI", "RARE_EARTH", "POWER",
        "GOLD", "DRUG", "COAL", "PCB", "DEFENCE", "COMMUNICATION",
    ]
    packet = {
        "previousIndustryRiskStates": [
            risk_state(industry) for industry in industries
        ]
        + [risk_state(industries[0])],
        "alphaPublic": {"sectorCatalysts": {"NEWS": ["today"]}},
    }
    result = industry_cycle_research_tasks(packet, NOW)
    assert [row["industry"] for row in result["candidates"]] == industries[:10]
    assert sum(len(row["queries"]) for row in result["candidates"]) == 50
    assert result["omittedCandidateCount"] == 3


def test_history_without_risk_flags_does_not_add_an_unrelated_industry():
    packet = {
        "previousIndustryRiskStates": [
            None,
            {"industry": "UNKNOWN", "industryCycle": {"riskFlags": []}},
        ],
        "alphaPublic": {"sectorCatalysts": {"NEWS": ["today"]}},
    }
    assert [
        row["industry"]
        for row in industry_cycle_research_tasks(packet, NOW)["candidates"]
    ] == ["NEWS"]
