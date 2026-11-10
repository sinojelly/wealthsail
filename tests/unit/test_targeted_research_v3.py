"""Top-five complete audits coexist with wider observations without fabricated proof."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

import pytest

from backend.integrations.daily_sources import industry_cycle_research_tasks
from backend.integrations import research_execution as execution
from backend.macro import research_topics as topics
from backend.macro.research_assurance import audit_assurance, bind_execution
from backend.macro.value_directions import validate_value_directions
from tests.unit.test_daily_research import NOW
from tests.unit.test_research_execution import event, observe
from tests.unit.test_industry_allocation_policy_v2 import anchor
from tests.unit.test_value_directions import direction


INDUSTRIES = [
    "BANK",
    "BROKER",
    "ROBOT",
    "AI",
    "GOLD",
    "NONFERROUS",
    "RARE_EARTH",
    "FINTECH",
    "DEFENCE",
    "COAL",
    "PCB",
    "POWER",
]


def input_packet():
    anchors = [anchor(index + 1, industry) for index, industry in enumerate(INDUSTRIES)]
    for index, row in enumerate(anchors):
        row["signals"][0]["distanceToEntryPct"] = float(index)
    return {
        "asOf": NOW.isoformat(),
        "directions": [],
        "discoveryUniverse": deepcopy(anchors),
        "discoveryActionableZones": anchors,
        "quantitativeScan": {"items": deepcopy(anchors)},
        "pendingTopicScopes": [{"industryId": "SATELLITE"}],
    }


def test_new_freeze_targets_five_industries_and_never_schedules_searches_for_observations():
    packet = input_packet()
    before = deepcopy(packet)
    frozen = topics.freeze_discovery_packet(packet, NOW)
    manifest = topics.frozen_manifest(frozen)
    assert frozen["researchDiscovery"]["version"] == "research-discovery-v3"
    assert manifest["allocationVersion"] == "industry-allocation-v3"
    assert manifest["selectedScopes"] == INDUSTRIES[:5]
    assert len(manifest["tasks"]) == 25
    assert manifest["budget"]["maxScopes"] == 5
    assert manifest["budget"]["maxQueries"] == 25
    assert manifest["budget"]["maxFormalDirections"] == 10
    assert (
        manifest["candidateContract"]["observationScopes"] == manifest["deferredScopes"]
    )
    assert all(task["industryId"] in INDUSTRIES[:5] for task in manifest["tasks"])
    assert {
        task["kind"] for task in manifest["tasks"] if task["industryId"] == "BANK"
    } == {
        "FINANCING_COOLING",
        "LAYOFFS_CLOSURES",
        "ORDER_CUTS",
        "OVERCAPACITY_PRICE_WAR",
        "PRODUCTION_DELAY",
    }
    assert packet == before


def test_explicit_v2_allocation_still_schedules_its_original_ten_scope_budget():
    result = industry_cycle_research_tasks(
        input_packet(), NOW, allocation_version="industry-allocation-v2"
    )
    assert result["allocationVersion"] == "industry-allocation-v2"
    assert len(result["candidates"]) == 10
    assert sum(len(row["queries"]) for row in result["candidates"]) == 50
    assert "observationScopes" not in result


@pytest.mark.parametrize("in_zone", [True, False])
def test_targeted_buy_proximity_holding_and_historical_risks_precede_news(in_zone):
    bank = anchor(1, "BANK")
    bank["entryReady"] = in_zone
    bank["signals"] = [
        {
            "entryReady": in_zone,
            "candidate": in_zone,
            "distanceToEntryPct": 0 if in_zone else 1.2,
        }
    ]
    packet = {
        "asOf": NOW.isoformat(),
        "holdingIndustryIds": ["DEFENCE"],
        "previousIndustryRiskStates": [
            {"industry": "COAL", "industryCycle": {"riskFlags": ["ORDER_CUTS"]}}
        ],
        "discoveryActionableZones": [deepcopy(bank)],
        "quantitativeScan": {"items": [bank]},
        "alphaPublic": {
            "sectorCatalysts": {
                industry: ["today-positive"]
                for industry in [
                    "ROBOT",
                    "AI",
                    "GOLD",
                    "NONFERROUS",
                    "RARE_EARTH",
                    "POWER",
                ]
            },
            "catalysts": [
                {
                    "id": "today-positive",
                    "title": "产业企业已披露盈利增长",
                    "publishedAt": NOW.isoformat(),
                    "retrievedAt": NOW.isoformat(),
                    "availableAt": NOW.isoformat(),
                    "sourceUri": "https://www.cls.cn/detail/12345",
                }
            ],
        },
    }
    before = deepcopy(packet)
    result = industry_cycle_research_tasks(
        packet, NOW, allocation_version="industry-allocation-v3"
    )
    selected = [row["industry"] for row in result["candidates"]]
    assert selected[:3] == ["DEFENCE", "BANK", "COAL"]
    assert len(selected) == 5
    assert packet == before


@pytest.mark.parametrize(
    "failure",
    [
        "CONFLICT",
        "entryBlocked",
        "subscriptionRestricted",
        "inSellZone",
        "preferredEntryEligible",
        "blocked_signal",
        "sell_signal",
        "restricted_signal",
        "preferred_signal",
        "mixed_signal",
    ],
)
def test_targeted_same_industry_bad_quote_or_blocked_signal_cannot_promote_far_fund(
    failure,
):
    industries = ["BANK", "BROKER", "ROBOT", "AI", "GOLD", "NONFERROUS"]
    rows = [anchor(index + 1, industry) for index, industry in enumerate(industries)]
    for index, row in enumerate(rows):
        row["entryReady"] = False
        row["signals"] = [
            {
                "entryReady": False,
                "candidate": False,
                "triggerState": "WATCH",
                "distanceToEntryPct": 50 if not index else index,
            }
        ]
    bad = anchor(7, "BANK")
    bad["entryReady"] = False
    bad["signals"] = [
        {
            "entryReady": False,
            "candidate": True,
            "triggerState": "CANDIDATE",
            "distanceToEntryPct": 0,
        }
    ]
    if failure == "CONFLICT":
        bad["historyQuality"] = failure
    elif failure in {"entryBlocked", "subscriptionRestricted", "inSellZone"}:
        bad[failure] = True
    elif failure == "preferredEntryEligible":
        bad[failure] = False
    else:
        field = {
            "blocked_signal": "entryBlocked",
            "sell_signal": "inSellZone",
            "restricted_signal": "subscriptionRestricted",
            "preferred_signal": "preferredEntryEligible",
            "mixed_signal": "entryBlocked",
        }[failure]
        bad["signals"][0][field] = failure != "preferred_signal"
        if failure == "mixed_signal":
            bad["entryReady"] = True
            bad["signals"][0]["entryReady"] = True
            bad["signals"].append(
                {
                    "entryReady": False,
                    "candidate": False,
                    "triggerState": "WATCH",
                    "distanceToEntryPct": 100,
                }
            )
    packet = {
        "asOf": NOW.isoformat(),
        "discoveryActionableZones": deepcopy([*rows, bad]),
        "quantitativeScan": {"items": [*rows, bad]},
    }
    before = deepcopy(packet)
    direct = industry_cycle_research_tasks(
        packet, NOW, allocation_version="industry-allocation-v3"
    )
    assert [row["industry"] for row in direct["candidates"]] == industries[1:]
    frozen = topics.freeze_discovery_packet(packet, NOW)
    assert topics.frozen_manifest(frozen)["selectedScopes"] == industries[1:]
    assert packet == before


def test_targeted_observation_has_no_mandatory_five_kind_follow_up_searches():
    frozen = topics.freeze_discovery_packet(input_packet(), NOW)
    manifest = topics.frozen_manifest(frozen)
    match = {"industryIds": ["BANK", "NONFERROUS"], "proposedScopes": []}
    tasks = topics.topic_tasks(match, manifest)
    assert len(tasks) == 5
    assert {row["industryId"] for row in tasks} == {"BANK"}
    assert (
        topics.topic_tasks(
            {"industryIds": ["NONFERROUS"], "proposedScopes": []}, manifest
        )
        == []
    )
    legacy = deepcopy(manifest)
    legacy["allocationVersion"] = "industry-allocation-v2"
    assert len(topics.topic_tasks(match, legacy)) == 10


def test_targeted_pending_tasks_reuse_frozen_search_identity_query_and_window():
    frozen = topics.freeze_discovery_packet(input_packet(), NOW)
    manifest = topics.frozen_manifest(frozen)
    before = deepcopy(manifest)
    match = {"industryIds": ["BANK", "ROBOT", "NONFERROUS"], "proposedScopes": []}
    tasks = topics.topic_tasks(match, manifest)
    expected = [
        {**deepcopy(row), "status": "PENDING_REVIEW", "reasonCode": "AUDIT_INCOMPLETE"}
        for row in manifest["tasks"]
        if row["industryId"] in {"BANK", "ROBOT"}
    ]
    assert tasks == expected
    assert {row["taskId"] for row in tasks} == {row["taskId"] for row in expected}
    tasks[0]["window"]["lookbackDays"] = 1
    tasks[0]["query"] = "mutated display query"
    assert manifest == before


def test_targeted_named_scope_preserves_identity_pending_with_frozen_queries():
    term = "量子传感器"
    scope = {
        "industryId": "TOPIC_SCOPE:" + topics.digest(term)[:32],
        "searchTerm": term,
    }
    packet = topics.freeze_discovery_packet(
        {"asOf": NOW.isoformat(), "pendingTopicScopes": [scope]}, NOW
    )
    manifest = topics.frozen_manifest(packet)
    tasks = topics.topic_tasks({"industryIds": [], "proposedScopes": [scope]}, manifest)
    assert tasks == [
        {
            **deepcopy(row),
            "status": "PLANNED_DEFERRED",
            "reasonCode": "INDUSTRY_IDENTITY_PENDING",
        }
        for row in manifest["tasks"]
    ]


def test_original_v2_manifest_is_not_reallocated_or_rehashed_by_new_policy():
    path = (
        Path(__file__).parents[1] / "fixtures/research_autodowngrade_d781_public.json"
    )
    old = json.loads(path.read_text("utf-8"))["packet"]
    before = deepcopy(old)
    at = datetime.fromisoformat(old["asOf"])
    manifest = topics.frozen_manifest(old)
    assert manifest["budget"]["maxScopes"] == 10
    assert len(manifest["tasks"]) == 50
    assert topics.freeze_discovery_packet(old, at) == before
    assert industry_cycle_research_tasks(old, at) == manifest["candidateContract"]
    assert topics.direction_limit(old) == 8
    assert old == before


def test_new_contract_allows_ten_directions_but_loose_version_claim_does_not_expand_old_limits():
    frozen = topics.freeze_discovery_packet(input_packet(), NOW)
    assert topics.direction_limit(frozen) == 10
    assert (
        topics.direction_limit(
            {"industryCycleResearch": {"allocationVersion": "industry-allocation-v3"}}
        )
        == 8
    )


@pytest.mark.parametrize("count", [6, 10])
def test_observations_need_no_unallocated_five_kind_search_records(count):
    packet = topics.freeze_discovery_packet(input_packet(), NOW)
    rows = [
        direction(
            key=f"observe-{index}",
            sourceIds=["s1"],
            macroCodes=[],
            plans=[],
            reviewIndustryIds=[INDUSTRIES[(index + 5) % len(INDUSTRIES)]],
            industryCycle=None,
        )
        for index in range(count)
    ]
    before = deepcopy(rows)
    result = validate_value_directions(
        rows,
        packet,
        NOW,
        {"s1": "source:one"},
        sources={
            "source:one": {
                "verification": "SOURCE_CHECKED",
                "quote": "公开的行业观察。",
            }
        },
        industry_reviews=[],
        audit_completed_scopes=set(),
    )
    assert len(result) == count
    for proposal, row in zip(before, result, strict=True):
        assert row["signal"] == proposal["signal"] and row["plans"] == []
        assert row["auditStatus"] == "UNAUDITED_SCOPE"
        assert row["qualificationStatus"] == "OBSERVATION_ONLY"
        assert row["investmentEligible"] is False
        assert row["industryCycle"]["riskChecks"] == []
        assert (
            "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED" in row["formalEligibilityReasons"]
        )
    assert rows == before


@pytest.mark.parametrize(
    "failure", ["unknown_industry", "unknown_source", "no_evidence"]
)
def test_observation_layer_repairs_identity_sources_and_keeps_prose(
    failure,
):
    packet = topics.freeze_discovery_packet(input_packet(), NOW)
    row = direction(
        key="observe",
        sourceIds=["s1"],
        macroCodes=[],
        plans=[],
        reviewIndustryIds=["NONFERROUS"],
        industryCycle=None,
    )
    if failure == "unknown_industry":
        row["reviewIndustryIds"] = ["FAKE_INDUSTRY"]
    elif failure == "unknown_source":
        row["sourceIds"] = ["invented"]
    else:
        row["sourceIds"] = []
    result = validate_value_directions(
            [row],
            packet,
            NOW,
            {"s1": "source:one"},
            industry_reviews=[],
            audit_completed_scopes=set(),
        )
    assert result[0]["thesis"] == row["thesis"]
    assert result[0]["auditStatus"] == "UNAUDITED_SCOPE"
    if failure == "unknown_industry":
        assert result[0]["reviewIndustryIds"] == ["BROAD"]
        assert result[0]["originalIndustryIds"] == ["FAKE_INDUSTRY"]
    elif failure in {"unknown_source", "no_evidence"}:
        assert result[0]["sourceIds"] == []


@pytest.mark.parametrize("count", [6, 10])
def test_complete_batch_retains_wider_observations_and_macro_without_any_audits(count):
    from backend.macro.daily_research import validate_report
    from backend.macro.research_batch import evaluate_research_batch
    from tests.unit.test_industrial_metals_research_regression import case

    packet, raw, source_checks = case()
    packet["pendingTopicScopes"] = [{"industryId": industry} for industry in INDUSTRIES]
    packet = topics.freeze_discovery_packet(packet, NOW)
    manifest = topics.frozen_manifest(packet)
    assert len(manifest["selectedScopes"]) == 5
    scopes = manifest["selectedScopes"] + manifest["deferredScopes"]
    raw["industryCycleReviews"] = []
    raw["valueDirections"] = [
        direction(
            key=f"complete-observe-{index}",
            title=f"行业观察{index}",
            sourceIds=["s2"],
            macroCodes=[],
            plans=[],
            quantitativeFundCodes=[],
            reviewIndustryIds=[scopes[index]],
            industryCycle=None,
        )
        for index in range(count)
    ]
    raw["topicCatalog"] = [
        {
            "key": row["key"],
            "title": row["title"],
            "thesis": row["thesis"],
            "industryClaims": row["reviewIndustryIds"],
            "relatedIndustryIds": [],
            "sourceIds": ["s2"],
            "unknowns": ["尚未完成反方核查。"],
            "pendingTasks": [],
        }
        for row in raw["valueDirections"]
    ]
    before = deepcopy((packet, raw, source_checks))
    validated = validate_report(
        raw, packet, source_checks, completed_at=NOW, research_id=UUID(int=1)
    )
    result = evaluate_research_batch(
        raw, packet, source_checks, validated_at=NOW, research_id=UUID(int=1)
    )
    assert result["autoPublishAllowed"] is True
    assert result["formalReport"]["summary"] == validated["summary"] == raw["summary"]
    for report in [validated, result["formalReport"]]:
        assert len(report["valueDirections"]) == count
        assert all(
            row["plans"] == [] and row["investmentEligible"] is False
            for row in report["valueDirections"]
        )
    assert len(result["registry"]["auditTasks"]) == 25
    assert all(
        item["investmentEligible"] is False for item in result["registry"]["items"]
    )
    assert (packet, raw, source_checks) == before


def test_readable_positive_frozen_source_never_certifies_five_not_found_checks():
    packet = topics.freeze_discovery_packet(input_packet(), NOW)
    manifest = topics.frozen_manifest(packet)
    reviews = {
        "BANK": {
            "riskChecks": [
                {
                    "kind": row["kind"],
                    "status": "NOT_FOUND",
                    "sourceIds": ["source:positive"],
                }
                for row in manifest["tasks"]
                if row["industryId"] == "BANK"
            ]
        }
    }
    checks = {
        "source:positive": {
            "verification": "SOURCE_CHECKED",
            "quoteMatched": True,
            "contentHash": "a" * 64,
            "retrievedAt": NOW.isoformat(),
            "quote": "银行盈利改善。",
        }
    }
    assurance = audit_assurance(reviews, manifest, checks, NOW, research_id=UUID(int=1))
    assert assurance["completedKinds"]["BANK"] == set()
    assert assurance["executionVerifiedCheckCount"] == 0


def test_real_completed_normalized_searches_can_complete_only_the_frozen_industry_and_risk():
    packet = topics.freeze_discovery_packet(input_packet(), NOW)
    manifest = topics.frozen_manifest(packet)
    run_id = UUID(int=1)
    phrases = {
        "FINANCING_COOLING": "融资下降",
        "LAYOFFS_CLOSURES": "裁员",
        "ORDER_CUTS": "订单取消",
        "OVERCAPACITY_PRICE_WAR": "降价竞争",
        "PRODUCTION_DELAY": "量产延期",
    }
    receipt = observe(
        *[
            event(
                str(index),
                action={
                    "type": "search",
                    "query": f"{NOW.year} 银行 {phrases[task['kind']]}",
                },
            )
            for index, task in enumerate(manifest["tasks"])
            if task["industryId"] == "BANK"
        ]
    )
    checks = bind_execution({}, receipt, manifest, run_id, NOW)
    reviews = {
        "BANK": {
            "riskChecks": [
                {"kind": row["kind"], "status": "NOT_FOUND"}
                for row in manifest["tasks"]
                if row["industryId"] == "BANK"
            ]
        }
    }
    assurance = audit_assurance(reviews, manifest, checks, NOW, research_id=run_id)
    assert len(assurance["completedKinds"]["BANK"]) == 5
    assert assurance["executionVerifiedCheckCount"] == 5
    assert execution.executed_task_ids(receipt, manifest) == {
        task["taskId"] for task in manifest["tasks"] if task["industryId"] == "BANK"
    }


def test_trusted_not_found_updates_audit_status_without_stripping_unaudited_fund_plan():
    from backend.macro.daily_research import validate_report
    from tests.unit.test_industrial_metals_research_regression import case

    packet, raw, checks = case()
    packet = topics.freeze_discovery_packet(packet, NOW)
    raw["industryCycleReviews"] = [
        row for row in raw["industryCycleReviews"] if row["industry"] == "NONFERROUS"
    ]
    direction = raw["valueDirections"][0]
    direction.update(reviewIndustryIds=["NONFERROUS"], industryCycle=None)
    direction["plans"] = direction["plans"][:1]
    for source in raw["sources"]:
        checks[source["id"]] = {
            **source,
            "verification": "SOURCE_CHECKED",
            "quoteMatched": True,
            "retrievedAt": NOW.isoformat(),
            "contentHash": "a" * 64,
        }
    before = deepcopy((packet, raw, checks))
    without = validate_report(
        raw, packet, checks, completed_at=NOW, research_id=UUID(int=1)
    )
    assert without["valueDirections"][0]["investmentEligible"] is True
    assert without["valueDirections"][0]["plans"]
    assert without["valueDirections"][0]["auditStatus"] == "UNAUDITED_SCOPE"
    manifest = topics.frozen_manifest(packet)
    receipt = observe(
        *[
            event(str(index), action={"type": "search", "query": task["query"]})
            for index, task in enumerate(manifest["tasks"])
            if task["industryId"] == "NONFERROUS"
        ]
    )
    trusted = bind_execution(checks, receipt, manifest, UUID(int=1), NOW)
    with_proof = validate_report(
        raw, packet, trusted, completed_at=NOW, research_id=UUID(int=1)
    )
    strict = with_proof["valueDirections"][0]
    assert strict["plans"]
    assert strict["auditStatus"] == "AUDITED"
    assert strict.get("qualificationStatus") != "OBSERVATION_ONLY"
    assert strict.get("investmentEligible") is not False
    assert (packet, raw, checks) == before
