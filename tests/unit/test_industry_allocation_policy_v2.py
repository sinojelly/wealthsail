"""Future allocation preserves risk and cannot audit prose as an industry."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.daily_sources import industry_cycle_research_tasks as _tasks
from backend.macro.research_topics import digest, freeze_discovery_packet

NOW = datetime(2026, 11, 6, 8, tzinfo=ZoneInfo("Asia/Shanghai"))


def industry_cycle_research_tasks(packet, now):
    return _tasks(packet, now, allocation_version="industry-allocation-v2")


def scope(term):
    return {"industryId": "TOPIC_SCOPE:" + digest(term)[:32], "searchTerm": term}


def anchor(index, industry):
    code = f"{index:06d}"
    return {
        "subjectKind": "nav",
        "subjectId": code,
        "fundCode": code,
        "market": "FUND_CN",
        "discoverySector": industry,
        "historyQuality": "READY",
        "entryReady": True,
        "signals": [
            {
                "track": "LEFT_VALUE",
                "triggerState": "CANDIDATE",
                "candidate": True,
                "entryReady": True,
                "distanceToEntryPct": 0.0,
            }
        ],
    }


def test_sentence_backlog_cannot_fill_ten_slots_ahead_of_actual_fund_scope():
    packet = {
        "pendingTopicScopes": [
            scope(f"企业{i}业务增长不能证明机器人盈利。") for i in range(10)
        ],
        "discoveryActionableZones": [anchor(4432, "NONFERROUS")],
        "quantitativeScan": {"items": [anchor(4432, "NONFERROUS")]},
    }
    before = deepcopy(packet)
    result = industry_cycle_research_tasks(packet, NOW)
    assert [r["industry"] for r in result["candidates"]] == ["NONFERROUS"]
    assert len(result["excludedScopes"]) == 10
    assert all(
        r["reasonCode"] == "NARRATIVE_SCOPE_NOT_AUDITABLE"
        for r in result["excludedScopes"]
    )
    assert result["planReviewEligibility"][0]["industryReviewEligible"] is True
    assert packet == before


def test_canonical_pending_without_search_term_uses_known_registry_name():
    result = industry_cycle_research_tasks(
        {"pendingTopicScopes": [{"industryId": "BROKER"}]}, NOW
    )
    assert [r["industry"] for r in result["candidates"]] == ["BROKER"]
    assert "券商" in result["candidates"][0]["queries"][0]["query"]
    assert not result["excludedScopes"]


def test_thirty_six_standard_pending_rows_without_terms_are_not_silently_discarded():
    codes = [
        "BROAD",
        "BANK",
        "AI",
        "ACTIVE_GROWTH",
        "RARE_EARTH",
        "NONFERROUS",
        "BROKER",
        "FINTECH",
        "ROBOT",
        "DEFENCE",
        "COMMUNICATION",
        "INNOVATIVE_DRUG",
        "CONSUMPTION",
        "GOLD",
        "GLOBAL_ENERGY",
        "US_TECH",
        "ROBOTICS_MOBILITY",
        "SEMICONDUCTOR_AI",
        "RARE_EARTH_METALS",
        "FINANCIAL",
        "FINANCE_POLICY",
        "DRUG",
        "HEALTHCARE_CLINICAL",
        "GOLD_ENERGY",
        "AEROSPACE",
        "MEMORY",
        "PCB",
        "POWER",
        "SOLID_BATTERY",
        "SATELLITE",
        "LOW_ALTITUDE",
        "HK_TECH",
        "DIVIDEND",
        "TRADITIONAL_MEDICINE",
        "COAL",
        "GAMING",
    ]
    result = industry_cycle_research_tasks(
        {"pendingTopicScopes": [{"industryId": code} for code in codes]}, NOW
    )
    assert len(result["candidates"]) == 10
    assert len(result["omittedIndustries"]) == 26
    assert set(result["allIndustries"]) == set(codes)
    assert result["excludedScopes"] == []


def test_historical_twelve_keep_first_priority_and_remain_deferred_without_clearance():
    historical = [
        "ROBOT",
        "BANK",
        "BROKER",
        "AI",
        "RARE_EARTH",
        "POWER",
        "GOLD",
        "DRUG",
        "COAL",
        "PCB",
        "DEFENCE",
        "COMMUNICATION",
    ]
    packet = {
        "previousIndustryRiskStates": [
            {"industry": industry, "industryCycle": {"riskFlags": ["ORDER_CUTS"]}}
            for industry in historical
        ],
        "discoveryActionableZones": [anchor(4432, "NONFERROUS")],
    }
    result = industry_cycle_research_tasks(packet, NOW)
    assert [r["industry"] for r in result["candidates"]] == historical[:10]
    assert result["omittedRiskCandidateCount"] == 2
    assert result["omittedPlanIndustryIds"] == ["NONFERROUS"]
    assert len(result["selectionReasons"]) == len(result["allIndustries"])


def test_quantitative_scope_priority_keeps_two_backlog_rotation_places():
    industries = [
        "BROKER",
        "BANK",
        "DEFENCE",
        "FINTECH",
        "RARE_EARTH",
        "BROAD",
        "ROBOT",
        "AI",
    ]
    funds = [anchor(i + 1, industry) for i, industry in enumerate(industries)]
    packet = {
        "pendingTopicScopes": [scope(f"合成生物材料{i}") for i in range(10)],
        "discoveryActionableZones": funds,
        "quantitativeScan": {"items": funds},
    }
    result = industry_cycle_research_tasks(packet, NOW)
    selected = [r["industry"] for r in result["candidates"]]
    assert len(selected) == 10
    assert set(industries) <= set(selected)
    assert sum(s.startswith("TOPIC_SCOPE:") for s in selected) == 2
    assert len({q["query"] for r in result["candidates"] for q in r["queries"]}) == 50


def test_invalid_sentence_scope_with_historical_risk_is_preserved_as_exclusion_evidence():
    pending = scope("汽车业务增长不能证明机器人盈利。")
    packet = {
        "pendingTopicScopes": [pending],
        "previousIndustryRiskStates": [
            {
                "industry": pending["industryId"],
                "industryCycle": {"riskFlags": ["LAYOFFS_CLOSURES"]},
            }
        ],
    }
    before = deepcopy(packet)
    result = industry_cycle_research_tasks(packet, NOW)
    assert not result["candidates"]
    assert any(
        r["industryId"] == pending["industryId"] and r["riskPreserved"]
        for r in result["excludedScopes"]
    )
    assert packet == before


def test_excluded_sentence_scope_cannot_reenter_queries_through_news_bucket():
    pending = scope("汽车业务增长不能证明机器人盈利。")
    packet = {
        "pendingTopicScopes": [pending],
        "alphaPublic": {"sectorCatalysts": {pending["industryId"]: ["news-1"]}},
    }
    result = industry_cycle_research_tasks(packet, NOW)
    assert result["candidates"] == []
    assert result["excludedScopeCount"] == 1


def test_unregistered_product_codes_preserve_obligations_without_auditable_eligibility():
    funds = [anchor(1, "MOBILITY"), anchor(2, "EVENT_ROBOT"), anchor(3, "DEFENCE")]
    packet = {
        "discoveryActionableZones": funds,
        "quantitativeScan": {"items": funds},
        "discoveryUniverse": funds,
        "alphaPublic": {"sectorCatalysts": {"EVENT_ROBOT": ["news-1"]}},
        "pendingTopicScopes": [
            {"industryId": "MOBILITY"},
            {"industryId": "EVENT_ROBOT", "searchTerm": "机器人"},
        ],
    }
    before = deepcopy(packet)
    result = industry_cycle_research_tasks(packet, NOW)
    assert [row["industry"] for row in result["candidates"]] == ["DEFENCE"]
    assert result["omittedPlanIndustryIds"] == ["EVENT_ROBOT", "MOBILITY"]
    eligibility = {row["subjectId"]: row for row in result["planReviewEligibility"]}
    assert eligibility["000001"]["requiredReviewIndustryIds"] == ["MOBILITY"]
    assert eligibility["000002"]["requiredReviewIndustryIds"] == ["EVENT_ROBOT"]
    assert eligibility["000001"]["industryReviewEligible"] is False
    assert eligibility["000002"]["industryReviewEligible"] is False
    assert eligibility["000003"]["industryReviewEligible"] is True
    assert {
        row["industryId"]
        for row in result["excludedScopes"]
        if row["reasonCode"] == "UNKNOWN_PRODUCT_SCOPE"
    } == {"MOBILITY", "EVENT_ROBOT"}
    assert packet == before


def test_unknown_historical_product_scope_keeps_risk_without_bare_code_search():
    packet = {
        "previousIndustryRiskStates": [
            {"industry": "EVENT_ROBOT", "industryCycle": {"riskFlags": ["ORDER_CUTS"]}},
            {
                "industry": "MOBILITY",
                "industryCycle": {"riskFlags": ["FINANCING_COOLING"]},
            },
            {"industry": "ROBOT", "industryCycle": {"riskFlags": ["ORDER_CUTS"]}},
        ],
        "discoveryActionableZones": [anchor(1, "EVENT_ROBOT")],
    }
    before = deepcopy(packet)
    result = industry_cycle_research_tasks(packet, NOW)
    assert [row["industry"] for row in result["candidates"]] == ["ROBOT"]
    assert result["omittedRiskCandidateCount"] == 2
    assert {
        row["industryId"] for row in result["excludedScopes"] if row["riskPreserved"]
    } == {"EVENT_ROBOT", "MOBILITY"}
    assert result["planReviewEligibility"][0]["industryReviewEligible"] is False
    assert packet == before


@pytest.mark.parametrize("entry", ["scan", "catalog"])
def test_unanchored_unknown_product_scope_is_excluded_and_coverage_remains_partial(
    entry,
):
    from backend.macro.daily_research import validate_report
    from test_daily_research import report

    rows = [anchor(1, "MOBILITY"), anchor(2, "EVENT_ROBOT")]
    packet = {
        "asOf": NOW.isoformat(),
        "directions": [],
        "industryCycleResearch": {"required": True},
        "alphaPublic": {"sectorCatalysts": {"EVENT_ROBOT": ["news-1"]}},
        "pendingTopicScopes": [{"industryId": "EVENT_ROBOT", "searchTerm": "机器人"}],
    }
    if entry == "scan":
        packet["quantitativeScan"] = {
            "methodVersion": "fund-quant-scan-v1",
            "asOf": NOW.isoformat(),
            "items": rows,
        }
    else:
        packet["discoveryUniverse"] = rows
    before = deepcopy(packet)
    tasks = industry_cycle_research_tasks(packet, NOW)
    assert tasks["candidates"] == []
    assert tasks["planReviewEligibility"] == []
    assert tasks["omittedPlanIndustryIds"] == []
    assert {
        row["industryId"]
        for row in tasks["excludedScopes"]
        if row["reasonCode"] == "UNKNOWN_PRODUCT_SCOPE"
    } == {"MOBILITY", "EVENT_ROBOT"}
    raw = {
        **report(),
        "sources": [],
        "events": [],
        "valueDirections": [],
        "industryCycleReviews": [],
    }
    actual = validate_report(
        raw, freeze_discovery_packet(packet, NOW), {}, completed_at=NOW
    )
    assert actual["industryResearchCoverage"]["status"] == "PARTIAL"
    assert packet == before


def test_unknown_product_scope_in_legacy_frozen_manifest_is_not_reallocated():
    from backend.macro.industry_cycle import RISK_CHECKS
    from backend.macro.research_topics import freeze_discovery_packet, make_task

    packet = freeze_discovery_packet({"asOf": NOW.isoformat()}, NOW)
    discovery = packet["researchDiscovery"]
    manifest = discovery["auditManifest"]
    contract = manifest["candidateContract"]
    for obj in (discovery, manifest, contract):
        obj.pop("allocationVersion", None)
        obj.pop("topicMatchVersion", None)
    contract["candidates"] = [
        {
            "industry": "EVENT_ROBOT",
            "queries": [
                {"kind": kind, "query": f"EVENT_ROBOT {kind}"} for kind in RISK_CHECKS
            ],
        }
    ]
    manifest["selectedScopes"] = ["EVENT_ROBOT"]
    manifest["tasks"] = [
        make_task("EVENT_ROBOT", kind, f"EVENT_ROBOT {kind}", manifest["window"])
        for kind in RISK_CHECKS
    ]
    manifest["manifestHash"] = digest(
        {key: value for key, value in manifest.items() if key != "manifestHash"}
    )
    before = deepcopy(packet)
    assert industry_cycle_research_tasks(packet, NOW) == contract
    assert freeze_discovery_packet(packet, NOW) == before
    assert packet == before


@pytest.mark.parametrize("historical", [False, True])
def test_excluded_scope_coverage_is_partial_even_without_query_budget_omissions(
    historical,
):
    from backend.macro.daily_research import validate_report
    from test_daily_research import report

    packet = {
        "asOf": NOW.isoformat(),
        "directions": [],
        "industryCycleResearch": {"required": True},
        "discoveryActionableZones": [anchor(1, "EVENT_ROBOT")],
    }
    if historical:
        packet["previousIndustryRiskStates"] = [
            {"industry": "EVENT_ROBOT", "industryCycle": {"riskFlags": ["ORDER_CUTS"]}}
        ]
    raw = {
        **report(),
        "sources": [],
        "events": [],
        "valueDirections": [],
        "industryCycleReviews": [],
    }
    actual = validate_report(
        raw, freeze_discovery_packet(packet, NOW), {}, completed_at=NOW
    )
    coverage = actual["industryResearchCoverage"]
    assert coverage["candidateCount"] == coverage["omittedCandidateCount"] == 0
    assert coverage["omittedRiskCandidateCount"] == int(historical)
    assert coverage["status"] == "PARTIAL"


def test_legacy_frozen_coverage_keeps_original_status_semantics():
    from backend.macro.daily_research import validate_report
    from backend.macro.research_topics import freeze_discovery_packet
    from test_daily_research import report

    packet = freeze_discovery_packet({"asOf": NOW.isoformat(), "directions": []}, NOW)
    discovery = packet["researchDiscovery"]
    manifest = discovery["auditManifest"]
    contract = manifest["candidateContract"]
    for obj in (discovery, manifest, contract):
        obj.pop("allocationVersion", None)
        obj.pop("topicMatchVersion", None)
    for field in ("excludedScopes", "excludedScopeCount", "selectionReasons"):
        contract.pop(field, None)
    contract["omittedRiskCandidateCount"] = 1
    manifest["manifestHash"] = digest(
        {key: value for key, value in manifest.items() if key != "manifestHash"}
    )
    before = deepcopy(packet)
    raw = {
        **report(),
        "sources": [],
        "events": [],
        "valueDirections": [],
        "industryCycleReviews": [],
    }
    actual = validate_report(raw, packet, {}, completed_at=NOW)
    assert actual["industryResearchCoverage"]["status"] == "COMPLETE"
    assert packet == before
