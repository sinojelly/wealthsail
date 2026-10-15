"""Bounded requests to CSI's public, named-field index history endpoint."""

import math
from datetime import datetime

import httpx

from backend.dependencies import now_shanghai
from backend.market.index_policy import source_uri
from backend.market.research_history import normalize

ENDPOINT = "https://www.csindex.com.cn/csindex-home/perf/index-perf"


async def fetch_index_history(code, start, end, *, client=None, clock=None):
    if code not in {"000300", "000510", "000905"} or start > end:
        raise ValueError("unsupported official index or window")
    if client is None:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as owned:
            return await fetch_index_history(
                code, start, end, client=owned, clock=clock
            )
    response = await client.get(
        ENDPOINT,
        params={
            "indexCode": code,
            "startDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
        },
        timeout=20,
    )
    response.raise_for_status()
    retrieved = (clock or now_shanghai)()
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise ValueError("retrieval clock must be aware")
    data = response.json().get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("official index history unavailable")
    prices, pe_values, pe_conflict, invalid_pe = [], {}, False, []
    for row in data:
        if not isinstance(row, dict) or str(row.get("indexCode")) != code:
            raise ValueError("official index identity mismatch")
        raw_day = str(row.get("tradeDate", ""))
        if len(raw_day) != 8 or not raw_day.isdigit():
            raise ValueError("invalid official observation date")
        day = datetime.strptime(raw_day, "%Y%m%d").date()
        if day > end or day > retrieved.date():
            raise ValueError("future official observation")
        if day < start:
            continue
        if row.get("close") is None:
            raise ValueError("official close unavailable")
        prices.append(
            {
                "date": day.isoformat(),
                **{key: row.get(key) for key in ("open", "high", "low", "close")},
                "volume": None,
            }
        )
        raw = row.get("peg")
        if raw in (None, "", "--"):
            continue
        try:
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value):
                raise ValueError("invalid PE")
            if value <= 0:
                invalid_pe.append(
                    {
                        "date": day.isoformat(),
                        "value": value,
                        "reason": "NON_POSITIVE_PE",
                    }
                )
                raise ValueError("PE does not provide a positive valuation multiple")
            if day in pe_values and pe_values[day] != value:
                raise ValueError("conflicting PE")
            pe_values[day] = value
        except (TypeError, ValueError, OverflowError):
            pe_conflict = True
    points = normalize(prices)
    if not points:
        raise ValueError("official index history window empty")
    pe = {
        "state": "CONFLICT" if pe_conflict else "READY" if pe_values else "UNAVAILABLE",
        "metric": "PE_TTM",
        "sourceUri": source_uri(code),
        "points": [
            {"date": day.isoformat(), "value": value}
            for day, value in sorted(pe_values.items())
        ],
        "sampleCount": len(pe_values),
        "historyStart": min(pe_values).isoformat() if pe_values else None,
        "historyEnd": max(pe_values).isoformat() if pe_values else None,
        "retrievedAt": retrieved.isoformat(),
        "timeBasis": "RETRIEVAL_ONLY",
        "invalidPoints": invalid_pe,
    }
    return {
        "points": points,
        "pe": pe,
        "sourceUri": source_uri(code),
        "requestUri": ENDPOINT,
        "retrievedAt": retrieved.isoformat(),
        "publishedAt": None,
        "timeBasis": "RETRIEVAL_ONLY",
    }
