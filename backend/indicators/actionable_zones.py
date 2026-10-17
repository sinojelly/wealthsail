"""Versioned, deterministic research coordinates for two independent tracks."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import math
from statistics import pstdev

VERSION = "actionable-zones-v2"
PARAMETERS = {
    "ma": [20, 60, 200],
    "rsi": 14,
    "atr": 14,
    "bollinger": [20, 2],
    "momentum": 20,
    "maximumPePercentile": 40,
    "maximumMA200Gap": -0.08,
    "oversoldRSI": 38,
    "bollingerProximity": 1.02,
    "maximumTrendRSI": 75,
}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and math.isfinite(float(result)) else None
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        return None


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _wilder(values):
    if len(values) < 14:
        return None
    average = sum(value / 14 for value in values[:14])
    for value in values[14:]:
        average = average * (13 / 14) + value / 14
    return average


def _price(value):
    value = _number(value)
    return format(value, "f") if value is not None and value > 0 else None


def _zone(kind, values, close):
    result = {
        "entryZoneMin": None,
        "entryZoneMax": None,
        "stopLossPrice": None,
        "takeProfitT1": None,
        "takeProfitT2": None,
        "priceState": "INSUFFICIENT",
    }
    reasons = []
    ma20, lower, atr = (
        _number(values.get(key)) for key in ("ma20", "bollLower", "atr14")
    )
    if close is None or ma20 is None or lower is None:
        return result, ["入场参考区间缺少有效收盘、MA20或布林带。"]
    if atr is None:
        minimum, maximum = (
            (lower, min(close, ma20))
            if kind == "LEFT_VALUE"
            else (ma20, max(close, ma20))
        )
        result["priceState"] = "PARTIAL"
        reasons.append("缺少连续真实高低价，保留均线/布林参考区间；止损和目标未计算。")
    else:
        minimum, maximum = (
            (
                min(lower, close - Decimal("0.8") * atr),
                min(ma20, close + Decimal("0.3") * atr),
            )
            if kind == "LEFT_VALUE"
            else (ma20 - Decimal("0.2") * atr, max(close, ma20 + Decimal("0.6") * atr))
        )
        result["priceState"] = "READY"
    if minimum <= 0 or maximum <= 0 or minimum > maximum:
        result["priceState"] = "INVALID"
        return result, reasons + ["计算入场区间非正或顺序无效，未调整原始锚点。"]
    result.update(entryZoneMin=_price(minimum), entryZoneMax=_price(maximum))
    if atr is not None:
        stop = minimum - 2 * atr
        ma60, ma200 = (_number(values.get(key)) for key in ("ma60", "ma200"))
        first = max(ma60 or maximum + 2 * atr, maximum + 2 * atr)
        second = max(ma200 or first + 2 * atr, first + 2 * atr)
        if 0 < stop < minimum:
            result["stopLossPrice"] = _price(stop)
        else:
            reasons.append("止损计算非正或未低于入场区，止损不可用。")
        if first > maximum:
            result["takeProfitT1"] = _price(first)
            if second > first:
                result["takeProfitT2"] = _price(second)
        if not result["takeProfitT1"] or not result["takeProfitT2"]:
            reasons.append("目标未依次高于入场区，未制造替代目标。")
        if any(
            result[key] is None
            for key in ("stopLossPrice", "takeProfitT1", "takeProfitT2")
        ):
            result["priceState"] = "PARTIAL"
    return result, reasons


def compute_actionable_price_plan(
    points,
    *,
    subject_kind,
    subject_id,
    label=None,
    price_basis="INDEX_POINTS",
    unit=None,
    as_of=None,
    pe_percentile=None,
    source_uri=None,
    market=None,
):
    """Calculate from the ordered real suffix; never substitute another asset."""
    cutoff = _day(as_of) if as_of is not None else None
    rows, previous, order_conflict = [], None, False
    for point in points or []:
        day = _day(point.get("date"))
        if cutoff and day and day > cutoff:
            continue
        if day is None or previous is not None and day <= previous:
            order_conflict = True
        previous = day
        rows.append(point)
    # An invalid observation breaks continuity; do not remove it and join days.
    suffix = []
    for point in reversed(rows):
        value = _number(point.get("close"))
        if (
            value is None
            or value <= 0
            or float(value) <= 0
            or point.get("isTradingDay") is False
        ):
            break
        suffix.append(point)
    suffix.reverse()
    if order_conflict:
        suffix = []
    closes = [float(_number(point["close"])) for point in suffix]
    close = _number(suffix[-1]["close"]) if suffix else None
    values, states = {}, {}
    for period in (20, 60, 200):
        key = f"ma{period}"
        values[key] = (
            float(
                sum((_number(point["close"]) for point in suffix[-period:]), Decimal(0))
                / period
            )
            if len(closes) >= period
            else None
        )
        states[key] = {
            "state": "READY" if values[key] is not None else "INSUFFICIENT",
            "sampleCount": len(closes),
            "requiredSamples": period,
        }
    changes = [right - left for left, right in zip(closes, closes[1:])]
    gains = _wilder([max(0, change) for change in changes])
    losses = _wilder([max(0, -change) for change in changes])
    values["rsi14"] = (
        None
        if gains is None
        else 50.0
        if gains == losses == 0
        else 100.0
        if losses == 0
        else 100 - 100 / (1 + gains / losses)
    )
    values["momentum20"] = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else None
    for key, count in (("rsi14", 15), ("momentum20", 21)):
        states[key] = {
            "state": "READY" if values[key] is not None else "INSUFFICIENT",
            "sampleCount": len(closes),
            "requiredSamples": count,
        }
    deviation = pstdev(closes[-20:]) if len(closes) >= 20 else None
    values.update(
        bollMiddle=values["ma20"],
        bollLower=values["ma20"] - 2 * deviation if deviation is not None else None,
        bollUpper=values["ma20"] + 2 * deviation if deviation is not None else None,
    )
    for key in ("bollMiddle", "bollLower", "bollUpper"):
        states[key] = dict(states["ma20"])
    true_ranges, ohlc_conflict = [], False
    for index in range(len(suffix) - 1, 0, -1):
        point = suffix[index]
        high, low, current = (
            _number(point.get(key)) for key in ("high", "low", "close")
        )
        if high is None or low is None or not 0 < low <= current <= high:
            ohlc_conflict = (
                point.get("high") is not None and point.get("low") is not None
            )
            break
        prior = _number(suffix[index - 1]["close"])
        true_ranges.append(float(max(high - low, abs(high - prior), abs(low - prior))))
    true_ranges.reverse()
    values["atr14"] = _wilder(true_ranges)
    states["atr14"] = {
        "state": "READY"
        if values["atr14"] is not None
        else "CONFLICT"
        if ohlc_conflict
        else "MISSING_OHLC"
        if len(closes) >= 15
        else "INSUFFICIENT",
        "sampleCount": len(true_ranges),
        "requiredSamples": 14,
    }
    pe = _number(pe_percentile)
    pe = pe if pe is not None and 0 <= pe <= 100 else None
    values["pePercentile"] = float(pe) if pe is not None else None
    values["distanceMA200"] = (
        float(close) / values["ma200"] - 1
        if close is not None and values["ma200"]
        else None
    )
    states["distanceMA200"] = dict(states["ma200"])
    states["pePercentile"] = {
        "state": "READY" if pe is not None else "UNAVAILABLE",
        "sampleCount": None,
        "requiredSamples": None,
    }
    for key, value in values.items():
        if value is not None and not math.isfinite(value):
            values[key] = None
            states[key]["state"] = "CONFLICT"
    left_conditions = [
        ("PE历史分位不高于40%", pe <= 40 if pe is not None else None),
        (
            "收盘偏离真实MA200不高于-8%",
            values["distanceMA200"] <= -0.08
            if values["distanceMA200"] is not None
            else None,
        ),
        (
            "Wilder RSI14不高于38",
            values["rsi14"] <= 38 if values["rsi14"] is not None else None,
        ),
        (
            "收盘处于布林下轨的1.02倍以内",
            float(close) <= values["bollLower"] * 1.02
            if close is not None and values["bollLower"] is not None
            else None,
        ),
    ]
    right_conditions = [
        (
            "收盘不低于MA20",
            float(close) >= values["ma20"]
            if close is not None and values["ma20"] is not None
            else None,
        ),
        (
            "20期动量为正",
            values["momentum20"] > 0 if values["momentum20"] is not None else None,
        ),
        (
            "Wilder RSI14低于75",
            values["rsi14"] < 75 if values["rsi14"] is not None else None,
        ),
    ]
    tracks = []
    for kind, conditions in (
        ("LEFT_VALUE", left_conditions),
        ("RIGHT_TREND", right_conditions),
    ):
        known = [value for _, value in conditions if value is not None]
        candidate = (
            any(known) if kind == "LEFT_VALUE" else len(known) == 3 and all(known)
        )
        state = (
            "INSUFFICIENT"
            if close is None
            else "CANDIDATE"
            if candidate
            else "WATCH"
            if (
                len(known) == len(conditions)
                if kind == "LEFT_VALUE"
                else any(value is False for value in known)
            )
            else "INSUFFICIENT"
        )
        zone, reasons = _zone(kind, values, close)
        missing = [name for name, value in conditions if value is None]
        tracks.append(
            {
                "track": kind,
                "state": state,
                **zone,
                "asOf": _day(suffix[-1]["date"]).isoformat() if suffix else None,
                "reasons": [
                    name + ("：满足" if value else "：未触发")
                    for name, value in conditions
                    if value is not None
                ]
                + reasons,
                "missingEvidence": missing,
            }
        )
    if order_conflict:
        for item in states.values():
            if item["state"] != "UNAVAILABLE":
                item["state"] = "CONFLICT"
    return {
        "methodVersion": VERSION,
        "parameters": dict(PARAMETERS),
        "subjectKind": str(subject_kind).lower(),
        "subjectId": str(subject_id),
        "label": label or str(subject_id),
        "priceBasis": price_basis,
        "unit": unit or ("points" if str(subject_kind).lower() == "index" else None),
        "market": market,
        "asOf": _day(suffix[-1]["date"]).isoformat()
        if suffix
        else (_day(as_of).isoformat() if _day(as_of) else None),
        "sourceUri": source_uri,
        "currentPrice": _price(close),
        "orderPriceAvailable": close is not None
        and str(subject_kind).lower() in {"fund", "etf"}
        and price_basis in {"ETF_RAW", "RAW_PRICE"}
        and bool(source_uri)
        and bool(market)
        and bool(unit),
        "inputWindow": {
            "start": _day(suffix[0]["date"]).isoformat() if suffix else None,
            "end": _day(suffix[-1]["date"]).isoformat() if suffix else None,
            "sampleCount": len(suffix),
        },
        "indicators": values,
        "indicatorStates": states,
        "tracks": tracks,
    }
