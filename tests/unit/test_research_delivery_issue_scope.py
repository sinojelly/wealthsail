"""Investment price errors do not erase independently valid economic research."""

from copy import deepcopy
import json

import pytest

from tests.unit.test_research_delivery import NOW, RUN, read, summary


def plan_issue(**changes):
    return {
        "itemType": "FUND_PLAN",
        "itemIndex": 0,
        "path": "valueDirections[0].plans[0]",
        "businessKey": "fund:PRIVATE_FUND:LEFT",
        "ruleId": "ACCEPTANCE_FUND_PLAN_INVALID",
        "reasonCode": "FUND_PLAN_INVALID",
        "dependencies": [],
        "missingIndustryCodes": [],
        **changes,
    }


def blocked_direction(**changes):
    return {
        "itemType": "VALUE_DIRECTION",
        "itemIndex": 0,
        "path": "valueDirections[0]",
        "businessKey": "PRIVATE_DIRECTION",
        "ruleId": "ACCEPTANCE_DEPENDENCY_BLOCKED",
        "reasonCode": "DIRECTION_VALIDATION_FAILED",
        "dependencies": [
            {"itemType": "FUND_PLAN", "businessKey": "fund:PRIVATE_FUND:LEFT"}
        ],
        **changes,
    }


def receipt(issues):
    from backend.macro.research_acceptance_repository import safe_outcome_summary

    value = safe_outcome_summary(summary(issues=issues, issueCount=len(issues)))
    return {
        **value,
        "kind": "INITIAL",
        "archiveState": "ARCHIVED",
        "recordedAt": NOW.isoformat(),
    }


@pytest.mark.asyncio
async def test_price_only_failure_keeps_economic_content_available_and_formal_failure(
    monkeypatch,
):
    value = receipt([plan_issue(), blocked_direction()])
    result, _, _ = await read(monkeypatch, receipt=value)
    assert result["status"] == "AVAILABLE"
    assert result["issueCount"] == 2 and result["economicIssueCount"] == 0
    assert result["actionable"] is False
    assert value["decision"] == "PARTIAL" and value["formalPublishable"] is False


def test_economic_issue_aggregate_roundtrips_without_private_dependency_data():
    from backend.macro.research_acceptance_repository import safe_outcome_summary

    value = receipt([plan_issue(), blocked_direction()])
    projected = safe_outcome_summary(value)
    assert projected["economicIssueCount"] == 0
    assert projected["issueCount"] == 2
    assert "PRIVATE" not in json.dumps(projected)
    assert "dependencies" not in json.dumps(projected)


