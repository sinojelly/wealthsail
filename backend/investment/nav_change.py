"""Add display-only daily returns to frozen reports without changing their NAV."""

from copy import deepcopy
from backend.funds.nav_history import valid_points
from backend.indicators.actionable_zones import _price, nav_daily_change_pct


def report_with_nav_changes(report, histories, now):
    if not isinstance(report, dict) or not isinstance(histories, list):
        return report
    result = deepcopy(report)
    coordinates = {}
    for history in histories:
        if not isinstance(history, dict):
            continue
        code = history.get("fundCode")
        if not isinstance(code, str):
            continue
        points = valid_points(history, code, now)
        if not points:
            continue
        reported = {
            point["date"]: point.get("returnPct") for point in history["points"]
        }
        for index, point in enumerate(points):
            current = {**point, "returnPct": reported.get(point["date"])}
            prior = points[index - 1 : index] if index else []
            change = nav_daily_change_pct([*prior, current])
            key = (code, point["date"], _price(point["close"]))
            if key in coordinates and coordinates[key] != change:
                coordinates[key] = None
            else:
                coordinates[key] = change
    plans = [
        *result.get("actionablePlans", []),
        *((result.get("quantitativeScan") or {}).get("items") or []),
        *(
            plan
            for direction in result.get("valueDirections", [])
            for plan in direction.get("plans", [])
        ),
    ]
    for plan in plans:
        if (
            not isinstance(plan, dict)
            or plan.get("priceBasis") != "UNIT_NAV"
            or plan.get("subjectKind") not in {"fund", "nav"}
            or plan.get("currentChangePct") is not None
        ):
            continue
        code = plan.get("fundCode") or plan.get("subjectId")
        price = _price(plan.get("currentPrice"))
        if price is None:
            continue
        key = (code, str(plan.get("asOf") or "")[:10], price)
        change = coordinates.get(key)
        if change is not None:
            plan["currentChangePct"] = change
    return result
