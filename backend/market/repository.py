"""Market repository: observation persistence, snapshot recomputation, calendar.

Every financial amount is NUMERIC in the database; Python side stays on
Decimal end-to-end. The recompute path runs inside a per-tenant RLS
transaction so projection rows can only be written for the acting tenant.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

import asyncpg

from backend.common.formatting import merge_quality

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------
async def get_active_instruments(
    conn: asyncpg.Connection,
) -> list[asyncpg.Record]:
    """Active instruments with their TICKER alias (the external lookup key)."""
    return await conn.fetch(
        """
        SELECT i.id AS instrument_id, i.asset_type, i.canonical_name,
               COALESCE(a.alias_value, '') AS ticker
        FROM reference.instruments i
        LEFT JOIN LATERAL (
            SELECT alias_value
            FROM reference.instrument_aliases
            WHERE instrument_id = i.id AND alias_type = 'TICKER'
              AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
            ORDER BY effective_from DESC
            LIMIT 1
        ) a ON TRUE
        WHERE i.lifecycle_status = 'ACTIVE'
        """,
    )


async def get_holdings_for_refresh(
    conn: asyncpg.Connection,
) -> list[asyncpg.Record]:
    """Latest per-instrument holdings across all tenants (for scoped refresh)."""
    return await conn.fetch(
        """
        SELECT DISTINCT ON (ps.tenant_id, ps.instrument_id)
            ps.tenant_id, ps.account_id, ps.instrument_id, ps.quantity,
            ps.avg_cost, ps.data_quality_status
        FROM ledger.position_snapshots ps
        ORDER BY ps.tenant_id, ps.instrument_id, ps.as_of DESC
        """
    )


async def get_source_id(conn: asyncpg.Connection, source_name: str) -> str | None:
    return await conn.fetchval(
        "SELECT id FROM reference.data_sources WHERE name = $1", source_name
    )


# ---------------------------------------------------------------------------
# writes: source snapshots + observations
# ---------------------------------------------------------------------------
async def upsert_data_source(
    conn: asyncpg.Connection, *, name: str, source_type: str
) -> None:
    await conn.execute(
        """
        INSERT INTO reference.data_sources
            (id, name, source_type, license_model, allowed_usage, priority, status)
        VALUES (gen_random_uuid(), $1, $2, 'PUBLIC_API', 'PRICE_OBSERVATION', 80, 'ACTIVE')
        ON CONFLICT (name) DO UPDATE SET status = 'ACTIVE'
        """,
        name,
        source_type,
    )


async def insert_source_snapshot(
    conn: asyncpg.Connection,
    *,
    source_id: str,
    retrieval_key: str,
    observations: list[dict],
    quality_status: str,
    retrieved_at: datetime,
) -> str:
    canonical = json.dumps(observations, sort_keys=True, ensure_ascii=False, default=str)
    content_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return str(
        await conn.fetchval(
            """
            INSERT INTO market.source_snapshots
                (source_id, retrieval_key, origin_uri, as_of, retrieved_at,
                 content_hash, quality_status)
            VALUES ($1, $2, 'internal:worker:market', $3, $4, $5, $6)
            RETURNING id
            """,
            source_id,
            retrieval_key,
            retrieved_at,
            retrieved_at,
            content_hash,
            quality_status,
        )
    )


async def insert_observations(
    conn: asyncpg.Connection,
    rows: list[tuple],
) -> None:
    """Batch-insert normalized observations (UNIQUE conflict → skip)."""
    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO market.market_observations
            (subject_type, subject_id, field_code, value, unit, currency,
             as_of, observed_at, snapshot_id, source_id, quality_status,
             method_version, revision)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 1)
        ON CONFLICT (subject_type, subject_id, field_code, as_of, source_id, revision)
        DO NOTHING
        """,
        rows,
    )


# ---------------------------------------------------------------------------
# snapshot recomputation (per tenant, inside RLS transaction)
# ---------------------------------------------------------------------------
async def refresh_positions(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    quotes: dict[str, dict],
    as_of_date: date,
) -> int:
    """Upsert today's position snapshots from the latest observations.

    ``quotes`` maps instrument_id → {"price": Decimal, "quality": str}.
    Quantity/avg_cost are preserved; market_value and unrealized_pnl are
    recomputed with Decimal precision.
    """
    holdings = await conn.fetch(
        """
        SELECT DISTINCT ON (account_id, instrument_id)
            account_id, instrument_id, quantity, avg_cost
        FROM ledger.position_snapshots
        WHERE tenant_id = $1
        ORDER BY account_id, instrument_id, as_of DESC
        """,
        tenant_id,
    )
    updated = 0
    for holding in holdings:
        quote = quotes.get(str(holding["instrument_id"]))
        if quote is None or quote["price"] is None:
            continue
        price = quote["price"]
        quality = quote["quality"]
        quantity = Decimal(holding["quantity"] or 0)
        avg_cost = Decimal(holding["avg_cost"] or 0)
        market_value = (quantity * price).quantize(Decimal("0.01"))
        unrealized_pnl = (market_value - quantity * avg_cost).quantize(Decimal("0.01"))
        await conn.execute(
            """
            INSERT INTO ledger.position_snapshots
                (tenant_id, account_id, instrument_id, as_of, quantity,
                 avg_cost, market_price, market_value, unrealized_pnl,
                 data_quality_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (tenant_id, account_id, instrument_id, as_of) DO UPDATE
            SET market_price = EXCLUDED.market_price,
                market_value = EXCLUDED.market_value,
                unrealized_pnl = EXCLUDED.unrealized_pnl,
                data_quality_status = EXCLUDED.data_quality_status
            """,
            tenant_id,
            holding["account_id"],
            holding["instrument_id"],
            as_of_date,
            quantity,
            avg_cost,
            price,
            market_value,
            unrealized_pnl,
            quality,
        )
        updated += 1
    return updated