def test_original_private_dependencies_override_a_claimed_safe_aggregate():
    from backend.macro.research_acceptance_repository import safe_outcome_summary

    value = summary(
        issues=[
            plan_issue(),
            blocked_direction(
                dependencies=[{"itemType": "SOURCE", "businessKey": "PRIVATE_SOURCE"}]
            ),
        ],
        issueCount=2,
        economicIssueCount=0,
        economicIssueScopeVersion="economic-issue-scope-v1",
    )
    assert safe_outcome_summary(value)["economicIssueCount"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issue",
    [
        plan_issue(
            ruleId="DIRECTION_PLAN_SECTOR_UNREVIEWED",
            reasonCode="DIRECTION_VALIDATION_FAILED",
        ),
        blocked_direction(ruleId="DIRECTION_MACRO_REFERENCE_UNKNOWN"),
        blocked_direction(ruleId="INDUSTRY_RISK_SOURCE_MISSING"),
        blocked_direction(ruleId="ACCEPTANCE_SCHEMA_INVALID"),
        blocked_direction(ruleId="UNKNOWN_FUTURE_RULE"),
        blocked_direction(
            dependencies=[{"itemType": "SOURCE", "businessKey": "PRIVATE_SOURCE"}]
        ),
        blocked_direction(itemIndex=1),
        blocked_direction(dependencies=[]),
        plan_issue(itemIndex=True),
    ],
)
async def test_economic_risk_or_unproven_dependency_remains_partial(monkeypatch, issue):
    value = receipt([plan_issue(), issue])
    result, _, _ = await read(monkeypatch, receipt=value)
    assert result["status"] == "PARTIAL"
    assert result["economicIssueCount"] > 0
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes", [{"issueListComplete": False}, {"acceptedContentComplete": False}]
)
async def test_price_only_errors_cannot_override_missing_or_excerpted_economic_material(
    monkeypatch, changes
):
    value = {**receipt([plan_issue(), blocked_direction()]), **changes}
    result, _, _ = await read(monkeypatch, receipt=value)
    assert result["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_legacy_public_dependency_without_proof_stays_conservative(monkeypatch):
    value = summary(issueCount=2, issues=[plan_issue(), blocked_direction()])
    for row in value["issues"]:
        row.pop("dependencies")
    before = deepcopy(value)
    result, _, _ = await read(monkeypatch, receipt=value)
    assert result["status"] == "PARTIAL"
    assert value == before


@pytest.mark.asyncio
@pytest.mark.parametrize("risk_level", ["SUSPECTED", "CONFIRMED"])
async def test_retained_verified_negative_fact_is_not_an_economic_delivery_failure(
    monkeypatch, risk_level
):
    value = receipt([plan_issue(), blocked_direction()])
    value["accepted"]["industryCycleReviews"] = [
        {
            "industry": "ROBOTICS",
            "sourceIds": ["s1"],
            "riskLevel": risk_level,
            "confirmedRiskFlags": ["FINANCING_COOLING"],
        }
    ]
    result, _, _ = await read(monkeypatch, receipt=value)
    assert result["status"] == "AVAILABLE"
    assert result["observations"]["industryCycleReviews"][0]["riskLevel"] == risk_level


@pytest.mark.asyncio
async def test_investment_publication_blocker_count_does_not_reclassify_economic_content(
    monkeypatch,
):
    from tests.unit.test_research_delivery import coverage

    value = receipt([plan_issue(), blocked_direction()])
    result, _, _ = await read(
        monkeypatch, receipt=value, measured=coverage(autoPublishBlockerCount=1)
    )
    assert result["status"] == "AVAILABLE"


@pytest.mark.asyncio
async def test_real_collector_plan_failure_keeps_checked_news_and_verified_bad_news_readable(
    monkeypatch,
):
    from backend.macro.research_acceptance_repository import safe_outcome_summary
    from backend.macro.research_batch import diagnose_research_batch
    from tests.unit.test_research_cost_review75 import prepared, executed_checks
    from tests.unit.test_research_event_links import event

    packet, raw, checks = prepared()
    raw["valueDirections"][0]["plans"][0]["subjectId"] = "999999"
    raw["events"] = [event(sourceIds=["s1"])]
    batch = diagnose_research_batch(
        raw,
        packet,
        executed_checks(packet, checks, run_id=str(RUN), at=NOW),
        research_id=str(RUN),
        validated_at=NOW,
    )
    original = batch["originalOutcome"].to_dict()
    assert original["issues"] == []
    observation = original["accepted"]["valueDirections"][0]
    assert observation["signal"] == "WATCH"
    assert observation["investmentEligible"] is False
    assert "FUND_PLAN_INVALID" in observation["formalEligibilityReasons"]
    value = {
        **safe_outcome_summary(original),
        "kind": "INITIAL",
        "archiveState": "ARCHIVED",
        "recordedAt": NOW.isoformat(),
    }
    result, _, _ = await read(
        monkeypatch, receipt=value, measured=batch["registry"]["coverage"]
    )

    assert result["status"] == "AVAILABLE" and result["economicIssueCount"] == 0
    assert result["observations"]["events"][0]["summary"] == raw["events"][0]["summary"]
    assert any(
        row["riskLevel"] == "CONFIRMED"
        for row in result["observations"]["industryCycleReviews"]
    )
    assert batch["autoPublishAllowed"] is True
    assert not batch["formalReport"]["valueDirections"][0]["plans"]
    assert original["decision"] == "COMPLETE" and original["formalPublishable"] is True
