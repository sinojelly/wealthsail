"""Portfolio repository: read-optimized queries over the derived projections.

All queries run inside a tenant RLS transaction (``tenant_transaction``); the
queries still carry an explicit ``tenant_id`` predicate as defense in depth.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import asyncpg

from backend.integrations.market_data import (
    F_CLOSE,
    F_ESTIMATED_NAV,
    F_NAV,
    Q_UNAVAILABLE,
)

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


# ---------------------------------------------------------------------------
# 期初组合导入：经核验的标的主数据、账户与两阶段命令
# ---------------------------------------------------------------------------
async def find_instrument_by_ticker(
    conn: asyncpg.Connection,
    ticker: str,
    *,
    primary_market: str | None = None,
    asset_type: str | None = None,
) -> asyncpg.Record | None:
    """Resolve a current ticker without collapsing cross-market identities."""
    return await conn.fetchrow(
        """
        SELECT i.id, i.asset_type, i.primary_market, i.canonical_name
        FROM reference.instruments i
        JOIN reference.instrument_aliases a ON a.instrument_id = i.id
        WHERE a.alias_type = 'TICKER' AND a.alias_value = $1
          AND ($2::text IS NULL OR a.market = $2)
          AND ($3::text IS NULL OR i.asset_type = $3)
          AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
        ORDER BY a.effective_from DESC
        LIMIT 1
        """,
        ticker,
        primary_market,
        asset_type,
    )


async def upsert_instrument(
    conn: asyncpg.Connection,
    *,
    ticker: str,
    name: str,
    asset_type: str,
    primary_market: str,
) -> str:
    """Create instrument + TICKER alias idempotently; return its stable id.

    ``reference.instruments`` has no natural unique key. A transaction-level
    advisory lock serializes creation for one market+ticker before the alias
    unique key is checked, avoiding both duplicate aliases and orphan
    instrument rows under concurrent requests.
    """
    await conn.execute(
        """
        SELECT pg_advisory_xact_lock(
            hashtextextended('instrument:' || $1 || ':' || $2, 0)
        )
        """,
        primary_market,
        ticker,
    )
    instrument_id = await conn.fetchval(
        """
        SELECT i.id
        FROM reference.instruments i
        JOIN reference.instrument_aliases a ON a.instrument_id = i.id
        WHERE a.alias_type = 'TICKER'
          AND a.alias_value = $1
          AND a.market = $2
          AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
        ORDER BY a.effective_from DESC
        LIMIT 1
        """,
        ticker,
        primary_market,
    )
    if instrument_id is not None:
        return str(instrument_id)

    instrument_id = await conn.fetchval(
        """
        INSERT INTO reference.instruments
            (asset_type, primary_market, canonical_name, lifecycle_status)
        VALUES ($1, $2, $3, 'ACTIVE')
        RETURNING id
        """,
        asset_type,
        primary_market,
        name,
    )
    await conn.execute(
        """
        INSERT INTO reference.instrument_aliases
            (instrument_id, alias_type, alias_value, market, effective_from)
        VALUES ($1, 'TICKER', $2, $3, CURRENT_DATE)
        """,
        instrument_id,
        ticker,
        primary_market,
    )
    return str(instrument_id)


async def find_position_import_by_idempotency_key(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    idempotency_key: str,
) -> asyncpg.Record | None:
    """Load the original single-position import response for safe command replay."""
    return await conn.fetchrow(
        """
        SELECT b.id AS batch_id, b.source_content_hash, b.account_id,
               item.id AS item_id, item.quantity, item.unit_cost,
               item.trade_date, item.validation_status,
               instrument.canonical_name, instrument.asset_type,
               alias.alias_value AS ticker
        FROM ledger.portfolio_import_batches b
        JOIN ledger.portfolio_import_items item
          ON item.tenant_id = b.tenant_id AND item.batch_id = b.id
        JOIN reference.instruments instrument ON instrument.id = item.instrument_id
        LEFT JOIN LATERAL (
            SELECT alias_value
            FROM reference.instrument_aliases
            WHERE instrument_id = instrument.id AND alias_type = 'TICKER'
              AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
            ORDER BY effective_from DESC LIMIT 1
        ) alias ON TRUE
        WHERE b.tenant_id = $1 AND b.idempotency_key = $2
        ORDER BY item.source_row NULLS LAST, item.id
        LIMIT 1
        """,
        tenant_id,
        idempotency_key,
    )


async def pick_or_create_account(
    conn: asyncpg.Connection, *, tenant_id: str, asset_type: str
) -> str:
    """Pick the tenant's active account matching the asset type, else create one."""
    account_type = (
        "BROKERAGE" if asset_type in ("CN_STOCK", "EXCHANGE_FUND") else "FUND_PLATFORM"
    )
    account_id = await conn.fetchval(
        """
        SELECT id
        FROM identity.accounts
        WHERE tenant_id = $1 AND status = 'ACTIVE' AND account_type = $2
        ORDER BY created_at
        LIMIT 1
        """,
        tenant_id,
        account_type,
    )
    if account_id is not None:
        return str(account_id)
    default_name = "证券账户" if account_type == "BROKERAGE" else "基金账户"
    return str(
        await conn.fetchval(
            """
            INSERT INTO identity.accounts
                (tenant_id, account_type, name, base_currency)
            VALUES ($1, $2, $3, 'CNY')
            RETURNING id
            """,
            tenant_id,
            account_type,
            default_name,
        )
    )


