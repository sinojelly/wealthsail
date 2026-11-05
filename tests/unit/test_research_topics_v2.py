"""Discovery size is independent of formal-plan and industry-review budgets."""

from copy import deepcopy
from importlib import import_module

import pytest

from tests.unit.test_industrial_metals_research_regression import case
from tests.unit.test_daily_research import NOW

RUN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ADDITIONAL_CATALOG_INDUSTRIES = [
    "BANK", "AI", "POWER", "GOLD", "DRUG", "COAL", "PCB", "DEFENCE",
    "COMMUNICATION", "MEMORY", "FINTECH", "BROAD", "BROKER", "GAMING",
    "DIVIDEND", "HK_TECH", "AEROSPACE", "SATELLITE", "SOLID_BATTERY", "US_TECH",
]


def topics():
    return import_module("backend.macro.research_topics")


def evaluate_with_execution(raw, packet, checks, **kwargs):
    """Attach offline protocol evidence to the caller's already-frozen packet."""
    from backend.macro.research_batch import evaluate_research_batch
    from tests.unit.test_research_cost_review75 import executed_checks

    checks = executed_checks(
        packet, checks, run_id=kwargs["research_id"], at=kwargs["validated_at"]
    )
    return evaluate_research_batch(raw, packet, checks, **kwargs)


def topic(index, industry="NONFERROUS"):
    return {
        "key": f"topic-{index}",
        "title": f"公开观察{index}",
        "thesis": "观察公开产业资料。",
        "industryClaims": [industry],
        "relatedIndustryIds": [],
        "sourceIds": ["s2"],
        "unknowns": [],
        "pendingTasks": [],
    }


def evaluate(count=12):
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [topic(i) for i in range(count)]
    return evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )


@pytest.mark.parametrize("count", [12, 20, 30])
def test_all_topic_discoveries_survive_one_call_without_expanding_formal_directions(
    count,
):
    result = evaluate(count)
    registry = result["registry"]
    # The strict formal direction is also registered; discoveries are not top8.
    assert len(registry["items"]) == count + 1
    assert registry["coverage"]["maxModelCalls"] == 1
    assert registry["coverage"]["additionalModelCalls"] == 0
    assert "modelCalls" not in registry["coverage"]
    assert registry["coverage"]["searchExecutionVerified"] is True
    assert len(result["formalReport"]["valueDirections"]) == 1
    assert all(
        not row["investmentEligible"]
        for row in registry["items"]
        if row["key"].startswith("topic-")
    )


def test_manifest_preserves_all_scopes_but_only_allocates_ten_times_five_queries():
    packet, _, _ = case()
    packet["discoveryUniverse"] += [
        {"discoverySector": industry} for industry in ADDITIONAL_CATALOG_INDUSTRIES
    ]
    original = deepcopy(packet)
    frozen = topics().freeze_discovery_packet(packet, NOW)
    manifest = frozen["researchDiscovery"]["auditManifest"]
    assert len(manifest["selectedScopes"]) == 10 and len(manifest["tasks"]) == 50
    assert len(manifest["deferredScopes"]) >= 10
    assert len({row["taskId"] for row in manifest["tasks"]}) == 50
    assert packet == original
    assert topics().freeze_discovery_packet(frozen, NOW) == frozen


def test_multiple_funds_in_one_sector_share_exactly_five_tasks_and_frozen_contract():
    from backend.integrations.daily_sources import industry_cycle_research_tasks

    packet, _, _ = case()
    frozen = topics().freeze_discovery_packet(packet, NOW)
    frozen["discoveryUniverse"].append({"discoverySector": "UNPLANNED_LATER"})
    tasks = industry_cycle_research_tasks(frozen, NOW)
    assert (
        sum(
            row["industryId"] == "NONFERROUS"
            for row in frozen["researchDiscovery"]["auditManifest"]["tasks"]
        )
        == 5
    )
    assert "UNPLANNED_LATER" not in {row["industry"] for row in tasks["candidates"]}


