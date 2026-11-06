"""Closed cost observations, with unknown work kept distinct from zero."""

from datetime import datetime

from backend.integrations.research_execution import (
    sanitize_execution_progress,
    sanitize_execution_receipt,
    sanitize_execution_statistics,
    sanitize_execution_transport_reason,
)

VERSION = "research-workload-v2"
LEGACY_VERSION = "research-workload-v1"
COUNTS = {
    "observedWebToolCalls": "webToolCalls",
    "observedSearchQueries": "searchQueries",
    "observedPageOpens": "pageOpens",
    "observedPageFinds": "pageFinds",
}
_EXECUTION_STATES = {"QUEUED", "RUNNING", "TERMINATING", "EXITED", "UNKNOWN"}
_EXECUTION_REASONS = {
    "CLI_MODEL_TIMEOUT",
    "RUNNER_OPERATION_TIMEOUT",
    "WORKER_RPC_TIMEOUT",
    "WORKER_OUTER_TIMEOUT",
    "RUNNER_DISCONNECTED",
    "MODEL_EXIT_UNCONFIRMED",
    "COMPLETED",
    "CLI_FAILED",
    "CANCELLED",
    "CLIENT_DISCONNECTED",
}


def safe_model_execution(value, *, dispatch_started=None):
    """Display already identity-verified lifecycle metadata, never exit authority.

    The worker supplies only its strictly bound lifecycle result. This closed
    user projection excludes all invocation credentials and cannot release a
    model guard. Missing fields stay unknown, including a never-dispatched call.
    """
    value = value if isinstance(value, dict) else {}
    state, reason = value.get("state"), value.get("terminationReason")
    dispatch = (
        dispatch_started
        if dispatch_started is not None
        else value.get("dispatchStarted")
    )
    result = {
        "state": state
        if isinstance(state, str) and state in _EXECUTION_STATES
        else "UNKNOWN",
        "dispatchStarted": dispatch if type(dispatch) is bool else None,
        "terminationReason": reason
        if isinstance(reason, str) and reason in _EXECUTION_REASONS
        else None,
    }
    for key in (
        "terminationRequested",
        "reaped",
        "descendantsConfirmed",
        "exitConfirmed",
    ):
        result[key] = value.get(key) if type(value.get(key)) is bool else None
    if result["dispatchStarted"] is False:
        result.update(state="UNKNOWN", reaped=None, descendantsConfirmed=None)
    if result["exitConfirmed"] is True:
        result["exitConfirmed"] = (
            result["state"] == "EXITED"
            and result["reaped"] is True
            and result["descendantsConfirmed"] is True
            and result["dispatchStarted"] is not False
        )
    for key in ("queuedAt", "startedAt", "completedAt"):
        result[key] = None
        stamp = value.get(key)
        if isinstance(stamp, str) and len(stamp) <= 64:
            try:
                parsed = datetime.fromisoformat(stamp)
                if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                    result[key] = parsed.isoformat()
            except (ValueError, OverflowError):
                pass
    elapsed = value.get("elapsedMs")
    result["elapsedMs"] = (
        elapsed if type(elapsed) is int and 0 <= elapsed <= 2_000_000_000 else None
    )
    return result


def current_opportunity_industries(packet, now):
    """Prioritize visible positive news, without granting investment eligibility."""
    from zoneinfo import ZoneInfo

    from backend.investment.opportunity_scoring import (
        _NEGATIVE,
        _POSITIVE,
        _source,
        _stamp,
    )

    public = packet.get("alphaPublic") or {}
    positive_ids = set()
    for row in public.get("catalysts", [])[:300]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        published, retrieved = (
            _stamp(row.get("publishedAt")),
            _stamp(row.get("retrievedAt")),
        )
        available = (
            _stamp(row.get("availableAt")) if "availableAt" in row else retrieved
        )
        if (
            published is None
            or retrieved is None
            or available is None
            or published > now
            or retrieved > now
            or available > now
            or published.astimezone(ZoneInfo("Asia/Shanghai")).date()
            != now.astimezone(ZoneInfo("Asia/Shanghai")).date()
            or not _source(row.get("sourceUri"))
        ):
            continue
        text = " ".join(
            str(row.get(key) or "") for key in ("title", "content", "summary")
        )
        if _POSITIVE.search(text) and not _NEGATIVE.search(text):
            positive_ids.add(row["id"])
    buckets = public.get("sectorCatalysts") or {}
    return {
        industry
        for industry, refs in buckets.items()
        if isinstance(industry, str)
        and industry != "OTHER"
        and isinstance(refs, list)
        and any(isinstance(ref, str) and ref in positive_ids for ref in refs)
    }


