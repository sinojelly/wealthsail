"""Bounded disclosed unit-NAV retrieval for webpage research requests."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import httpx

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.errors import DataUnavailableError
from backend.integrations.market_data import fetch_fund_nav_history


async def refresh_fund_nav_history(
    pool, fund_code, *, now=None, fetcher=None, now_provider=None
):
    """Append confirmed unit NAV; provider I/O never holds a DB connection."""
    if not isinstance(fund_code, str) or not re.fullmatch(r"\d{6}", fund_code):
        raise ValueError("fund NAV requires a six-digit code")
    now = now or now_shanghai()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("fund NAV requires an aware clock")
    start = now.date() - timedelta(days=730)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        observations = await asyncio.wait_for(
            (fetcher or fetch_fund_nav_history)(
                fund_code, start, now.date(), client, page_size=100, max_pages=32
            ),
            timeout=60,
        )
    by_day = {}
    for observation in observations:
        try:
            value = Decimal(str(observation.value))
            stamp = observation.as_of
            if (
                observation.symbol != fund_code
                or observation.subject_type != "FUND"
                or observation.field_code != "NAV"
                or observation.quality_status not in {"FRESH", "STALE", "REVISED"}
                or (observation.metadata or {}).get("officialUnitNav") is not True
                or not value.is_finite()
                or value <= 0
                or stamp.tzinfo is None
                or stamp > now
                or not start <= stamp.date() <= now.date()
            ):
                continue
        except (InvalidOperation, TypeError, ValueError, AttributeError):
            continue
        by_day.setdefault(
            stamp.date(), {"date": stamp.date().isoformat(), "close": str(value)}
        )
    points = [by_day[day] for day in sorted(by_day)][-500:]
    if not points:
        raise DataUnavailableError("来源没有有效的已披露单位净值")
    finished = max(now, (now_provider or now_shanghai)())
    history = {
        "fundCode": fund_code,
        "asOf": points[-1]["date"],
        "sourceUri": f"https://fund.eastmoney.com/{fund_code}.html",
        "retrievedAt": finished.isoformat(),
        "availableAt": finished.isoformat(),
        "priceBasis": "UNIT_NAV",
        "points": points,
    }
    proofs = [
        (obs.metadata or {}).get("providerHistoryProof")
        for obs in observations
        if (obs.metadata or {}).get("providerHistoryProof")
    ]
    if proofs and all(proof == proofs[0] for proof in proofs):
        history["providerHistoryProof"] = proofs[0]
    async with system_connection(pool) as conn:
        await conn.execute(
            """INSERT INTO market.fund_research_nav_histories
            (fund_code,retrieved_at,available_at,payload)
            VALUES($1,$2,$2,$3::jsonb)""",
            fund_code,
            finished,
            json.dumps(history),
        )
    return history