def test_strict_alias_composite_and_new_topic_are_distinct_without_keyword_guessing():
    assert topics().match_topic(topic(1, "有色金属"))["status"] == "STRICT_ALIAS"
    composite = topics().match_topic(topic(2, "RARE_EARTH_METALS"))
    assert composite["status"] == "COMPOSITE" and composite["industryIds"] == [
        "RARE_EARTH_METALS"
    ]
    assert set(composite["relatedIndustryIds"]) == {"RARE_EARTH", "NONFERROUS"}
    assert topics().match_topic(topic(3, "稀土有色新材料机会"))["status"] == "NEW_TOPIC"


def test_unrelated_checked_source_does_not_verify_topic_industry_scope():
    proposal = topic(1, "ROBOT")
    match = topics().match_topic(
        proposal,
        sources=[
            {
                "title": "机器人观察",
                "quote": "近期造船行业融资下降。",
                "verification": "SOURCE_CHECKED",
            }
        ],
    )
    assert match["scopeVerified"] is False


def test_missing_fund_industry_review_moves_whole_direction_pending_and_never_emits_plans():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["industryCycleReviews"] = raw["industryCycleReviews"][:2]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    item = next(
        row
        for row in result["registry"]["items"]
        if row["key"] == raw["valueDirections"][0]["key"]
    )
    assert item["status"] == "PENDING_REVIEW" and not item["investmentEligible"]
    assert "plans" not in item
    assert not result["formalReport"] or not result["formalReport"]["valueDirections"]


def test_exact_server_plan_scope_can_repair_model_review_label_without_alias_guess():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["valueDirections"][0]["reviewIndustryIds"] = ["UNKNOWN_MODEL_LABEL"]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert result["formalReport"] and result["autoPublishAllowed"]
    assert set(result["formalReport"]["valueDirections"][0]["reviewIndustryIds"]) == {
        "RARE_EARTH",
        "NONFERROUS",
    }


def test_deferred_topic_is_pending_not_failed_or_silently_omitted():
    packet, raw, checks = case()
    packet["discoveryUniverse"] += [
        {"discoverySector": industry} for industry in ADDITIONAL_CATALOG_INDUSTRIES
    ]
    packet = topics().freeze_discovery_packet(packet, NOW)
    deferred = packet["researchDiscovery"]["auditManifest"]["deferredScopes"][-1]
    raw["topicCatalog"] = [topic(1, deferred)]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    row = next(row for row in result["registry"]["items"] if row["key"] == "topic-1")
    assert row["status"] == "PENDING_REVIEW" and row["pendingTasks"]
    assert result["formalReport"] is not None


def test_v1_coverage_gate_remains_strict_while_v2_tracks_missing_batch_reviews():
    from backend.macro.daily_research import ResearchValidationError, validate_report

    packet, raw, checks = case()
    packet["discoveryUniverse"].append({"discoverySector": "BANK"})
    with pytest.raises(ResearchValidationError):
        validate_report(raw, packet, checks, completed_at=NOW)
    frozen = topics().freeze_discovery_packet(packet, NOW)
    from tests.unit.test_research_cost_review75 import executed_checks
    assert validate_report(raw, frozen, executed_checks(frozen, checks), completed_at=NOW, research_id=RUN)


def test_one_bad_topic_does_not_destroy_independent_formal_projection():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [topic(1), {"key": "bad-topic", "title": "缺失其他字段"}]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert result["formalReport"] is not None
    assert any(item["status"] == "QUARANTINED" for item in result["registry"]["items"])


def test_unsafe_topic_text_is_global_hard_failure_not_repaired_by_deleting_catalog():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [{**topic(1), "thesis": "<script>unsafe</script>"}]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert result["formalReport"] is None and result["safetyBlocked"]


def test_catalog_label_cannot_borrow_an_unrelated_formal_direction_investment_permission():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [
        {**topic(1, "ROBOT"), "key": raw["valueDirections"][0]["key"]}
    ]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    item = result["registry"]["items"][0]
    assert item["status"] == "PENDING_REVIEW" and not item["investmentEligible"]


def test_backlog_contains_deferred_scopes_and_does_not_repeat_completed_review_tasks():
    packet, raw, checks = case()
    packet["discoveryUniverse"] += [
        {"discoverySector": industry} for industry in ADDITIONAL_CATALOG_INDUSTRIES
    ]
    packet = topics().freeze_discovery_packet(packet, NOW)
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    tasks = result["registry"]["auditTasks"]
    reviewed = {row["industry"] for row in raw["industryCycleReviews"]}
    assert not reviewed & {row["industryId"] for row in tasks}
    assert any(row["status"] == "DEFERRED" for row in tasks)


