"""Economic observations do not need an artificial investable direction."""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module

import pytest
from pydantic import ValidationError

from backend.macro import daily_research as strict
from backend.macro import research_acceptance as acceptance
from backend.macro import research_acceptance_repository as repository
from backend.macro.research_topics import freeze_discovery_packet
from tests.unit.test_daily_research import NOW
from tests.unit.test_industrial_metals_research_regression import case
from tests.unit.test_research_cost_review75 import RUN, executed_checks

LINK_FIELDS = (
    "relatedTopicKeys",
    "relatedFormalDirectionKeys",
    "relatedMacroConcepts",
    "candidateConceptLabels",
)


def topic(key="steel-repricing", **changes):
    return {
        "key": key,
        "title": "钢铁供需观察",
        "thesis": "保留公开经济研究，行业资格仍待审核。",
        "industryClaims": ["待核对产业"],
        "relatedIndustryIds": [],
        "sourceIds": ["s2"],
        "unknowns": ["盈利传导待核对"],
        "pendingTasks": ["补充行业审核后再考虑正式计划"],
        **changes,
    }


def event(**changes):
    return {
        "title": "供需与价格观察",
        "summary": "需求可能转弱，价格和企业利润仍需核对。",
        "phase": "ANNOUNCED",
        "importance": "HIGH",
        "sourceIds": ["s2"],
        "channels": ["需求变化影响订单与利润"],
        "affectedDirectionIds": [],
        "scenarios": [
            {"condition": "需求转弱", "impact": "利润承压", "counter": "核对订单"}
        ],
        "watchFor": ["后续订单"],
        "unknowns": ["盈利尚未兑现"],
        "marketReaction": "UNVERIFIED",
        **changes,
    }


def observation_case(*, v2=True):
    packet, raw, checks = case()
    if v2:
        packet = freeze_discovery_packet(packet, NOW)
    raw.update(
        valueDirections=[], topicCatalog=[topic()], events=[event()], upcoming=[]
    )
    return packet, raw, checks


def validate(raw, packet, checks):
    return strict.validate_report(
        raw, packet, checks, completed_at=NOW, research_id=RUN
    )


def test_model_schema_requires_new_link_lists_but_old_python_reports_default_empty():
    row = strict._Event.model_validate(event()).model_dump()
    for name in LINK_FIELDS:
        assert row[name] == []
        for schema_name in ("_Event", "_Upcoming"):
            schema = strict.REPORT_SCHEMA["$defs"][schema_name]
            assert name in schema["required"]
            assert schema["properties"][name]["maxItems"] == 16
    assert (
        "referenceResolution"
        not in strict.REPORT_SCHEMA["$defs"]["_Event"]["properties"]
    )


def test_v2_sixteen_unknown_legacy_links_preserve_high_negative_news_without_formal_plan():
    packet, raw, checks = observation_case()
    unknown = [f"unregistered-theme-{index}" for index in range(16)]
    raw["events"][0]["affectedDirectionIds"] = unknown
    before = deepcopy((packet, raw, checks))

    report = validate(raw, packet, checks)

    row = report["events"][0]
    assert row["affectedDirectionIds"] == []
    assert row["candidateConceptLabels"] == unknown
    assert row["summary"] == raw["events"][0]["summary"]
    assert (
        row["importance"] == "HIGH"
        and row["scenarios"] == raw["events"][0]["scenarios"]
    )
    assert len(row["referenceResolution"]["unresolved"]) == 16
    assert row["referenceResolution"]["resolved"] == []
    assert report["valueDirections"] == [] and report["actionablePlans"] == []
    assert (packet, raw, checks) == before


def test_v1_unknown_legacy_direction_keeps_original_hard_reference_failure():
    packet, raw, checks = observation_case(v2=False)
    raw["events"][0]["affectedDirectionIds"] = ["unregistered-theme"]
    with pytest.raises(strict.ResearchValidationError):
        strict.diagnose_report(raw, packet, checks, completed_at=NOW, research_id=RUN)


