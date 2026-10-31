"""Versioned, deterministic research coordinates for two independent tracks."""

from copy import deepcopy
from datetime import date, datetime
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    localcontext,
)
import math
from statistics import pstdev

VERSION = "actionable-zones-v6"
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
    "historicalPriceWindow": [120, 500],
    "historicalPercentileMethod": "LINEAR_INTERPOLATION",
    "middleHighNavPercentile": 65,
    "middleHighPriceSpanRatio": 0.60,
    "maximumHighBuyPricePercentile": 45,
    "maximumLowBuyPricePercentile": 40,
    "maximumHighBuyPriceSpanRatio": 0.45,
    "maximumLowBuyPriceSpanRatio": 0.35,
    "sellZoneLowerPricePercentile": 75,
    "sellZoneUpperPricePercentile": 92,
    "minimumSellPriceSpanRatio": 0.60,
    "minimumTargetProfitRatio": 0.12,
    "historicalPeakSellCapRatio": 0.992,
    "navRewardRiskBasis": "TWO_YEAR_SPAN_SUPPORT_RESISTANCE_PEAK_CAPPED",
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
    "relativeWeaknessConfirmationDays": 3,
    "industryLeftDiscountScale": 1.0,
}

INDUSTRY_RISK_WARNING = "⚠️ 行业去泡沫/等待订单期 · 谨防左侧接飞刀"

HISTORICAL_PERCENTILES = (30, 35, 40, 45, 50, 55, 60, 65, 75, 85, 92, 95)


def _historical_nav_anchors(points):
    """Linear percentiles of the real ordered suffix, never a synthetic year."""
    if len(points) < 120:
        return {}
    prices = sorted(_number(point["close"]) for point in points[-500:])
    anchors = {}
    for percentile in HISTORICAL_PERCENTILES:
        index = Decimal(len(prices) - 1) * Decimal(percentile) / 100
        lower_index = int(index)
        upper_index = min(lower_index + 1, len(prices) - 1)
        anchors[f"p{percentile}"] = prices[lower_index] + (
            prices[upper_index] - prices[lower_index]
        ) * (index - lower_index)
    anchors["peakHigh"] = prices[-1]
    anchors["troughLow"] = prices[0]
    anchors["priceSpan"] = prices[-1] - prices[0]
    return anchors


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


def _quantized(value, places, *, rounding=ROUND_HALF_UP):
    value = _number(value)
    if value is None or value <= 0:
        return None
    with localcontext() as context:
        context.prec = max(28, value.adjusted() + places + 2)
        value = value.quantize(Decimal(1).scaleb(-places), rounding=rounding)
    return value if value > 0 else None


def _price(value, places=4):
    value = _quantized(value, places)
    return format(value, "f") if value is not None else None


