"""Versioned, deterministic research coordinates for two independent tracks."""

from datetime import date, datetime
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
    ROUND_HALF_UP,
    localcontext,
)
import math
from statistics import pstdev

VERSION = "actionable-zones-v4"
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
    "maximumTrendRSI": 60,
    "navPercentileWindow": 220,
    "maximumNavPercentile": 85,
    "maximumHighNavMA20Ratio": 1.02,
    "trendEntryLowScale": 0.55,
    "trendEntryHighScale": 0.25,
    "navAbsoluteChangeWindow": 14,
    "navAbsoluteChangeMultiplier": 1.4,
    "navStdWindow": 20,
    "navStdMultiplier": 0.65,
    "navRiskFloorRatio": 0.012,
    "minimumRewardRiskT1": 1.5,
    "minimumRewardRiskT2": 2.4,
    "rewardRiskBasis": "DISPLAYED_ENTRY_MID_AND_STOP",
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


def _quantized(value, places):
    value = _number(value)
    if value is None or value <= 0:
        return None
    with localcontext() as context:
        context.prec = max(28, value.adjusted() + places + 2)
        value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return value if value > 0 else None


def _price(value, places=4):
    value = _quantized(value, places)
    return format(value, "f") if value is not None else None


def nav_daily_change_pct(points):
    """Use the disclosed daily return before the raw unit-NAV price change."""
    if not points:
        return None
    change = _number(points[-1].get("returnPct"))
    if change is None and len(points) >= 2:
        previous, current = (_number(point.get("close")) for point in points[-2:])
        if (
            current is not None
            and previous is not None
            and current > 0
            and previous > 0
        ):
            change = (current / previous - 1) * 100
    if change is None or change <= -100:
        return None
    with localcontext() as context:
        context.prec = max(28, change.adjusted() + 4)
        rounded = change.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded.copy_abs() if rounded == 0 else rounded, "f")


