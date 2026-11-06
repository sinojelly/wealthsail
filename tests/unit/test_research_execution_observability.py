"""Safe JSONL cost and progress observations never grant audit authority."""

import json

import pytest

from backend.integrations.research_execution import ExecutionObserver, executed_task_ids


def web(identity, action, kind="item.completed"):
    return {
        "type": kind,
        "item": {"id": identity, "type": "web_search", "action": action},
    }


def feed(observer, *events):
    observer.feed(("\n".join(json.dumps(row) for row in events) + "\n").encode())


def test_duplicate_queries_and_page_targets_count_actual_distinct_actions_only():
    observer = ExecutionObserver()
    first = web(
        "a", {"type": "search", "queries": ["PRIVATE query", "PRIVATE query", "other"]}
    )
    feed(
        observer,
        {"type": "thread.started"},
        first,
        first,
        web("b", {"type": "search", "query": "PRIVATE query"}),
        web("c", {"type": "openPage", "url": "https://private.invalid/page?secret=1"}),
        web("d", {"type": "openPage", "url": "https://private.invalid/page?secret=1"}),
        web(
            "e",
            {
                "type": "findInPage",
                "url": "https://private.invalid/page?secret=1",
                "pattern": "PRIVATE",
            },
        ),
        {"type": "turn.completed"},
    )
    assert hasattr(observer, "statistics"), "repeat work is not currently observable"
    stats = observer.statistics()
    assert stats["distinctSearchQueries"] == 2
    assert stats["duplicateSearchQueries"] == 2
    assert stats["distinctPageTargets"] == 1
    assert stats["repeatedPageOpens"] == 1
    assert stats["completedToolActions"] == 5
    assert stats["sourceReferenceReuses"] is None
    assert stats["preDispatchEnforcement"] is False
    assert stats["providerRetries"] is None
    serialized = json.dumps(stats)
    assert "PRIVATE" not in serialized and "private.invalid" not in serialized
    assert observer.finish()["counts"]["searchQueries"] == 4
    assert executed_task_ids(
        observer.finish(), {"tasks": [{"taskId": "exact", "query": "PRIVATE query"}]}
    ) == {"exact"}


def test_unavailable_and_unattributed_event_keep_duplicate_observations_unknown():
    observer = ExecutionObserver()
    assert hasattr(observer, "statistics"), "unknown repeat counts must be explicit"
    assert observer.statistics()["duplicateSearchQueries"] is None
    assert observer.statistics()["repeatedPageOpens"] is None
    feed(
        observer,
        {"type": "thread.started"},
        web("a", {"type": "search", "query": "one"}),
        {"type": "future.privateEvent"},
    )
    stats = observer.statistics()
    assert stats["coverage"] == "PARTIAL"
    assert stats["duplicateSearchQueries"] is None
    assert stats["repeatedPageOpens"] is None


def test_missing_target_keeps_open_total_but_cannot_claim_distinct_pages():
    observer = ExecutionObserver()
    feed(
        observer,
        {"type": "thread.started"},
        web("a", {"type": "openPage"}),
        {"type": "turn.completed"},
    )
    assert hasattr(observer, "statistics"), (
        "URL identity is distinct from a page-open count"
    )
    assert observer.finish()["counts"]["pageOpens"] == 1
    assert observer.statistics()["distinctPageTargets"] is None
    assert observer.statistics()["repeatedPageOpens"] is None


def test_progress_records_bounded_stage_and_counts_without_arbitrary_content():
    observer = ExecutionObserver()
    assert hasattr(observer, "progress"), (
        "non-streaming RPC needs a safe status projection"
    )
    initial = observer.progress()
    assert initial["lastEventAt"] is None
    assert initial["completedToolActions"] is None
    feed(
        observer,
        {"type": "thread.started"},
        web("a", {"type": "search", "query": "PRIVATE"}, "item.started"),
    )
    waiting = observer.progress()
    assert waiting["phase"] == "WAITING_TOOL"
    assert waiting["activeToolActions"] == 1
    assert waiting["waitingExternalTools"] is True
    assert waiting["modelCompleted"] is False
    feed(observer, web("a", {"type": "search", "query": "PRIVATE"}))
    assert observer.progress()["activeToolActions"] == 0
    feed(
        observer,
        {
            "type": "item.started",
            "item": {
                "id": "message",
                "type": "agent_message",
                "text": "PRIVATE output",
            },
        },
    )
    assert observer.progress()["phase"] == "MODEL_MESSAGE"
    assert observer.progress()["modelCompleted"] is False
    feed(observer, {"type": "turn.completed"})
    final = observer.progress()
    assert final["phase"] == "TURN_COMPLETED" and final["modelCompleted"] is True
    assert final["completedToolActions"] == 1
    assert final["observedSearchQueries"] == 1
    assert final["observedPageOpens"] == 0
    assert final["observedPageFinds"] == 0
    assert final["elapsedMs"] >= initial["elapsedMs"]
    assert final["lastEventAt"] is not None
    assert "PRIVATE" not in json.dumps(final)
    assert "reportStarted" not in final


def test_safe_metadata_sanitizers_drop_untrusted_text_and_reject_inconsistent_state():
    from backend.integrations import research_execution as execution

    assert hasattr(execution, "sanitize_execution_statistics")
    assert hasattr(execution, "sanitize_execution_progress")
    observer = ExecutionObserver()
    feed(observer, {"type": "thread.started"}, {"type": "turn.completed"})
    stats = execution.sanitize_execution_statistics(
        {**observer.statistics(), "raw": "PRIVATE"}
    )
    progress = execution.sanitize_execution_progress(
        {**observer.progress(), "raw": "PRIVATE"}
    )
    assert stats == observer.statistics()
    assert progress["startedAt"] == observer.progress()["startedAt"]
    assert "PRIVATE" not in json.dumps(progress)
    assert (
        execution.sanitize_execution_progress({**progress, "phase": "PRIVATE"}) is None
    )
    assert (
        execution.sanitize_execution_progress({**progress, "lastEventAt": "PRIVATE"})
        is None
    )
    assert (
        execution.sanitize_execution_statistics({**stats, "completedToolActions": -1})
        is None
    )
    assert (
        execution.sanitize_execution_statistics(
            {**stats, "preDispatchEnforcement": True}
        )
        is None
    )


@pytest.mark.parametrize(
    "item",
    [
        {"type": []},
        {"type": "mcp_tool_call"},
        {"type": "mcp_tool_call", "id": "x" * 201},
    ],
)
def test_malformed_tool_identity_never_crashes_or_claims_zero_completed_actions(item):
    observer = ExecutionObserver()
    feed(observer, {"type": "thread.started"}, {"type": "item.completed", "item": item})
    assert observer.progress()["completedToolActions"] is None
    assert observer.statistics()["completedToolActions"] is None


def test_optional_progress_budget_does_not_change_existing_receipt_proof_authority():
    observer = ExecutionObserver()
    feed(
        observer,
        {"type": "thread.started"},
        *(
            {
                "type": "item.completed",
                "item": {"type": "mcp_tool_call", "id": f"mcp-{index}"},
            }
            for index in range(1024)
        ),
        web("audit", {"type": "search", "query": "exact"}),
        {"type": "turn.completed"},
    )
    receipt = observer.finish()
    assert receipt["coverage"] == "COMPLETE"
    assert executed_task_ids(
        receipt, {"tasks": [{"taskId": "audit", "query": "exact"}]}
    ) == {"audit"}
    assert observer.progress()["completedToolActions"] is None
