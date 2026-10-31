"""Reading a report must not renew its bounded historical risk observation."""

from copy import deepcopy
from datetime import timedelta

import pytest

from backend.indicators.actionable_zones import apply_industry_cycle
from backend.investment import opportunity_scoring, service
from test_live_fund_plans_v6 import NOW, LocalHistory, history, old_plan


def watch(*, days=15):
    stamp = (NOW - timedelta(days=days)).isoformat()
    return {
        "stage": "UNKNOWN",
        "earningsVerified": False,
        "riskLevel": "SUSPECTED",
        "leftDiscountScale": 0.5,
        "riskFlags": ["PERSISTED_RISK_WATCH"],
        "confirmedRiskFlags": [],
        "suspectedRiskFlags": [],
        "persistedRiskFlags": ["FINANCING_COOLING"],
        "confirmedRiskAt": {"FINANCING_COOLING": stamp},
        "confirmedAt": stamp,
        "persistedEvidence": [{"flag": "FINANCING_COOLING", "confirmedAt": stamp}],
        "evidenceSummary": "延续近14日已确认的行业降温观察，待盈利恢复证据解除",
        "riskChecks": [],
    }


def report(cycle):
    return {
        "asOf": (NOW - timedelta(days=2)).isoformat(),
        "industryCycleReviews": [{"industry": "ROBOT", **deepcopy(cycle)}],
        "actionablePlans": [
            {**old_plan(), "industryCycle": deepcopy(cycle), "discoverySector": "ROBOT"}
        ],
    }


@pytest.mark.asyncio
async def test_fresh_nav_read_restores_baseline_after_fifteen_day_watch_expires():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    public = report(watch())
    saved = deepcopy(public)
    actual = await refresh_report_fund_plans(LocalHistory([history()]), public, NOW)
    ordinary = await refresh_report_fund_plans(
        LocalHistory([history()]), {"actionablePlans": [old_plan()]}, NOW
    )
    plan = actual["actionablePlans"][0]
    assert plan["tracks"] == ordinary["actionablePlans"][0]["tracks"]
    assert "industryRiskAdjustment" not in plan
    assert "PERSISTED_RISK_WATCH" not in plan["industryCycle"]["riskFlags"]
    assert "PERSISTED_RISK_WATCH" not in actual["industryCycleReviews"][0]["riskFlags"]
    assert public == saved


def test_ranking_cannot_revive_expired_watch_from_top_level_sector_review():
    from test_industry_cycle_priority import real_anchor

    anchor = real_anchor("008298")
    anchor["discoverySector"] = "ROBOT"
    adjusted = apply_industry_cycle(anchor, watch())
    public = {
        "asOf": NOW.isoformat(),
        "actionablePlans": [adjusted],
        "industryCycleReviews": [{"industry": "ROBOT", **watch()}],
    }
    result = opportunity_scoring.rank_report(public, {"asOf": NOW.isoformat()})
    restored = result["actionablePlans"][0]
    assert restored["tracks"][0]["entryZoneMax"] == anchor["tracks"][0]["entryZoneMax"]
    assert restored["scoreBreakdown"]["industryCyclePenalty"] == 0
    assert "industryRiskWarning" not in restored


def test_manual_context_drops_expired_observation_and_protect_cannot_reopen_it():
    from test_industry_cycle_priority import real_anchor

    public = report(watch())
    context = service.fund_risk_context(public, NOW)
    anchor = real_anchor("008298")
    protected = service.protect_fund_plan(anchor, context)
    assert protected["industryCycle"]["riskLevel"] == "NONE"
    assert "industryRiskAdjustment" not in protected
    assert protected["tracks"][0]["entryZoneMax"] == anchor["tracks"][0]["entryZoneMax"]


@pytest.mark.parametrize("days", [15, -1])
def test_expiry_rejects_old_and_future_original_clock_without_renewing_it(days):
    from backend.macro.industry_cycle import expire_report_industry_risks

    public = report(watch(days=days))
    first = expire_report_industry_risks(public, NOW)
    second = expire_report_industry_risks(first, NOW + timedelta(days=1))
    assert first["industryCycleReviews"][0]["riskLevel"] == "NONE"
    assert second["industryCycleReviews"][0]["riskLevel"] == "NONE"


def test_expiry_preserves_current_confirmed_evidence_and_external_purchase_veto():
    from backend.macro.industry_cycle import expire_report_industry_risks
    from test_industry_cycle_priority import real_anchor

    cycle = watch()
    cycle.update(
        riskFlags=["PERSISTED_RISK_WATCH", "ORDER_CUTS"],
        confirmedRiskFlags=["ORDER_CUTS"],
        riskLevel="CONFIRMED",
    )
    anchor = real_anchor("008298")
    adjusted = apply_industry_cycle(anchor, cycle)
    adjusted["tracks"][0].update(
        entryBlocked=True,
        entryReady=False,
        inBuyZone=False,
        entryBlockReason="SUBSCRIPTION_RESTRICTED",
    )
    public = {"actionablePlans": [adjusted]}
    result = expire_report_industry_risks(public, NOW)["actionablePlans"][0]
    assert result["industryCycle"]["confirmedRiskFlags"] == ["ORDER_CUTS"]
    assert result["industryCycle"]["riskLevel"] == "CONFIRMED"
    assert result["industryCycle"]["stage"] == "UNKNOWN"
    assert result["tracks"][0]["entryBlocked"] is True
    assert result["tracks"][0]["entryBlockReason"] == "SUBSCRIPTION_RESTRICTED"
    assert result["industryRiskAdjustment"]["entryShiftScale"] == 1


def test_manual_protection_expires_own_plan_without_a_new_matching_context():
    from test_industry_cycle_priority import real_anchor

    anchor = real_anchor("008298")
    adjusted = apply_industry_cycle(anchor, watch())
    restored = service.protect_fund_plan(adjusted, {}, now=NOW)
    assert restored["tracks"][0]["entryZoneMax"] == anchor["tracks"][0]["entryZoneMax"]
    assert "industryRiskAdjustment" not in restored


def test_orphan_watch_without_original_clock_expires_instead_of_renewing():
    from backend.macro.industry_cycle import expire_report_industry_risks

    cycle = watch()
    cycle.pop("persistedRiskFlags")
    cycle.pop("confirmedAt")
    cycle.pop("confirmedRiskAt")
    result = expire_report_industry_risks(report(cycle), NOW)
    assert result["industryCycleReviews"][0]["riskLevel"] == "NONE"
    assert result["industryCycleReviews"][0]["persistedEvidence"] == []