async def create_import_draft(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
    created_by: str,
    instrument_id: str,
    quantity: Decimal,
    unit_cost: Decimal,
    trade_date: date,
    idempotency_key: str,
    source_content_hash: str,
) -> dict[str, str]:
    """Persist one reviewed initialization row without changing economic facts.

    The batch and item remain ``DRAFT``.  A separate confirmation command is
    the only path that may create an opening ledger adjustment.
    """
    batch_id = str(
        await conn.fetchval(
            """
            INSERT INTO ledger.portfolio_import_batches
                (tenant_id, account_id, created_by, source_type,
                 source_content_hash, status, idempotency_key)
            VALUES ($1, $2, $3, 'MANUAL', $4, 'DRAFT', $5)
            RETURNING id
            """,
            tenant_id,
            account_id,
            created_by,
            source_content_hash,
            idempotency_key,
        )
    )
    item_id = str(
        await conn.fetchval(
            """
            INSERT INTO ledger.portfolio_import_items
                (tenant_id, batch_id, item_type, instrument_id, quantity,
                 unit_cost, trade_date, source_row, validation_status,
                 validation_errors)
            VALUES ($1, $2, 'POSITION', $3, $4, $5, $6, 1, 'VALID', '[]'::jsonb)
            RETURNING id
            """,
            tenant_id,
            batch_id,
            instrument_id,
            quantity,
            unit_cost,
            trade_date,
        )
    )
    return {"batch_id": batch_id, "item_id": item_id}


async def confirm_import_batch(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    batch_id: str,
    idempotency_key: str,
) -> dict:
    """Atomically confirm a draft as opening ``ADJUSTMENT`` events.

    An initialization describes the portfolio state observed by the user; it
    does not prove that a trade or cash transfer occurred in WealthSail.  Each
    row therefore creates exactly one provenance-linked ADJUSTMENT.  Position
    rows have only an INSTRUMENT leg, so no synthetic cash flow or principal is
    introduced.
    """
    batch = await conn.fetchrow(
        """
        SELECT id AS batch_id, account_id, status, version
        FROM ledger.portfolio_import_batches
        WHERE tenant_id = $1 AND id = $2
        FOR UPDATE
        """,
        tenant_id,
        batch_id,
    )
    if batch is None:
        return {}

    if batch["status"] == "CONFIRMED":
        event_id = await conn.fetchval(
            """
            SELECT id FROM ledger.ledger_events
            WHERE tenant_id = $1 AND import_batch_id = $2
              AND status = 'CONFIRMED'
            ORDER BY created_at, id LIMIT 1
            """,
            tenant_id,
            batch_id,
        )
        return {
            "batch_id": batch_id,
            "event_id": str(event_id) if event_id else None,
            "version": int(batch["version"]),
            "already_confirmed": True,
        }
    if batch["status"] != "DRAFT":
        return {"batch_id": batch_id, "status": str(batch["status"])}

    items = await conn.fetch(
        """
        SELECT id AS item_id, item_type, instrument_id, quantity, unit_cost,
               cash_amount, currency, trade_date, validation_status
        FROM ledger.portfolio_import_items
        WHERE tenant_id = $1 AND batch_id = $2
        ORDER BY source_row NULLS LAST, id
        """,
        tenant_id,
        batch_id,
    )
    if not items or any(item["validation_status"] != "VALID" for item in items):
        return {"batch_id": batch_id, "status": "INVALID"}

    event_ids: list[str] = []
    for index, item in enumerate(items, start=1):
        event_id = str(
            await conn.fetchval(
                """
                INSERT INTO ledger.ledger_events
                    (tenant_id, account_id, event_type, status, trade_date,
                     idempotency_key, import_batch_id, import_item_id,
                     description, schema_version)
                VALUES ($1, $2, 'ADJUSTMENT', 'CONFIRMED', $3, $4, $5, $6,
                        '用户确认的期初组合基线；不代表真实成交或出入金', 1)
                RETURNING id
                """,
                tenant_id,
                str(batch["account_id"]),
                item["trade_date"],
                f"{idempotency_key}:item:{index}",
                batch_id,
                str(item["item_id"]),
            )
        )
        if item["item_type"] == "POSITION":
            legs = [
                (
                    tenant_id,
                    event_id,
                    "INSTRUMENT",
                    str(item["instrument_id"]),
                    item["quantity"],
                    item["unit_cost"],
                )
            ]
            await conn.executemany(
                """
                INSERT INTO ledger.ledger_legs
                    (tenant_id, event_id, leg_type, instrument_id, currency,
                     quantity, unit_price)
                VALUES ($1, $2, $3, $4, 'CNY', $5, $6)
                """,
                legs,
            )
        else:
            await conn.execute(
                """
                INSERT INTO ledger.ledger_legs
                    (tenant_id, event_id, leg_type, currency, amount)
                VALUES ($1, $2, 'CASH', $3, $4)
                """,
                tenant_id,
                event_id,
                item["currency"],
                item["cash_amount"],
            )
        event_ids.append(event_id)

    next_version = int(batch["version"]) + 1
    await conn.execute(
        """
        UPDATE ledger.portfolio_import_batches
        SET status = 'CONFIRMED', confirmed_at = now(),
            version = $3, updated_at = now()
        WHERE tenant_id = $1 AND id = $2 AND status = 'DRAFT'
        """,
        tenant_id,
        batch_id,
        next_version,
    )
    return {
        "batch_id": batch_id,
        "event_id": event_ids[0],
        "event_ids": event_ids,
        "version": next_version,
        "already_confirmed": False,
    }


