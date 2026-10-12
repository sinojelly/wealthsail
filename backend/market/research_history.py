"""Research history validation, without synthetic OHLC or missing-day fills."""

import math
from datetime import date


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
        if (
            point["close"] is None
            or point["close"] <= 0
            or any(
                v is not None and not math.isfinite(v)
                for k, v in point.items()
                if k != "date"
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


def quality(*, latest, expected, verified, missing):
    if not verified or expected is None:
        return "UNVERIFIED"
    if missing:
        return "CONFLICT"
    return "STALE" if latest < expected else "READY"
