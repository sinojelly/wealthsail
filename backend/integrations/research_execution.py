"""Bounded CLI event observations, never a pre-dispatch web budget.

The observer retains only counters and hashes. COMPLETE describes the JSONL
observation stream, not source quality or provider-internal network retries.
"""

from copy import deepcopy
import hashlib
import json
import re

VERSION = "research-execution-v2"
LEGACY_VERSION = "research-execution-v1"
MAX_RECEIPTS = 256
MAX_ITEMS = 1024
MAX_LINE_BYTES = 256 * 1024
SOFT_QUERY_LIMIT = 50
EXECUTION_TRANSPORT_HEADER = "X-WealthSail-Execution-Receipt-Reason"
EXECUTION_TRANSPORT_REASONS = frozenset(
    {
        "RECEIPT_SUMMARIZED_SIZE_LIMIT",
        "RECEIPT_OMITTED_SIZE_LIMIT",
    }
)
_HASH = re.compile(r"[a-f0-9]{64}")
_COUNTS = (
    "webToolCalls",
    "searchActions",
    "searchQueries",
    "pageOpens",
    "pageFinds",
    "otherWebActions",
)
_CAPABILITIES = {"preDispatchEnforcement": False, "providerRetriesObservable": False}
_PROOF_STATES = frozenset(
    {
        "COMPLETED_SEARCH",
        "COMPLETED_OPEN",
        "COMPLETED_FIND",
        "UNKNOWN_ACTION",
        "INCOMPLETE",
        "CONFLICT",
        "RECEIPT_LIMIT",
    }
)
_PROOF_BARRIERS = frozenset(
    {
        "UNATTRIBUTED_EVENT",
        "STREAM_TRUNCATED",
        "ITEM_LIMIT_EXCEEDED",
        "TURN_NOT_COMPLETED",
        "THREAD_NOT_STARTED",
        "TURN_FAILED",
        "RECEIPT_SUMMARIZED",
    }
)
_NON_WEB_ITEMS = {
    "agent_message",
    "reasoning",
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "collab_tool_call",
    "todo_list",
}


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous event object")
        result[key] = value
    return result


