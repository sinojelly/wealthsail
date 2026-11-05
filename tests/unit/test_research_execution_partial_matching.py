"""Matching progress is diagnostic and never changes exact-query permissions."""

from uuid import uuid4

import pytest

from backend.integrations import research_execution as execution
from backend.macro.research_topic_repository import prepare_topic_registry
from tests.unit.test_research_execution import event, observe
from tests.unit.test_research_topic_repository import NOW, registry


def receipt_for(count, *, version="v2", extra=False, local_unknown=False):
    events = [
        event(str(index), action={"type": "search", "query": f"exact-{index}"})
        for index in range(count)
    ]
    if extra:
        events.append(event("extra", action={"type": "search", "query": "macro-extra"}))
    if local_unknown:
        events.append(event("unknown", action={"type": "unknownAction"}))
    receipt = observe(*events)
    if version == "v1":
        receipt = {
            key: value
            for key, value in receipt.items()
            if key not in {"actionProofs", "proofBarrier"}
        }
        receipt["version"] = "research-execution-v1"
    return receipt


MANIFEST = {
    "tasks": [
        {"taskId": f"task-{index}", "query": f"exact-{index}"} for index in range(50)
    ]
}


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize(
    "count,extra,expected",
    [
        (48, False, "EXECUTION_PARTIALLY_MATCHED"),
        (50, False, "EXECUTION_MATCHED"),
        (48, True, "EXECUTION_UNMATCHED"),
        (0, False, "EXECUTION_NOT_OBSERVED"),
    ],
)
def test_matching_classification_distinguishes_task_progress_from_extra_queries(
    version, count, extra, expected
):
    receipt = receipt_for(count, version=version, extra=extra)
    stats = execution.execution_match_stats(receipt, MANIFEST)
    assert stats["classification"] == expected
    assert stats["plannedTaskCount"] == 50
    assert stats["matchedTaskCount"] == count
    assert stats["unmatchedQueryHashCount"] == int(extra)
    assert execution.executed_task_ids(receipt, MANIFEST) == {
        f"task-{i}" for i in range(count)
    }


def test_v2_local_unknown_keeps_trusted_partial_progress_while_v1_partial_cannot_upgrade():
    current = receipt_for(48, local_unknown=True)
    stats = execution.execution_match_stats(current, MANIFEST)
    assert stats["classification"] == "EXECUTION_PARTIALLY_MATCHED"
    assert stats["matchedTaskCount"] == 48
    legacy = receipt_for(48, version="v1", local_unknown=True)
    stats = execution.execution_match_stats(legacy, MANIFEST)
    assert stats["classification"] == "EXECUTION_UNVERIFIED"
    assert stats["matchedTaskCount"] == 0
    assert not execution.executed_task_ids(legacy, MANIFEST)


def test_partial_progress_classification_survives_closed_registry_projection():
    run_id = uuid4()
    value = registry(run_id, 1)
    value["coverage"].update(
        executionMatchStatus="EXECUTION_PARTIALLY_MATCHED",
        executionPlannedTaskCount=50,
        executionMatchedTaskCount=48,
    )
    safe = prepare_topic_registry(value, run_id, NOW)
    assert safe["coverage"]["executionMatchStatus"] == "EXECUTION_PARTIALLY_MATCHED"
    assert safe["coverage"]["executionMatchedTaskCount"] == 48
    assert not safe["items"][0]["investmentEligible"]
