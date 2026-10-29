"""Conservative attribution of observed account changes; never currency pricing."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result <= Decimal("1e18") else None
    except (ValueError, InvalidOperation, TypeError):
        return None


def stamp(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def tokens(raw):
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for name, aliases in (
        ("inputTokens", ("input_tokens", "prompt_tokens", "inputTokens")),
        ("cachedTokens", ("cached_input_tokens", "cachedInputTokens")),
        ("outputTokens", ("output_tokens", "completion_tokens", "outputTokens")),
    ):
        value = next((raw[key] for key in aliases if key in raw), None)
        result[name] = value if type(value) is int and 0 <= value <= 10**12 else None
    if (
        result["inputTokens"] is not None
        and result["cachedTokens"] is not None
        and result["cachedTokens"] > result["inputTokens"]
    ):
        result["cachedTokens"] = None
    result["totalTokens"] = (
        result["inputTokens"] + result["outputTokens"]
        if result["inputTokens"] is not None and result["outputTokens"] is not None
        else None
    )
    return result


def snapshot_diagnostic(snapshot):
    """Public capture metadata contains no identity or arbitrary error text."""
    diagnostic = snapshot.get("diagnostic") or {}
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
    reason = diagnostic.get("reason")
    if not isinstance(reason, str) or reason not in {
        "TIMEOUT",
        "UNAVAILABLE",
        "NOT_FRESH",
        "ERROR",
        "CANCELLED",
    }:
        reason = None
    status = "READY"
    if not snapshot:
        status, reason = "MISSING", "MISSING"
    elif reason or snapshot.get("status", "READY") != "READY":
        status = "UNAVAILABLE"
        reason = reason or (
            "NOT_FRESH" if snapshot.get("status") == "STALE" else "UNAVAILABLE"
        )
    observed = stamp(snapshot.get("observedAt")) if status == "READY" else None
    elapsed = diagnostic.get("elapsedMs")
    return {
        "status": status,
        "reason": reason,
        "observedAt": observed.isoformat() if observed else None,
        "elapsedMs": elapsed
        if type(elapsed) is int and 0 <= elapsed <= 10**9
        else None,
    }


def quota_delta(before, after, *, concurrent=False):
    before, after = before or {}, after or {}
    start, end = stamp(before.get("observedAt")), stamp(after.get("observedAt"))
    snapshots = {
        "before": snapshot_diagnostic(before),
        "after": snapshot_diagnostic(after),
    }
    common, common_reason = None, None
    if concurrent:
        common = "CONCURRENT"
    elif not before and not after:
        common, common_reason = "UNKNOWN", "BOTH_MISSING"
    elif any(row["status"] != "READY" for row in snapshots.values()):
        stage, capture = next(
            (stage, row) for stage, row in snapshots.items() if row["status"] != "READY"
        )
        common, common_reason = "UNKNOWN", f"{stage.upper()}_{capture['reason']}"
    elif not before.get("accountFingerprint") or not after.get("accountFingerprint"):
        common, common_reason = "UNKNOWN", "IDENTITY_MISSING"
    elif start is None or end is None or end <= start:
        common, common_reason = "UNKNOWN", "OBSERVATION_TIME_INVALID"
    elif (before.get("observationId") or after.get("observationId")) and (
        not before.get("observationId")
        or not after.get("observationId")
        or before["observationId"] == after["observationId"]
    ):
        common, common_reason = "UNKNOWN", "OBSERVATION_REUSED"
    elif (
        before["accountFingerprint"],
        before.get("planType"),
        before.get("authGeneration"),
    ) != (
        after["accountFingerprint"],
        after.get("planType"),
        after.get("authGeneration"),
    ):
        common = "ACCOUNT_CHANGED"
    elif (start.year, start.month) != (end.year, end.month):
        common = "PERIOD_CHANGED"
    windows = []
    previous = {
        row["key"]: row
        for row in before.get("windows") or []
        if isinstance(row, dict) and row.get("key")
    }
    current = {
        row["key"]: row
        for row in after.get("windows") or []
        if isinstance(row, dict) and row.get("key")
    }
    periods = {
        key: f"{before.get('accountFingerprint')}:{row.get('windowMinutes')}:{row.get('resetsAt')}"
        for key, row in previous.items()
    }
    periods["credits"] = (
        f"{before.get('accountFingerprint')}:{before.get('planType')}:{start.year}-{start.month}"
        if start
        else None
    )
    for key in sorted(previous.keys() | current.keys()):
        old, new = previous.get(key, {}), current.get(key, {})
        status, reason = common, common_reason
        low, high = number(old.get("usedPercent")), number(new.get("usedPercent"))
        reset = number(old.get("resetsAt"))
        if status is None:
            if not old or not new:
                status, reason = "UNKNOWN", "WINDOW_MISSING"
            elif (
                low is None or high is None or low > 100 or high > 100 or reset is None
            ):
                status, reason = "UNKNOWN", "WINDOW_VALUE_INVALID"
            elif (
                (old.get("windowMinutes"), old.get("resetsAt"))
                != (new.get("windowMinutes"), new.get("resetsAt"))
                or number(old.get("windowMinutes")) in {None, 0}
                or end.timestamp() >= float(reset)
            ):
                status = "PERIOD_CHANGED"
            elif high < low:
                status = "RESET_OR_TOPUP"
        windows.append(
            {
                "key": key,
                "label": new.get("label") or old.get("label") or key,
                "consumedPercent": float(high - low) if status is None else None,
                "status": status or "ESTIMATED",
                **({"reason": reason} if reason else {}),
            }
        )
    old, new = before.get("credits") or {}, after.get("credits") or {}
    low, high = number(old.get("balance")), number(new.get("balance"))
    status, reason = common, common_reason
    if status is None:
        if (
            low is None
            or high is None
            or old.get("unlimited") is not False
            or new.get("unlimited") is not False
        ):
            status, reason = "UNKNOWN", "CREDITS_UNAVAILABLE"
        elif high > low:
            status = "RESET_OR_TOPUP"
    return {
        "windows": windows,
        "credits": {
            "consumed": str(low - high) if status is None else None,
            "status": status or "ESTIMATED",
            **({"reason": reason} if reason else {}),
        },
        "periods": periods,
        "snapshots": snapshots,
    }


def aggregate_quota_status(rows, *, complete, same_period):
    """Keep a shared precise cause; mixed/missing calls require expansion."""
    if complete:
        return {"status": "ESTIMATED"}
    if any(row.get("status") == "CONCURRENT" for row in rows):
        return {"status": "CONCURRENT"}
    states = {row.get("status") for row in rows}
    reasons = {row.get("reason") for row in rows}
    if (
        len(states) == 1
        and len(reasons) == 1
        and states <= {"UNKNOWN", "ACCOUNT_CHANGED", "PERIOD_CHANGED", "RESET_OR_TOPUP"}
    ):
        reason = next(iter(reasons))
        return {"status": next(iter(states)), **({"reason": reason} if reason else {})}
    if states == {"ESTIMATED"} and not same_period:
        return {"status": "PERIOD_CHANGED"}
    return {"status": "INCOMPLETE"}


def aggregate_task(task, calls):
    """Aggregate multi-call telemetry and quota attribution into a task DTO.

    Args:
        task: Dict containing task metadata (id, businessStatus, finishedAt, etc.).
        calls: List of call DTOs belonging to the task.

    Returns:
        Dict representing aggregated task DTO with normalized inference status.
    """
    task_finished = task.get("finishedAt") is not None or task.get("businessStatus") not in (None, "RUNNING")
    normalized_calls = []
    for call in calls:
        if task_finished and call.get("status") == "RUNNING":
            normalized_calls.append(
                {
                    **call,
                    "status": "UNCERTAIN",
                    "errorClass": call.get("errorClass") or "ZombieCallRecovered",
                }
            )
        else:
            normalized_calls.append(call)

    states = {call.get("status") for call in normalized_calls}
    state = (
        "NONE"
        if not normalized_calls
        else "RUNNING"
        if "RUNNING" in states
        else "UNCERTAIN"
        if "UNCERTAIN" in states
        else "SUCCEEDED"
        if states == {"SUCCEEDED"}
        else "FAILED"
        if states == {"FAILED"}
        else "PARTIAL"
    )
    result = {
        **task,
        "inferenceStatus": state,
        "callCount": len(normalized_calls),
        "models": sorted({call["model"] for call in normalized_calls}),
        "calls": normalized_calls,
    }
    for key in ("inputTokens", "cachedTokens", "outputTokens", "totalTokens"):
        values = [call.get(key) for call in normalized_calls]
        result[key] = (
            sum(values)
            if values and all(value is not None for value in values)
            else None
        )
    keys = {window["key"] for call in normalized_calls for window in call["quota"]["windows"]}
    windows = []
    for key in sorted(keys):
        rows = [
            next((row for row in call["quota"]["windows"] if row["key"] == key), {})
            for call in normalized_calls
        ]
        complete = all(row.get("consumedPercent") is not None for row in rows)
        same_period = (
            len({call["quota"].get("periods", {}).get(key) for call in normalized_calls}) == 1
        )
        complete = complete and same_period
        windows.append(
            {
                "key": key,
                "label": next(row["label"] for row in rows if row),
                "consumedPercent": sum(row["consumedPercent"] for row in rows)
                if complete
                else None,
                **aggregate_quota_status(
                    rows, complete=complete, same_period=same_period
                ),
            }
        )
    credits = [number(call["quota"]["credits"].get("consumed")) for call in normalized_calls]
    same_period = (
        len({call["quota"].get("periods", {}).get("credits") for call in normalized_calls}) == 1
    )
    complete = (
        bool(credits) and all(value is not None for value in credits) and same_period
    )
    result["quota"] = {
        "windows": windows,
        "credits": {
            "consumed": str(sum(credits)) if complete else None,
            **aggregate_quota_status(
                [call["quota"]["credits"] for call in normalized_calls],
                complete=complete,
                same_period=same_period,
            ),
        },
    }
    return result
