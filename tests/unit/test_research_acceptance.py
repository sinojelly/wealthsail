"""Independent, observation-only salvage never promotes a failed formal report."""

from copy import deepcopy
from datetime import timedelta
import json

import pytest

from backend.macro.research_acceptance import validate_report_outcome
from backend.macro.daily_research import ResearchValidationError, validate_report
from test_daily_research import NOW
from test_industrial_metals_research_regression import case


def eight_directions():
    packet, raw, checks = case()
    raw["valueDirections"] = [
        {**deepcopy(raw["valueDirections"][0]), "key": f"direction-{i}"}
        for i in range(8)
    ]
    return packet, raw, checks


def validate(raw, packet, checks):
    return validate_report_outcome(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        completed_at=NOW,
    )


def test_eight_directions_two_fail_six_retained_without_formal_payload():
    packet, raw, checks = eight_directions()
    raw["valueDirections"][0]["macroCodes"] = ["UNKNOWN_MACRO"]
    raw["valueDirections"][1]["reviewIndustryIds"] = ["MODEL_UNKNOWN"]
    before = deepcopy((packet, raw, checks))
    with pytest.raises(ResearchValidationError):
        validate_report(raw, packet, checks, completed_at=NOW)
    outcome = validate(raw, packet, checks)
    result = outcome.to_dict()
    assert result["decision"] == "PARTIAL"
    assert result["counts"]["valueDirections"] == {
        "inputCount": 8,
        "acceptedCount": 6,
        "quarantinedCount": 2,
    }
    assert len(result["accepted"]["valueDirections"]) == 6
    assert outcome.formal_payload is None
    assert result["actionable"] is False
    assert result["issueListComplete"] is True
    assert all(
        "plans" not in direction for direction in result["accepted"]["valueDirections"]
    )
    assert {
        issue["itemIndex"]
        for issue in result["issues"]
        if issue["itemType"] == "VALUE_DIRECTION"
    } == {0, 1}
    assert before == (packet, raw, checks)


def test_missing_negative_review_blocks_only_dependent_funds_without_rewriting_checks():
    packet, raw, checks = case()
    other = deepcopy(raw["valueDirections"][0])
    other.update(key="rare-earth-only", reviewIndustryIds=["RARE_EARTH"])
    other["plans"] = other["plans"][1:]
    raw["valueDirections"].append(other)
    raw["industryCycleReviews"] = raw["industryCycleReviews"][:2]
    before = deepcopy(raw)
    result = validate(raw, packet, checks).to_dict()
    assert [row["key"] for row in result["accepted"]["valueDirections"]] == [
        "rare-earth-only"
    ]
    assert any(
        row["ruleId"] == "DIRECTION_PLAN_SECTOR_UNREVIEWED"
        and row["missingIndustryCodes"] == ["NONFERROUS"]
        for row in result["issues"]
    )
    assert (
        result["accepted"]["valueDirections"][0]["industryCycle"]["riskLevel"]
        == "CONFIRMED"
    )
    assert raw == before


def test_bad_news_reference_and_quarantined_direction_propagate_to_events():
    packet, raw, checks = eight_directions()
    raw["valueDirections"][0]["sourceIds"].append("missing-news")
    event = {
        "title": "独立事实",
        "summary": "保留报道身份。",
        "phase": "REPORTED",
        "importance": "MEDIUM",
        "sourceIds": ["s1"],
        "channels": [],
        "affectedDirectionIds": [],
        "scenarios": [],
        "watchFor": [],
        "unknowns": [],
        "marketReaction": "UNVERIFIED",
    }
    raw["events"] = [
        {**event, "affectedDirectionIds": ["direction-0"]},
        {**event, "title": "可保留事实"},
    ]
    result = validate(raw, packet, checks).to_dict()
    assert result["counts"]["valueDirections"]["acceptedCount"] == 7
    assert result["counts"]["events"]["acceptedCount"] == 1
    issue = next(row for row in result["issues"] if row["itemType"] == "EVENT")
    assert issue["dependencies"]


def test_future_source_and_invalid_calendar_are_separately_collected():
    packet, raw, checks = case()
    raw["sources"][1]["publishedDate"] = "2099-01-01"
    raw["upcoming"] = [
        {
            "title": "无效日历",
            "phase": "PLANNED",
            "scheduledDate": "2026-02-30",
            "scheduledAt": None,
            "sourceIds": ["s1"],
            "affectedDirectionIds": [],
            "watchFor": [],
        }
    ]
    result = validate(raw, packet, checks).to_dict()
    assert {row["itemType"] for row in result["issues"]} >= {
        "SOURCE",
        "VALUE_DIRECTION",
        "UPCOMING",
    }
    assert result["counts"]["sources"]["acceptedCount"] == 1
    assert not result["accepted"]["upcoming"]


