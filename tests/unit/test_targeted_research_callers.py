"""The worker/acceptance pipeline respects frozen two-tier audit budgets."""

from copy import deepcopy

import pytest

from backend.macro.daily_research import ResearchValidationError, diagnose_report as validate_report
from backend.macro.research_batch import evaluate_research_batch
from backend.macro.research_topics import freeze_discovery_packet, frozen_manifest
from tests.unit.test_targeted_research_v3 import input_packet, NOW
from tests.unit.test_research_autodowngrade_audit import public_failure, RUN


def test_registry_pending_searches_are_exactly_the_frozen_targets_not_observation_tasks():
    source = input_packet()
    source.pop(
        "quantitativeScan"
    )  # The fixture's selection signals are not a public chart DTO.
    packet = freeze_discovery_packet(source, NOW)
    manifest = frozen_manifest(packet)
    result = evaluate_research_batch(
        {"valueDirections": [], "topicCatalog": []},
        packet,
        {},
        research_id=RUN,
        validated_at=NOW,
    )
    registry = result["registry"]
    assert len(registry["auditTasks"]) == 25
    assert {row["taskId"] for row in registry["auditTasks"]} == {
        row["taskId"] for row in manifest["tasks"]
    }
    assert {row["query"] for row in registry["auditTasks"]} == {
        row["query"] for row in manifest["tasks"]
    }
    assert registry["coverage"]["pendingScopeCount"] == 5
    assert registry["coverage"]["observationScopeCount"] > 0


def test_legacy_frozen_report_cannot_expand_its_original_eight_direction_envelope():
    value, at = public_failure()
    raw = deepcopy(value["raw"])
    raw["valueDirections"] = [deepcopy(raw["valueDirections"][0]) for _ in range(9)]
    with pytest.raises(ResearchValidationError, match="frozen direction budget"):
        validate_report(
            raw,
            value["packet"],
            value["sourceChecks"],
            completed_at=at,
            research_id=RUN,
        )


def test_global_dynamic_audit_tasks_keep_identity_pending_without_changing_frozen_queries():
    from tests.unit.test_industry_allocation_policy_v2 import scope

    packet = freeze_discovery_packet({"asOf": NOW.isoformat(), "pendingTopicScopes": [scope("合成生物材料")], "directions": []}, NOW)
    manifest = frozen_manifest(packet)
    result = evaluate_research_batch({"valueDirections": [], "topicCatalog": []}, packet, {}, research_id=RUN, validated_at=NOW)
    tasks = result["registry"]["auditTasks"]
    assert len(tasks) == 5
    assert {row["taskId"] for row in tasks} == {row["taskId"] for row in manifest["tasks"]}
    assert all(row["status"] == "PLANNED_DEFERRED" and row["reasonCode"] == "INDUSTRY_IDENTITY_PENDING" for row in tasks)
