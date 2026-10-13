"""Monthly A-share macro histories from the pinned AKShare EastMoney adapters.

Reporting periods are source facts. These adapters expose no verified release
timestamp, so collection time never becomes a publication timestamp.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("wealthsail.investment_macro")
_FETCH_TIMEOUT_SECONDS = 20
_SERIES = (
    ("CPI_YOY", "中国CPI同比", "macro_china_cpi", "全国-同比增长", "%", "cpi"),
    ("PMI", "中国制造业PMI", "macro_china_pmi", "制造业-指数", "", "pmi"),
    ("PPI_YOY", "中国PPI同比", "macro_china_ppi", "当月同比增长", "%", "ppi"),
    (
        "M2_YOY",
        "中国M2同比",
        "macro_china_money_supply",
        "货币和准货币(M2)-同比增长",
        "%",
        "hbgyl",
    ),
)
_BY_CODE = {spec[0]: spec for spec in _SERIES}
_BY_NAME = {spec[1]: spec[0] for spec in _SERIES}


def _period(value):
    if isinstance(value, (date, datetime)):
        return date(value.year, value.month, 1)
    text = str(value).strip()
    match = re.fullmatch(r"(\d{4})(?:-)?(\d{2})", text)
    if match is None:
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(?:份)?", text)
    if match is None:
        raise ValueError("invalid macro reporting month")
    return date(int(match[1]), int(match[2]), 1)


def normalize_macro(items, *, start=None, end=None):
    """Sort truthful monthly facts, excluding bad rows and conflicting periods."""
    by_period, conflicts = {}, set()
    for item in items:
        try:
            code = item.get("fieldCode") or _BY_NAME[item["name"]]
            spec = _BY_CODE[code]
            if isinstance(item["value"], bool):
                continue
            value = Decimal(str(item["value"]))
            period = _period(item.get("period", item.get("date")))
            if (
                not value.is_finite()
                or (start and period < start)
                or (end and period > end)
            ):
                continue
        except (KeyError, ValueError, TypeError, InvalidOperation):
            continue
        row = {
            "fieldCode": code,
            "period": period,
            "value": value,
            "unit": spec[4],
            "sourceUri": item.get("sourceUri")
            or f"https://data.eastmoney.com/cjsj/{spec[5]}.html",
            "publishedAt": None,
            "quality": "STALE",
        }
        key = code, period
        if key in by_period and by_period[key] != row:
            conflicts.add(key)
        by_period[key] = row
    return [by_period[key] for key in sorted(by_period) if key not in conflicts]


async def fetch_macro_history(start: date, end: date, *, provider=None):
    """Return every valid observation in the inclusive reporting-month window.

    A failed source, changed provider schema, or bounded timeout only removes
    that source; other series remain available for persistence and display.
    """
    if start > end:
        raise ValueError("macro history start must not be after end")
    if provider is None:
        import akshare as provider

    items = []
    for code, label, function, column, unit, source in _SERIES:
        try:
            frame = await asyncio.wait_for(
                asyncio.to_thread(getattr(provider, function)),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
            if (
                frame is None
                or frame.empty
                or "月份" not in frame.columns
                or column not in frame.columns
            ):
                raise ValueError("macro history provider schema is unavailable")
            items.extend(
                {
                    "fieldCode": code,
                    "date": row["月份"],
                    "value": row[column],
                    "sourceUri": f"https://data.eastmoney.com/cjsj/{source}.html",
                }
                for row in frame.to_dict("records")
            )
        except Exception as exc:  # noqa: BLE001 -- source failure must be isolated
            logger.warning(
                "macro_history_fetch_failed field=%s error_class=%s",
                code,
                type(exc).__name__,
            )
    return normalize_macro(items, start=start, end=end)
