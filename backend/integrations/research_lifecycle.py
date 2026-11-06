"""Closed execution evidence for one authenticated, non-retrying research call.

The registry is intentionally ephemeral: a restarted runner knows nothing about
old children and must return UNKNOWN rather than claiming that they exited.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import math
import time
from uuid import UUID

from backend.integrations.research_execution import sanitize_execution_progress

VERSION = "research-lifecycle-v1"
CLEANUP_SECONDS = 15
RUNNER_RESPONSE_SECONDS = 5
RPC_MARGIN_SECONDS = 15
WORKER_MARGIN_SECONDS = 5
RECOVERY_SECONDS = 20
TIMEOUT_ORIGINS = frozenset(
    {
        "CLI_MODEL_TIMEOUT",
        "RUNNER_OPERATION_TIMEOUT",
        "WORKER_RPC_TIMEOUT",
        "WORKER_OUTER_TIMEOUT",
        "RUNNER_DISCONNECTED",
        "MODEL_EXIT_UNCONFIRMED",
    }
)
_STATES = {"QUEUED", "RUNNING", "TERMINATING", "EXITED", "UNKNOWN"}
_REASONS = TIMEOUT_ORIGINS | {
    "COMPLETED",
    "CLI_FAILED",
    "CANCELLED",
    "CLIENT_DISCONNECTED",
}


def research_deadlines(cli_seconds):
    if (
        isinstance(cli_seconds, bool)
        or not math.isfinite(cli_seconds)
        or not 30 <= cli_seconds <= 1200
    ):
        raise ValueError("invalid research model budget")
    runner = cli_seconds + CLEANUP_SECONDS + RUNNER_RESPONSE_SECONDS
    rpc = runner + RPC_MARGIN_SECONDS
    return {
        "cliSeconds": cli_seconds,
        "cleanupSeconds": CLEANUP_SECONDS,
        "runnerSeconds": runner,
        "rpcSeconds": rpc,
        "workerSeconds": rpc + RECOVERY_SECONDS + WORKER_MARGIN_SECONDS,
    }


def call_identity(value):
    if not isinstance(value, dict) or set(value) != {"runId", "ownerToken", "callId"}:
        raise ValueError("invalid research call identity")
    try:
        return {
            key: str(UUID(str(value[key]))) for key in ("runId", "ownerToken", "callId")
        }
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid research call identity") from exc


def _binding(identity):
    identity = call_identity(identity)
    return hashlib.sha256(
        (
            identity["runId"] + ":" + identity["ownerToken"] + ":" + identity["callId"]
        ).encode()
    ).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def sanitize_lifecycle(value, identity):
    """Accept only evidence bound to this exact owner, run and invocation."""
    if not isinstance(value, dict):
        return None
    try:
        identity = call_identity(identity)
    except ValueError:
        return None
    binding = value.get("identityBinding")
    if (
        value.get("version") != VERSION
        or value.get("runId") != identity["runId"]
        or value.get("callId") != identity["callId"]
        or not isinstance(binding, str)
        or not hmac.compare_digest(binding, _binding(identity))
        or not isinstance(value.get("state"), str)
        or value["state"] not in _STATES
    ):
        return None
    safe = {
        key: value[key]
        for key in ("version", "runId", "callId", "identityBinding", "state")
    }
    for name in (
        "terminationRequested",
        "reaped",
        "descendantsConfirmed",
        "exitConfirmed",
    ):
        safe[name] = value.get(name) is True
    safe["exitConfirmed"] = (
        safe["exitConfirmed"]
        and safe["reaped"]
        and safe["descendantsConfirmed"]
        and safe["state"] == "EXITED"
    )
    safe["terminationReason"] = (
        value.get("terminationReason")
        if isinstance(value.get("terminationReason"), str)
        and value["terminationReason"] in _REASONS
        else None
    )
    for name in ("queuedAt", "startedAt", "completedAt"):
        item = value.get(name)
        if isinstance(item, str) and len(item) <= 40:
            try:
                datetime.fromisoformat(item)
            except ValueError:
                continue
            safe[name] = item
    elapsed = value.get("elapsedMs")
    if type(elapsed) is int and 0 <= elapsed <= 2_000_000_000:
        safe["elapsedMs"] = elapsed
    progress = sanitize_execution_progress(value.get("progress"))
    if progress is not None:
        safe["progress"] = progress
    return safe


class ResearchLifecycle:
    def __init__(self, identity):
        self.identity = call_identity(identity)
        self.task = None
        self.created = time.monotonic()
        self.finished = None
        self.data = {
            "version": VERSION,
            "runId": self.identity["runId"],
            "callId": self.identity["callId"],
            "identityBinding": _binding(self.identity),
            "state": "QUEUED",
            "queuedAt": _now(),
            "terminationRequested": False,
            "reaped": False,
            "descendantsConfirmed": False,
            "exitConfirmed": False,
            "terminationReason": None,
        }

    def started(self):
        self.data.update(state="RUNNING", startedAt=_now())

    def terminating(self, reason):
        self.data.update(
            state="TERMINATING",
            terminationRequested=True,
            terminationReason=reason if reason in _REASONS else "CANCELLED",
        )

    def exited(self, *, reaped, descendants_confirmed, returncode):
        confirmed = reaped is True and descendants_confirmed is True
        self.finished = time.monotonic()
        self.data.update(
            state="EXITED" if confirmed else "UNKNOWN",
            reaped=reaped is True,
            descendantsConfirmed=descendants_confirmed is True,
            exitConfirmed=confirmed,
            completedAt=_now(),
        )
        if self.data["terminationReason"] is None:
            self.data["terminationReason"] = (
                "COMPLETED" if returncode == 0 else "CLI_FAILED"
            )

    def snapshot(self):
        return {
            **self.data,
            "elapsedMs": round(
                ((self.finished or time.monotonic()) - self.created) * 1000
            ),
        }

    def progress(self, value):
        safe = sanitize_execution_progress(value)
        if safe is not None:
            self.data["progress"] = safe


class ResearchRegistry:
    def __init__(self, *, limit=256, retention_seconds=3600, history_limit=4096):
        self.entries = {}
        self.used_owners = set()
        self.limit, self.retention_seconds = limit, retention_seconds
        self.history_limit = history_limit

    def create(self, identity):
        identity = call_identity(identity)
        key = _binding(identity)
        now = time.monotonic()
        self.entries = {
            key: state
            for key, state in self.entries.items()
            if state.finished is None or now - state.finished < self.retention_seconds
        }
        owner = hashlib.sha256(
            (identity["runId"] + ":" + identity["ownerToken"]).encode()
        ).hexdigest()
        # Expiring diagnostic detail never reopens dispatch. Tombstones remain
        # bounded; a full registry rejects admission until a controlled restart.
        # Across restart, the Worker's immutable DB cost barrier is authoritative.
        if (
            key in self.entries
            or owner in self.used_owners
            or len(self.entries) >= self.limit
            or len(self.used_owners) >= self.history_limit
        ):
            raise ValueError(
                "research execution identity already used or registry full"
            )
        state = ResearchLifecycle(identity)
        self.entries[key] = state
        self.used_owners.add(owner)
        return state

    def lookup(self, identity):
        return self.entries.get(_binding(identity))
