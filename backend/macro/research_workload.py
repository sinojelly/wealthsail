"""Closed cost observations, with unknown work kept distinct from zero."""

from backend.integrations.research_execution import sanitize_execution_receipt, sanitize_execution_transport_reason

VERSION = "research-workload-v1"
COUNTS = {
    "observedWebToolCalls": "webToolCalls",
    "observedSearchQueries": "searchQueries",
    "observedPageOpens": "pageOpens",
    "observedPageFinds": "pageFinds",
}


def safe_workload(value):
    if not isinstance(value, dict) or value.get("version") != VERSION:
        return None
    result = {
        "version": VERSION,
        "preDispatchEnforcement": False,
        "providerRetries": None,
        "executionCoverage": value.get("executionCoverage")
        if isinstance(value.get("executionCoverage"), str)
        and value["executionCoverage"] in {"COMPLETE", "PARTIAL", "UNAVAILABLE"}
        else "UNAVAILABLE",
    }
    for key in ("promptBytes", "modelElapsedMs", "sourceVerificationElapsedMs", "inputTokens", "cachedInputTokens", "outputTokens"):
        number = value.get(key)
        if type(number) is int and 0 <= number <= 1_000_000_000_000:
            result[key] = number
    for key in COUNTS:
        number = value.get(key)
        result[key] = number if type(number) is int and 0 <= number <= 1_000_000 else None
    result["searchSoftLimitExceeded"] = (
        result["observedSearchQueries"] > 50
        if result["observedSearchQueries"] is not None else None
    )
    if result.get("cachedInputTokens", 0) > result.get("inputTokens", 0):
        result.pop("cachedInputTokens", None)
    reason = sanitize_execution_transport_reason(value.get("executionReceiptTransportReason"))
    if reason is not None:
        result["executionReceiptTransportReason"] = reason
    return result


def measured_workload(prompt_bytes, receipt, usage, *, model_ms=None, sources_ms=None, transport_reason=None):
    receipt = sanitize_execution_receipt(receipt)
    usage = usage if isinstance(usage, dict) else {}
    counts = receipt["counts"] if receipt else {}
    return safe_workload({
        "version": VERSION,
        "promptBytes": prompt_bytes,
        "modelElapsedMs": model_ms,
        "sourceVerificationElapsedMs": sources_ms,
        "executionCoverage": receipt["coverage"] if receipt else "UNAVAILABLE",
        "executionReceiptTransportReason": transport_reason,
        **{key: counts.get(source) for key, source in COUNTS.items()},
        **{key: usage.get(source) for key, source in (
            ("inputTokens", "prompt_tokens"),
            ("cachedInputTokens", "cached_input_tokens"),
            ("outputTokens", "completion_tokens"),
        )},
    })
