"""Historical strict audit diagnostics, independent of sanitized publication."""

from copy import deepcopy
import hashlib

import pytest

from backend.macro.research_batch import diagnose_research_batch
from backend.macro.research_topics import freeze_discovery_packet
from tests.unit.test_daily_research import NOW
from tests.unit.test_industrial_metals_research_regression import case

RUN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
KEY = "__wealthsail_execution_v1"


def executed_checks(packet, checks, *, run_id=RUN, at=NOW):
    """Protocol-shaped local evidence, never a claim of real web execution."""
    manifest = packet["researchDiscovery"]["auditManifest"]
    value = deepcopy(checks)
    value[KEY] = {
        "version": "research-execution-binding-v1",
        "researchId": run_id,
        "manifestHash": manifest["manifestHash"],
        "inputAsOf": manifest["asOf"],
        "validatedAt": at.isoformat(),
        "receipt": {
            "version": "research-execution-v1",
            "observer": "CODEX_EXEC_JSONL",
            "coverage": "COMPLETE",
            "eventsComplete": True,
            "turnCompleted": True,
            "capabilities": {
                "preDispatchEnforcement": False,
                "providerRetriesObservable": False,
            },
            "counts": {
                "webToolCalls": len(manifest["tasks"]),
                "searchActions": len(manifest["tasks"]),
                "searchQueries": len(manifest["tasks"]),
                "pageOpens": 0,
                "pageFinds": 0,
                "otherWebActions": 0,
                "providerRetries": None,
                "clientRetries": 0,
            },
            "queryReceipts": [
                {
                    "queryHash": hashlib.sha256(task["query"].encode()).hexdigest(),
                    "completedItemId": hashlib.sha256(
                        task["taskId"].encode()
                    ).hexdigest(),
                }
                for task in manifest["tasks"]
            ],
            "softQueryLimit": 50,
            "softQueryLimitExceeded": False,
        },
    }
    return value


def prepared():
    packet, raw, checks = case()
    return freeze_discovery_packet(packet, NOW), raw, checks


def evaluate(packet, raw, checks):
    return diagnose_research_batch(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )


def test_model_not_found_without_execution_receipt_is_read_only_observation():
    packet, raw, checks = prepared()
    result = evaluate(packet, raw, checks)
    coverage = result["registry"]["coverage"]
    assert coverage["structureCompleteScopeCount"] == 3
    assert coverage["executionVerifiedScopeCount"] == 0
    assert coverage["executionVerifiedCheckCount"] == 0
    assert coverage["reviewedScopeCount"] == 0
    assert coverage["executionMatchStatus"] == "EXECUTION_UNVERIFIED"
    assert coverage["executionObservedQueryCount"] is None
    assert coverage["searchExecutionVerified"] is False
    assert result["autoPublishAllowed"] is True
    direction = result["formalReport"]["valueDirections"][0]
    assert direction["key"] == raw["valueDirections"][0]["key"]
    assert direction["signal"] == "WATCH"
    assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
    assert direction["investmentEligible"] is False
    assert direction["plans"] == direction["quantitativeFundCodes"] == []
    assert direction["industryCycle"]["searchExecutionVerified"] is False
    assert (
        "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED"
        in direction["formalEligibilityReasons"]
    )
    assert all(
        item["status"] == "OBSERVATION_ONLY" for item in result["registry"]["items"]
    )
    assert not any(item["investmentEligible"] for item in result["registry"]["items"])
    assert result["registry"]["auditTasks"]


def test_public_strict_validator_cannot_bypass_execution_assurance():
    from backend.macro.daily_research import diagnose_report

    packet, raw, checks = prepared()
    before = deepcopy((packet, raw, checks))
    result = diagnose_report(raw, packet, checks, completed_at=NOW)
    direction = result["valueDirections"][0]
    assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
    assert direction["investmentEligible"] is False
    assert direction["plans"] == direction["quantitativeFundCodes"] == []
    assert direction["industryCycle"]["searchExecutionVerified"] is False
    assert (
        "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED"
        in direction["formalEligibilityReasons"]
    )
    assert direction["industryCycle"]["confirmedRiskFlags"]
    assert (packet, raw, checks) == before


def test_exact_completed_queries_close_only_their_frozen_tasks():
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    result = evaluate(packet, raw, checks)
    coverage = result["registry"]["coverage"]
    assert coverage["executionVerifiedScopeCount"] == 3
    assert coverage["searchExecutionVerified"] is True
    assert result["autoPublishAllowed"]
    assert len(result["formalReport"]["valueDirections"]) == 1
    assert (
        result["formalReport"]["valueDirections"][0]["industryCycle"][
            "searchExecutionVerified"
        ]
        is True
    )
    assert checks == executed_checks(packet, prepared()[2])


@pytest.mark.parametrize(
    "field,value",
    [
        ("researchId", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("manifestHash", "0" * 64),
        ("validatedAt", "2026-01-01T00:00:00+08:00"),
    ],
)
def test_receipt_from_other_run_scope_or_clock_cannot_authorize(field, value):
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    checks[KEY][field] = value
    result = evaluate(packet, raw, checks)
    assert result["registry"]["coverage"]["executionVerifiedScopeCount"] == 0
    assert (
        result["registry"]["coverage"]["executionMatchStatus"] == "EXECUTION_UNVERIFIED"
    )
    assert result["registry"]["coverage"]["searchExecutionVerified"] is False
    direction = result["formalReport"]["valueDirections"][0]
    assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
    assert direction["plans"] == []
    assert direction["investmentEligible"] is False
    assert direction["industryCycle"]["searchExecutionVerified"] is False
    assert not any(item["investmentEligible"] for item in result["registry"]["items"])


def test_direction_can_reference_top_reviews_without_repeating_five_checks():
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    before = evaluate(packet, raw, checks)
    compact = deepcopy(raw)
    compact["valueDirections"][0]["industryCycle"] = None
    after = evaluate(packet, compact, checks)
    assert after["autoPublishAllowed"]
    previous = before["formalReport"]["valueDirections"][0]
    current = after["formalReport"]["valueDirections"][0]
    # The compact narrative changes; the actual risk floor, prices and scores do not.
    for field in (
        "riskFlags",
        "confirmedRiskFlags",
        "suspectedRiskFlags",
        "riskLevel",
        "leftDiscountScale",
    ):
        assert current["industryCycle"][field] == previous["industryCycle"][field]
    for old_plan, new_plan in zip(previous["plans"], current["plans"], strict=True):
        assert {k: v for k, v in old_plan.items() if k != "industryCycle"} == {
            k: v for k, v in new_plan.items() if k != "industryCycle"
        }
    assert raw["valueDirections"][0]["industryCycle"] is not None


def test_inherited_review_never_hides_unverified_or_found_risks():
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    raw["valueDirections"][0]["industryCycle"] = None
    raw["industryCycleReviews"][0]["riskChecks"][0]["status"] = "UNVERIFIED"
    result = evaluate(packet, raw, checks)
    assert not any(item["investmentEligible"] for item in result["registry"]["items"])
    assert result["registry"]["coverage"]["reviewedScopeCount"] < 3