def test_64_catalog_entries_and_eight_distinct_formal_keys_keep_all_72_registry_items():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [topic(i) for i in range(64)]
    raw["valueDirections"] = [
        {**deepcopy(raw["valueDirections"][0]), "key": f"formal-{i}"} for i in range(8)
    ]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert len(result["registry"]["items"]) == 72
    assert result["registry"]["coverage"]["storedCount"] == 72


def test_high_dependency_allows_strict_historical_observation_but_never_auto_ready():
    from tests.unit.test_research_repair import event

    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    orphan = deepcopy(raw["valueDirections"][0])
    orphan.update(
        key="orphan-watch",
        plans=[],
        quantitativeFundCodes=[],
        reviewIndustryIds=[],
        risks=[],
    )
    raw["valueDirections"].append(orphan)
    raw["events"] = [event(raw, checks, importance="HIGH")]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert result["formalReport"] is None and not result["autoPublishAllowed"]
    assert result["strictFormalReport"] is not None and result["strictPass"]
    assert result["historicalPublishAllowed"] and result["quarantined"][-1]["mandatory"]


def test_unreviewed_systemic_topic_keeps_the_fact_but_blocks_automatic_formal_publication():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [
        {**topic(1), "thesis": "系统性风险及金融危机影响尚待进一步核实。"}
    ]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert not result["autoPublishAllowed"] and result["registry"]["items"]
    assert not result["registry"]["items"][0]["investmentEligible"]


def test_new_explicit_scope_gets_five_real_search_terms_without_inventing_an_alias():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [topic(1, "硅光互连")]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    row = result["registry"]["items"][0]
    assert row["match"]["unresolvedClaims"] == ["硅光互连"]
    assert not row["match"]["industryIds"] and row["match"]["proposedScopes"]
    assert len(row["pendingTasks"]) == 5
    assert all(
        task["industryId"].startswith("TOPIC_SCOPE:") and "硅光互连" in task["query"]
        for task in row["pendingTasks"]
    )
    assert all(task["searchTerm"] == "硅光互连" for task in row["pendingTasks"])


def test_industrial_metals_is_related_not_a_strict_broader_nonferrous_alias():
    match = topics().match_topic(topic(1, "工业金属"))
    assert match["status"] == "NEW_TOPIC" and match["industryIds"] == []
    assert "NONFERROUS" in match["relatedIndustryIds"]
    assert (
        topics().match_topic({**topic(2), "industryClaims": ["ROBOT", "BANK"]})[
            "status"
        ]
        == "COMPOSITE"
    )


def test_client_rule_version_cannot_impersonate_a_validator_fingerprint():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    with pytest.raises(ValueError, match="rule version"):
        evaluate_with_execution(
            raw,
            packet,
            checks,
            research_id=RUN,
            validated_at=NOW,
            rule_version="forged-rules",
        )


def test_deferred_dynamic_scope_next_batch_uses_explicit_chinese_search_term():
    packet, _, _ = case()
    proposed = topics().match_topic(topic(1, "硅光互连"))["proposedScopes"][0]
    packet["pendingTopicScopes"] = [proposed]
    frozen = topics().freeze_discovery_packet(packet, NOW)
    manifest = frozen["researchDiscovery"]["auditManifest"]
    assert proposed["industryId"] in manifest["selectedScopes"]
    assert all(
        "硅光互连" in task["query"]
        for task in manifest["tasks"]
        if task["industryId"] == proposed["industryId"]
    )


def test_frozen_manifest_cannot_be_reused_with_a_different_packet_clock():
    packet, _, _ = case()
    frozen = topics().freeze_discovery_packet(packet, NOW)
    frozen["asOf"] = "2026-10-16T10:00:00+08:00"
    with pytest.raises(ValueError, match="manifest"):
        topics().freeze_discovery_packet(frozen, NOW)


