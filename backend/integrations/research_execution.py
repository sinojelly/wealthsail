"""Bounded CLI event observations, never a pre-dispatch web budget.

The observer retains only counters and hashes. COMPLETE describes the JSONL
observation stream, not source quality or provider-internal network retries.
"""

from copy import deepcopy
import hashlib
import json
import re

VERSION = "research-execution-v1"
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
        self.receipts = []
        self.counts = dict.fromkeys(_COUNTS, 0)
        self.unknown_actions = False
        self.unknown_events = False

    def feed(self, chunk):
        if not isinstance(chunk, bytes):
            self.partial = True
            return
        for index, part in enumerate(chunk.split(b"\n")):
            if index:
                if not self.discard_line:
                    self._line(self.pending)
                self.pending, self.discard_line = b"", False
            if not self.discard_line:
                if len(self.pending) + len(part) > MAX_LINE_BYTES:
                    self.pending, self.discard_line, self.partial = b"", True, True
                    self.unknown_events = True
                else:
                    self.pending += part

    def _line(self, line):
        if not line.strip():
            return
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            self.partial = self.unknown_events = True
            return
        if not isinstance(value, dict):
            self.partial = self.unknown_events = True
            return
        kind = value.get("type")
        if not isinstance(kind, str):
            self.partial = self.unknown_events = True
            return
        if kind == "thread.started":
            self.recognized = self.thread_started = True
        elif kind == "turn.started":
            self.recognized = True
        elif kind == "turn.completed":
            self.recognized = self.turn_completed = True
        elif kind in {"turn.failed", "error"}:
            self.recognized = self.partial = True
        elif kind in {"item.started", "item.updated", "item.completed"}:
            self.recognized = True
            item = value.get("item")
            if not isinstance(item, dict):
                self.partial = self.unknown_events = True
                return
            if item.get("type") != "web_search":
                if (
                    not isinstance(item.get("type"), str)
                    or item["type"] not in _NON_WEB_ITEMS
                ):
                    self.partial = self.unknown_events = True
                return
            identity = item.get("id")
            if not isinstance(identity, str) or not 1 <= len(identity) <= 200:
                self.partial = True
                return
            identity = _hash(identity)
            if identity not in self.items:
                if len(self.items) >= MAX_ITEMS:
                    self.partial = self.unknown_events = True
                    return
                self.items[identity] = None
                self.counts["webToolCalls"] += 1
            if kind != "item.completed":
                return
            action = item.get("action")
            fingerprint = _hash(json.dumps(action, sort_keys=True))
            previous = self.items[identity]
            if previous is not None:
                self.partial |= previous != fingerprint
                return
            self.items[identity] = fingerprint
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
                    return
                self.counts["searchActions"] += 1
                self.counts["searchQueries"] += len(queries)
                try:
                    query_hashes = dict.fromkeys(_hash(row) for row in queries)
                except UnicodeError:
                    self.partial = self.unknown_actions = True
                    return
                for query_hash in query_hashes:
                    if len(self.receipts) >= MAX_RECEIPTS:
                        self.partial = True
                        break
                    self.receipts.append(
                        {"queryHash": query_hash, "completedItemId": identity}
                    )
            elif action_type == "openPage":
                self.counts["pageOpens"] += 1
            elif action_type == "findInPage":
                self.counts["pageFinds"] += 1
            else:
                self.counts["otherWebActions"] += 1
                self.partial = self.unknown_actions = True
        else:
            self.partial = self.unknown_events = True

    def finish(self):
        # A trailing unterminated event is not a fully observed JSONL record.
        incomplete = (
            self.partial
            or bool(self.pending.strip())
            or self.discard_line
            or any(value is None for value in self.items.values())
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
            value is None for value in self.items.values()
        ):
            counts.update(
                dict.fromkeys(
                    ("searchActions", "searchQueries", "pageOpens", "pageFinds")
                )
            )
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
            "queryReceipts": deepcopy(self.receipts),
            "softQueryLimit": SOFT_QUERY_LIMIT,
            "softQueryLimitExceeded": counts["searchQueries"] > SOFT_QUERY_LIMIT
            if counts["searchQueries"] is not None
            else None,
        }


def sanitize_execution_receipt(value):
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
        value["version"] != VERSION
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


def executed_task_ids(receipt, manifest):
    """Bind only exact original queries from a complete host observation."""
    receipt = sanitize_execution_receipt(receipt)
    if (
        receipt is None
        or receipt["coverage"] != "COMPLETE"
        or not receipt["turnCompleted"]
    ):
        return set()
    tasks = manifest.get("tasks") if isinstance(manifest, dict) else None
    if not isinstance(tasks, list) or len(tasks) > 50:
        return set()
    hashes = {row["queryHash"] for row in receipt["queryReceipts"]}
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
    if (
        receipt is None
        or receipt["coverage"] != "COMPLETE"
        or not receipt["turnCompleted"]
    ):
        return output
    observed_hashes = {row["queryHash"] for row in receipt["queryReceipts"]}
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
