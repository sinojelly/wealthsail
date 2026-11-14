"""Optional cycle outlook: preserve views, keep clocks and references honest."""

from datetime import date, datetime


def sanitize_cycle_insights(raw, *, aliases, now, warnings):
    """Normalize a local display extension without gating report publication.

    A model's fact/expectation label is attribution, never independent fact
    verification. No rates, probabilities or industry stages are inferred here.
    """
    from backend.macro.research_sanitize import _references, _text, _warn

    if isinstance(raw, str) and raw.strip():
        raw = {"marketRegime": {"summary": raw}}
    if not isinstance(raw, dict):
        return None

    def clock(value, path):
        if value is None or value == "":
            return None
        try:
            if not isinstance(value, str):
                raise ValueError("invalid clock")
            if len(value) == 10:
                parsed = date.fromisoformat(value)
                valid = parsed <= now.date()
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                valid = parsed.tzinfo is not None and parsed <= now
            if valid:
                return value
        except (ValueError, TypeError):
            pass
        _warn(
            warnings,
            path,
            "CYCLE_CLOCK_CLEARED",
            "周期观点时间未能对应，保留观点并清空无效时点。",
        )
        return None

    def insight(value, path):
        if isinstance(value, str):
            value = {"summary": value}
        if not isinstance(value, dict):
            return None
        row = {
            field: _text(value.get(field))
            for field in (
                "title",
                "stage",
                "summary",
                "impact",
                "action",
                "industryId",
                "label",
                "driver",
            )
        }
        if not any(row.values()):
            return None
        kind = value.get("evidenceKind")
        row.update(
            evidenceKind=kind
            if kind in ("OBSERVED_FACT", "MARKET_EXPECTATION", "MODEL_ASSESSMENT")
            else "MODEL_ASSESSMENT",
            factVerification="MODEL_UNVERIFIED",
            asOf=clock(value.get("asOf"), path + ".asOf"),
            sourceIds=_references(
                value.get("sourceIds"), aliases, warnings, path + ".sourceIds"
            ),
        )
        if value.get("importance") in ("HIGH", "MEDIUM", "LOW"):
            row["importance"] = value["importance"]
        return row

    result = {"version": "macro-cycle-v1"}
    for key in ("globalLiquidity", "domesticCycle", "marketRegime"):
        row = insight(raw.get(key), "cycleInsights." + key)
        if row:
            result[key] = row
    for key in ("keyFactors", "industries"):
        values = raw.get(key)
        values = (
            values
            if isinstance(values, list)
            else [values]
            if isinstance(values, (dict, str))
            else []
        )
        rows = [
            insight(value, f"cycleInsights.{key}[{index}]")
            for index, value in enumerate(values)
        ]
        result[key] = [row for row in rows if row]
    return (
        result
        if len(result) > 3 or result["keyFactors"] or result["industries"]
        else None
    )
