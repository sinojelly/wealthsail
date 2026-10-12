"""Versioned technical research; a score is not a probability or trade signal."""

import math
from statistics import stdev

VERSION = "technical-v1"
PARAMETERS = {
    "ma": [20, 60, 200],
    "macd": [12, 26, 9],
    "kdj": [9, 3, 3],
    "rsi": 14,
    "atr": 14,
}


def _ema(values, period):
    result = [values[0]]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result.append(result[-1] + alpha * (value - result[-1]))
    return result


def _wilder(values, period=14):
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = (value * (period - 1) + item) / period
    return value


def calculate(points):
    if not points:
        return {}
    close = [float(p["close"]) for p in points]
    result = {
        f"ma{n}": sum(close[-n:]) / n if len(close) >= n else None
        for n in (20, 60, 200)
    }
    fast, slow = _ema(close, 12), _ema(close, 26)
    dif = [a - b for a, b in zip(fast, slow)]
    dea = _ema(dif, 9)
    result.update(
        dif=dif[-1] if len(close) >= 26 else None,
        dea=dea[-1] if len(close) >= 35 else None,
        macd=2 * (dif[-1] - dea[-1]) if len(close) >= 35 else None,
        previousMacd=2 * (dif[-2] - dea[-2]) if len(close) >= 36 else None,
        momentum63=close[-1] / close[-64] - 1 if len(close) >= 64 else None,
    )
    changes = [b - a for a, b in zip(close, close[1:])]
    gains, losses = (
        _wilder([max(0, x) for x in changes]),
        _wilder([max(0, -x) for x in changes]),
    )
    result["rsi14"] = (
        None
        if gains is None
        else 50
        if gains == losses == 0
        else 100
        if losses == 0
        else 100 - 100 / (1 + gains / losses)
    )
    returns = [b / a - 1 for a, b in zip(close, close[1:])]
    result["volatility20"] = (
        stdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 20 else None
    )
    result.update(k=None, d=None, j=None, atr14=None)
    if all(p.get("high") is not None and p.get("low") is not None for p in points):
        k = d = 50.0
        for i in range(8, len(points)):
            high = max(float(p["high"]) for p in points[i - 8 : i + 1])
            low = min(float(p["low"]) for p in points[i - 8 : i + 1])
            rsv = 50 if high == low else (close[i] - low) / (high - low) * 100
            k = 2 * k / 3 + rsv / 3
            d = 2 * d / 3 + k / 3
        if len(points) >= 9:
            result.update(k=k, d=d, j=3 * k - 2 * d)
        tr = [
            max(
                float(p["high"]) - float(p["low"]),
                abs(float(p["high"]) - close[i - 1]),
                abs(float(p["low"]) - close[i - 1]),
            )
            for i, p in enumerate(points)
            if i > 0
        ]
        result["atr14"] = _wilder(tr)
    return result


def analyze(points, *, quality):
    invalid = any(
        not math.isfinite(float(p["close"]))
        or float(p["close"]) <= 0
        or (
            p.get("high") is not None
            and p.get("low") is not None
            and not (
                float(p["low"]) > 0
                and float(p["low"]) <= float(p["close"]) <= float(p["high"])
            )
        )
        for p in points
    )
    indicators = {} if invalid else calculate(points)
    result = {
        "score": None,
        "state": "CONFLICT" if invalid else quality,
        "indicators": indicators,
        "reasons": [],
        "algorithmVersion": VERSION,
        "parameters": PARAMETERS,
    }
    if invalid or quality != "READY":
        return result
    if len(points) < 200:
        result["state"] = "INSUFFICIENT"
        return result
    if indicators["k"] is None:
        result["state"] = "UNSUPPORTED"
        return result
    x = indicators
    tests = [
        (
            "close_ma200",
            "收盘价高于 MA200",
            float(points[-1]["close"]) > x["ma200"],
            25,
        ),
        ("ma60_ma200", "MA60 高于 MA200", x["ma60"] > x["ma200"], 15),
        ("ma20_ma60", "MA20 高于 MA60", x["ma20"] > x["ma60"], 10),
        ("momentum63", "63 日动量为正", x["momentum63"] > 0, 15),
        ("macd_trend", "DIF 高于 DEA", x["dif"] > x["dea"], 10),
        ("macd_improving", "MACD 柱值较前日改善", x["macd"] > x["previousMacd"], 5),
        (
            "kdj_support",
            "K>D 且 K 位于 20–80",
            x["k"] > x["d"] and 20 <= x["k"] <= 80,
            10,
        ),
        ("rsi_support", "RSI 位于 40–70", 40 <= x["rsi14"] <= 70, 10),
    ]
    result["reasons"] = [
        {
            "key": key,
            "reason": reason,
            "passed": passed,
            "weight": weight,
            "points": weight if passed else 0,
        }
        for key, reason, passed, weight in tests
    ]
    result["score"] = sum(r["points"] for r in result["reasons"])
    result["state"] = (
        "STRONG"
        if result["score"] >= 75
        else "MIXED"
        if result["score"] >= 55
        else "WEAK"
    )
    return result