class ExecutionObserver:
    """Consume actual stdout chunks without storing queries, pages or messages."""

    def __init__(self):
        self.pending = b""
        self.discard_line = False
        self.partial = False
        self.recognized = False
        self.thread_started = False
        self.turn_completed = False
        self.items = {}
        self.non_web_ids = set()
        self.proof_barrier = None
        self.counts = dict.fromkeys(_COUNTS, 0)
        self.unknown_actions = False
        self.unknown_events = False

    def _damage(self, reason="UNATTRIBUTED_EVENT"):
        self.partial = self.unknown_events = True
        self.proof_barrier = self.proof_barrier or reason

    def feed(self, chunk):
        if not isinstance(chunk, bytes):
            self._damage()
            return
        for index, part in enumerate(chunk.split(b"\n")):
            if index:
                if not self.discard_line:
                    self._line(self.pending)
                self.pending, self.discard_line = b"", False
            if not self.discard_line:
                if len(self.pending) + len(part) > MAX_LINE_BYTES:
                    self.pending, self.discard_line, self.partial = b"", True, True
                    self._damage("STREAM_TRUNCATED")
                else:
                    self.pending += part

    def _line(self, line):
        if not line.strip():
            return
        try:
            value = json.loads(line, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            self._damage()
            return
        if not isinstance(value, dict):
            self._damage()
            return
        kind = value.get("type")
        if not isinstance(kind, str):
            self._damage()
            return
        if kind == "thread.started":
            self.recognized = self.thread_started = True
        elif kind == "turn.started":
            self.recognized = True
        elif kind == "turn.completed":
            self.recognized = self.turn_completed = True
        elif kind in {"turn.failed", "error"}:
            self.recognized = self.partial = True
            self.proof_barrier = self.proof_barrier or "TURN_FAILED"
        elif kind in {"item.started", "item.updated", "item.completed"}:
            self.recognized = True
            item = value.get("item")
            if not isinstance(item, dict):
                self._damage()
                return
            if item.get("type") != "web_search":
                if (
                    not isinstance(item.get("type"), str)
                    or item["type"] not in _NON_WEB_ITEMS
                ):
                    self._damage()
                elif isinstance(item.get("id"), str):
                    try:
                        non_web_id = _hash(item["id"])
                        previous = self.items.get(non_web_id)
                    except UnicodeError:
                        self._damage()
                        return
                    if non_web_id not in self.non_web_ids:
                        if len(self.non_web_ids) >= MAX_ITEMS:
                            self._damage("ITEM_LIMIT_EXCEEDED")
                            return
                        self.non_web_ids.add(non_web_id)
                    if previous is not None:
                        previous.update(status="CONFLICT", queryHashes=[])
                        self.partial = True
                return
            identity = item.get("id")
            if not isinstance(identity, str) or not 1 <= len(identity) <= 200:
                self._damage()
                return
            try:
                identity = _hash(identity)
            except UnicodeError:
                self._damage()
                return
            if identity not in self.items:
                if len(self.items) >= MAX_ITEMS:
                    self._damage("ITEM_LIMIT_EXCEEDED")
                    return
                self.items[identity] = {
                    "fingerprint": None,
                    "status": "INCOMPLETE",
                    "queryHashes": [],
                }
                self.counts["webToolCalls"] += 1
            if identity in self.non_web_ids:
                self.items[identity].update(status="CONFLICT", queryHashes=[])
                self.partial = True
                return
            if kind != "item.completed":
                return
            action = item.get("action")
            fingerprint = _hash(json.dumps(action, sort_keys=True))
            previous = self.items[identity]
            if previous["fingerprint"] is not None:
                if previous["fingerprint"] != fingerprint:
                    previous.update(status="CONFLICT", queryHashes=[])
                    self.partial = True
                return
            previous["fingerprint"] = fingerprint
            if previous["status"] == "CONFLICT":
                return
            action_type = action.get("type") if isinstance(action, dict) else None
            if action_type == "search":
                query, queries = action.get("query"), action.get("queries")
                if queries is None and isinstance(query, str):
                    queries = [query]
                if (
                    not isinstance(queries, list)
                    or not queries
                    or len(queries) > 256
                    or any(
                        not isinstance(row, str) or not row or len(row) > 8192
                        for row in queries
                    )
                    or (
                        query is not None
                        and (not isinstance(query, str) or query not in queries)
                    )
                ):
                    self.partial = self.unknown_actions = True
                    previous["status"] = "UNKNOWN_ACTION"
                    return
                self.counts["searchActions"] += 1
                self.counts["searchQueries"] += len(queries)
                try:
                    query_hashes = dict.fromkeys(_hash(row) for row in queries)
                except UnicodeError:
                    self.partial = self.unknown_actions = True
                    previous["status"] = "UNKNOWN_ACTION"
                    return
                retained = sum(len(row["queryHashes"]) for row in self.items.values())
                if retained + len(query_hashes) > MAX_RECEIPTS:
                    previous["status"] = "RECEIPT_LIMIT"
                    self.partial = True
                else:
                    previous.update(
                        status="COMPLETED_SEARCH", queryHashes=list(query_hashes)
                    )
            elif action_type == "openPage":
                self.counts["pageOpens"] += 1
                previous["status"] = "COMPLETED_OPEN"
            elif action_type == "findInPage":
                self.counts["pageFinds"] += 1
                previous["status"] = "COMPLETED_FIND"
            else:
                self.counts["otherWebActions"] += 1
                self.partial = self.unknown_actions = True
                previous["status"] = "UNKNOWN_ACTION"
        else:
            self._damage()

    def finish(self):
        # A trailing unterminated event is not a fully observed JSONL record.
        incomplete = (
            self.partial
            or bool(self.pending.strip())
            or self.discard_line
            or any(value["status"] == "INCOMPLETE" for value in self.items.values())
        )
        complete = (
            self.recognized
            and self.thread_started
            and self.turn_completed
            and not incomplete
        )
        counts = {**self.counts, "providerRetries": None, "clientRetries": 0}
        if not self.recognized or self.unknown_events:
            counts.update(dict.fromkeys(_COUNTS))
        elif self.unknown_actions or any(
            value["status"] in {"INCOMPLETE", "CONFLICT"}
            for value in self.items.values()
        ):
            counts.update(
                dict.fromkeys(
                    ("searchActions", "searchQueries", "pageOpens", "pageFinds")
                )
            )
        barrier = self.proof_barrier
        if self.pending.strip() or self.discard_line:
            barrier = barrier or "STREAM_TRUNCATED"
        if not self.thread_started:
            barrier = barrier or "THREAD_NOT_STARTED"
        if not self.turn_completed:
            barrier = barrier or "TURN_NOT_COMPLETED"
        proofs = [
            {
                "completedItemId": identity,
                "status": row["status"],
                "queryHashes": list(row["queryHashes"]),
            }
            for identity, row in self.items.items()
        ]
        receipts = [
            {"completedItemId": row["completedItemId"], "queryHash": query_hash}
            for row in proofs
            if row["status"] == "COMPLETED_SEARCH"
            for query_hash in row["queryHashes"]
        ]
        return {
            "version": VERSION,
            "observer": "CODEX_EXEC_JSONL",
            "coverage": "COMPLETE"
            if complete
            else "PARTIAL"
            if self.recognized or incomplete
            else "UNAVAILABLE",
            "turnCompleted": self.turn_completed,
            "eventsComplete": complete,
            "capabilities": deepcopy(_CAPABILITIES),
            "counts": counts,
            "queryReceipts": receipts,
            "actionProofs": proofs,
            "proofBarrier": barrier,
            "softQueryLimit": SOFT_QUERY_LIMIT,
            "softQueryLimitExceeded": counts["searchQueries"] > SOFT_QUERY_LIMIT
            if counts["searchQueries"] is not None
            else None,
        }


def _sanitize_v1(value):
    """Validate internal metadata strictly; no arbitrary text crosses transport."""
    keys = {
        "version",
        "observer",
        "coverage",
        "turnCompleted",
        "eventsComplete",
        "capabilities",
        "counts",
        "queryReceipts",
        "softQueryLimit",
        "softQueryLimitExceeded",
    }
    if not isinstance(value, dict) or set(value) != keys:
        return None
    if (
        value["version"] != LEGACY_VERSION
        or value["observer"] != "CODEX_EXEC_JSONL"
        or not isinstance(value["coverage"], str)
        or value["coverage"] not in {"COMPLETE", "PARTIAL", "UNAVAILABLE"}
    ):
        return None
    if (
        type(value["turnCompleted"]) is not bool
        or type(value["eventsComplete"]) is not bool
    ):
        return None
    if value["capabilities"] != _CAPABILITIES or any(
        type(row) is not bool for row in value["capabilities"].values()
    ):
        return None
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) != {
        *_COUNTS,
        "providerRetries",
        "clientRetries",
    }:
        return None
    if (
        counts["providerRetries"] is not None
        or type(counts["clientRetries"]) is not int
        or counts["clientRetries"] != 0
    ):
        return None
    if any(
        row is not None and (type(row) is not int or not 0 <= row <= 1_000_000)
        for row in (counts[key] for key in _COUNTS)
    ):
        return None
    rows = value["queryReceipts"]
    if (
        not isinstance(rows, list)
        or len(rows) > MAX_RECEIPTS
        or any(
            not isinstance(row, dict)
            or set(row) != {"queryHash", "completedItemId"}
            or any(
                not isinstance(part, str) or not _HASH.fullmatch(part)
                for part in row.values()
            )
            for row in rows
        )
    ):
        return None
    if len({(row["queryHash"], row["completedItemId"]) for row in rows}) != len(rows):
        return None
    if (
        type(value["softQueryLimit"]) is not int
        or value["softQueryLimit"] != SOFT_QUERY_LIMIT
    ):
        return None
    exceeded = (
        counts["searchQueries"] > SOFT_QUERY_LIMIT
        if counts["searchQueries"] is not None
        else None
    )
    if value["softQueryLimitExceeded"] is not exceeded:
        return None
    if value["coverage"] == "COMPLETE" and (
        not value["turnCompleted"]
        or not value["eventsComplete"]
        or any(counts[key] is None for key in _COUNTS)
        or len(rows) > counts["searchQueries"]
    ):
        return None
    item_count = len({row["completedItemId"] for row in rows})
    if any(
        counts[key] is not None and counts[key] < size
        for key, size in (
            ("webToolCalls", item_count),
            ("searchActions", item_count),
            ("searchQueries", len(rows)),
        )
    ):
        return None
    if value["eventsComplete"] is not (value["coverage"] == "COMPLETE"):
        return None
    if value["coverage"] == "COMPLETE" and (
        counts["otherWebActions"] != 0
        or counts["webToolCalls"]
        != sum(
            counts[key]
            for key in ("searchActions", "pageOpens", "pageFinds", "otherWebActions")
        )
        or counts["searchActions"] != item_count
        or not counts["searchActions"]
        <= counts["searchQueries"]
        <= counts["searchActions"] * 256
    ):
        return None
    return deepcopy(value)


