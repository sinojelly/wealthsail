"""Validation and response shaping for fund-screener range-cache APIs."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from asyncpg import Connection

from backend.errors import ValidationError_
from backend.funds import repository


_FUND_CODE = re.compile(r"^[0-9]{6}$")
# 名称搜索的展示上限，以及单次搜索自动入队抓取的上限：
# 模糊词（如"混合"）可能命中成百上千条主数据，无上限会瞬间制造大量抓取任务
_MASTER_SEARCH_LIMIT = 10
_AUTO_ENQUEUE_LIMIT = 5


def _not_collected_dto(row) -> dict:
    return {
        "fundCode": str(row["fund_code"]),
        "fundName": str(row["fund_name"]),
        "fundType": str(row["fund_type"]),
        "latestNav": None,
        "latestNavDate": None,
        "highestNav": None,
        "highestNavDate": None,
        "lowestNav": None,
        "lowestNavDate": None,
        "drawdownFromHigh": None,
        "riseFromLow": None,
        "currentPercentile": None,
        "inceptionDate": None,
        "dataSource": None,
        "updatedAt": None,
        "status": "NOT_COLLECTED",
    }


def normalize_fund_code(value: str) -> str:
    """Accept only the six-digit mainland mutual-fund code used by AkShare."""

    fund_code = value.strip()
    if not _FUND_CODE.fullmatch(fund_code):
        raise ValidationError_("基金代码必须为 6 位数字")
    return fund_code


def _iso(value: Any) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value) if value is not None else None


def _range_dto(row) -> dict:
    source = str(row["data_source"])
    return {
        "fundCode": str(row["fund_code"]),
        "fundName": str(row["fund_name"]),
        "fundType": str(row["fund_type"]),
        "latestNav": row["latest_nav"],
        "latestNavDate": _iso(row["latest_nav_date"]),
        "highestNav": row["highest_nav"],
        "highestNavDate": _iso(row["highest_nav_date"]),
        "lowestNav": row["lowest_nav"],
        "lowestNavDate": _iso(row["lowest_nav_date"]),
        "drawdownFromHigh": row["drawdown_from_high"],
        "riseFromLow": row["rise_from_low"],
        "currentPercentile": row["current_percentile"],
        "inceptionDate": _iso(row["inception_date"]),
        "dataSource": source,
        "updatedAt": _iso(row["updated_at"]),
        "status": "UNAVAILABLE" if source == "UNAVAILABLE" else "AVAILABLE",
    }


async def search(
    conn: Connection, keyword: str, *, tenant_id: str
) -> list[dict]:
    """Search cached ranges first, then the market-wide fund master list.

    A six-digit code absent from the cache enqueues one durable fetch job
    (idempotent per tenant+code).  A name keyword resolves through the master
    list: cached funds render normally, uncached ones show as NOT_COLLECTED
    with up to ``_AUTO_ENQUEUE_LIMIT`` of them auto-enqueued per search.
    Enqueueing is a plain DB write; all provider I/O stays Worker-only.
    """

    normalized = keyword.strip()
    if not normalized:
        raise ValidationError_("搜索关键词不能为空")

    if _FUND_CODE.fullmatch(normalized):
        cached = await repository.get_nav_range(conn, normalized)
        if cached is not None:
            return [_range_dto(cached)]
        await repository.enqueue_nav_range_fetch(
            conn, tenant_id=tenant_id, fund_code=normalized
        )
        return []

    results = [
        _range_dto(row)
        for row in await repository.search_nav_ranges(conn, normalized)
    ]
    cached_codes = {item["fundCode"] for item in results}

    master_rows = await repository.search_fund_master(
        conn, normalized, limit=_MASTER_SEARCH_LIMIT
    )
    remaining = [
        str(row["fund_code"])
        for row in master_rows
        if str(row["fund_code"]) not in cached_codes
    ]
    cached_extra = (
        await repository.get_nav_ranges_by_codes(conn, remaining)
        if remaining
        else []
    )
    cached_by_code = {
        str(row["fund_code"]): row for row in cached_extra
    }

    enqueued = 0
    for row in master_rows:
        fund_code = str(row["fund_code"])
        if fund_code in cached_codes:
            continue
        cached_row = cached_by_code.get(fund_code)
        if cached_row is not None:
            # 主数据命中、且缓存里其实已有（名称拼写与缓存不同的场景）
            results.append(_range_dto(cached_row))
            cached_codes.add(fund_code)
            continue
        results.append(_not_collected_dto(row))
        if enqueued < _AUTO_ENQUEUE_LIMIT:
            await repository.enqueue_nav_range_fetch(
                conn, tenant_id=tenant_id, fund_code=fund_code
            )
            enqueued += 1
    return results


async def get_range(conn: Connection, fund_code: str) -> dict:
    """Return a cache row or the explicit non-error state required by the UI."""

    normalized = normalize_fund_code(fund_code)
    row = await repository.get_nav_range(conn, normalized)
    if row is None:
        return {
            "fundCode": normalized,
            "status": "NOT_COLLECTED",
            "message": "未收录",
        }
    return _range_dto(row)


async def enqueue_fetch(
    conn: Connection, *, tenant_id: str, fund_code: str
) -> dict:
    """Queue a recoverable Worker request and return without provider latency."""

    normalized = normalize_fund_code(fund_code)
    row = await repository.enqueue_nav_range_fetch(
        conn, tenant_id=tenant_id, fund_code=normalized
    )
    return {
        "fundCode": normalized,
        "status": "QUEUED",
        "message": "已提交抓取请求",
        "runId": str(row["id"]),
        "created": bool(row["created"]),
    }
