"""Offline shape regression, not a substitute for the ef47f9fd frozen export."""

from copy import deepcopy
from datetime import timedelta
import json

import pytest

from backend.integrations.research_execution import ExecutionObserver
from backend.macro.research_assurance import bind_execution
from backend.macro.research_batch import evaluate_research_batch
from backend.macro.research_topics import freeze_discovery_packet
from tests.unit.test_daily_research import NOW, report
from tests.unit.test_industrial_metals_research_regression import case

RUN = "ef47f9fd-88a3-424a-8584-a562843805cc"
CONCEPTS = ["AI_HARDWARE_REPRICING", "GOLD_OIL_DIVERGENCE"] + [
    f"UNREGISTERED_CONCEPT_{index}" for index in range(14)
]


def combined_case():
    packet, raw, _ = case()
    packet["discoveryUniverse"] = [
        {"discoverySector": sector}
        for sector in (
            "NONFERROUS", "RARE_EARTH", "RARE_EARTH_METALS", "ROBOT", "BANK",
            "GOLD", "COAL", "CHEMICAL", "INNOVATIVE_DRUG", "COMMUNICATION",
        )
    ]
    packet = freeze_discovery_packet(packet, NOW)
    manifest = packet["researchDiscovery"]["auditManifest"]
    assert len(manifest["tasks"]) == 50
    raw["valueDirections"] = []
    raw["narrativeDirectionIds"] = {"summary": [], "macroCycle": [], "coverage": []}
    raw["sources"] = []
    checks = {}
    for index in range(24):
        source = {
            "id": f"s{index:02d}",
            "url": f"https://www.cls.cn/detail/offline-independent-{index}",
            "title": f"公开经济资料{index}",
            "quote": "已公布供应安排，后续交付数量与行业影响仍待观察。",
            "publishedDate": "2026-10-15", "publishedAt": None,
        }
        raw["sources"].append(source)
        checks[source["id"]] = {
            **source, "verification": "SOURCE_CHECKED", "quoteMatched": True,
            "retrievedAt": NOW.isoformat(), "contentHash": "a" * 64,
        }
    raw["industryCycleReviews"] = [
        {
            "industry": sector, "sourceIds": [],
            "riskChecks": [
                {
                    "kind": task["kind"], "status": "NOT_FOUND",
                    "query": task["query"], "summary": "未检出明确公开材料，不代表风险不存在。",
                    "sourceIds": [],
                }
                for task in manifest["tasks"] if task["industryId"] == sector
            ],
        }
        for sector in manifest["selectedScopes"]
    ]
    raw["topicCatalog"] = [
        {
            "key": f"economic-insight-{index}", "title": f"独立经济观察{index}",
            "thesis": "依据已披露供应安排观察经济传导，实际影响仍待核实。",
            "industryClaims": [], "relatedIndustryIds": [], "sourceIds": ["s00"],
            "unknowns": ["经济传导幅度"], "pendingTasks": ["核查后续交付资料"],
        }
        for index in range(64)
    ]
    template = report()["events"][0]
    raw["events"], start = [], 0
    for index, size in enumerate((3, 3, 3, 3, 2, 2)):
        raw["events"].append({
            **deepcopy(template), "title": f"独立经济事件{index}",
            "sourceIds": [f"s{index:02d}"],
            "affectedDirectionIds": CONCEPTS[start:start + size],
        })
        start += size
    raw["upcoming"] = [
        {
            "title": f"后续经济日程{index}", "phase": "PLANNED", "importance": "HIGH",
            "scheduledDate": (NOW + timedelta(days=index + 1)).date().isoformat(),
            "scheduledAt": None, "sourceIds": [f"s{index + 10:02d}"],
            "affectedDirectionIds": [CONCEPTS[index]], "watchFor": ["正式披露"],
        }
        for index in range(3)
    ]
    observer = ExecutionObserver()
    events = [{"type": "thread.started"}, {"type": "turn.started"}]
    queries = [row["query"] for row in manifest["tasks"]] + ["宏观供应安排", "经济日程"]
    for index in range(13):
        events.append({
            "type": "item.completed",
            "item": {"type": "web_search", "id": f"search-{index}",
                     "action": {"type": "search", "queries": queries[index * 4:(index + 1) * 4]}},
        })
    events += [
        {"type": "item.completed", "item": {"type": "web_search", "id": "open-1", "action": {"type": "openPage"}}},
        {"type": "item.completed", "item": {"type": "web_search", "id": "unknown-1", "action": {"type": "new-protocol-action"}}},
        {"type": "turn.completed"},
    ]
    observer.feed(("\n".join(json.dumps(row, ensure_ascii=False) for row in events) + "\n").encode())
    return packet, raw, bind_execution(checks, observer.finish(), manifest, RUN, NOW)


def evaluate(packet, raw, checks):
    return evaluate_research_batch(raw, packet, checks, research_id=RUN, validated_at=NOW)