def test_readonly_topic_canonical_links_and_declared_concepts_do_not_grant_qualification():
    packet, raw, checks = observation_case()
    raw["events"][0].update(
        relatedTopicKeys=["STEEL_REPRICING"],
        relatedFormalDirectionKeys=["not-formal"],
        relatedMacroConcepts=["全球需求"],
        candidateConceptLabels=["钢铁候选"],
    )
    report = validate(raw, packet, checks)
    row = report["events"][0]
    assert row["relatedTopicKeys"] == ["steel-repricing"]
    assert row["relatedFormalDirectionKeys"] == []
    assert row["relatedMacroConcepts"] == ["全球需求"]
    assert row["candidateConceptLabels"] == ["钢铁候选"]
    assert row["referenceResolution"] == {
        "version": "research-event-links-v1",
        "resolved": [
            {
                "namespace": "TOPIC",
                "inputKey": "STEEL_REPRICING",
                "key": "steel-repricing",
                "method": "CANONICAL",
            }
        ],
        "unresolved": [
            {
                "namespace": "FORMAL_DIRECTION",
                "key": "not-formal",
                "reasonCode": "UNKNOWN_REFERENCE",
            }
        ],
    }
    assert (
        next(
            source
            for source in report["sources"]
            if source["url"].endswith("offline-industrial-metals")
        )["verification"]
        == "UNVERIFIED"
    )
    assert report["valueDirections"] == []


@pytest.mark.parametrize("duplicate_exact", [False, True])
def test_ambiguous_topic_keys_never_resolve_by_canonical_guess(duplicate_exact):
    packet, raw, checks = observation_case()
    raw["topicCatalog"].append(
        topic("steel-repricing" if duplicate_exact else "steel_repricing")
    )
    raw["events"][0]["relatedTopicKeys"] = [
        "steel-repricing" if duplicate_exact else "STEEL_REPRICING"
    ]
    row = validate(raw, packet, checks)["events"][0]
    assert row["relatedTopicKeys"] == []
    assert (
        row["referenceResolution"]["unresolved"][0]["reasonCode"]
        == "AMBIGUOUS_REFERENCE"
    )


def test_unique_exact_topic_key_still_resolves_when_only_canonical_fallback_collides():
    packet, raw, checks = observation_case()
    raw["topicCatalog"].append(topic("steel_repricing"))
    raw["events"][0]["relatedTopicKeys"] = ["steel-repricing"]
    row = validate(raw, packet, checks)["events"][0]
    assert row["relatedTopicKeys"] == ["steel-repricing"]
    assert row["referenceResolution"]["resolved"][0]["method"] == "EXACT"


def test_topic_source_defects_are_repaired_without_losing_its_event_link():
    packet, raw, checks = observation_case()
    raw["topicCatalog"] = [topic(sourceIds=["missing-source"]), {"key": ["malformed"]}]
    raw["events"][0]["relatedTopicKeys"] = ["steel-repricing"]
    row = validate(raw, packet, checks)["events"][0]
    assert row["relatedTopicKeys"] == ["steel-repricing"]
    assert row["referenceResolution"]["resolved"][0]["key"] == "steel-repricing"


def test_v2_real_formal_legacy_reference_remains_an_explicit_formal_dependency():
    packet, raw, checks = case()
    packet = freeze_discovery_packet(packet, NOW)
    checks = executed_checks(packet, checks)
    key = raw["valueDirections"][0]["key"]
    raw["events"] = [event(affectedDirectionIds=[key])]
    row = validate(raw, packet, checks)["events"][0]
    assert row["affectedDirectionIds"] == [key]
    assert row["relatedFormalDirectionKeys"] == [key]
    assert row["referenceResolution"]["resolved"][0]["namespace"] == "FORMAL_DIRECTION"