def _profit_percentage(buy, sell):
    with localcontext() as context:
        context.prec = max(
            28,
            buy.adjusted() + 10,
            sell.adjusted() + 10,
            sell.adjusted() - buy.adjusted() + 10,
        )
        value = ((sell - buy) / buy * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return format(value.copy_abs() if value == 0 else value, "f")


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


def _ordered_price_suffix(points, cutoff=None):
    rows, previous, conflict = [], None, False
    for point in points or []:
        day = _day(point.get("date"))
        if cutoff and day and day > cutoff:
            continue
        if day is None or previous is not None and day <= previous:
            conflict = True
        previous = day
        rows.append(point)
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
    return ([] if conflict else suffix), conflict


def _relative_strength(points, benchmark_points, cutoff=None):
    """Return percentage-point excess only for complete matching trading dates."""
    if points:
        latest = _day(points[-1]["date"])
        cutoff = min(cutoff, latest) if cutoff else latest
    benchmark, conflict = _ordered_price_suffix(benchmark_points, cutoff)
    result = {
        "state": "INSUFFICIENT",
        "benchmark": "沪深300",
        "asOf": _day(points[-1]["date"]).isoformat() if points else None,
        "excessReturn20d": None,
        "excessReturn60d": None,
        "excessReturnUnit": "PERCENTAGE_POINTS",
        "drawdown60d": None,
        "drawdownUnit": "PERCENT",
        "breakdownDate": None,
        "weakening": None,
        "lowerHighs": None,
        "trendWeakening": None,
        "consecutiveWeakDays": None,
        "sampleCount": len(points),
        "benchmarkSampleCount": len(benchmark),
        "reasons": [],
        "missingEvidence": [],
    }
    prices = [_number(point["close"]) for point in points]
    if len(prices) >= 60:
        result["drawdown60d"] = float((1 - prices[-1] / max(prices[-60:])) * 100)
        result["lowerHighs"] = (
            max(prices[-60:-40]) > max(prices[-40:-20]) > max(prices[-20:])
        )
    else:
        result["missingEvidence"].append("近60日高点回落幅度需要60个连续有效样本。")
    if not points or not benchmark or conflict:
        result["missingEvidence"].append(
            "缺少连续有效、日期有序的沪深300基准或基金价格。"
        )
        return result
    fund_dates = [_day(point["date"]) for point in points]
    benchmark_dates = [_day(point["date"]) for point in benchmark]
    if fund_dates[-1] != benchmark_dates[-1]:
        result["missingEvidence"].append(
            "基金与沪深300最新交易日期不同，未对旧基准补值。"
        )
        return result
    benchmark_by_day = {
        day: _number(point["close"]) for day, point in zip(benchmark_dates, benchmark)
    }

    def aligned_at(index, period):
        start = index - period
        if start < 0:
            return None
        dates = fund_dates[start : index + 1]
        matched = [day for day in benchmark_dates if dates[0] <= day <= dates[-1]]
        if dates != matched:
            return None
        return (prices[index] / prices[start] - 1) * 100 - (
            benchmark_by_day[dates[-1]] / benchmark_by_day[dates[0]] - 1
        ) * 100

    for period in (20, 60):
        excess = aligned_at(len(prices) - 1, period)
        numeric = _number(excess)
        result[f"excessReturn{period}d"] = (
            float(numeric) if numeric is not None else None
        )
        if excess is None:
            result["missingEvidence"].append(
                f"{period}日超额收益缺少{period + 1}个同日对齐的连续有效基金/沪深300样本。"
            )
        elif numeric is None:
            result["missingEvidence"].append(f"{period}日超额收益超出可表示数值范围。")
    if any(result[f"excessReturn{period}d"] is None for period in (20, 60)):
        return result
    lower_high_risk = (
        result["lowerHighs"] is True
        and result["excessReturn20d"] < 0
        and result["excessReturn60d"] < 0
    )
    if lower_high_risk:
        result["trendWeakening"] = True
        result["reasons"].append(
            "近60个交易日三个连续20日窗口反弹高点依次下移，且20日/60日均跑输沪深300，触发技术趋势风控。"
        )
    if len(prices) < 63 or any(
        aligned_at(index, 60) is None
        for index in range(max(0, len(prices) - 3), len(prices))
    ):
        result["missingEvidence"].append("连续走弱确认需要至少63个同日对齐样本。")
        return result
    moving_averages = [
        sum(prices[index - 59 : index + 1], Decimal(0)) / 60 if index >= 59 else None
        for index in range(len(prices))
    ]
    consecutive = 0
    for index in range(len(prices) - 1, 59, -1):
        excess20, excess60 = aligned_at(index, 20), aligned_at(index, 60)
        if (
            excess20 is None
            or excess60 is None
            or excess20 >= 0
            or excess60 >= 0
            or prices[index] >= moving_averages[index]
        ):
            break
        consecutive += 1
    declining = all(
        prices[index] < prices[index - 1]
        and moving_averages[index] < moving_averages[index - 1]
        for index in range(len(prices) - 2, len(prices))
    )
    result.update(
        state="READY",
        consecutiveWeakDays=consecutive,
        weakening=consecutive >= 3 and declining,
        trendWeakening=consecutive >= 3 and declining or lower_high_risk,
    )
    if result["weakening"]:
        # A recovered earlier crossing cannot be the start of the current leg.
        for index in range(len(prices) - 1, 59, -1):
            if (
                prices[index] < moving_averages[index]
                and prices[index - 1] >= moving_averages[index - 1]
            ):
                result["breakdownDate"] = fund_dates[index].isoformat()
                break
        result["reasons"].append(
            "最近3个交易观察日20日/60日超额均为负、收盘均低于MA60，且收盘与MA60持续下行。"
        )
        if result["breakdownDate"]:
            result["reasons"].append(
                f"本轮走弱分水岭日期：{result['breakdownDate']}（真实收盘跌破MA60）。"
            )
        else:
            result["missingEvidence"].append(
                "当前连续样本未覆盖本轮跌破MA60的交叉，分水岭日期未知。"
            )
    else:
        result["reasons"].append(
            "尚未同时满足连续跑输沪深300、位于MA60下方且持续下行的3日确认条件。"
        )
    return result


def _zone(
    kind,
    values,
    close,
    *,
    places,
    nav_scale=False,
    nav_basis=False,
    nav_history=None,
    discount_scale=0.0,
):
    result = {
        "entryZoneMin": None,
        "entryZoneMax": None,
        "stopLossPrice": None,
        "takeProfitT1": None,
        "takeProfitT2": None,
        "sellZoneMin": None,
        "sellZoneMax": None,
        "estimatedProfitMinPct": None,
        "estimatedProfitMaxPct": None,
        "currentToSellMaxPct": None,
        "buyZoneAnchor": None,
        "priceState": "INSUFFICIENT",
    }
    reasons = []
    ma20, lower, atr = (
        _number(values.get(key)) for key in ("ma20", "bollLower", "atr14")
    )
    scale = _number(values.get("navVolatility14")) if nav_scale else atr
    historical = bool(nav_history) and scale is not None
    if close is None or ma20 is None or lower is None:
        return result, ["入场参考区间缺少有效收盘、MA20或布林带。"]
    if historical:
        ma60, ma200 = (_number(values.get(key)) for key in ("ma60", "ma200"))
        trough, span = (nav_history[key] for key in ("troughLow", "priceSpan"))
        middle_high = (
            values.get("navPercentile1y") is not None
            and values["navPercentile1y"] >= 65
        ) or close > trough + Decimal("0.60") * span
        if middle_high:
            maximum = min(
                ma60,
                ma200 or ma60,
                nav_history["p45"],
                trough + Decimal("0.45") * span,
            )
            minimum = max(
                trough,
                min(
                    maximum - Decimal("0.8") * scale,
                    nav_history["p30"],
                    trough + Decimal("0.32") * span,
                ),
            )
            result["buyZoneAnchor"] = "TWO_YEAR_LOWER_SUPPORT"
            reasons.append(
                "中高位买区锚定近两年下半区支撑与MA60/MA200，不随MA20追高；"
                "上沿同时受45%历史分位价和周期跨度45%位置约束。"
            )
        else:
            maximum = min(
                ma20,
                close + Decimal("0.3") * scale,
                nav_history["p40"],
                trough + Decimal("0.35") * span,
            )
            minimum = max(
                trough * Decimal("0.98"),
                min(
                    lower,
                    close - Decimal("0.6") * scale,
                    maximum - Decimal("0.6") * scale,
                ),
            )
            result["buyZoneAnchor"] = "TWO_YEAR_LOW_SUPPORT"
            reasons.append(
                "中低位买区采用布林下轨与现价回落参考，上沿不超过近两年40%历史分位价"
                "及周期跨度35%位置，下沿不低于周期低点的98%。"
            )
        result["priceState"] = "READY"
    elif nav_history and scale is None:
        ma60, ma200 = (_number(values.get(key)) for key in ("ma60", "ma200"))
        maximum = min(
            ma60,
            ma200 or ma60,
            nav_history["p45"],
            nav_history["troughLow"] + Decimal("0.45") * nav_history["priceSpan"],
        )
        minimum = min(nav_history["p30"], maximum)
        result["buyZoneAnchor"] = "TWO_YEAR_LOWER_SUPPORT"
        result["priceState"] = "PARTIAL"
        reasons.append(
            "缺少可用风险尺度，仅保留中期均线与真实历史支撑参考；不计算止损和卖区。"
        )
    elif scale is None:
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
    if discount_scale and kind == "LEFT_VALUE":
        if scale is None:
            result["priceState"] = "PARTIAL"
            return result, reasons + [
                "行业降温但缺少有效风险尺度，未展示未经折价保护的左侧买区。"
            ]
        discount = Decimal(str(discount_scale)) * scale
        minimum -= discount
        maximum -= discount
        reasons.append(
            f"产业风险保护：左侧原支撑买区额外下移{discount_scale:.1f}倍风险尺度，止损与收益按折价后区间重新计算。"
        )
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
        if historical:
            stop = minimum - 2 * scale
            trough, span = (nav_history[key] for key in ("troughLow", "priceSpan"))
            sell_min_target = max(
                maximum * Decimal("1.12"),
                ma200 or Decimal(0),
                nav_history["p75"],
                trough + Decimal("0.60") * span,
            )
            sell_cap = nav_history["peakHigh"] * Decimal("0.992")
            second = min(
                sell_cap,
                max(
                    sell_min_target + Decimal("1.2") * scale,
                    nav_history["p92"],
                    trough + Decimal("0.82") * span,
                ),
            )
            first = min(sell_min_target, second - Decimal("0.8") * scale)
            reasons.append(
                "卖区下沿目标取买区上沿至少12%空间、MA200、近两年75%分位价与周期跨度60%"
                "位置的最大值；上沿结合92%分位价与跨度82%位置，并严格封顶于周期最高价的99.2%。"
            )
            reasons.append(
                "历史跨度分支不套用旧1.5R/2.4R抬价下限；止损独立低于买区，预估盈利仅按区间测算。"
            )
        elif nav_scale:
            stop = (minimum if kind == "LEFT_VALUE" else ma20) - 2 * scale
            first = max(
                ma60 or maximum + 2 * scale,
                maximum + 2 * scale,
            )
            second = max(
                _number(values.get("bollUpper")) or first + scale,
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
        if historical and second is not None:
            # A half-up display must never push the band above the actual cap.
            capped = _quantized(sell_cap, places, rounding=ROUND_FLOOR)
            second = min(second, capped) if capped is not None else None
        if stop is not None and stop < rounded_minimum:
            result["stopLossPrice"] = _price(stop, places)
        else:
            reasons.append("止损在舍入后非正或未低于入场区，止损不可用。")
        if stop is not None and stop < rounded_minimum and not historical:
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
        if (
            first is not None
            and first > rounded_maximum
            and second is not None
            and second > first
        ):
            result["takeProfitT1"] = _price(first, places)
            result["takeProfitT2"] = _price(second, places)
            if historical:
                result["estimatedProfitMinPct"] = _profit_percentage(
                    rounded_maximum, first
                )
                result["estimatedProfitMaxPct"] = _profit_percentage(
                    rounded_minimum, second
                )
                displayed_close = _quantized(close, places)
                if displayed_close is not None and displayed_close < rounded_maximum:
                    result["currentToSellMaxPct"] = _profit_percentage(
                        displayed_close, second
                    )
                if first < rounded_maximum * Decimal("1.12"):
                    result["priceState"] = "PARTIAL"
                    reasons.append(
                        "历史高点封顶后无法同时满足12%保底波段空间，保留客观区间但不构成可执行入场条件。"
                    )
        if not result["takeProfitT1"] or not result["takeProfitT2"]:
            reasons.append("卖区经周期高点封顶或舍入后未依次高于买区，未制造替代目标。")
        if any(
            result[key] is None
            for key in ("stopLossPrice", "takeProfitT1", "takeProfitT2")
        ):
            result["priceState"] = "PARTIAL"
    if nav_basis and not nav_history:
        if result["priceState"] == "READY":
            result["priceState"] = "PARTIAL"
        result["buyZoneAnchor"] = "SHORT_HISTORY_REFERENCE"
        reasons.append(
            "不足120个连续有效交易样本，历史买卖锚点不足；现有短窗价格仅作参考，不构成入场条件。"
        )
    result["sellZoneMin"] = result["takeProfitT1"]
    result["sellZoneMax"] = result["takeProfitT2"]
    return result, reasons


_PRICE_FIELDS = (
    "entryZoneMin",
    "entryZoneMax",
    "stopLossPrice",
    "takeProfitT1",
    "takeProfitT2",
    "sellZoneMin",
    "sellZoneMax",
    "estimatedProfitMinPct",
    "estimatedProfitMaxPct",
    "currentToSellMaxPct",
    "buyZoneAnchor",
    "priceState",
)
_ENTRY_FIELDS = (
    "technicalTriggered",
    "inBuyZone",
    "entryReady",
    "entryBlocked",
    "inSellZone",
    "entryBlockReason",
    "entryStatus",
    "entryStatusTone",
)


def _refresh_signals_and_selected(plan):
    """Keep already-projected scanner and selected-track coordinates consistent."""
    top_guard = (
        {
            key: deepcopy(plan[key])
            for key in (*_ENTRY_FIELDS, "preferredEntryEligible")
            if key in plan
        }
        if plan.get("entryBlocked") is True
        else {}
    )
    if "signals" in plan:
        refreshed = []
        current = _number(plan.get("currentPrice"))
        for signal in plan.get("signals", []):
            track = next(
                (
                    row
                    for row in plan.get("tracks", [])
                    if row.get("track") == signal.get("track")
                ),
                None,
            )
            if track is None:
                continue
            low, high = (
                _number(track.get(key)) for key in ("entryZoneMin", "entryZoneMax")
            )
            if (
                current is None
                or low is None
                or high is None
                or not 0 < low <= high
                or current <= 0
            ):
                continue
            in_zone = low <= current <= high
            distance = (
                (low - current) / current
                if current < low
                else (current - high) / current
                if current > high
                else Decimal(0)
            )
            candidate = track.get("state") == "CANDIDATE"
            ready = (
                candidate
                and in_zone
                and track.get("priceState") == "READY"
                and not any(
                    row.get("entryReady") is False
                    or row.get("entryBlocked") is True
                    or row.get("inSellZone") is True
                    or row.get("subscriptionRestricted") is True
                    for row in (track,)
                )
                and not any(
                    plan.get(key) is True
                    for key in ("entryBlocked", "inSellZone", "subscriptionRestricted")
                )
            )
            new_signal = deepcopy(signal)
            for key in _ENTRY_FIELDS:
                new_signal.pop(key, None)
            new_signal.update(
                {key: deepcopy(track[key]) for key in _ENTRY_FIELDS if key in track}
            )
            new_signal.update(
                triggerState=track.get("state"),
                candidate=candidate,
                inEntryZone=in_zone,
                entryReady=ready,
                distanceToEntryPct=round(float(distance * 100), 4),
            )
            if top_guard:
                new_signal.update(top_guard)
                new_signal.update(entryReady=False, inBuyZone=False)
            refreshed.append(new_signal)
        plan["signals"] = refreshed
    selected = plan.get("selectedTrack", plan.get("track"))
    chosen = next(
        (row for row in plan.get("tracks", []) if row.get("track") == selected), None
    )
    if chosen is not None:
        for key in (
            *_PRICE_FIELDS,
            *_ENTRY_FIELDS,
            "state",
            "candidate",
            "reasons",
            "missingEvidence",
        ):
            plan.pop(key, None)
            if key in chosen:
                plan[key] = deepcopy(chosen[key])
        plan.update(top_guard)


def _external_left_guard(track, baseline):
    fields = {}
    if (
        track.get("entryBlocked") is True
        and track.get("entryBlockReason") != "IN_SELL_ZONE"
        or track.get("subscriptionRestricted") is True
        or track.get("preferredEntryEligible") is False
        and track.get("entryBlockReason") != "IN_SELL_ZONE"
    ):
        fields = {
            key: deepcopy(track[key])
            for key in (*_ENTRY_FIELDS, "preferredEntryEligible")
            if key in track
        }
        fields.update(entryBlocked=True, entryReady=False, inBuyZone=False)
    if (
        track.get("priceState") not in {None, "READY"}
        and baseline.get("priceState") == "READY"
    ):
        fields.update(priceState=track["priceState"], entryReady=False)
    return fields


def apply_industry_cycle(plan, industry_cycle):
    """Apply once from saved server anchors, never from an already reduced band."""
    from backend.macro.industry_cycle import industry_risk_policy

    result = deepcopy(plan)
    result["industryCycle"] = (
        deepcopy(industry_cycle) if isinstance(industry_cycle, dict) else None
    )
    policy = industry_risk_policy(industry_cycle)
    base_discount_scale = policy["leftDiscountScale"]
    relative_upgrade = (
        base_discount_scale == 0.5
        and (result.get("relativeStrength") or {}).get("weakening") is True
    )
    discount_scale = 1.0 if relative_upgrade else base_discount_scale
    risk = discount_scale > 0
    warning = policy.get("industryRiskWarning")
    base = result.get("industryAdjustmentBase")
    current_left = next(
        (row for row in result.get("tracks", []) if row.get("track") == "LEFT_VALUE"),
        None,
    )
    external_guard = _external_left_guard(
        current_left or {},
        (base or {}).get("leftTrack", {}) if isinstance(base, dict) else {},
    )
    result["riskAdjustmentVersion"] = "industry-left-discount-v2"
    result.pop("industryRiskWarning", None)
    result.pop("industryRiskAdjustment", None)
    if current_left is None:
        return result
    if not isinstance(base, dict) or not isinstance(base.get("leftTrack"), dict):
        if not risk:
            return result
        # Historical snapshots without unrounded server anchors cannot safely
        # be discounted or reused as today's actionable left-side reference.
        adjusted = deepcopy(current_left)
        for key in _PRICE_FIELDS:
            adjusted[key] = None
        adjusted.update(priceState="PARTIAL", inBuyZone=False, entryReady=False)
        adjusted["reasons"] = list(
            dict.fromkeys(
                [
                    *adjusted.get("reasons", []),
                    "缺少原始服务器锚点，行业折价无法验证，暂不提供左侧入场价格。",
                ]
            )
        )
        applied = False
    else:
        adjusted = deepcopy(current_left)
        baseline = base["leftTrack"]
        for key in (
            *_PRICE_FIELDS,
            *_ENTRY_FIELDS,
            "state",
            "candidate",
            "reasons",
            "missingEvidence",
        ):
            adjusted.pop(key, None)
            if key in baseline:
                adjusted[key] = deepcopy(baseline[key])
        if not risk:
            adjusted.update(external_guard)
            result["tracks"] = [
                adjusted if row.get("track") == "LEFT_VALUE" else row
                for row in result["tracks"]
            ]
            _refresh_signals_and_selected(result)
            return result
        close = _number(base.get("close"))
        values = result.get("indicators") or {}
        history = {
            key: _number(value) for key, value in (base.get("navHistory") or {}).items()
        }
        zone, reasons = _zone(
            "LEFT_VALUE",
            values,
            close,
            places=base.get("places", 4),
            nav_scale=base.get("navScale", False),
            nav_basis=base.get("navBasis", False),
            nav_history=history,
            discount_scale=discount_scale,
        )
        adjusted.update(zone)
        adjusted["reasons"] = [*base.get("conditionReasons", []), *reasons]
        if relative_upgrade:
            adjusted["reasons"].append(
                "产业线索与相对沪深300持续走弱共振，左侧风险折价由0.5倍升级为1.0倍。"
            )
        low, high, sell = (
            _number(zone.get(key))
            for key in ("entryZoneMin", "entryZoneMax", "sellZoneMin")
        )
        percentile = values.get("navPercentile1y")
        in_sell = bool(
            base.get("navBasis")
            and (
                close is not None
                and sell is not None
                and close >= sell
                or percentile is not None
                and percentile >= 85
            )
        )
        candidate = base.get(
            "technicalCandidate", baseline.get("technicalTriggered", False)
        )
        state = (
            "WATCH"
            if in_sell and candidate
            else "CANDIDATE"
            if candidate
            else baseline.get("state", "INSUFFICIENT")
        )
        in_zone = (
            close is not None
            and low is not None
            and high is not None
            and low <= close <= high
        )
        adjusted.update(
            state=state,
            candidate=state == "CANDIDATE",
            technicalTriggered=state == "CANDIDATE",
            inBuyZone=in_zone and not in_sell,
            entryReady=state == "CANDIDATE"
            and in_zone
            and zone["priceState"] == "READY"
            and not in_sell,
            inSellZone=in_sell,
            entryBlocked=in_sell,
        )
        if in_sell:
            adjusted["entryBlockReason"] = "IN_SELL_ZONE"
        else:
            adjusted.pop("entryBlockReason", None)
        applied = (
            low is not None
            and high is not None
            and _number(base.get("scale")) is not None
        )
    result["industryRiskWarning"] = warning
    result["industryRiskAdjustment"] = {
        "state": "APPLIED" if applied else "INSUFFICIENT",
        "riskLevel": policy["riskLevel"],
        "entryShiftScale": discount_scale,
        "baseEntryShiftScale": base_discount_scale,
        "relativeStrengthUpgrade": relative_upgrade,
        "scale": base.get("scale") if isinstance(base, dict) else None,
    }
    adjusted.update(entryStatus=warning, entryStatusTone="orange")
    adjusted.update(external_guard)
    result["tracks"] = [
        adjusted if row.get("track") == "LEFT_VALUE" else row
        for row in result["tracks"]
    ]
    _refresh_signals_and_selected(result)
    return result


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
    benchmark_points=None,
    industry_cycle=None,
):
    """Calculate from the ordered real suffix; never substitute another asset."""
    cutoff = _day(as_of) if as_of is not None else None
    # An invalid observation breaks continuity; do not remove it and join days.
    suffix, order_conflict = _ordered_price_suffix(points, cutoff)
    closes = [float(_number(point["close"])) for point in suffix]
    close = _number(suffix[-1]["close"]) if suffix else None
    values, states = {}, {}
    nav_basis = price_basis == "UNIT_NAV" and str(subject_kind).lower() in {
        "nav",
        "fund",
    }
    historical_points = suffix[-500:] if nav_basis else []
    nav_history = _historical_nav_anchors(historical_points) if nav_basis else {}
    for key in [
        *(f"p{percentile}" for percentile in HISTORICAL_PERCENTILES),
        "peakHigh",
        "troughLow",
        "priceSpan",
    ]:
        value = nav_history.get(key)
        values[key] = float(value) if value is not None else None
        states[key] = {
            "state": "READY"
            if value is not None
            else "INSUFFICIENT"
            if nav_basis
            else "NOT_APPLICABLE",
            "sampleCount": len(historical_points),
            "requiredSamples": 120,
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
    relative_strength = _relative_strength(suffix, benchmark_points, cutoff)
    for key, samples in (
        ("excessReturn20d", 21),
        ("excessReturn60d", 61),
        ("drawdown60d", 60),
    ):
        values[key] = relative_strength[key]
        states[key] = {
            "state": "READY" if values[key] is not None else "INSUFFICIENT",
            "sampleCount": len(suffix),
            "requiredSamples": samples,
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
    left_base = None
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
            nav_basis=nav_basis,
            nav_history=nav_history,
        )
        missing = [name for name, value in conditions if value is None]
        low, high = (_number(zone[key]) for key in ("entryZoneMin", "entryZoneMax"))
        sell_minimum = _number(zone["sellZoneMin"])
        in_sell_zone = nav_basis and (
            close is not None
            and sell_minimum is not None
            and close >= sell_minimum
            or values["navPercentile1y"] is not None
            and values["navPercentile1y"] >= 85
        )
        blocked = in_sell_zone or kind == "RIGHT_TREND" and high_nav
        if in_sell_zone and state == "CANDIDATE":
            state = "WATCH"
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
            "inSellZone": in_sell_zone,
        }
        if in_sell_zone:
            percentile = values["navPercentile1y"]
            percentile_label = (
                f"{percentile:.0f}%分位" if percentile is not None else "分位未知"
            )
            entry.update(
                entryBlockReason="IN_SELL_ZONE",
                entryStatus=f"🔥 已入建议卖出区间({percentile_label})·持仓宜止盈/空仓勿追高",
                entryStatusTone="rose",
            )
            if percentile is not None and percentile >= 85:
                reasons.append(
                    "近1年净值分位达到85%，触发卖出观察条件；该分位条件不等同于现价已落在价格带内。"
                )
            if close is not None and sell_minimum is not None and close >= sell_minimum:
                reasons.append("当前净值已达到建议卖出区间下沿。")
        elif blocked:
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
                entryStatus=f"🚀 高于买区 {(close / high - 1) * 100:.2f}%·等待回落买区"
                if nav_history
                else f"🚀 高于买区 {(close / high - 1) * 100:.2f}%·等待回踩均线",
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
        if kind == "LEFT_VALUE":
            left_base = {
                "leftTrack": deepcopy(tracks[-1]),
                "close": str(close) if close is not None else None,
                "scale": str(
                    _number(values.get("navVolatility14") or values.get("atr14"))
                )
                if values.get("navVolatility14") is not None
                or values.get("atr14") is not None
                else None,
                "navScale": values["navVolatility14"] is not None,
                "navBasis": nav_basis,
                "navHistory": {key: str(value) for key, value in nav_history.items()},
                "places": 2
                if str(subject_kind).lower() == "index"
                or price_basis in {"INDEX_POINTS", "INDEX_PRICE"}
                else 4,
                "technicalCandidate": candidate,
                "conditionReasons": [
                    name + ("：满足" if value else "：未触发")
                    for name, value in conditions
                    if value is not None
                ]
                + relative_strength["reasons"],
            }
        tracks[-1]["reasons"].extend(relative_strength["reasons"])
        if left_base is not None and kind == "LEFT_VALUE":
            left_base["leftTrack"] = deepcopy(tracks[-1])
    if order_conflict:
        for item in states.values():
            if item["state"] != "UNAVAILABLE":
                item["state"] = "CONFLICT"
    result = {
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
        "historicalPriceWindow": {
            "state": "CONFLICT"
            if order_conflict
            else "READY"
            if nav_history
            else "INSUFFICIENT"
            if nav_basis
            else "NOT_APPLICABLE",
            "sampleCount": len(historical_points),
            "requiredSamples": 120,
            "start": _day(historical_points[0]["date"]).isoformat()
            if historical_points
            else None,
            "end": _day(historical_points[-1]["date"]).isoformat()
            if historical_points
            else None,
        },
        "indicators": values,
        "indicatorStates": states,
        "relativeStrength": relative_strength,
        "trendWeakening": relative_strength["trendWeakening"],
        "industryAdjustmentBase": left_base,
        "tracks": tracks,
    }
    return apply_industry_cycle(result, industry_cycle)
