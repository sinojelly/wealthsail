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


async def search_fund_master(conn: Connection, keyword: str, *, limit: int = 10):
    """Find master-list funds by name or code, best matches first."""

    pattern = f"%{keyword}%"
    return await conn.fetch(
        """
        SELECT fund_code, fund_name, fund_type
          FROM reference.fund_master
         WHERE fund_name ILIKE $1 OR fund_code ILIKE $1
         ORDER BY CASE WHEN fund_code = $2 THEN 0
                       WHEN fund_name = $3 THEN 1
                       WHEN fund_name ILIKE $2 || '%' THEN 2
                       ELSE 3 END,
                  fund_code
         LIMIT $4
        """,
        pattern,
        keyword,
        keyword,
        limit,
    )


async def search_fund_master_similar(
    conn: Connection, keyword: str, *, limit: int = 10
):
    """Trigram-similarity fallback for full marketing names.

    AkShare stores abbreviated names ("兴全合宜混合(LOF)A") while users paste
    the full name ("兴全合宜灵活配置混合(LOF)A"); ILIKE misses those, the
    pg_trgm % operator (068) does not.
    """

    return await conn.fetch(
        """
        SELECT fund_code, fund_name, fund_type
          FROM reference.fund_master
         WHERE fund_name % $1
         ORDER BY similarity(fund_name, $1) DESC, fund_code
         LIMIT $2
        """,
        keyword,
        limit,
    )


async def get_nav_ranges_by_codes(conn: Connection, fund_codes: list[str]):
    """Load cached range rows for an explicit code list."""

    return await conn.fetch(
        """
        SELECT fund_code, fund_name, fund_type, latest_nav, latest_nav_date,
               highest_nav, highest_nav_date, lowest_nav, lowest_nav_date,
               drawdown_from_high, rise_from_low, current_percentile,
               inception_date, data_source, updated_at
          FROM reference.fund_nav_ranges
         WHERE fund_code = ANY($1)
        """,
        fund_codes,
    )


_BROWSE_SELECT = """
    SELECT r.fund_code, r.fund_name, r.fund_type, r.latest_nav,
           r.latest_nav_date, r.highest_nav, r.highest_nav_date,
           r.lowest_nav, r.lowest_nav_date, r.drawdown_from_high,
           r.rise_from_low, r.current_percentile, r.inception_date,
           r.data_source, r.updated_at
      FROM reference.fund_nav_ranges r
      JOIN reference.fund_master m ON m.fund_code = r.fund_code
"""


async def browse_fund_cache_by_type(
    conn: Connection,
    patterns: list[str],
    *,
    limit: int = 20,
    desc: bool = False,
):
    """Cached funds whose master type matches any pattern, by percentile.

    The cache table's own fund_type is unreliable (early daily-sync rows store
    the internal OFF_EXCHANGE_FUND value), so classification always comes from
    the joined master list.
    """

    order = "DESC" if desc else "ASC"
    return await conn.fetch(
        _BROWSE_SELECT
        + f"""
         WHERE m.fund_type LIKE ANY($1)
         ORDER BY r.current_percentile {order} NULLS LAST, r.fund_code
         LIMIT $2
        """,
        patterns,
        limit,
    )


async def browse_fund_cache_by_percentile(
    conn: Connection,
    *,
    min_percentile: float | None = None,
    max_percentile: float | None = None,
    limit: int = 20,
    desc: bool = False,
):
    """Cached funds above/below a percentile threshold."""

    order = "DESC" if desc else "ASC"
    if min_percentile is not None:
        condition = "r.current_percentile > $1"
        args: list = [min_percentile]
    else:
        condition = "r.current_percentile < $1"
        args = [max_percentile]
    return await conn.fetch(
        _BROWSE_SELECT
        + f"""
         WHERE {condition}
         ORDER BY r.current_percentile {order} NULLS LAST, r.fund_code
         LIMIT $2
        """,
        *args,
        limit,
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
    """Create one durable on-demand refresh request per tenant and fund code.

    An existing request is reused as-is while it is still pending, running, or
    awaiting a retry.  Only a terminal row (already completed, or failed with
    retries exhausted) is reset back to PENDING so the code can be fetched
    again instead of being stuck forever behind its first request.
    """

    job_key = f"fund-nav-range-fetch:{fund_code}"
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
        job_key,
        json.dumps({"fundCode": fund_code}, ensure_ascii=False),
    )
    if row is not None:
        return {**dict(row), "created": True}
    row = await conn.fetchrow(
        """
        UPDATE workflow.job_runs
           SET status = 'PENDING', available_at = now(), started_at = NULL,
               finished_at = NULL, error_class = NULL, next_retry_at = NULL,
               attempt_count = 0, lease_token = NULL, lease_owner = NULL,
               lease_until = NULL, heartbeat_at = now()
         WHERE tenant_id = $1
           AND job_key = $2
           AND (
               status = 'COMPLETED'
               OR (status = 'FAILED' AND next_retry_at IS NULL)
           )
        RETURNING id, status, job_type
        """,
        tenant_id,
        job_key,
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
        job_key,
    )
    if existing is None:
        raise RuntimeError("fund NAV range fetch conflict row disappeared")
    return {**dict(existing), "created": False}
