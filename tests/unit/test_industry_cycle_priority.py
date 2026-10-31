"""Industry risks cannot be offset by unconverted investment intentions."""

from copy import deepcopy
from datetime import timedelta

import pytest

from test_daily_research import NOW
from test_opportunity_priority47 import evidence, plan


EARLY = {
    "stage": "EARLY_COMMERCIALIZATION",
    "riskFlags": ["FINANCING_COOLING"],
    "sourceIds": ["industry-check"],
}


def real_anchor(code):
    from backend.indicators.actionable_zones import compute_actionable_price_plan

    closes = [1 + index / 500 for index in range(140)] + [1.12] * 80
    points = [
        {
            "date": (NOW.date() - timedelta(days=len(closes) - index)).isoformat(),
            "close": value,
        }
        for index, value in enumerate(closes)
    ]
    return {
        **compute_actionable_price_plan(
            points,
            subject_kind="nav",
            subject_id=code,
            price_basis="UNIT_NAV",
            label="芯片联接",
        ),
        "fundCode": code,
        "selectedTrack": "LEFT_VALUE",
    }


def test_cooling_industry_loses_twenty_points_without_mutating_the_anchor():
    from backend.investment.opportunity_scoring import score_plan

    base = plan()
    risky = {**deepcopy(base), "industryCycle": deepcopy(EARLY)}
    before = deepcopy(risky)
    ordinary = score_plan(base, evidence(), NOW)
    protected = score_plan(risky, evidence(), NOW)
    assert protected["opportunityScore"] == ordinary["opportunityScore"] - 20
    assert protected["scoreBreakdown"]["industryCyclePenalty"] == -20
    assert risky == before
    assert (
        score_plan(protected, evidence(), NOW)["opportunityScore"]
        == protected["opportunityScore"]
    )


@pytest.mark.parametrize("stage", ["EARLY_COMMERCIALIZATION", "UNKNOWN"])
def test_expansion_and_framework_agreements_cannot_earn_catalyst_points_without_delivery(
    stage,
):
    from backend.investment.opportunity_scoring import score_plan

    packet = evidence()
    packet["alphaPublic"]["catalysts"] = [
        {
            **packet["alphaPublic"]["catalysts"][0],
            "title": "芯片公司拟投资建厂扩产，签署框架协议，预计未来订单增长",
        }
    ]
    result = score_plan({**plan(), "industryCycle": {"stage": stage}}, packet, NOW)
    assert result["scoreBreakdown"]["catalyst"] == 0


def test_delivered_earnings_remain_a_catalyst_even_with_expansion_in_the_same_disclosure():
    from backend.investment.opportunity_scoring import score_plan

    packet = evidence()
    packet["alphaPublic"]["catalysts"][0]["title"] = (
        "芯片公司已披露三季报净利润增长50%、实际回款增加，同时拟扩产"
    )
    packet["alphaPublic"]["catalysts"] = packet["alphaPublic"]["catalysts"][:1]
    result = score_plan({**plan(), "industryCycle": deepcopy(EARLY)}, packet, NOW)
    assert result["scoreBreakdown"]["catalyst"] == 10


def test_forecast_profit_in_an_expansion_plan_is_still_not_realized_delivery():
    from backend.investment.opportunity_scoring import score_plan

    packet = evidence()
    packet["alphaPublic"]["catalysts"] = [
        {
            **packet["alphaPublic"]["catalysts"][0],
            "title": "芯片公司拟扩产，预计明年年报净利润增长，预计回款增加",
        }
    ]
    result = score_plan({**plan(), "industryCycle": deepcopy(EARLY)}, packet, NOW)
    assert result["scoreBreakdown"]["catalyst"] == 0


