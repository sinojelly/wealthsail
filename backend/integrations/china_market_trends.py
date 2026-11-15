"""Dated, unadjusted Chinese benchmark closes and scoped A-share turnover."""

from datetime import date, time, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
from zoneinfo import ZoneInfo

INDEX_ROWS = {
    "CN_SH_COMP": ("上证指数", "000001", "SSE_COMPOSITE_HISTORY"),
    "CN_CSI300": ("沪深300", "000300", "CSI300_HISTORY"),
}
INDEX_CATALOG = {
    code: (label, "DAILY", "点", source_key)
    for code, (label, _, source_key) in INDEX_ROWS.items()
}
TURNOVER_METHOD = "sentiment/eastmoney-sh-sz-bj-v3"
TURNOVER_URI = "https://quote.eastmoney.com/center/gridlist.html#hs_a_board"
TURNOVER_CLOSE = time(15, 30)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_COVERAGE = {
    "SSE": re.compile(r"(?:600|601|603|605|688|689)\d{3}$"),
    "SZSE": re.compile(r"(?:000|001|002|003|300|301)\d{3}$"),
    "BSE": re.compile(r"(?:4[03]|8[2378]|92)\d{4}$"),
}


def _number(raw):
    try:
        value = Decimal(str(raw))
        return value if value.is_finite() and value >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def covered_turnover(frame, stamp):
    """Sum observed amounts when each exchange has at least 99% numeric coverage."""
    stamp = stamp.astimezone(_SHANGHAI)
    if stamp.weekday() >= 5 or stamp.time() < TURNOVER_CLOSE:
        return None
    if not {"代码", "成交额"} <= set(frame.columns):
        return None
    seen = set()
    counts, numeric = (dict.fromkeys(_COVERAGE, 0) for _ in range(2))
    total = Decimal(0)
    for symbol, raw in frame[["代码", "成交额"]].itertuples(index=False, name=None):
        symbol = str(symbol).strip().zfill(6)
        market = next(
            (key for key, rule in _COVERAGE.items() if rule.fullmatch(symbol)), None
        )
        if not market or symbol in seen:
            return None
        seen.add(symbol)
        counts[market] += 1
        if (amount := _number(raw)) is not None:
            numeric[market] += 1
            total += amount
    # Detect obviously partial lists without inferring or adding missing rows.
    if (
        len(seen) < 4500
        or min(counts["SSE"], counts["SZSE"]) < 1000
        or counts["BSE"] < 150
        or any(numeric[market] * 100 < counts[market] * 99 for market in counts)
    ):
        return None
    total /= Decimal("100000000")
    return total.quantize(Decimal("0.01")) if total > 0 else None


async def fetch_index_rows(client, code, now):
    label, symbol, source = INDEX_ROWS[code]
    now = now.astimezone(_SHANGHAI)
    end = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    start = end - timedelta(days=740)
    params = {
        "secid": f"1.{symbol}",
        "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53",
        "klt": 101,
        "fqt": 0,
        "beg": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
    }
    async with client.stream("GET", _KLINE, params=params, timeout=10) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > 262_144:
                raise ValueError("index daily response exceeds bound")
    raw = json.loads(body)
    data = raw.get("data") if isinstance(raw, dict) else None
    lines = data.get("klines") if isinstance(data, dict) else None
    if (
        not isinstance(lines, list)
        or not 1 <= len(lines) <= 600
        or str(data.get("code")) != symbol
        or str(data.get("market")) != "1"
    ):
        raise ValueError("index daily history identity or coverage unavailable")
    prices = {}
    for line in lines:
        day, _, close = line.split(",")
        day = date.fromisoformat(day)
        value = _number(close)
        if (
            not start <= day <= end
            or day.weekday() >= 5
            or value is None
            or value <= 0
            or day in prices
        ):
            raise ValueError("invalid daily index observation")
        prices[day] = value
    stamp = now.isoformat()
    return [
        {
            "code": code,
            "frequency": "DAILY",
            "unit": "点",
            "referenceDate": day.isoformat(),
            "value": str(value),
            "sourceUri": f"https://quote.eastmoney.com/zs{symbol}.html",
            "sourceName": f"东方财富 · {label}",
            "retrievedAt": stamp,
            "availableAt": stamp,
            "publishedAt": None,
            "timeBasis": "RETRIEVAL_ONLY",
            "methodVersion": "eastmoney-index-close-v1",
            "metadata": {
                "definitionKey": code,
                "definitionVersion": "PRICE_CLOSE",
                "sourceKey": source,
            },
        }
        for day, value in sorted(prices.items())
    ]
