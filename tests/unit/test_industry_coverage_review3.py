"""Expose bounded research omissions and preserve fact identities publicly."""

from copy import deepcopy

import pytest

from backend.integrations.daily_sources import industry_cycle_research_tasks
from backend.macro.daily_research import (
    ResearchValidationError,
    _public_industry_cycle,
    validate_report,
)
from test_daily_research import NOW, report
from test_industry_cycle import checks


def packet_and_report(total):
    public = {
        "asOf": NOW.isoformat(),
        "directions": [],
        "industryCycleResearch": {"required": True},
        "previousIndustryRiskStates": [
            {
                "industry": f"RISK_{index}",
                "industryCycle": {"riskFlags": ["ORDER_CUTS"]},
            }
            for index in range(total)
        ],
    }
    raw = {
        **report(),
        "sources": [],
        "events": [],
        "industryCycleReviews": [
            {"industry": f"RISK_{index}", "sourceIds": [], "riskChecks": checks()}
            for index in range(min(total, 10))
        ],
    }
    return public, raw


def test_query_budget_reports_omitted_historical_risk_candidates():
    public, _ = packet_and_report(12)
    tasks = industry_cycle_research_tasks(public, NOW)
    assert tasks["omittedCandidateCount"] == 2
    assert tasks["omittedRiskCandidateCount"] == 2
    assert len(tasks["candidates"]) == 10


@pytest.mark.parametrize("total", [0, 4, 12])
def test_report_carries_server_computed_research_coverage(total):
    public, raw = packet_and_report(total)
    before = deepcopy(public)
    actual = validate_report(raw, public, {}, completed_at=NOW)
    assert actual["industryResearchCoverage"] == {
        "maximumCandidates": 10,
        "candidateCount": min(total, 10),
        "omittedCandidateCount": max(total - 10, 0),
        "omittedRiskCandidateCount": max(total - 10, 0),
        "status": "PARTIAL" if total > 10 else "COMPLETE",
    }
    assert public == before


def test_model_cannot_fabricate_complete_research_coverage():
    public, raw = packet_and_report(12)
    raw["industryResearchCoverage"] = {"status": "COMPLETE", "omittedCandidateCount": 0}
    with pytest.raises(ResearchValidationError, match="schema"):
        validate_report(raw, public, {}, completed_at=NOW)


def test_public_cycle_keeps_verified_fact_identity_and_event_time_separate_from_publication():
    actual = _public_industry_cycle(
        {
            "earningsEntity": "甲公司",
            "earningsReportPeriod": "2026Q3",
            "evidence": [
                {
                    "kind": "PROFIT_GROWTH",
                    "companyName": "甲公司",
                    "entityId": "SSE:600001",
                    "reportPeriod": "2026Q3",
                    "factIdentityVerified": True,
                }
            ],
            "riskChecks": [
                {
                    "kind": "ORDER_CUTS",
                    "timeVerification": "RECENT",
                    "eventTimeVerification": "HISTORICAL",
                    "eventPeriod": "2025-01",
                    "eventPeriodStart": "2025-01-01",
                    "eventPeriodEnd": "2025-01-31",
                }
            ],
        }
    )
    assert actual["evidence"][0]["companyName"] == "甲公司"
    assert actual["earningsEntity"] == "甲公司"
    assert actual["earningsReportPeriod"] == "2026Q3"
    assert actual["evidence"][0]["entityId"] == "SSE:600001"
    assert actual["evidence"][0]["reportPeriod"] == "2026Q3"
    assert actual["evidence"][0]["factIdentityVerified"] is True
    assert actual["riskChecks"][0]["timeVerification"] == "RECENT"
    assert actual["riskChecks"][0]["eventTimeVerification"] == "HISTORICAL"
    assert actual["riskChecks"][0]["eventPeriod"] == "2025-01"
    assert actual["riskChecks"][0]["eventPeriodEnd"] == "2025-01-31"