def test_upcoming_and_merged_events_keep_independent_topic_links():
    packet, raw, checks = observation_case()
    raw["topicCatalog"].append(topic("orders-watch"))
    raw["events"] = [
        event(relatedTopicKeys=["steel-repricing"]),
        event(relatedTopicKeys=["orders-watch"]),
    ]
    raw["upcoming"] = [
        {
            "title": "后续订单披露",
            "phase": "PLANNED",
            "importance": "HIGH",
            "scheduledDate": (NOW + timedelta(days=3)).date().isoformat(),
            "scheduledAt": None,
            "sourceIds": ["s2"],
            "affectedDirectionIds": ["candidate-calendar"],
            "relatedTopicKeys": ["STEEL_REPRICING"],
            "watchFor": ["订单与盈利兑现"],
        }
    ]
    report = validate(raw, packet, checks)
    assert set(report["events"][0]["relatedTopicKeys"]) == {
        "steel-repricing",
        "orders-watch",
    }
    assert len(report["events"][0]["referenceResolution"]["resolved"]) == 2
    assert report["upcoming"][0]["relatedTopicKeys"] == ["steel-repricing"]
    assert (
        report["upcoming"][0]["referenceResolution"]["unresolved"][0]["key"]
        == "candidate-calendar"
    )


@pytest.mark.parametrize("failure", ["source", "calendar", "unsafe"])
def test_observation_link_tolerance_never_weakens_independent_strict_rules(failure):
    packet, raw, checks = observation_case()
    raw["events"][0]["affectedDirectionIds"] = ["unregistered-theme"]
    if failure == "source":
        raw["events"][0]["sourceIds"] = ["missing-source"]
    elif failure == "calendar":
        raw["upcoming"] = [
            {
                "title": "已过期事项",
                "phase": "PLANNED",
                "scheduledDate": "2020-01-01",
                "scheduledAt": None,
                "sourceIds": ["s2"],
                "affectedDirectionIds": [],
                "watchFor": [],
            }
        ]
    else:
        raw["events"][0]["summary"] = "<script>unsafe</script>"
    with pytest.raises(strict.ResearchValidationError):
        strict.diagnose_report(raw, packet, checks, completed_at=NOW, research_id=RUN)


def test_partial_acceptance_probes_share_original_topic_context_and_preserve_story():
    packet, raw, checks = observation_case()
    raw["events"] = [
        event(relatedTopicKeys=["STEEL_REPRICING"]),
        event(sourceIds=["missing-source"]),
    ]
    result = acceptance.diagnose_report_outcome(
        raw, packet, checks, research_id=RUN, completed_at=NOW
    )
    assert result.decision == "PARTIAL" and result.formal_payload is None
    row = result.accepted["events"][0]
    assert row["relatedTopicKeys"] == ["steel-repricing"]
    assert row["channels"] == raw["events"][0]["channels"]
    assert row["scenarios"] == raw["events"][0]["scenarios"]
    assert result.accepted["valueDirections"] == []


def test_safe_acceptance_projection_keeps_story_links_and_clocks_without_private_fields():
    from tests.unit.test_research_acceptance_repository import outcome

    resolution = {
        "version": "research-event-links-v1",
        "resolved": [
            {
                "namespace": "TOPIC",
                "inputKey": "STEEL_REPRICING",
                "key": "steel-repricing",
                "method": "CANONICAL",
                "private": "PRIVATE",
            }
        ],
        "unresolved": [],
    }
    value = outcome(
        accepted={
            "sources": [
                {
                    "id": "s2",
                    "title": "公开来源",
                    "url": "https://example.org/source",
                    "quote": "公开原文",
                    "verification": "UNVERIFIED",
                    "publishedDate": NOW.date().isoformat(),
                    "publishedAt": NOW.isoformat(),
                    "retrievedAt": NOW.isoformat(),
                }
            ],
            "events": [
                event(
                    relatedTopicKeys=["steel-repricing"],
                    referenceResolution=resolution,
                    plans=[{"price": "PRIVATE"}],
                )
            ],
            "upcoming": [],
            "industryCycleReviews": [],
            "valueDirections": [],
        }
    )
    result = repository.safe_outcome_summary(value)
    row = result["accepted"]["events"][0]
    assert row["channels"] == value["accepted"]["events"][0]["channels"]
    assert row["scenarios"] == value["accepted"]["events"][0]["scenarios"]
    assert row["referenceResolution"]["resolved"][0]["key"] == "steel-repricing"
    assert result["accepted"]["sources"][0]["retrievedAt"] == NOW.isoformat()
    assert result["accepted"]["sources"][0]["publishedAt"] == NOW.isoformat()
    assert "PRIVATE" not in str(result) and "plans" not in str(row)


