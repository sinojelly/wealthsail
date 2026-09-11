"""Database operations for the shared fund-screener NAV-range cache."""

from __future__ import annotations

import json

from asyncpg import Connection


async def search_nav_ranges(conn: Connection, keyword: str):
    """Find cached range rows by code or name without invoking a provider."""

    pattern = f"%{keyword}%"
    return await conn.fetch(
        """
        SELECT fund_code, fund_name, fund_type, latest_nav, latest_nav_date,
               highest_nav, highest_nav_date, lowest_nav, lowest_nav_date,
               drawdown_from_high, rise_from_low, current_percentile,
               inception_date, data_source, updated_at
          FROM reference.fund_nav_ranges
         WHERE fund_code ILIKE $1 OR fund_name ILIKE $1
         ORDER BY CASE WHEN fund_code = $2 THEN 0 ELSE 1 END,
                  fund_code
         LIMIT 50
        """,
        pattern,
        keyword,
    )


async def get_nav_range(conn: Connection, fund_code: str):
    """Return one cache row, or ``None`` when the fund has not been collected."""

    return await conn.fetchrow(
        """
        SELECT fund_code, fund_name, fund_type, latest_nav, latest_nav_date,
               highest_nav, highest_nav_date, lowest_nav, lowest_nav_date,
               drawdown_from_high, rise_from_low, current_percentile,
               inception_date, data_source, updated_at
          FROM reference.fund_nav_ranges
         WHERE fund_code = $1
        """,
        fund_code,
    )


async def enqueue_nav_range_fetch(
    conn: Connection, *, tenant_id: str, fund_code: str
):
    """Create one durable on-demand refresh request per tenant and fund code."""

    row = await conn.fetchrow(
        """
        INSERT INTO workflow.job_runs (
            tenant_id, job_key, status, job_type, payload, available_at
        ) VALUES (
            $1, $2, 'PENDING', 'FUND_NAV_RANGE_FETCH', $3::jsonb, now()
        )
        ON CONFLICT (tenant_id, job_key) DO NOTHING
        RETURNING id, status, job_type
        """,
        tenant_id,
        f"fund-nav-range-fetch:{fund_code}",
        json.dumps({"fundCode": fund_code}, ensure_ascii=False),
    )
    if row is not None:
        return {**dict(row), "created": True}
    existing = await conn.fetchrow(
        """
        SELECT id, status, job_type
          FROM workflow.job_runs
         WHERE tenant_id = $1
           AND job_key = $2
        """,
        tenant_id,
        f"fund-nav-range-fetch:{fund_code}",
    )
    if existing is None:
        raise RuntimeError("fund NAV range fetch conflict row disappeared")
    return {**dict(existing), "created": False}
