"""Original sector failure shape and explicit risk/coverage invariants, offline."""

from copy import deepcopy

import pytest

from backend.macro.daily_research import diagnose_report
from tests.unit.test_daily_research import NOW
from tests.unit.test_industrial_metals_research_regression import case


def test_missing_nonferrous_audit_preserves_observation_without_trade_permission():
    from backend.macro.research_batch import diagnose_research_batch
    from tests.unit.test_industry_allocation_policy_v2 import freeze_discovery_packet

    packet, raw, checks = case()
    packet = freeze_discovery_packet(packet, NOW)
    raw["industryCycleReviews"] = raw["industryCycleReviews"][:2]
    raw["narrativeDirectionIds"] = {"summary": [], "macroCycle": [], "coverage": []}
    before = deepcopy((packet, raw, checks))
    result = diagnose_research_batch(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        validated_at=NOW,
    )
    topic = next(
        row
        for row in result["registry"]["items"]
        if row["key"] == "industrial-metals-repricing"
    )
    assert topic["status"] == "OBSERVATION_ONLY"
    assert topic["investmentEligible"] is False
    assert any(
        task["industryId"] == "NONFERROUS" for task in result["registry"]["auditTasks"]
    )
    assert result["registry"]["coverage"]["pendingScopeCount"] >= 1
    assert (
        result["registry"]["coverage"]["executionMatchStatus"] == "EXECUTION_UNVERIFIED"
    )
    assert result["registry"]["coverage"]["searchExecutionVerified"] is False
    assert result["autoPublishAllowed"] is True
    directions = result["formalReport"]["valueDirections"]
    assert len(directions) == 1
    direction = directions[0]
    assert direction["key"] == "industrial-metals-repricing"
    assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
    assert direction["signal"] == "WATCH"
    assert direction["investmentEligible"] is False
    assert direction["plans"] == direction["quantitativeFundCodes"] == []
    assert direction["industryCycle"]["searchExecutionVerified"] is False
    assert "DIRECTION_PLAN_SECTOR_UNREVIEWED" in direction["formalEligibilityReasons"]
    assert result["candidateRaw"]["industryCycleReviews"] == raw["industryCycleReviews"]
    assert any(
        review["confirmedRiskFlags"]
        for review in result["formalReport"]["industryCycleReviews"]
    )
    assert (
        diagnose_report(
            result["candidateRaw"],
            packet,
            checks,
            completed_at=NOW,
            research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )["valueDirections"][0]["plans"]
        == []
    )
    assert before == (packet, raw, checks)


def test_unrelated_sector_and_composite_never_replace_product_audit():
    from backend.macro.research_batch import diagnose_research_batch
    from tests.unit.test_industry_allocation_policy_v2 import freeze_discovery_packet

    packet, raw, checks = case()
    packet = freeze_discovery_packet(packet, NOW)
    raw["industryCycleReviews"] = [
        row for row in raw["industryCycleReviews"] if row["industry"] != "NONFERROUS"
    ]
    raw["valueDirections"][0]["reviewIndustryIds"] = ["RARE_EARTH_METALS"]
    value = diagnose_research_batch(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        validated_at=NOW,
    )
    assert not any(row["investmentEligible"] for row in value["registry"]["items"])
    assert "NONFERROUS" not in {row["industry"] for row in raw["industryCycleReviews"]}
    assert value["autoPublishAllowed"] is True
    direction = value["formalReport"]["valueDirections"][0]
    assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
    assert direction["investmentEligible"] is False
    assert direction["signal"] == "WATCH"
    assert direction["plans"] == direction["quantitativeFundCodes"] == []
    assert direction["industryCycle"]["searchExecutionVerified"] is False
    assert "DIRECTION_PLAN_SECTOR_UNREVIEWED" in direction["formalEligibilityReasons"]
    assert (
        value["registry"]["coverage"]["executionMatchStatus"] == "EXECUTION_UNVERIFIED"
    )
    assert any(
        task["industryId"] == "NONFERROUS" for task in value["registry"]["auditTasks"]
    )


