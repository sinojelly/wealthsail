"""Validation and response shaping for fund-screener range-cache APIs."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from asyncpg import Connection

from backend.errors import ValidationError_
from backend.funds import repository


_FUND_CODE = re.compile(r"^[0-9]{6}$")


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
    """Search the persisted cache by code or name.

    When the keyword is an exact six-digit fund code absent from the cache,
    one durable fetch job is enqueued (idempotent per tenant+code) so a later
    search can hit.  Enqueueing is a plain DB write; all provider I/O stays
    Worker-only.
    """

    normalized = keyword.strip()
    if not normalized:
        raise ValidationError_("搜索关键词不能为空")
    results = [
        _range_dto(row)
        for row in await repository.search_nav_ranges(conn, normalized)
    ]
    if _FUND_CODE.fullmatch(normalized) and not any(
        item["fundCode"] == normalized for item in results
    ):
        await repository.enqueue_nav_range_fetch(
            conn, tenant_id=tenant_id, fund_code=normalized
        )
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