async def holding_instrument_ids(
    conn: asyncpg.Connection, tenant_id: str
) -> list[str]:
    """Distinct instrument ids currently present in the tenant's projections."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT instrument_id
        FROM ledger.position_snapshots
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    return [str(r["instrument_id"]) for r in rows]


# field_code priority: intraday close / estimated NAV beat the dated official NAV.
_FIELD_PRIORITY = {F_CLOSE: 0, F_ESTIMATED_NAV: 0, F_NAV: 1}


async def latest_observation_quotes(
    conn: asyncpg.Connection, instrument_ids: list[str]
) -> dict[str, dict]:
    """Best known quote per instrument (latest observation, quality aware).

    Returns ``{instrument_id: {"price": Decimal, "quality": str}}`` — used to
    re-price the whole tenant's positions after a manual add.
    """
    if not instrument_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (subject_id) subject_id, field_code, value, quality_status
        FROM market.market_observations
        WHERE subject_id = ANY($1::uuid[]) AND value IS NOT NULL
        ORDER BY subject_id, as_of DESC
        """,
        instrument_ids,
    )
    best: dict[str, dict] = {}
    for row in rows:
        if row["quality_status"] == Q_UNAVAILABLE:
            continue
        instrument_id = str(row["subject_id"])
        priority = _FIELD_PRIORITY.get(row["field_code"], 9)
        current = best.get(instrument_id)
        if current is None or priority < current["_priority"]:
            best[instrument_id] = {
                "price": row["value"],
                "quality": row["quality_status"],
                "_priority": priority,
            }
    return {
        instrument_id: {"price": value["price"], "quality": value["quality"]}
        for instrument_id, value in best.items()
    }


async def search_instruments_local(
    conn: asyncpg.Connection, keyword: str, limit: int = 20
) -> list[asyncpg.Record]:
    """Local instrument search: name ILIKE or TICKER prefix/exact match."""
    return await conn.fetch(
        """
        SELECT DISTINCT ON (i.id)
            i.id, i.asset_type, i.primary_market, i.canonical_name,
            COALESCE(a.alias_value, '') AS ticker
        FROM reference.instruments i
        LEFT JOIN reference.instrument_aliases a
          ON a.instrument_id = i.id AND a.alias_type = 'TICKER'
          AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
        WHERE i.lifecycle_status = 'ACTIVE'
          AND (i.canonical_name ILIKE '%' || $1 || '%'
               OR a.alias_value LIKE $1 || '%'
               OR a.alias_value = $1)
        ORDER BY i.id, i.canonical_name
        LIMIT $2
        """,
        keyword,
        limit,
    )