def test_v2_model_cannot_exchange_frozen_scope_budget_for_an_unallocated_review():
    from backend.macro.daily_research import ResearchValidationError, validate_report

    packet, raw, checks = case()
    frozen = topics().freeze_discovery_packet(packet, NOW)
    raw["industryCycleReviews"].append(
        {**deepcopy(raw["industryCycleReviews"][-1]), "industry": "BROKER"}
    )
    with pytest.raises(ResearchValidationError) as rejected:
        validate_report(raw, frozen, checks, completed_at=NOW)
    assert rejected.value.rule_id == "INDUSTRY_REVIEW_BUDGET_EXCEEDED"


def test_invalid_direction_schema_with_reliable_key_keeps_independent_formal_work():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["narrativeDirectionIds"] = {"summary": [], "macroCycle": [], "coverage": []}
    invalid = {
        **deepcopy(raw["valueDirections"][0]),
        "key": "bad-schema",
        "signal": "INVALID",
        "plans": [],
        "quantitativeFundCodes": [],
    }
    raw["valueDirections"].append(invalid)
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert (
        result["autoPublishAllowed"]
        and len(result["formalReport"]["valueDirections"]) == 1
    )
    assert result["quarantined"] and not result["safetyBlocked"]


def test_scalar_topic_is_quarantined_without_poisoning_independent_source_proofs():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [42]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert (
        result["autoPublishAllowed"]
        and result["registry"]["items"][0]["status"] == "QUARANTINED"
    )


def test_new_scope_with_checked_same_scope_review_can_close_as_observation_only():
    from tests.unit.test_research_repair import event

    packet, raw, checks = case()
    proposed = topics().match_topic(topic(1, "硅光互连"))["proposedScopes"][0]
    packet["pendingTopicScopes"] = [proposed]
    packet = topics().freeze_discovery_packet(packet, NOW)
    event(raw, checks, summary="近期硅光互连行业融资规模下降30%。")
    raw["sources"][-1]["title"] = "硅光互连行业融资观察"
    checks["s3"]["title"] = raw["sources"][-1]["title"]
    review = deepcopy(raw["industryCycleReviews"][-1])
    review.update(industry=proposed["industryId"], sourceIds=["s3"])
    review["riskChecks"][0].update(status="FOUND", sourceIds=["s3"])
    raw["industryCycleReviews"].append(review)
    raw["topicCatalog"] = [{**topic(1, "硅光互连"), "sourceIds": ["s3"]}]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    item = result["registry"]["items"][0]
    assert (
        item["match"]["status"] == "NEW_TOPIC" and item["status"] == "OBSERVATION_ONLY"
    )
    assert "reviewState" not in item and not item["investmentEligible"]
    assert "DYNAMIC_SCOPE_REVIEW_SUPPORTED" in item["match"]["evidence"]
    assert not item["pendingTasks"]


def test_systemic_source_signal_blocks_auto_without_promoting_a_confirmed_fact():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["sources"][1]["quote"] = "全行业信用违约影响尚待核实。"
    raw["topicCatalog"] = [topic(1, "新型服务行业")]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert not result["autoPublishAllowed"] and result["registry"]["items"]
    assert not result["safetyBlocked"]


def test_unresolved_negative_source_related_to_retained_scope_blocks_auto():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [
        {
            **topic(1, "新型磁材服务"),
            "sourceIds": ["s1"],
            "relatedIndustryIds": ["RARE_EARTH"],
        }
    ]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert (
        not result["autoPublishAllowed"]
        and result["registry"]["items"][0]["status"] == "PENDING_REVIEW"
    )
    assert (
        "SCOPE_UNRESOLVED_RETAINED_DEPENDENCY"
        in result["registry"]["items"][0]["match"]["evidence"]
    )


def test_registry_overflow_is_explicit_and_includes_task_storage_budget():
    import json

    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    raw["topicCatalog"] = [
        {
            **topic(index),
            "industryClaims": [
                f"第{index}主题第{claim}产业" + "公开产业范围" * 4
                for claim in range(8)
            ],
        }
        for index in range(64)
    ]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    registry = result["registry"]
    assert len(json.dumps(registry, ensure_ascii=False).encode()) <= 1024 * 1024
    assert registry["coverage"]["hasMore"]
    assert registry["coverage"]["storedCount"] == 0
    assert registry["coverage"]["totalCount"] == 65
    assert any(
        row["reasonCode"] == "REGISTRY_OUTPUT_LIMIT" for row in registry["issues"]
    )