def sanitize_execution_receipt(value):
    """Read v1 unchanged; v2 proves each action independently of coverage."""
    if not isinstance(value, dict):
        return None
    if value.get("version") == LEGACY_VERSION:
        return _sanitize_v1(value)
    if (
        value.get("version") != VERSION
        or not {"actionProofs", "proofBarrier"} <= value.keys()
    ):
        return None
    base = {
        key: row
        for key, row in value.items()
        if key not in {"actionProofs", "proofBarrier"}
    }
    base["version"] = LEGACY_VERSION
    if _sanitize_v1(base) is None:
        return None
    barrier = value["proofBarrier"]
    if barrier is not None and (
        not isinstance(barrier, str) or barrier not in _PROOF_BARRIERS
    ):
        return None
    proofs = value["actionProofs"]
    if not isinstance(proofs, list) or len(proofs) > MAX_ITEMS:
        return None
    identities, projected = set(), []
    for proof in proofs:
        if not isinstance(proof, dict) or set(proof) != {
            "completedItemId",
            "status",
            "queryHashes",
        }:
            return None
        identity, status, hashes = (
            proof["completedItemId"],
            proof["status"],
            proof["queryHashes"],
        )
        if (
            not isinstance(identity, str)
            or not _HASH.fullmatch(identity)
            or identity in identities
            or not isinstance(status, str)
            or status not in _PROOF_STATES
            or not isinstance(hashes, list)
            or len(hashes) > MAX_RECEIPTS
            or any(
                not isinstance(row, str) or not _HASH.fullmatch(row) for row in hashes
            )
            or len(set(hashes)) != len(hashes)
            or (bool(hashes) != (status == "COMPLETED_SEARCH"))
        ):
            return None
        identities.add(identity)
        projected.extend(
            {"completedItemId": identity, "queryHash": query_hash}
            for query_hash in hashes
        )
    if projected != value["queryReceipts"]:
        return None
    counts = value["counts"]
    if counts["webToolCalls"] is not None and counts["webToolCalls"] < len(proofs):
        return None
    if barrier is None and (
        not value["turnCompleted"]
        or value["coverage"] == "UNAVAILABLE"
        or counts["webToolCalls"] != len(proofs)
    ):
        return None
    if value["coverage"] == "COMPLETE" and (
        barrier is not None
        or any(
            row["status"]
            not in {"COMPLETED_SEARCH", "COMPLETED_OPEN", "COMPLETED_FIND"}
            for row in proofs
        )
    ):
        return None
    if value["coverage"] == "UNAVAILABLE" and proofs:
        return None
    return deepcopy(value)