def safe_workload(value):
    if not isinstance(value, dict) or value.get("version") not in (
        VERSION,
        LEGACY_VERSION,
    ):
        return None
    result = {
        "version": value["version"],
        "preDispatchEnforcement": False,
        "providerRetries": None,
        "executionCoverage": value.get("executionCoverage")
        if isinstance(value.get("executionCoverage"), str)
        and value["executionCoverage"] in {"COMPLETE", "PARTIAL", "UNAVAILABLE"}
        else "UNAVAILABLE",
    }
    for key in (
        "promptBytes",
        "modelElapsedMs",
        "sourceVerificationElapsedMs",
        "inputTokens",
        "cachedInputTokens",
        "outputTokens",
    ):
        number = value.get(key)
        if type(number) is int and 0 <= number <= 1_000_000_000_000:
            result[key] = number
    for key in COUNTS:
        number = value.get(key)
        result[key] = (
            number if type(number) is int and 0 <= number <= 1_000_000 else None
        )
    result["searchSoftLimitExceeded"] = (
        result["observedSearchQueries"] > 50
        if result["observedSearchQueries"] is not None
        else None
    )
    if (
        "cachedInputTokens" in result
        and "inputTokens" in result
        and result["cachedInputTokens"] > result["inputTokens"]
    ):
        result.pop("cachedInputTokens", None)
    if (
        value["version"] == VERSION
        and value.get("modelElapsedScope") == "MODEL_OPERATION"
    ):
        result["modelElapsedScope"] = "MODEL_OPERATION"
    if value["version"] == VERSION:
        result["modelExecution"] = safe_model_execution(value.get("modelExecution"))
    statistics = sanitize_execution_statistics(value.get("executionStatistics"))
    if statistics is not None:
        result["executionStatistics"] = statistics
    progress = sanitize_execution_progress(value.get("executionProgress"))
    if progress is not None:
        result["executionProgress"] = progress
    reason = sanitize_execution_transport_reason(
        value.get("executionReceiptTransportReason")
    )
    if reason is not None:
        result["executionReceiptTransportReason"] = reason
    return result


def measured_workload(
    prompt_bytes,
    receipt,
    usage,
    *,
    model_ms=None,
    sources_ms=None,
    transport_reason=None,
    statistics=None,
    progress=None,
    model_timing_scope="MODEL_OPERATION",
    lifecycle=None,
    dispatch_started=None,
):
    receipt = sanitize_execution_receipt(receipt)
    usage = usage if isinstance(usage, dict) else {}
    counts = receipt["counts"] if receipt else {}
    return safe_workload(
        {
            "version": VERSION,
            "promptBytes": prompt_bytes,
            "modelElapsedMs": model_ms,
            "modelElapsedScope": model_timing_scope,
            "sourceVerificationElapsedMs": sources_ms,
            "executionCoverage": receipt["coverage"] if receipt else "UNAVAILABLE",
            "executionReceiptTransportReason": transport_reason,
            "executionStatistics": statistics,
            "executionProgress": progress,
            "modelExecution": safe_model_execution(
                lifecycle, dispatch_started=dispatch_started
            ),
            **{key: counts.get(source) for key, source in COUNTS.items()},
            **{
                key: usage.get(source)
                for key, source in (
                    ("inputTokens", "prompt_tokens"),
                    ("cachedInputTokens", "cached_input_tokens"),
                    ("outputTokens", "completion_tokens"),
                )
            },
        }
    )