def _zone(kind, values, close, *, places, nav_scale=False):
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
    scale = _number(values.get("navVolatility14")) if nav_scale else atr
    if close is None or ma20 is None or lower is None:
        return result, ["入场参考区间缺少有效收盘、MA20或布林带。"]
    if scale is None:
        minimum, maximum = (
            (lower, min(close, ma20)) if kind == "LEFT_VALUE" else (ma20, ma20)
        )
        result["priceState"] = "PARTIAL"
        reasons.append("缺少连续真实高低价，保留均线/布林参考区间；止损和目标未计算。")
    else:
        minimum, maximum = (
            (
                min(lower, close - Decimal("0.8") * scale),
                min(ma20, close + Decimal("0.3") * scale),
            )
            if kind == "LEFT_VALUE"
            else (
                ma20 - Decimal("0.55") * scale,
                ma20 + Decimal("0.25") * scale,
            )
        )
        result["priceState"] = "READY"
    rounded_minimum, rounded_maximum = (
        _quantized(value, places) for value in (minimum, maximum)
    )
    if (
        minimum > maximum
        or rounded_minimum is None
        or rounded_maximum is None
        or rounded_minimum > rounded_maximum
    ):
        result["priceState"] = "INVALID"
        return result, reasons + [
            "计算入场区间在舍入后非正或顺序无效，未调整原始锚点。"
        ]
    result.update(
        entryZoneMin=_price(rounded_minimum, places),
        entryZoneMax=_price(rounded_maximum, places),
    )
    if scale is not None:
        ma60, ma200 = (_number(values.get(key)) for key in ("ma60", "ma200"))
        if nav_scale:
            stop = (minimum if kind == "LEFT_VALUE" else ma20) - 2 * scale
            first = max(
                ma60 or maximum + 2 * scale,
                close + Decimal("2.5") * scale,
                maximum + 2 * scale,
            )
            second = max(
                _number(values.get("bollUpper")) or first + scale,
                close + Decimal("4.5") * scale,
                first + scale,
            )
            reasons.append(
                "风险价使用真实净值变化、20期标准差与明确的最小风险幅度；该尺度不是ATR。"
            )
        else:
            stop = minimum - 2 * scale
            first = max(ma60 or maximum + 2 * scale, maximum + 2 * scale)
            second = max(ma200 or first + 2 * scale, first + 2 * scale)
        stop, first, second = (
            _quantized(value, places) for value in (stop, first, second)
        )
        if stop is not None and stop < rounded_minimum:
            result["stopLossPrice"] = _price(stop, places)
            middle = (rounded_minimum + rounded_maximum) / 2
            risk = middle - stop
            # Round each lower bound upward so the displayed coordinates still
            # satisfy the minimum ratio; ordinary half-up can fall below it.
            quantum = Decimal(1).scaleb(-places)
            with localcontext() as context:
                context.prec = max(28, middle.adjusted() + places + 8)
                first_floor = (middle + Decimal("1.5") * risk).quantize(
                    quantum, rounding=ROUND_CEILING
                )
                second_floor = (middle + Decimal("2.4") * risk).quantize(
                    quantum, rounding=ROUND_CEILING
                )
            first = max(first, first_floor) if first is not None else first_floor
            second = max(second, second_floor) if second is not None else second_floor
            reasons.append("目标参考至少满足入场区间中点至止损的1.5倍/2.4倍风险空间。")
        else:
            reasons.append("止损在舍入后非正或未低于入场区，止损不可用。")
        if first is not None and first > rounded_maximum:
            result["takeProfitT1"] = _price(first, places)
            if second is not None and second > first:
                result["takeProfitT2"] = _price(second, places)
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
    nav_basis = price_basis == "UNIT_NAV" and str(subject_kind).lower() in {
        "nav",
        "fund",
    }
    # A short series cannot stand in for a year of trading observations. Use
    # the same valid suffix and observation cutoff as every other indicator.
    values["navPercentile1y"] = (
        sum(value <= closes[-1] for value in closes[-220:]) / 220 * 100
        if nav_basis and len(closes) >= 220
        else None
    )
    states["navPercentile1y"] = {
        "state": "READY"
        if values["navPercentile1y"] is not None
        else "INSUFFICIENT"
        if nav_basis
        else "NOT_APPLICABLE",
        "sampleCount": min(len(closes), 220),
        "requiredSamples": 220,
    }
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
    values["navVolatility14"] = None
    nav_applicable = (
        price_basis == "UNIT_NAV"
        and str(subject_kind).lower() in {"nav", "fund"}
        and values["atr14"] is None
    )
    if (
        nav_applicable
        and not ohlc_conflict
        and len(closes) >= 20
        and deviation is not None
    ):
        nav_changes = [
            abs(_number(right["close"]) - _number(left["close"]))
            for left, right in zip(suffix[-15:], suffix[-14:])
        ]
        scale = _quantized(
            max(
                sum(nav_changes, Decimal(0)) / 14 * Decimal("1.4"),
                Decimal(str(deviation)) * Decimal("0.65"),
                close * Decimal("0.012"),
            ),
            4,
        )
        values["navVolatility14"] = float(scale) if scale is not None else None
    states["navVolatility14"] = {
        "state": "READY"
        if values["navVolatility14"] is not None
        else "CONFLICT"
        if nav_applicable and ohlc_conflict
        else "INSUFFICIENT"
        if nav_applicable
        else "NOT_APPLICABLE",
        "sampleCount": len(closes),
        "requiredSamples": 20,
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
            "Wilder RSI14不高于60",
            values["rsi14"] <= 60 if values["rsi14"] is not None else None,
        ),
    ]
    high_nav = (
        values["navPercentile1y"] is not None
        and values["navPercentile1y"] >= 85
        and close is not None
        and values["ma20"] is not None
        and close > Decimal(str(values["ma20"])) * Decimal("1.02")
    )
    tracks = []
    for kind, conditions in (
        ("LEFT_VALUE", left_conditions),
        ("RIGHT_TREND", right_conditions),
    ):
        known = [value for _, value in conditions if value is not None]
        candidate = (
            any(known)
            if kind == "LEFT_VALUE"
            else len(known) == 3 and all(known) and not high_nav
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
                else high_nav or any(value is False for value in known)
            )
            else "INSUFFICIENT"
        )
        zone, reasons = _zone(
            kind,
            values,
            close,
            places=2
            if str(subject_kind).lower() == "index"
            or price_basis in {"INDEX_POINTS", "INDEX_PRICE"}
            else 4,
            nav_scale=values["navVolatility14"] is not None,
        )
        missing = [name for name, value in conditions if value is None]
        low, high = (_number(zone[key]) for key in ("entryZoneMin", "entryZoneMax"))
        blocked = kind == "RIGHT_TREND" and high_nav
        in_zone = (
            close is not None
            and low is not None
            and high is not None
            and low <= close <= high
        )
        entry = {
            "technicalTriggered": state == "CANDIDATE",
            "inBuyZone": in_zone and not blocked,
            "entryReady": state == "CANDIDATE"
            and in_zone
            and zone["priceState"] == "READY"
            and not blocked,
            "entryBlocked": blocked,
        }
        if blocked:
            entry.update(
                entryBlockReason="HIGH_NAV_PERCENTILE",
                entryStatus=f"⚠️ 处于近1年高位区({values['navPercentile1y']:.0f}%分位)·勿追高",
                entryStatusTone="amber",
            )
            reasons.append(entry["entryStatus"])
        elif (
            kind == "RIGHT_TREND"
            and close is not None
            and high is not None
            and close > high
        ):
            entry.update(
                entryStatus=f"🚀 高于买区 {(close / high - 1) * 100:.2f}%·等待回踩均线",
                entryStatusTone="amber",
            )
        tracks.append(
            {
                "track": kind,
                "state": state,
                "candidate": state == "CANDIDATE",
                **zone,
                **entry,
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
        "volatilityMethod": "NAV_ABS_CHANGE_STD_V1"
        if values["navVolatility14"] is not None
        else "ATR_WILDER14"
        if values["atr14"] is not None
        else None,
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
        "currentPrice": _price(
            close,
            2
            if str(subject_kind).lower() == "index"
            or price_basis in {"INDEX_POINTS", "INDEX_PRICE"}
            else 4,
        ),
        "currentChangePct": nav_daily_change_pct(suffix) if nav_basis else None,
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