def test_resolution_serialization_is_closed_and_cannot_claim_unknown_methods():
    links = import_module("backend.macro.research_event_links")
    result = links.safe_reference_resolution(
        {
            "version": "research-event-links-v1",
            "private": "PRIVATE",
            "resolved": [
                {
                    "namespace": "TOPIC",
                    "inputKey": "x",
                    "key": "x",
                    "method": "MODEL_VERIFIED",
                    "raw": "PRIVATE",
                }
            ],
            "unresolved": [
                {
                    "namespace": "TOPIC",
                    "key": "missing",
                    "reasonCode": "UNKNOWN_REFERENCE",
                }
            ],
        }
    )
    assert result["resolved"] == []
    assert result["unresolved"] == [
        {"namespace": "TOPIC", "key": "missing", "reasonCode": "UNKNOWN_REFERENCE"}
    ]
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize("field", LINK_FIELDS)
@pytest.mark.parametrize("invalid", [[True], ["x" * 121], ["x"] * 17])
def test_new_declared_lists_reject_non_strings_oversize_labels_and_excess_items(
    field, invalid
):
    with pytest.raises(ValidationError):
        strict._Event.model_validate(event(**{field: invalid}))


@pytest.mark.parametrize("field,invalid", [("namespace", []), ("method", {})])
def test_untrusted_resolution_metadata_cannot_raise_a_projection_type_error(
    field, invalid
):
    links = import_module("backend.macro.research_event_links")
    row = {
        "namespace": "TOPIC",
        "inputKey": "x",
        "key": "x",
        "method": "EXACT",
        field: invalid,
    }
    assert (
        links.safe_reference_resolution(
            {"version": "research-event-links-v1", "resolved": [row], "unresolved": []}
        )["resolved"]
        == []
    )


@pytest.mark.parametrize(
    "keys,reference,expected",
    [
        (["foo_bar", "foo-bar"], "FOO_BAR", {"foo_bar", "foo-bar"}),
        (["foo-bar", "foo-bar"], "foo-bar", {"foo-bar"}),
        (["foo-bar"], "FOO_BAR", {"foo-bar"}),
        (["foo-bar"], "unrelated", set()),
    ],
)
def test_possible_formal_dependencies_only_expand_ambiguity_for_risk_closure(
    keys, reference, expected
):
    links = import_module("backend.macro.research_event_links")
    row = event(relatedFormalDirectionKeys=[reference])
    assert links.possible_formal_dependencies(row, keys) == expected
    if len(keys) > 1:
        assert links.resolve_reference(reference, keys)[0] is None
        assert links.resolved_formal_dependencies(row, keys) == set()


def test_canonical_collision_cannot_lose_high_event_risk_after_direction_pruning():
    from tests.unit.test_research_independent_contracts import combined_case
    from backend.macro.research_batch import diagnose_research_batch

    packet, raw, checks = combined_case()
    _, sample, _ = case()
    retained = deepcopy(sample["valueDirections"][0])
    retained.update(
        key="foo_bar",
        title="公开计划",
        thesis="依据公开资料观察。",
        sourceIds=["s00", "s01"],
        industryCycle=None,
        risks=[],
        catalysts=[],
    )
    removed = deepcopy(retained)
    removed.update(
        key="foo-bar",
        title="额外观察",
        thesis="继续核对公开资料。",
        plans=[],
        quantitativeFundCodes=[],
        reviewIndustryIds=[],
    )
    raw["valueDirections"] = [retained, removed]
    raw["events"][0]["relatedFormalDirectionKeys"] = ["FOO_BAR"]

    result = diagnose_research_batch(raw, packet, checks, research_id=RUN, validated_at=NOW)

    assert result["autoPublishAllowed"] is False
    assert result["retained"]["directionKeys"] == ["foo_bar"]
    assert any(
        block["code"] == "HIGH_IMPORTANCE_DEPENDENCY"
        for block in result["autoBlockers"]
    )
    assert any(
        item["itemType"] == "EVENT"
        and item["itemIndex"] == 0
        and {dependency["businessKey"] for dependency in item["dependencies"]}
        == {"foo-bar"}
        for item in result["quarantined"]
    )
