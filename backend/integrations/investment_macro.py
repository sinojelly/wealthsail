"""Monthly A-share macro histories from the pinned AKShare EastMoney adapters.

Reporting periods are source facts. These adapters expose no verified release
timestamp, so collection time never becomes a publication timestamp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

logger = logging.getLogger("wealthsail.investment_macro")
_FETCH_TIMEOUT_SECONDS = 20
_MACRO_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DIRECT_FIELDS = {
    "CPI_YOY": ("RPT_ECONOMY_CPI", "NATIONAL_SAME"),
    "PMI": ("RPT_ECONOMY_PMI", "MAKE_INDEX"),
    "PPI_YOY": ("RPT_ECONOMY_PPI", "BASE_SAME"),
}
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
                or (code == "PMI" and not 0 < value <= 100)
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


def _latest_window_complete(rows, start, end):
    # The current month may legitimately be unpublished. Test the last 24
    # completed months without assuming a release timestamp for the current one.
    now_month = end.year * 12 + end.month - 1
    lower = max(start.year * 12 + start.month - 1, now_month - 24)
    actual = {row["period"].year * 12 + row["period"].month - 1 for row in rows}
    return bool(rows) and all(month in actual for month in range(lower, now_month))


async def _fetch_eastmoney_history(client, code, start, end):
    """Use the pinned adapter's upstream report, parsing named columns."""
    report, column = _DIRECT_FIELDS[code]
    observations = []
    # The worker's five-year window generally fits on page one. Stay bounded
    # if the provider applies a smaller page size.
    for page in range(1, 5):
        params = {
            "columns": f"REPORT_DATE,TIME,{column}",
            "pageNumber": page,
            "pageSize": 80,
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
            "reportName": report,
        }
        async with client.stream("GET", _MACRO_API, params=params) as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 512 * 1024:
                    raise ValueError("macro history response exceeds limit")
        body = json.loads(raw)
        result = (
            body.get("result")
            if isinstance(body, dict) and body.get("success") is True
            else None
        )
        if not isinstance(result, dict) or type(result.get("pages")) is not int:
            raise ValueError("invalid macro history response")
        rows = result.get("data")
        if not isinstance(rows, list) or not rows:
            raise ValueError("macro history page is empty")
        oldest = None
        for row in rows:
            if not isinstance(row, dict) or column not in row:
                raise ValueError("macro history metric unavailable")
            period = _period(row.get("TIME"))
            reported = date.fromisoformat(str(row.get("REPORT_DATE"))[:10])
            if period != reported:
                raise ValueError("macro history period mismatch")
            observations.append(
                {"fieldCode": code, "period": period, "value": row[column]}
            )
            oldest = min(oldest, period) if oldest else period
        if oldest <= start or page >= result["pages"]:
            break
    return normalize_macro(observations, start=start, end=end)


async def fetch_macro_history(start: date, end: date, *, provider=None):
    """Return every valid observation in the inclusive reporting-month window.

    A failed source, changed provider schema, or bounded timeout only removes
    that source; other series remain available for persistence and display.
    """
    if start > end:
        raise ValueError("macro history start must not be after end")
    public_fallback = provider is None
    if public_fallback:
        import akshare as provider

    async def collect(spec, client):
        code, _, function, column, _, source = spec
        items = []
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
            items = normalize_macro(
                (
                    {
                        "fieldCode": code,
                        "date": row["月份"],
                        "value": row[column],
                        "sourceUri": f"https://data.eastmoney.com/cjsj/{source}.html",
                    }
                    for row in frame.to_dict("records")
                ),
                start=start,
                end=end,
            )
        except Exception as exc:  # noqa: BLE001 -- source failure must be isolated
            logger.warning(
                "macro_history_fetch_failed field=%s error_class=%s",
                code,
                type(exc).__name__,
            )
        if (
            client is not None
            and code in _DIRECT_FIELDS
            and not _latest_window_complete(items, start, end)
        ):
            try:
                fallback = await asyncio.wait_for(
                    _fetch_eastmoney_history(client, code, start, end),
                    timeout=_FETCH_TIMEOUT_SECONDS,
                )
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError) as exc:
                logger.warning(
                    "macro_history_fallback_failed field=%s error_class=%s",
                    code,
                    type(exc).__name__,
                )
            else:
                # Duplicate conflicts from the same upstream are excluded by
                # normalization rather than silently picking a value.
                items = normalize_macro([*items, *fallback], start=start, end=end)
        return items

    if public_fallback:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            groups = await asyncio.gather(*(collect(spec, client) for spec in _SERIES))
    else:
        groups = await asyncio.gather(*(collect(spec, None) for spec in _SERIES))
    return normalize_macro(
        [item for group in groups for item in group], start=start, end=end
    )