def test_unverified_review_rows_do_not_close_scope_or_authorize_formal_funds():
    packet, raw, checks = case()
    checks["s2"] = {
        **raw["sources"][1],
        "verification": "SOURCE_CHECKED",
        "quoteMatched": True,
        "retrievedAt": NOW.isoformat(),
        "contentHash": "b" * 64,
    }
    packet = topics().freeze_discovery_packet(packet, NOW)
    for review in raw["industryCycleReviews"]:
        for check in review["riskChecks"]:
            check.update(status="UNVERIFIED", sourceIds=[])
    raw["topicCatalog"] = [topic(1)]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    registry = result["registry"]
    item = next(row for row in registry["items"] if row["key"] == "topic-1")
    assert item["match"]["scopeVerified"] is True
    assert item["status"] == "PENDING_REVIEW" and len(item["pendingTasks"]) == 5
    assert registry["coverage"]["reviewedScopeCount"] == 0
    assert registry["coverage"]["pendingScopeCount"] == 3
    assert not any(row["investmentEligible"] for row in registry["items"])
    assert not result["strictFormalReport"]["valueDirections"]
    assert result["candidateRaw"]["industryCycleReviews"] == raw["industryCycleReviews"]


def test_only_unverified_risk_category_remains_in_scope_tasks():
    packet, raw, checks = case()
    checks["s2"] = {
        **raw["sources"][1],
        "verification": "SOURCE_CHECKED",
        "quoteMatched": True,
        "retrievedAt": NOW.isoformat(),
        "contentHash": "b" * 64,
    }
    packet = topics().freeze_discovery_packet(packet, NOW)
    review = next(
        row for row in raw["industryCycleReviews"] if row["industry"] == "NONFERROUS"
    )
    review["riskChecks"][2].update(status="UNVERIFIED")
    raw["topicCatalog"] = [topic(1)]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    item = next(row for row in result["registry"]["items"] if row["key"] == "topic-1")
    assert item["status"] == "PENDING_REVIEW"
    assert [task["kind"] for task in item["pendingTasks"]] == ["ORDER_CUTS"]
    assert {
        task["kind"]
        for task in result["registry"]["auditTasks"]
        if task["industryId"] == "NONFERROUS"
    } == {"ORDER_CUTS"}


def test_oversize_item_preserves_dynamic_search_terms_in_global_tasks():
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    claims = [f"第{index}产业" + "独立技术范围" * 5 for index in range(8)]
    raw["topicCatalog"] = [{**topic(1), "industryClaims": claims}]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    item = next(row for row in result["registry"]["items"] if row["key"] == "topic-1")
    assert item["status"] == "QUARANTINED" and not item["pendingTasks"]
    tasks = [
        task
        for task in result["registry"]["auditTasks"]
        if task["industryId"].startswith("TOPIC_SCOPE:")
    ]
    assert len(tasks) == 40
    assert {task["searchTerm"] for task in tasks} == set(claims)
    assert all(task["searchTerm"] in task["query"] for task in tasks)


@pytest.mark.parametrize("field", ["malformed-key", "unknowns", "pendingTasks"])
def test_original_catalog_systemic_signal_blocks_auto_before_item_isolation(field):
    packet, raw, checks = case()
    packet = topics().freeze_discovery_packet(packet, NOW)
    proposal = topic(1, "新型服务产业")
    signal = "全行业信用违约影响尚待核实。"
    if field == "malformed-key":
        proposal.update(key=["invalid"], thesis=signal)
    else:
        proposal[field] = [signal]
    raw["topicCatalog"] = [proposal]
    result = evaluate_with_execution(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )
    assert result["strictPass"] is True
    assert result["autoPublishAllowed"] is False
    assert result["formalReport"] is None
    assert result["safetyBlocked"] is False
    assert any(
        row["code"] == "NARRATIVE_RISK_REVIEW_REQUIRED"
        for row in result["autoBlockers"]
    )
    assert result["candidateRaw"]["industryCycleReviews"] == raw["industryCycleReviews"]