def test_independent_invalid_direction_reference_does_not_veto_valid_fund_direction():
    from backend.macro.research_batch import evaluate_research_batch
    from tests.unit.test_industry_allocation_policy_v2 import freeze_discovery_packet

    packet, raw, checks = case()
    raw["industryCycleReviews"].append(
        {**deepcopy(raw["industryCycleReviews"][-1]), "industry": "BROKER"}
    )
    invalid = deepcopy(raw["valueDirections"][0])
    invalid.update(
        key="independent-broker-observation",
        title="独立券商观察",
        thesis="等待后续公开资料。",
        sourceIds=["unresolved-source"],
        reviewIndustryIds=["BROKER"],
        plans=[],
        quantitativeFundCodes=[],
    )
    raw["valueDirections"].append(invalid)
    raw["narrativeDirectionIds"] = {"summary": [], "macroCycle": [], "coverage": []}
    packet["pendingTopicScopes"] = [{"industryId": "BROKER", "searchTerm": "券商"}]
    packet = freeze_discovery_packet(packet, NOW)
    from tests.unit.test_research_cost_review75 import executed_checks

    checks = executed_checks(packet, checks)
    result = evaluate_research_batch(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        validated_at=NOW,
    )
    assert result["autoPublishAllowed"] is True
    assert [row["key"] for row in result["formalReport"]["valueDirections"]] == [
        "industrial-metals-repricing",
        invalid["key"],
    ]
    retained = next(
        row for row in result["registry"]["items"] if row["key"] == invalid["key"]
    )
    assert (
        retained["status"] == "OBSERVATION_ONLY" and not retained["investmentEligible"]
    )
    assert result["formalReport"]["valueDirections"][0]["plans"]
    observation = result["formalReport"]["valueDirections"][1]
    assert observation["thesis"] == invalid["thesis"]
    assert observation["sourceIds"] == []
    assert any(
        row["code"] == "SOURCE_REFERENCE_REMOVED"
        for row in result["formalReport"]["validationWarnings"]
    )


def test_unhashable_topic_key_is_one_quarantined_item_not_a_system_failure():
    from backend.macro.research_batch import diagnose_research_batch
    from tests.unit.test_industry_allocation_policy_v2 import freeze_discovery_packet

    packet, raw, checks = case()
    packet = freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [{"key": ["malformed-key"], "title": "坏主题"}]
    from tests.unit.test_research_cost_review75 import executed_checks

    checks = executed_checks(packet, checks)
    result = diagnose_research_batch(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        validated_at=NOW,
    )
    assert result["autoPublishAllowed"] is True
    assert len(result["formalReport"]["valueDirections"]) == 1
    assert len(result["registry"]["items"]) == 2
    assert result["registry"]["items"][0]["status"] == "QUARANTINED"
    assert result["registry"]["items"][0]["reasonCode"] == "TOPIC_SCHEMA_INVALID"


def test_core_catalog_omission_cannot_be_stored_as_an_apparently_empty_directory():
    from backend.macro.research_topic_repository import (
        prepare_topic_registry,
        TopicRegistrySizeError,
    )
    from tests.unit.test_research_topic_repository import registry

    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    value = registry(run_id, 12)
    value["validatedAt"] = NOW.isoformat()
    value["items"] = []
    value["coverage"].update(hasMore=True, totalCount=12, storedCount=0)
    value["issues"] = [{"reasonCode": "REGISTRY_OUTPUT_LIMIT", "itemIndex": None}]
    # The archive converts this specific resource boundary into the existing
    # explicit UNRECOVERABLE directory receipt, preserving the full frozen raw.
    with pytest.raises(TopicRegistrySizeError):
        prepare_topic_registry(value, run_id, NOW)
