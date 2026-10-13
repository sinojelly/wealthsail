"""Versioned historical fund research, expressed as decimal returns."""

import math
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from statistics import stdev
from typing import Any

VERSION = "performance-v2"
PARAMETERS = {
    "returnWindows": [63, 252],
    "volatilityWindow": 20,
    "annualizationSessions": 252,
    "minimumEligibleSamples": 253,
    "returnBasis": "adjusted_etf_or_verified_provider_return_index",
    "volatilityEstimator": "sample_stddev_simple_returns",
    "drawdownWindow": "entire_available_sample",
    "returnUnit": "decimal_fraction",
    "drawdownSign": "positive_loss_fraction",
    "drawdownArithmetic": "decimal_string_price_ratios",
}


def _number(value: Any, *, volume: bool = False) -> float:
    label = "volume" if volume else "price"
    if isinstance(value, bool):
        raise ValueError(f"invalid {label}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not math.isfinite(number) or (number < 0 if volume else number <= 0):
        raise ValueError(f"invalid {label}")
    return number


def _validated(points: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[float]]:
    days = []
    close = []
    previous = None
    for point in points:
        raw_day = point.get("date")
        try:
            day = raw_day if type(raw_day) is date else date.fromisoformat(raw_day)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid normalized date") from exc
        if type(raw_day) is not date and day.isoformat() != raw_day:
            raise ValueError("invalid normalized date")
        if previous is not None and day <= previous:
            raise ValueError("duplicate or unordered date")
        previous = day
        days.append(day.isoformat())
        close.append(_number(point.get("close")))
        for key in ("open", "high", "low"):
            if point.get(key) is not None:
                _number(point[key])
        if point.get("volume") is not None:
            _number(point["volume"], volume=True)
    return days, close


def analyze(
    points: Sequence[Mapping[str, Any]],
    *,
    series_kind: str,
    quality: str,
    return_index_verified: bool = False,
) -> dict[str, Any]:
    """Describe normalized, ascending observations without inventing sessions.

    Windows count real observations, not calendar days. The caller must supply
    a quality state that accounts for missing sessions and source verification;
    only READY histories with all required windows are eligible for screening.
    ETF_HFQ uses the provider's dividend/split adjustment convention and is a
    research proxy before trading fees, taxes and ETF tracking costs.
    """
    days, close = _validated(points)
    count = len(close)
    supported = series_kind == "ETF_HFQ" or (
        series_kind == "PROVIDER_RETURN_INDEX" and return_index_verified is True
    )
    result = {
        "return63": None,
        "return252": None,
        "maxDrawdown": None,
        "volatility20": None,
        "seriesKind": series_kind,
        "eligible": False,
        "state": "UNSUPPORTED",
        "sampleCount": count,
        "historyStart": days[0] if count else None,
        "historyEnd": days[-1] if count else None,
        "algorithmVersion": VERSION,
        "parameters": {
            **PARAMETERS,
            "returnWindows": list(PARAMETERS["returnWindows"]),
        },
        "reasons": [],
    }
    if not supported:
        result["reasons"] = [
            "供应商收益指数尚未经登记确认，不能计算分红再投收益或参与筛选。"
            if series_kind == "PROVIDER_RETURN_INDEX"
            else "价格走势、单位净值或累计净值不等同于分红再投总收益，收益及风险指标不可用。"
        ]
        return result
    max_drawdown = None
    if count >= 2:
        peak = Decimal(str(close[0]))
        loss = Decimal(0)
        for value in close[1:]:
            price = Decimal(str(value))
            peak = max(peak, price)
            loss = max(loss, 1 - price / peak)
        max_drawdown = float(loss)
    returns = [b / a - 1 for a, b in zip(close, close[1:])]
    if any(not math.isfinite(value) for value in returns):
        raise ValueError("returns must remain finite")
    result.update(
        return63=close[-1] / close[-64] - 1 if count >= 64 else None,
        return252=close[-1] / close[-253] - 1 if count >= 253 else None,
        maxDrawdown=max_drawdown,
        volatility20=stdev(returns[-20:]) * math.sqrt(252) if count >= 21 else None,
    )
    if any(
        result[key] is not None and not math.isfinite(result[key])
        for key in ("return63", "return252", "maxDrawdown", "volatility20")
    ):
        raise ValueError("performance metrics must remain finite")
    if quality != "READY":
        result["state"] = quality
        result["reasons"].append(
            f"历史数据质量为 {quality}，仅供描述性研究，不参与筛选。"
        )
    elif count < 253:
        result["state"] = "INSUFFICIENT"
        result["reasons"].append(
            "不足 253 个交易观察值，无法提供完整的 252 日收益研究。"
        )
    else:
        result["state"] = "READY"
        result["eligible"] = True
    return result