def summarize_execution_receipt(value):
    """Keep bounded observed counters, explicitly withholding every proof."""
    receipt = sanitize_execution_receipt(value)
    if receipt is None:
        return None
    receipt.update(coverage="PARTIAL", eventsComplete=False, queryReceipts=[])
    if receipt["version"] == VERSION:
        receipt.update(actionProofs=[], proofBarrier="RECEIPT_SUMMARIZED")
    return sanitize_execution_receipt(receipt)


def _usable_query_hashes(receipt):
    if receipt is None or not receipt["turnCompleted"]:
        return None
    if receipt["version"] == LEGACY_VERSION:
        if receipt["coverage"] != "COMPLETE":
            return None
    elif receipt["proofBarrier"] is not None:
        return None
    return {row["queryHash"] for row in receipt["queryReceipts"]}


def executed_task_ids(receipt, manifest):
    """Bind exact queries from usable host proofs, never a model assertion."""
    receipt = sanitize_execution_receipt(receipt)
    hashes = _usable_query_hashes(receipt)
    if hashes is None:
        return set()
    tasks = manifest.get("tasks") if isinstance(manifest, dict) else None
    if not isinstance(tasks, list) or len(tasks) > 50:
        return set()
    return {
        task["taskId"]
        for task in tasks
        if isinstance(task, dict)
        and isinstance(task.get("taskId"), str)
        and isinstance(task.get("query"), str)
        and _hash(task["query"]) in hashes
    }


