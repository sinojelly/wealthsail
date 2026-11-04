"""Offline exec JSONL protocol shapes, never a real provider transcript."""

from copy import deepcopy
import hashlib
import json

import pytest

from backend.integrations import research_execution as execution


def event(identity, action=None, kind="item.completed"):
    return {
        "type": kind,
        "item": {
            "type": "web_search",
            "id": identity,
            "action": action or {"type": "search", "query": identity},
        },
    }


def encoded(*events):
    return ("\n".join(json.dumps(row) for row in events) + "\n").encode()


def observe(*events):
    observer = execution.ExecutionObserver()
    observer.feed(
        encoded({"type": "thread.started"}, *events, {"type": "turn.completed"})
    )
    return observer.finish()


def manifest(*queries):
    return {
        "tasks": [
            {"taskId": f"task-{i}", "query": query} for i, query in enumerate(queries)
        ]
    }


def test_unknown_distinct_action_preserves_fifty_completed_exact_query_proofs():
    queries = [f"exact-{i}" for i in range(50)]
    receipt = observe(
        *(event(query) for query in queries), event("unknown", {"type": "futureRead"})
    )
    assert receipt["coverage"] == "PARTIAL"
    assert execution.executed_task_ids(receipt, manifest(*queries)) == {
        f"task-{i}" for i in range(50)
    }
    assert receipt["version"] == "research-execution-v2"
    assert receipt["proofBarrier"] is None
    assert "futureRead" not in json.dumps(receipt)


def test_conflicting_identity_revokes_all_its_queries_but_not_other_actions():
    receipt = observe(
        event("conflict", {"type": "search", "queries": ["first", "second"]}),
        event("good", {"type": "search", "query": "third"}),
        event("conflict", {"type": "search", "query": "replacement"}),
    )
    assert execution.executed_task_ids(
        receipt, manifest("first", "second", "third", "replacement")
    ) == {"task-2"}
    tombstone = next(
        row for row in receipt["actionProofs"] if row["status"] == "CONFLICT"
    )
    assert tombstone["queryHashes"] == []


def test_incomplete_and_malformed_search_only_withhold_their_own_identity():
    receipt = observe(
        event("good"),
        event("pending", kind="item.started"),
        event("malformed", {"type": "search", "queries": []}),
    )
    assert execution.executed_task_ids(receipt, manifest("good", "pending")) == {
        "task-0"
    }
    assert receipt["counts"]["searchQueries"] is None


@pytest.mark.parametrize(
    "damage",
    [b"bad-json\n", b'{"type":', b"x" * (256 * 1024 + 1) + b"\n"],
    ids=["malformed", "trailing", "oversized"],
)
def test_unattributed_damage_withholds_all_proofs(damage):
    observer = execution.ExecutionObserver()
    observer.feed(
        encoded({"type": "thread.started"}, event("good"), {"type": "turn.completed"})
        + damage
    )
    receipt = observer.finish()
    assert not execution.executed_task_ids(receipt, manifest("good"))
    assert receipt.get("proofBarrier") is not None


@pytest.mark.parametrize(
    "ending",
    [[], [{"type": "turn.failed"}], [{"type": "error"}, {"type": "turn.completed"}]],
)
def test_missing_or_failed_turn_never_authorizes_completed_search(ending):
    observer = execution.ExecutionObserver()
    observer.feed(encoded({"type": "thread.started"}, event("good"), *ending))
    assert not execution.executed_task_ids(observer.finish(), manifest("good"))


def test_v1_partial_is_never_reinterpreted_as_per_action_proof():
    receipt = observe(event("good"))
    legacy = {
        key: value
        for key, value in receipt.items()
        if key not in {"actionProofs", "proofBarrier"}
    }
    legacy["version"] = "research-execution-v1"
    assert execution.executed_task_ids(legacy, manifest("good")) == {"task-0"}
    legacy.update(coverage="PARTIAL", eventsComplete=False)
    assert execution.sanitize_execution_receipt(legacy) is not None
    assert not execution.executed_task_ids(legacy, manifest("good"))


@pytest.mark.parametrize(
    "change", ["orphan", "conflict", "duplicate", "unknown", "barrier"]
)
def test_v2_forged_or_contradictory_proof_metadata_does_not_authorize(change):
    receipt = observe(event("good"))
    if change == "orphan":
        receipt["actionProofs"] = []
    elif change == "conflict":
        receipt["actionProofs"][0]["status"] = "CONFLICT"
    elif change == "duplicate":
        receipt["actionProofs"] *= 2
    elif change == "unknown":
        receipt["actionProofs"][0]["status"] = "FAKE_SUCCESS"
    else:
        receipt["proofBarrier"] = "PRIVATE free text"
    assert execution.sanitize_execution_receipt(receipt) is None
    assert not execution.executed_task_ids(receipt, manifest("good"))


def test_summary_retains_observed_counters_but_strips_every_action_authorization():
    receipt = observe(event("good"))
    snapshot = deepcopy(receipt)
    summary = execution.summarize_execution_receipt(receipt)
    assert summary["version"] == "research-execution-v2"
    assert execution.sanitize_execution_receipt(summary) == summary
    assert summary["counts"] == receipt["counts"]
    assert summary["actionProofs"] == summary["queryReceipts"] == []
    assert not execution.executed_task_ids(summary, manifest("good"))
    assert receipt == snapshot


def test_partial_usable_proofs_report_matching_without_inventing_unobserved_queries():
    receipt = observe(event("good"), event("unknown", {"type": "futureRead"}))
    stats = execution.execution_match_stats(receipt, manifest("good", "missing"))
    assert stats["matchedTaskCount"] == 1
    assert stats["matchedQueryHashCount"] == 1
    assert stats["observedQueryCount"] is None
    assert stats["unmatchedQueryHashCount"] == 0


def test_identical_repeated_completion_is_idempotent_and_hash_matching_remains_exact():
    receipt = observe(event("good"), event("good"))
    assert execution.executed_task_ids(receipt, manifest("good", "good ")) == {"task-0"}
    assert len(receipt.get("actionProofs", [])) == 1
    assert (
        receipt["queryReceipts"][0]["queryHash"] == hashlib.sha256(b"good").hexdigest()
    )


@pytest.mark.parametrize("web_first", [False, True])
def test_identity_reused_by_non_web_item_cannot_authorize_in_either_order(web_first):
    web = event("shared", {"type": "search", "query": "bad"})
    non_web = {
        "type": "item.completed",
        "item": {"type": "agent_message", "id": "shared", "text": "PRIVATE"},
    }
    pair = [web, non_web] if web_first else [non_web, web]
    receipt = observe(*pair, event("good"))
    assert execution.executed_task_ids(receipt, manifest("bad", "good")) == {"task-1"}


def test_ambiguous_duplicate_json_identity_is_unattributed_damage():
    observer = execution.ExecutionObserver()
    observer.feed(encoded({"type": "thread.started"}, event("good")))
    observer.feed(
        b'{"type":"item.completed","item":{"type":"web_search","id":"good","id":"other","action":{"type":"search","query":"replacement"}}}\n'
    )
    observer.feed(encoded({"type": "turn.completed"}))
    assert not execution.executed_task_ids(observer.finish(), manifest("good"))
