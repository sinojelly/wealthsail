"""Conservative, transparent research candidate gates; not investment advice."""

import math
from datetime import date
from decimal import Decimal

VERSION = "value-evidence-v1"
PARAMETERS = {
    "targetWindowYears": 5,
    "minimumWindowYears": 3,
    "minimumObservations": 756,
    "maximumValuationPercentile": 30,
    "percentileMethod": "(less_than_latest + 0.5 * equal_to_latest) / sample_count * 100",
    "maximumAnnualOperatingCost": 0.008,
    "maximumDrawdown": 0.35,
}
_RATES = ("managementRate", "custodyRate", "salesServiceRate")
_LIMITATIONS = [
    "宏观周期适配尚未评估。",
    "基金跟踪误差尚未评估。",
    "用户组合适配尚未评估。",
]


def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        return None
    return number


def _valuation_evidence(series, end, metric):
    result = {
        "state": series.get("state", "UNAVAILABLE"),
        "metric": metric,
        "latest": None,
        "percentile": None,
        "sampleCount": 0,
        "historyStart": None,
        "historyEnd": None,
        "sourceUri": series.get("sourceUri"),
        "method": PARAMETERS["percentileMethod"],
    }
    if result["state"] != "READY" or end is None:
        return result
    if series.get("metric") != metric:
        result["state"] = "UNSUPPORTED"
        return result
    points = []
    previous = None
    try:
        for point in series.get("points", []):
            day = date.fromisoformat(str(point["date"]))
            if previous is not None and day <= previous:
                raise ValueError("duplicate or unordered valuation date")
            previous = day
            if _years_before(end, 5) <= day <= end:
                value = _number(point["value"], positive=True)
                if value is None:
                    raise ValueError("invalid valuation")
                points.append((day, value))
    except (KeyError, TypeError, ValueError):
        result["state"] = "CONFLICT"
        return result
    count = len(points)
    result["sampleCount"] = count
    if not count:
        result["state"] = "INSUFFICIENT"
        return result
    result.update(
        latest=points[-1][1],
        historyStart=points[0][0].isoformat(),
        historyEnd=points[-1][0].isoformat(),
    )
    if points[-1][0] < end:
        result["state"] = "STALE"
        return result
    if count < 756 or points[0][0] > _years_before(points[-1][0], 3):
        result["state"] = "INSUFFICIENT"
        return result
    latest = points[-1][1]
    less = sum(value < latest for _, value in points)
    equal = sum(value == latest for _, value in points)
    result["percentile"] = (less + 0.5 * equal) / count * 100
    return result


def evaluate(valuation, costs, performance, *, mapped: bool):
    """Require independent valuation, disclosed cost, mapped index and risk gates.

    Valuation percentiles compare actual observations within five calendar
    years ending on the caller's latest published session. A shorter history
    must cover at least three calendar years and contain 756 observations.
    Fee rates and drawdown use decimal fractions, percentiles use 0–100.
    """
    valuation, costs, performance = valuation or {}, costs or {}, performance or {}
    try:
        end = date.fromisoformat(str(valuation.get("requestedEnd")))
    except ValueError:
        end = None
    evidence = {
        "pe": _valuation_evidence(valuation.get("pe") or {}, end, "PE_TTM"),
        "pb": _valuation_evidence(valuation.get("pb") or {}, end, "PB"),
        "costs": {
            "state": costs.get("state", "UNAVAILABLE"),
            "sourceUri": costs.get("sourceUri"),
            **{key: _number(costs.get(key)) for key in _RATES},
            "totalRate": None,
        },
        "performance": {
            "state": performance.get("state", "UNAVAILABLE"),
            "eligible": performance.get("eligible") is True,
            "maxDrawdown": _number(performance.get("maxDrawdown")),
        },
        "limitations": list(_LIMITATIONS),
    }
    result = {
        "eligible": False,
        "state": "INCOMPLETE",
        "reasons": [],
        "evidence": evidence,
        "algorithmVersion": VERSION,
        "parameters": dict(PARAMETERS),
    }
    reasons = result["reasons"]
    complete = True
    if mapped is not True:
        complete = False
        reasons.append("基金与跟踪指数映射未完整核验。")
    if (
        valuation.get("state") != "READY"
        or end is None
        or not valuation.get("sourceUri")
    ):
        complete = False
        reasons.append("指数估值来源或最新已发布交易日不可用。")
    for key, label in (("pe", "PE"), ("pb", "PB")):
        if evidence[key]["state"] != "READY":
            complete = False
            reasons.append(
                f"{label} 估值证据为 {evidence[key]['state']}，需至少 756 个观察值及 3 年实际历史。"
            )
    c = evidence["costs"]
    for key in _RATES:
        if c[key] is not None and c[key] > 1:
            c[key] = None
    if (
        c["state"] != "READY"
        or any(c[key] is None for key in _RATES)
        or not c["sourceUri"]
    ):
        complete = False
        reasons.append("管理、托管及销售服务费未取得有效、明确和完整披露。")
    else:
        c["totalRate"] = float(sum(Decimal(str(c[key])) for key in _RATES))
    p = evidence["performance"]
    if p["state"] != "READY" or not p["eligible"] or p["maxDrawdown"] is None:
        complete = False
        reasons.append("基金分红再投历史或风险证据未达到研究门槛。")
    if not complete:
        return result
    if evidence["pe"]["percentile"] > 30:
        reasons.append("PE 分位高于 30%，未达到低估值门槛。")
    if evidence["pb"]["percentile"] > 30:
        reasons.append("PB 分位高于 30%，未达到低估值门槛。")
    if c["totalRate"] > 0.008:
        reasons.append("年管理、托管及销售服务费合计超过 0.8%。")
    if p["maxDrawdown"] > 0.35:
        reasons.append("样本区间最大回撤超过 35%。")
    result["eligible"] = not reasons
    result["state"] = "RESEARCH_CANDIDATE" if result["eligible"] else "NOT_CANDIDATE"
    if result["eligible"]:
        reasons.append("估值、费用、历史风险及映射证据满足门槛，仅列为研究候选。")
    return result