def test_64_topics_zero_formal_16_unregistered_refs_preserve_six_news_three_dates():
    packet, raw, checks = combined_case()
    before = deepcopy((packet, raw, checks))
    result = evaluate(packet, raw, checks)
    assert result["strictPass"] is True
    retained = result["strictFormalReport"]
    assert retained["valueDirections"] == []
    assert len(retained["events"]) == 6 and len(retained["upcoming"]) == 3
    assert len(result["registry"]["items"]) == 64
    assert not any(row["investmentEligible"] for row in result["registry"]["items"])
    coverage = result["registry"]["coverage"]
    assert coverage["executionMatchedTaskCount"] == coverage["executionPlannedTaskCount"] == 50
    assert coverage["searchExecutionVerified"] is True
    assert coverage["discoveredTopicCount"] == coverage["evidenceBackedTopicCount"] == 64
    assert coverage["evaluableFundDirectionCount"] == coverage["qualifiedFormalDirectionCount"] == 0
    assert coverage["formalEligibilityReasons"]["NO_FORMAL_PROPOSAL"] == 1
    assert coverage["validEventCount"] == 6 and coverage["validUpcomingCount"] == 3
    assert coverage["unresolvedReferenceCount"] == 19
    assert coverage["researchDeliveryStatus"] == "VALIDATED_READ_ONLY"
    assert coverage["investmentQualificationStatus"] == "NO_QUALIFIED_OPPORTUNITIES"
    insight = result["registry"]["items"][0]
    assert insight["unknowns"] == ["经济传导幅度"]
    assert insight["followUp"] == ["核查后续交付资料"]
    assert insight["evidenceCoverageScore"] == 100
    assert any("AI_HARDWARE_REPRICING" in json.dumps(row) for row in retained["events"])
    assert retained["events"][0]["importance"] == "HIGH"
    assert before == (packet, raw, checks)


def test_unknown_news_references_cannot_bypass_a_funds_missing_industry_audit():
    packet, raw, checks = combined_case()
    _, formal, _ = case()
    raw["valueDirections"] = formal["valueDirections"]
    raw["industryCycleReviews"] = [row for row in raw["industryCycleReviews"] if row["industry"] != "NONFERROUS"]
    result = evaluate(packet, raw, checks)
    assert not any(row["investmentEligible"] for row in result["registry"]["items"])
    assert not (result.get("formalReport") or {}).get("valueDirections")


def test_normalized_formal_dependency_keeps_high_risk_quarantine_protection():
    packet, raw, checks = combined_case()
    _, formal, _ = case()
    raw["valueDirections"] = formal["valueDirections"]
    key = raw["valueDirections"][0]["key"]
    raw["events"][0]["relatedFormalDirectionKeys"] = [key.upper().replace("-", "_")]
    raw["industryCycleReviews"] = [row for row in raw["industryCycleReviews"] if row["industry"] != "NONFERROUS"]
    result = evaluate(packet, raw, checks)
    assert not result["autoPublishAllowed"]
    assert any(
        item["itemType"] == "EVENT" and item["itemIndex"] == 0
        and any(ref["businessKey"] == key for ref in item.get("dependencies", []))
        for item in result["quarantined"]
    )


@pytest.mark.parametrize("untrusted", ["UNVERIFIED", "UNAVAILABLE"])
def test_accepted_sources_do_not_become_reliable_topic_evidence(untrusted):
    packet, raw, checks = combined_case()
    for row in checks.values():
        if isinstance(row, dict) and row.get("verification"):
            row["verification"] = untrusted
            row["quoteMatched"] = False
    result = evaluate(packet, raw, checks)
    assert result["registry"]["coverage"]["evidenceBackedTopicCount"] == 0
    assert result["registry"]["coverage"]["qualifiedFormalDirectionCount"] == 0
    assert result["registry"]["coverage"]["researchDeliveryStatus"] == "OBSERVATION_READ_ONLY"


def test_invalid_fund_does_not_cancel_independently_validated_news_delivery():
    packet, raw, checks = combined_case()
    _, formal, _ = case()
    raw["valueDirections"] = formal["valueDirections"]
    raw["industryCycleReviews"] = [row for row in raw["industryCycleReviews"] if row["industry"] != "NONFERROUS"]
    result = evaluate(packet, raw, checks)
    assert result["registry"]["coverage"]["researchDeliveryStatus"] == "VALIDATED_READ_ONLY"
    assert result["registry"]["coverage"]["qualifiedFormalDirectionCount"] == 0
    assert result["registry"]["coverage"]["investmentQualificationStatus"] == "BLOCKED"


def test_empty_or_unsafe_output_cannot_claim_economic_research_delivered():
    packet, _, checks = combined_case()
    result = evaluate(packet, {}, checks)
    assert result["registry"]["coverage"]["researchDeliveryStatus"] == "UNAVAILABLE"
