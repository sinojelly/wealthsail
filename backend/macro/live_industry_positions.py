"""Live NAV observations for the report's read-time presentation."""

import re
from decimal import Decimal
from functools import lru_cache

from backend.funds.nav_window import two_year_nav_points
from backend.indicators.actionable_zones import buy_zone_backtest
from backend.investment.live_fund_plans import _plans
from backend.macro.cycle_insights import _number, _rows

_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_POSITION_TEXT = re.compile(r"买区|卖区|入区|止盈|止损|距.{0,8}区|持有至|等回调|追高")


def _live_position(plan):
    current = _number(plan.get("currentPrice"))
    if current is None or current <= 0:
        return None
    tracks = _rows(plan.get("tracks"))
    selected = plan.get("selectedTrack") or plan.get("track")
    track = next((row for row in tracks if row.get("track") == selected), None)
    track = track or next(
        (row for row in tracks if row.get("entryZoneMin") is not None), {}
    )
    low, high, stop, sell = (
        _number(value)
        for value in (
            track.get("entryZoneMin"),
            track.get("entryZoneMax"),
            track.get("stopLossPrice"),
            track.get("sellZoneMin") or track.get("takeProfitT1"),
        )
    )
    if stop is not None and 0 < current < stop:
        return "跌破止损参考"
    if sell is not None and sell > 0:
        if current >= sell:
            return "已入卖区"
        if (sell - current) / current <= Decimal("0.05"):
            return f"接近卖区 · 差 {(sell - current) / current:.2%}"
    if low is not None and high is not None and 0 < low <= high:
        distance = max(low - current, current - high, Decimal(0)) / current
        if not distance:
            return "已入买区"
        if distance <= Decimal("0.02"):
            return f"接近买区 · 差 {distance:.2%}"
        return f"未入区 · 距买区 {distance:.2%}"
    return None


def _without_frozen_position(text, codes, names):
    if not isinstance(text, str):
        return text
    clauses = re.split(r"(?<=[。；！？\n])", text)
    return "".join(
        clause
        for clause in clauses
        if not (
            _POSITION_TEXT.search(clause)
            and (
                set(_CODE.findall(clause)) & codes
                or any(name in clause for name in names)
            )
        )
    ).strip()


@lru_cache(maxsize=256)
def _backtest_for_window(closes, low, high):
    return buy_zone_backtest(closes, low, high)


def _add_backtests(plan):
    series = plan.get("series") or {}
    points = series.get("points", [])
    if (
        series.get("state") != "READY"
        or not points
        or series.get("asOf") != plan.get("asOf")
    ):
        return
    closes = tuple(
        str(point["value"]) for point in two_year_nav_points(points) if "value" in point
    )
    for copy in (plan, plan.get("actionableZones")):
        if not isinstance(copy, dict):
            continue
        for track in _rows(copy.get("tracks")):
            result = _backtest_for_window(
                closes, str(track.get("entryZoneMin")), str(track.get("entryZoneMax"))
            )
            if result is not None:
                track["buyZoneBacktest"] = result.copy()


def enrich_live_industry_positions(report):
    """Update public display copies only, after all live price guards run."""
    current = {}
    for plan in _plans(report):
        code = plan.get("fundCode")
        if (
            plan.get("priceBasis") != "UNIT_NAV"
            or not isinstance(code, str)
            or not _CODE.fullmatch(code)
        ):
            continue
        _add_backtests(plan)
        status = _live_position(plan)
        if not status:
            continue
        previous = current.get(code)
        if previous is None or str(plan.get("asOf") or "") > str(
            previous.get("asOf") or ""
        ):
            current[code] = {
                "fundCode": code,
                "label": str(plan.get("label") or plan.get("name") or code),
                "asOf": plan.get("asOf"),
                "status": status,
            }

    directions = {
        row.get("title"): row
        for row in _rows(report.get("valueDirections"))
        if isinstance(row.get("title"), str)
    }
    insights = report.get("cycleInsights") or {}
    for row in _rows(insights.get("industries")):
        direction = directions.get(row.get("label"), {})
        codes = dict.fromkeys(
            code
            for code in [
                row.get("fundCode"),
                *[plan.get("fundCode") for plan in _rows(direction.get("plans"))],
                *(direction.get("quantitativeFundCodes") or []),
                *_CODE.findall(
                    " ".join(
                        str(row.get(field) or "")
                        for field in ("driver", "summary", "action")
                    )
                ),
            ]
            if isinstance(code, str) and code in current
        )
        if not codes:
            continue
        positions = [current[code] for code in codes]
        row["fundPositions"] = positions
        if row.get("origin") == "原报告基金价格位置" or _POSITION_TEXT.search(
            str(row.get("stage") or "")
        ):
            row["stage"] = positions[0]["status"]
            row["asOf"] = positions[0]["asOf"]
            row["origin"] = "最新基金净值"
            row.pop("buyDistancePct", None)
        names = [
            name
            for name in [
                row.get("label"),
                *(position["label"] for position in positions),
            ]
            if isinstance(name, str) and len(name) >= 2 and not _CODE.fullmatch(name)
        ]
        for field in ("driver", "summary", "action"):
            if field in row:
                row[field] = _without_frozen_position(row[field], set(codes), names)
    return report