def test_all_invalid_items_are_empty_and_security_boundary_is_rejected():
    packet, raw, checks = case()
    raw.update(valueDirections=[], industryCycleReviews=[], events=[], upcoming=[])
    raw["sources"] = [{**raw["sources"][0], "publishedDate": "2099-01-01"}]
    assert validate(raw, packet, checks).decision == "EMPTY"
    raw["sources"][0]["url"] = "https://127.0.0.1/private"
    outcome = validate(raw, packet, checks)
    assert outcome.decision == "REJECTED"
    assert outcome.issue_list_complete is False
    assert not any(outcome.accepted.values())
    assert outcome.formal_payload is None


def test_collects_multiple_independent_problems_in_same_direction_and_schema_item():
    packet, raw, checks = case()
    raw["valueDirections"][0].update(
        macroCodes=["BAD_MACRO"],
        reviewIndustryIds=["BAD_INDUSTRY"],
        sourceIds=["bad-source"],
    )
    raw["upcoming"] = [
        {
            "title": "",
            "phase": "IMPLEMENTED",
            "scheduledDate": None,
            "scheduledAt": None,
            "sourceIds": [],
            "affectedDirectionIds": [],
            "watchFor": [],
        }
    ]
    result = validate(raw, packet, checks).to_dict()
    assert {
        row["ruleId"]
        for row in result["issues"]
        if row["itemType"] == "VALUE_DIRECTION"
    } >= {
        "DIRECTION_MACRO_REFERENCE_UNKNOWN",
        "DIRECTION_DECLARED_INDUSTRY_UNKNOWN",
        "ACCEPTANCE_SOURCE_DEPENDENCY_INVALID",
    }
    assert len([row for row in result["issues"] if row["itemType"] == "UPCOMING"]) >= 2
    assert all(
        {
            "researchId",
            "itemType",
            "itemIndex",
            "path",
            "businessKey",
            "ruleId",
            "missingIndustryCodes",
            "disposition",
            "dependencies",
        }
        <= row.keys()
        for row in result["issues"]
    )


def test_runtime_exception_is_not_swallowed_or_reclassified(monkeypatch):
    packet, raw, checks = case()

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected internal failure")

    with pytest.raises(RuntimeError):
        validate_report_outcome(
            raw,
            packet,
            checks,
            research_id="run",
            completed_at=NOW,
            _strict_validator=fail,
        )


def test_complete_matches_strict_prices_and_does_not_publish_partial_observations():
    packet, raw, checks = case()
    strict = validate_report(raw, packet, checks, completed_at=NOW)
    outcome = validate(raw, packet, checks)
    assert outcome.decision == "COMPLETE"
    assert outcome.formal_payload == strict
    assert outcome.counts["fundPlans"] == {
        "inputCount": 2,
        "acceptedCount": 2,
        "quarantinedCount": 0,
    }
    assert (
        outcome.to_dict()["accepted"]["valueDirections"][0]["industryCycle"][
            "riskLevel"
        ]
        == "CONFIRMED"
    )
    assert "actionablePlans" not in outcome.to_dict()


def test_retained_direction_keeps_existing_historical_risk_floor():
    packet, raw, checks = eight_directions()
    raw["valueDirections"][0]["macroCodes"] = ["BAD"]
    stamp = (NOW - timedelta(days=2)).isoformat()
    packet["previousIndustryRiskStates"] = [
        {
            "industry": "NONFERROUS",
            "researchedAt": stamp,
            "industryCycle": {
                "stage": "UNKNOWN",
                "riskLevel": "CONFIRMED",
                "riskFlags": ["ORDER_CUTS"],
                "confirmedRiskFlags": ["ORDER_CUTS"],
                "confirmedRiskAt": {"ORDER_CUTS": stamp},
            },
        }
    ]
    result = validate(raw, packet, checks).to_dict()
    assert all(
        "ORDER_CUTS" in row["industryCycle"]["persistedRiskFlags"]
        for row in result["accepted"]["valueDirections"]
    )


def test_direction_collects_risk_check_errors_even_when_other_references_are_bad():
    packet, raw, checks = case()
    value = raw["valueDirections"][0]
    value["macroCodes"] = ["BAD"]
    value["sourceIds"] = ["missing"]
    value["industryCycle"]["riskChecks"][0].update(status="FOUND", sourceIds=[])
    value["industryCycle"]["riskChecks"][-1]["kind"] = "ORDER_CUTS"
    result = validate(raw, packet, checks).to_dict()
    assert {
        row["ruleId"]
        for row in result["issues"]
        if row["itemType"] == "VALUE_DIRECTION"
    } >= {
        "DIRECTION_MACRO_REFERENCE_UNKNOWN",
        "ACCEPTANCE_SOURCE_DEPENDENCY_INVALID",
        "INDUSTRY_RISK_CHECK_DUPLICATE",
        "INDUSTRY_RISK_CHECK_MISSING",
        "INDUSTRY_RISK_SOURCE_MISSING",
    }


