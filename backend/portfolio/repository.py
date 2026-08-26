"""Portfolio repository: read-optimized queries over the derived projections.

All queries run inside a tenant RLS transaction (``tenant_transaction``); the
queries still carry an explicit ``tenant_id`` predicate as defense in depth.
"""

from __future__ import annotations

from datetime import date

import asyncpg

_SELECT_NAV_COLUMNS = """
    account_id, as_of, cash_balance, positions_market_value,
    total_nav, daily_pnl, cumulative_pnl, data_quality_status,
    known_value, valuation_coverage_pct, unpriced_position_count
"""

_SELECT_POSITION_COLUMNS = """
    ps.instrument_id, ps.account_id, ps.as_of, ps.quantity, ps.avg_cost,
    ps.market_price, ps.market_value, ps.unrealized_pnl, ps.data_quality_status,
    i.canonical_name, i.asset_type,
    COALESCE(a.ticker, '') AS ticker
"""


async def latest_nav_per_account(
    conn: asyncpg.Connection, tenant_id: str
) -> list[asyncpg.Record]:
    """Latest NAV snapshot per account (the current portfolio state)."""
    return await conn.fetch(
        f"""
        SELECT DISTINCT ON (account_id)
            {_SELECT_NAV_COLUMNS}
        FROM ledger.account_nav_snapshots
        WHERE tenant_id = $1
        ORDER BY account_id, as_of DESC
        """,
        tenant_id,
    )


async def nav_history(
    conn: asyncpg.Connection, tenant_id: str, since: date | None = None, limit: int = 90
) -> list[asyncpg.Record]:
    """Chronological daily NAV history (aggregated across accounts)."""
    if since is not None:
        rows = await conn.fetch(
            """
            SELECT as_of,
                   CASE WHEN COUNT(*) = COUNT(total_nav)
                        THEN SUM(total_nav) END AS total_nav,
                   CASE WHEN COUNT(*) = COUNT(daily_pnl)
                        THEN SUM(daily_pnl) END AS daily_pnl,
                   SUM(cash_balance) AS cash_balance
            FROM ledger.account_nav_snapshots
            WHERE tenant_id = $1 AND as_of >= $2
            GROUP BY as_of
            ORDER BY as_of ASC
            """,
            tenant_id,
            since,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT as_of,
                   CASE WHEN COUNT(*) = COUNT(total_nav)
                        THEN SUM(total_nav) END AS total_nav,
                   CASE WHEN COUNT(*) = COUNT(daily_pnl)
                        THEN SUM(daily_pnl) END AS daily_pnl,
                   SUM(cash_balance) AS cash_balance
            FROM ledger.account_nav_snapshots
            WHERE tenant_id = $1
            GROUP BY as_of
            ORDER BY as_of ASC
            """,
            tenant_id,
        )
    return rows[-limit:] if limit else rows


async def latest_positions(
    conn: asyncpg.Connection, tenant_id: str
) -> list[asyncpg.Record]:
    """Latest position snapshot per account+instrument with metadata."""
    return await conn.fetch(
        f"""
        SELECT DISTINCT ON (ps.account_id, ps.instrument_id)
            {_SELECT_POSITION_COLUMNS}
        FROM ledger.position_snapshots ps
        JOIN reference.instruments i ON i.id = ps.instrument_id
        LEFT JOIN LATERAL (
            SELECT alias_value AS ticker
            FROM reference.instrument_aliases
            WHERE instrument_id = ps.instrument_id
              AND alias_type = 'TICKER'
              AND (effective_to IS NULL OR effective_to >= ps.as_of)
            ORDER BY effective_from DESC
            LIMIT 1
        ) a ON TRUE
        WHERE ps.tenant_id = $1
        ORDER BY ps.account_id, ps.instrument_id, ps.as_of DESC
        """,
        tenant_id,
    )