@pytest.mark.parametrize(
    "title",
    [
        "芯片公司签署框架协议拟扩产，目前尚无实际回款，预计明年量产",
        "芯片公司拟斥资50亿元建厂，预计未来盈利增长",
        "芯片公司拟投建新厂，计划未来订单增长",
    ],
)
def test_negated_delivery_and_varied_announced_investments_are_not_catalysts(title):
    from backend.investment.opportunity_scoring import score_plan

    packet = evidence()
    packet["alphaPublic"]["catalysts"] = [
        {**packet["alphaPublic"]["catalysts"][0], "title": title}
    ]
    result = score_plan({**plan(), "industryCycle": deepcopy(EARLY)}, packet, NOW)
    assert result["scoreBreakdown"]["catalyst"] == 0


def test_direction_cycle_is_inherited_before_scoring_and_ranks_behind_delivered_peer():
    from backend.investment.opportunity_scoring import rank_report

    report = {
        "asOf": NOW.isoformat(),
        "valueDirections": [
            {
                "key": "early",
                "industryCycle": deepcopy(EARLY),
                "plans": [real_anchor("000001")],
            },
            {
                "key": "delivered",
                "industryCycle": {"stage": "EARNINGS_DELIVERY"},
                "plans": [real_anchor("000002")],
            },
        ],
    }
    result = rank_report(report, evidence())
    assert [row["key"] for row in result["valueDirections"]] == ["delivered", "early"]
    early = result["valueDirections"][1]
    assert early["plans"][0]["industryCycle"]["stage"] == "EARLY_COMMERCIALIZATION"
    assert early["opportunityScore"] < result["valueDirections"][0]["opportunityScore"]
    assert early["plans"][0]["scoreBreakdown"]["industryCyclePenalty"] == -20


def test_verified_cycle_follows_fund_into_the_public_scan_without_infecting_other_funds():
    from backend.investment.opportunity_scoring import rank_report

    early, other = real_anchor("000001"), real_anchor("000002")
    report = {
        "asOf": NOW.isoformat(),
        "valueDirections": [
            {"key": "early", "industryCycle": deepcopy(EARLY), "plans": [early]}
        ],
        "quantitativeScan": {"items": [deepcopy(early), deepcopy(other)]},
    }
    result = rank_report(report, evidence())
    items = result["quantitativeScan"]["items"]
    assert items[0]["scoreBreakdown"]["industryCyclePenalty"] == -20
    assert items[1]["scoreBreakdown"]["industryCyclePenalty"] == 0


def test_review_risk_protects_all_sector_funds_even_when_no_direction_is_selected():
    from backend.investment.opportunity_scoring import rank_report

    robotics, banking = real_anchor("000001"), real_anchor("000002")
    robotics["discoverySector"], banking["discoverySector"] = "ROBOT", "FINANCIAL"
    report = {
        "asOf": NOW.isoformat(),
        "valueDirections": [],
        "industryCycleReviews": [{"industry": "ROBOT", **deepcopy(EARLY)}],
        "quantitativeScan": {"items": [robotics, banking]},
    }
    result = rank_report(report, evidence())
    protected, unaffected = result["quantitativeScan"]["items"]
    assert protected["scoreBreakdown"]["industryCyclePenalty"] == -20
    assert protected["industryRiskAdjustment"]["state"] == "APPLIED"
    assert (
        protected["tracks"][0]["entryZoneMax"] < robotics["tracks"][0]["entryZoneMax"]
    )
    assert unaffected["scoreBreakdown"]["industryCyclePenalty"] == 0
    assert (
        unaffected["tracks"][0]["entryZoneMax"] == banking["tracks"][0]["entryZoneMax"]
    )


def test_same_execution_group_puts_delivered_sector_before_higher_scoring_debubble():
    from backend.investment.opportunity_scoring import priority_key

    early = {**plan(candidate=False), "industryCycle": deepcopy(EARLY)}
    delivered = {
        **plan(candidate=False),
        "industryCycle": {"stage": "EARNINGS_DELIVERY"},
    }
    early["tracks"][0]["opportunityScore"] = 60
    delivered["tracks"][0]["opportunityScore"] = 20
    assert priority_key(delivered, delivered["tracks"][0]) < priority_key(
        early, early["tracks"][0]
    )
