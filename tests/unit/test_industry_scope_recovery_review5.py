"""Verified company facts cannot release an unrelated industry's observation."""

from copy import deepcopy
from datetime import timedelta

import pytest

from backend.macro import daily_research
from test_daily_research import NOW
from test_industry_history_review2 import recovery_history, risk_history
from test_industry_risk_persistence import validate_with_history


FLAG = "NO_CURRENT_EARNINGS_PROOF"


def old_earnings_watch():
    record = risk_history(FLAG)
    record["report"]["industryCycleReviews"][0].update(
        stage="UNKNOWN",
        riskLevel="SUSPECTED",
        riskFlags=["PERSISTED_RISK_WATCH"],
        confirmedRiskFlags=[],
        persistedRiskFlags=[FLAG],
    )
    return record


@pytest.mark.parametrize("scope", [False, True, None])
def test_history_recovery_requires_company_facts_applicable_to_the_industry(scope):
    recovered = recovery_history()
    recovered["report"]["industryCycleReviews"][0].update(
        stage="UNKNOWN" if scope is False else "EARNINGS_DELIVERY",
        earningsIndustryScopeVerified=scope,
    )
    original = deepcopy(recovered)
    cycle = validate_with_history([old_earnings_watch(), recovered])[
        "industryCycleReviews"
    ][0]
    if scope is False:
        assert cycle["persistedRiskFlags"] == [FLAG]
        assert cycle["leftDiscountScale"] == 0.5
        assert cycle["confirmedRiskAt"][FLAG] == (NOW - timedelta(days=5)).isoformat()
    else:
        assert not cycle.get("persistedRiskFlags")
        assert cycle["leftDiscountScale"] == 0
    assert recovered == original


@pytest.mark.parametrize("scope", [False, True, None])
def test_current_direction_uses_industry_applicable_recovery_without_faking_review_facts(
    monkeypatch, scope
):
    monkeypatch.setattr(
        daily_research,
        "validate_value_directions",
        lambda *args, **kwargs: [
            {
                "key": "company-facts",
                "reviewIndustryIds": ["ROBOT"],
                "plans": [],
                "industryCycle": {
                    "stage": "UNKNOWN" if scope is False else "EARNINGS_DELIVERY",
                    "earningsVerified": True,
                    "earningsIndustryScopeVerified": scope,
                    "riskFlags": [],
                },
            }
        ],
    )
    cycle = validate_with_history([old_earnings_watch()])["industryCycleReviews"][0]
    if scope is False:
        assert cycle["persistedRiskFlags"] == [FLAG]
        assert cycle["leftDiscountScale"] == 0.5
    else:
        assert not cycle.get("persistedRiskFlags")
        assert cycle["leftDiscountScale"] == 0
    # A linked company's facts do not make the separate sector review profitable.
    assert cycle["earningsVerified"] is False
    assert cycle["earningsIndustryScopeVerified"] is False


def test_public_projection_retains_verified_company_facts_and_unverified_industry_relation():
    actual = daily_research._public_industry_cycle(
        {
            "earningsVerified": True,
            "earningsIndustryScopeVerified": False,
            "earningsEntity": "甲半导体股份有限公司",
            "earningsReportPeriod": "2026Q3",
            "evidence": [
                {
                    "kind": "PROFIT_GROWTH",
                    "currentFactVerified": True,
                    "factIdentityVerified": True,
                    "industryScopeVerified": False,
                }
            ],
        }
    )
    assert actual["earningsVerified"] is True
    assert actual["earningsIndustryScopeVerified"] is False
    assert actual["evidence"][0]["currentFactVerified"] is True
    assert actual["evidence"][0]["industryScopeVerified"] is False
