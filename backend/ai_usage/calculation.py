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


def quota_delta(before, after, *, concurrent=False):
    before, after = before or {}, after or {}
    start, end = stamp(before.get("observedAt")), stamp(after.get("observedAt"))
    common = None
    if concurrent:
        common = "CONCURRENT"
    elif (
        not before.get("accountFingerprint")
        or not after.get("accountFingerprint")
        or start is None
        or end is None
        or end < start
    ):
        common = "UNKNOWN"
    elif (before["accountFingerprint"], before.get("planType")) != (
        after["accountFingerprint"],
        after.get("planType"),
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
        status = common
        low, high = number(old.get("usedPercent")), number(new.get("usedPercent"))
        reset = number(old.get("resetsAt"))
        if status is None:
            if (
                not old
                or not new
                or low is None
                or high is None
                or low > 100
                or high > 100
                or reset is None
            ):
                status = "UNKNOWN"
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
            }
        )
    old, new = before.get("credits") or {}, after.get("credits") or {}
    low, high = number(old.get("balance")), number(new.get("balance"))
    status = common
    if status is None:
        if (
            low is None
            or high is None
            or old.get("unlimited") is not False
            or new.get("unlimited") is not False
        ):
            status = "UNKNOWN"
        elif high > low:
            status = "RESET_OR_TOPUP"
    return {
        "windows": windows,
        "credits": {
            "consumed": str(low - high) if status is None else None,
            "status": status or "ESTIMATED",
        },
        "periods": periods,
    }


def aggregate_task(task, calls):
    states = {call["status"] for call in calls}
    state = (
        "NONE"
        if not calls
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
        "callCount": len(calls),
        "models": sorted({call["model"] for call in calls}),
        "calls": calls,
    }
    for key in ("inputTokens", "cachedTokens", "outputTokens", "totalTokens"):
        values = [call.get(key) for call in calls]
        result[key] = (
            sum(values)
            if values and all(value is not None for value in values)
            else None
        )
    keys = {window["key"] for call in calls for window in call["quota"]["windows"]}
    windows = []
    for key in sorted(keys):
        rows = [
            next((row for row in call["quota"]["windows"] if row["key"] == key), {})
            for call in calls
        ]
        complete = all(row.get("consumedPercent") is not None for row in rows)
        same_period = (
            len({call["quota"].get("periods", {}).get(key) for call in calls}) == 1
        )
        complete = complete and same_period
        status = (
            "ESTIMATED"
            if complete
            else "CONCURRENT"
            if any(row.get("status") == "CONCURRENT" for row in rows)
            else "INCOMPLETE"
        )
        windows.append(
            {
                "key": key,
                "label": next(row["label"] for row in rows if row),
                "consumedPercent": sum(row["consumedPercent"] for row in rows)
                if complete
                else None,
                "status": status,
            }
        )
    credits = [number(call["quota"]["credits"].get("consumed")) for call in calls]
    complete = (
        bool(credits)
        and all(value is not None for value in credits)
        and len({call["quota"].get("periods", {}).get("credits") for call in calls})
        == 1
    )
    result["quota"] = {
        "windows": windows,
        "credits": {
            "consumed": str(sum(credits)) if complete else None,
            "status": "ESTIMATED" if complete else "INCOMPLETE",
        },
    }
    return result
