"""Original sector failure shape and explicit risk/coverage invariants, offline."""

from copy import deepcopy

import pytest

from backend.macro.daily_research import validate_report
from tests.unit.test_daily_research import NOW
from tests.unit.test_industrial_metals_research_regression import case


def test_missing_nonferrous_audit_is_pending_and_never_invents_trade_permission():
    from backend.macro.research_batch import evaluate_research_batch
    from backend.macro.research_topics import freeze_discovery_packet

    packet, raw, checks = case()
    packet = freeze_discovery_packet(packet, NOW)
    raw["industryCycleReviews"] = raw["industryCycleReviews"][:2]
    raw["narrativeDirectionIds"] = {"summary": [], "macroCycle": [], "coverage": []}
    before = deepcopy((packet, raw, checks))
    result = evaluate_research_batch(
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
    assert topic["status"] == "PENDING_REVIEW" and topic["investmentEligible"] is False
    assert any(task["industryId"] == "NONFERROUS" for task in topic["pendingTasks"])
    assert result["registry"]["coverage"]["pendingScopeCount"] >= 1
    if result["formalReport"] is not None:
        assert result["formalReport"]["valueDirections"] == []
        assert (
            result["candidateRaw"]["industryCycleReviews"]
            == raw["industryCycleReviews"]
        )
        assert any(
            review["confirmedRiskFlags"]
            for review in result["formalReport"]["industryCycleReviews"]
        )
        assert (
            validate_report(result["candidateRaw"], packet, checks, completed_at=NOW)[
                "valueDirections"
            ]
            == []
        )
    assert before == (packet, raw, checks)


def test_unrelated_sector_and_composite_never_replace_product_audit():
    from backend.macro.research_batch import evaluate_research_batch
    from backend.macro.research_topics import freeze_discovery_packet

    packet, raw, checks = case()
    packet = freeze_discovery_packet(packet, NOW)
    raw["industryCycleReviews"] = [
        row for row in raw["industryCycleReviews"] if row["industry"] != "NONFERROUS"
    ]
    raw["valueDirections"][0]["reviewIndustryIds"] = ["RARE_EARTH_METALS"]
    value = evaluate_research_batch(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        validated_at=NOW,
    )
    assert not any(row["investmentEligible"] for row in value["registry"]["items"])
    assert "NONFERROUS" not in {row["industry"] for row in raw["industryCycleReviews"]}
    assert not value["formalReport"] or not value["formalReport"]["valueDirections"]


def test_independent_invalid_direction_reference_does_not_veto_valid_fund_direction():
    from backend.macro.research_batch import evaluate_research_batch
    from backend.macro.research_topics import freeze_discovery_packet

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
    result = evaluate_research_batch(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        validated_at=NOW,
    )
    assert result["autoPublishAllowed"] is True
    assert [row["key"] for row in result["formalReport"]["valueDirections"]] == [
        "industrial-metals-repricing"
    ]
    isolated = next(
        row for row in result["registry"]["items"] if row["key"] == invalid["key"]
    )
    assert isolated["status"] == "QUARANTINED" and not isolated["investmentEligible"]
    assert result["candidateRaw"]["industryCycleReviews"] == raw["industryCycleReviews"]


def test_unhashable_topic_key_is_one_quarantined_item_not_a_system_failure():
    from backend.macro.research_batch import evaluate_research_batch
    from backend.macro.research_topics import freeze_discovery_packet

    packet, raw, checks = case()
    packet = freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [{"key": ["malformed-key"], "title": "坏主题"}]
    result = evaluate_research_batch(
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