def test_size_boundary_is_checked_before_running_any_expensive_validation():
    packet, raw, checks = case()
    raw["summary"] = "x" * (2 * 1024 * 1024)

    def forbidden(*args, **kwargs):
        raise AssertionError("oversized report must not reach strict validation")

    outcome = validate_report_outcome(
        raw,
        packet,
        checks,
        research_id="run",
        completed_at=NOW,
        _strict_validator=forbidden,
    )
    assert outcome.decision == "REJECTED"


def test_unsafe_source_with_other_schema_errors_still_rejects_the_security_boundary():
    packet, raw, checks = case()
    raw["sources"][1].update(url="http://localhost/private", title="")
    outcome = validate(raw, packet, checks)
    assert outcome.decision == "REJECTED"
    assert not any(outcome.accepted.values())


def test_each_invalid_plan_identity_is_recorded_despite_an_invalid_macro():
    packet, raw, checks = case()
    value = raw["valueDirections"][0]
    value["macroCodes"] = ["BAD_MACRO"]
    for plan, code in zip(value["plans"], ("999999", "888888")):
        plan["subjectId"] = code
    result = validate(raw, packet, checks).to_dict()
    issues = [row for row in result["issues"] if row["itemType"] == "FUND_PLAN"]
    assert {row["path"] for row in issues} == {
        "valueDirections[0].plans[0]",
        "valueDirections[0].plans[1]",
    }
    assert all(row["reasonCode"] == "FUND_PLAN_INVALID" for row in issues)
    assert result["issueListComplete"] is True


def test_each_plan_price_or_calibration_error_is_checked_independently():
    packet, raw, checks = case()
    value = raw["valueDirections"][0]
    value["macroCodes"] = ["BAD_MACRO"]
    value["plans"][0]["track"] = "RIGHT_TREND"
    packet["discoveryActionableZones"][0]["tracks"] = packet[
        "discoveryActionableZones"
    ][0]["tracks"][:1]
    value["plans"][1]["calibrationRuleKeys"] = ["UNKNOWN_RULE"]
    result = validate(raw, packet, checks).to_dict()
    issues = [row for row in result["issues"] if row["itemType"] == "FUND_PLAN"]
    assert {row["path"] for row in issues} >= {
        "valueDirections[0].plans[0]",
        "valueDirections[0].plans[1].calibrationRuleKeys",
    }


def test_excessive_nested_field_names_are_rejected_before_error_amplification():
    packet, raw, checks = case()
    raw["sources"][0]["private" * 1000] = "x"
    outcome = validate(raw, packet, checks)
    assert outcome.decision == "REJECTED"
    assert not outcome.accepted["sources"]


def test_observation_receipt_does_not_duplicate_large_frozen_history_proofs():
    packet, raw, checks = case()
    payload = validate_report(raw, packet, checks, completed_at=NOW)
    value = payload["valueDirections"][0]
    value["industryCycle"]["persistedEvidence"] = [{"sources": ["x" * 2000] * 60}] * 14
    outcome = validate_report_outcome(
        raw,
        packet,
        checks,
        research_id="run",
        completed_at=NOW,
        _strict_validator=lambda *_, **__: payload,
    )
    stored = outcome.to_dict()
    assert len(json.dumps(stored, ensure_ascii=False).encode()) < 1024 * 1024
    assert stored["accepted"]["valueDirections"][0]["industryCycle"][
        "confirmedRiskFlags"
    ] == ["FINANCING_COOLING"]
    assert (
        "persistedEvidence"
        not in stored["accepted"]["valueDirections"][0]["industryCycle"]
    )
    assert outcome.formal_payload == payload


@pytest.mark.parametrize("partial", [False, True])
def test_legal_large_event_receipt_is_explicitly_excerpted_without_vetoing_formal_result(
    partial,
):
    from test_daily_research import report

    packet, raw, checks = case()
    event = {**report()["events"][0], "sourceIds": ["s1"], "affectedDirectionIds": []}
    raw["events"] = [
        {
            **event,
            "watchFor": [f"{i}-{j}" + "观察" * 990 for j in range(8)],
            "unknowns": [f"{i}-{j}" + "跟踪" * 990 for j in range(8)],
        }
        for i in range(12)
    ]
    if partial:
        raw["valueDirections"][0]["macroCodes"] = ["BAD_MACRO"]
    before = deepcopy(raw)
    outcome = validate(raw, packet, checks)
    stored = outcome.to_dict()
    assert len(json.dumps(stored, ensure_ascii=False).encode()) < 768 * 1024
    assert stored["acceptedContentComplete"] is False
    assert stored["issueListComplete"] is True
    assert outcome.decision == ("PARTIAL" if partial else "COMPLETE")
    if partial:
        assert outcome.formal_payload is None
        assert any(
            issue["ruleId"] == "DIRECTION_MACRO_REFERENCE_UNKNOWN"
            for issue in stored["issues"]
        )
    else:
        assert outcome.formal_payload == validate_report(
            raw, packet, checks, completed_at=NOW
        )
    assert raw == before