async def refresh_account_nav(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    as_of_date: date,
) -> int:
    """Recompute today's NAV snapshot per account.

    cash_balance: net cash from confirmed CASH legs (ledger is the source of
    truth, not the previous projection);
    principal: net invested capital (CASH_IN − CASH_OUT);
    cumulative_pnl = total_nav − principal;
    daily_pnl     = total_nav − previous trading day total_nav.
    """
    accounts = await conn.fetch(
        """
        SELECT DISTINCT account_id
        FROM ledger.position_snapshots
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    updated = 0
    for account in accounts:
        account_id = str(account["account_id"])

        cash_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(leg.amount), 0) AS cash_balance,
                   -- 净投入本金 = CASH_IN 与 CASH_OUT 的净现金流
                   -- （CASH_OUT 的 amount 为负，直接求和即净额）
                   COALESCE(SUM(leg.amount)
                            FILTER (WHERE ev.event_type IN ('CASH_IN', 'CASH_OUT')), 0)
                   AS principal
            FROM ledger.ledger_legs leg
            JOIN ledger.ledger_events ev
              ON ev.tenant_id = leg.tenant_id AND ev.id = leg.event_id
            WHERE leg.tenant_id = $1 AND ev.account_id = $2
              AND leg.leg_type = 'CASH'
              AND ev.status IN ('CONFIRMED', 'PENDING')
            """,
            tenant_id,
            account_id,
        )
        cash_balance = Decimal((cash_row or {}).get("cash_balance") or 0)
        principal = Decimal((cash_row or {}).get("principal") or 0)

        positions_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(market_value), 0) AS positions_value,
                   COALESCE(SUM(market_value) FILTER (WHERE data_quality_status = 'UNAVAILABLE'), 0)
                   AS unavailable_value,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE data_quality_status IN ('FRESH', 'ESTIMATED'))
                   AS fresh_count
            FROM ledger.position_snapshots
            WHERE tenant_id = $1 AND account_id = $2 AND as_of = $3
            """,
            tenant_id,
            account_id,
            as_of_date,
        )
        positions_row = positions_row or {}
        positions_value = Decimal(positions_row.get("positions_value") or 0)
        total_nav = (cash_balance + positions_value).quantize(Decimal("0.01"))

        prev_row = await conn.fetchrow(
            """
            SELECT total_nav
            FROM ledger.account_nav_snapshots
            WHERE tenant_id = $1 AND account_id = $2 AND as_of < $3
            ORDER BY as_of DESC
            LIMIT 1
            """,
            tenant_id,
            account_id,
            as_of_date,
        )
        prev_total = (
            Decimal(prev_row["total_nav"]) if prev_row and prev_row["total_nav"] else total_nav
        )
        daily_pnl = (total_nav - prev_total).quantize(Decimal("0.01"))
        cumulative_pnl = (total_nav - principal).quantize(Decimal("0.01"))

        quality = "UNAVAILABLE"
        if positions_row.get("total"):
            quality = (
                "FRESH"
                if positions_row.get("fresh_count") == positions_row.get("total")
                else ("ESTIMATED" if positions_row.get("fresh_count") else "UNAVAILABLE")
            )

        await conn.execute(
            """
            INSERT INTO ledger.account_nav_snapshots
                (tenant_id, account_id, as_of, cash_balance,
                 positions_market_value, total_nav, daily_pnl, cumulative_pnl,
                 data_quality_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (tenant_id, account_id, as_of) DO UPDATE
            SET cash_balance = EXCLUDED.cash_balance,
                positions_market_value = EXCLUDED.positions_market_value,
                total_nav = EXCLUDED.total_nav,
                daily_pnl = EXCLUDED.daily_pnl,
                cumulative_pnl = EXCLUDED.cumulative_pnl,
                data_quality_status = EXCLUDED.data_quality_status
            """,
            tenant_id,
            account_id,
            as_of_date,
            cash_balance,
            positions_value,
            total_nav,
            daily_pnl,
            cumulative_pnl,
            quality,
        )
        updated += 1
    return updated


async def upsert_trading_calendar_rows(
    conn: asyncpg.Connection, rows: list[tuple]
) -> int:
    """Bulk upsert trading-calendar rows (market, calendar_date, is_trading_day)."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO reference.trading_calendars (market, calendar_date, is_trading_day, timezone)
        VALUES ('SSE', $1, $2, 'Asia/Shanghai')
        ON CONFLICT (market, calendar_date) DO UPDATE
        SET is_trading_day = EXCLUDED.is_trading_day
        """,
        rows,
    )
    return len(rows)
