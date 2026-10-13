"""Research history validation, without synthetic OHLC or missing-day fills."""

import math
from datetime import date, timedelta
from zoneinfo import ZoneInfo


def calendar_market(market):
    """Mainland fund/index sessions use the stored exchange holiday calendar."""
    return "SSE" if market in {"SSE", "SZSE", "FUND_CN"} else market


def published_day(as_of, publish_hour, now):
    """Latest potentially published calendar day; callers apply market calendars."""
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if (
        type(publish_hour) is not int
        or not 0 <= publish_hour <= 23
        or as_of > local.date()
    ):
        return None
    if as_of < local.date() or local.hour >= publish_hour:
        return as_of
    return as_of - timedelta(days=1)


def normalize(rows):
    by_date = {}
    for row in rows:
        day = date.fromisoformat(str(row["date"])[:10]).isoformat()
        point = {
            "date": day,
            **{
                k: float(row[k]) if row.get(k) is not None else None
                for k in ("open", "high", "low", "close", "volume")
            },
        }
        for key in ("unitNav", "returnPct"):
            if key in row:
                point[key] = float(row[key]) if row[key] is not None else None
        if row.get("returnQuality"):
            point["returnQuality"] = row["returnQuality"]
        if (
            point["close"] is None
            or point["close"] <= 0
            or any(
                v is not None and not math.isfinite(v)
                for k, v in point.items()
                if k not in {"date", "returnQuality"}
            )
        ):
            raise ValueError("invalid price")
        if point["high"] is not None and point["low"] is not None:
            if not (0 < point["low"] <= point["close"] <= point["high"]):
                raise ValueError("invalid OHLC")
        if day in by_date and by_date[day] != point:
            raise ValueError("conflicting duplicate date")
        by_date[day] = point
    return [by_date[day] for day in sorted(by_date)]


def fund_history(rows, *, return_verified, start, end):
    """Keep reported unit NAV; compound only explicitly verified daily returns.

    The first in-window observation anchors the return index at 100. Its daily
    return belongs to a prior interval and is not used or replaced with zero.
    """
    reported = []
    for row in rows:
        day = date.fromisoformat(str(row["净值日期"])[:10])
        if not start <= day <= end:
            continue
        try:
            raw_growth = row.get("日增长率")
            growth = float(raw_growth) if not isinstance(raw_growth, bool) else None
            if growth is not None and not math.isfinite(growth):
                growth = None
        except (TypeError, ValueError):
            growth = None
        reported.append({"date": day.isoformat(), "close": row["单位净值"],
                         "unitNav": row["单位净值"], "returnPct": growth})
    points = normalize(reported)
    if return_verified is not True or not points:
        return points, "UNIT_NAV"
    invalid = [p for p in points[1:] if p["returnPct"] is None or p["returnPct"] <= -100]
    if invalid:
        for point in invalid:
            point["returnQuality"] = "CONFLICT"
        return points, "UNIT_NAV"
    level = 100.0
    returns = []
    for i, point in enumerate(points):
        if i:
            level *= 1 + point["returnPct"] / 100
        if not math.isfinite(level) or level <= 0:
            # An overflowing compound series is not a usable return input.
            point["returnQuality"] = "CONFLICT"
            return points, "UNIT_NAV"
        returns.append({**point, "close": level})
    return returns, "PROVIDER_RETURN_INDEX"


def quality(*, latest, expected, verified, missing):
    if not verified or expected is None:
        return "UNVERIFIED"
    if missing:
        return "CONFLICT"
    if latest > expected:
        return "UNVERIFIED"
    return "STALE" if latest < expected else "READY"