def sanitize_execution_transport_reason(value):
    """Only fixed transport diagnostics may cross the internal boundary."""
    return (
        value
        if isinstance(value, str) and value in EXECUTION_TRANSPORT_REASONS
        else None
    )


def execution_match_stats(receipt, manifest):
    """Describe exact-query association; this never grants an audit permission.

    Hash counts are unique query hashes, while observedQueryCount retains the
    actual query counter. Unmatched searches can include general macro research.
    """
    receipt = sanitize_execution_receipt(receipt)
    output = {
        "version": "research-execution-match-v1",
        "classification": "EXECUTION_UNVERIFIED",
        "plannedTaskCount": None,
        "matchedTaskCount": 0,
        "observedQueryCount": receipt["counts"]["searchQueries"] if receipt else None,
        "matchedQueryHashCount": None,
        "unmatchedQueryHashCount": None,
    }
    tasks = manifest.get("tasks") if isinstance(manifest, dict) else None
    if (
        not isinstance(tasks, list)
        or len(tasks) > 50
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("taskId"), str)
            or not isinstance(row.get("query"), str)
            for row in tasks
        )
        or len({row["taskId"] for row in tasks}) != len(tasks)
    ):
        return output
    try:
        planned_hashes = {_hash(row["query"]) for row in tasks}
    except UnicodeError:
        return output
    output["plannedTaskCount"] = len(tasks)
    observed_hashes = _usable_query_hashes(receipt)
    if observed_hashes is None or (
        not observed_hashes and receipt["coverage"] != "COMPLETE"
    ):
        return output
    unmatched = observed_hashes - planned_hashes
    output.update(
        classification="EXECUTION_UNMATCHED"
        if unmatched
        else "EXECUTION_MATCHED"
        if observed_hashes
        else "EXECUTION_NOT_OBSERVED",
        matchedTaskCount=len(executed_task_ids(receipt, manifest)),
        matchedQueryHashCount=len(observed_hashes & planned_hashes),
        unmatchedQueryHashCount=len(unmatched),
    )
    return output
