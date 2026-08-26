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
# 手动添加持仓：标的主数据 / 账户 / 不可变账本 / 行情 quotes
# ---------------------------------------------------------------------------
async def find_instrument_by_ticker(
    conn: asyncpg.Connection, ticker: str
) -> asyncpg.Record | None:
    """Resolve an instrument by its current TICKER alias."""
    return await conn.fetchrow(
        """
        SELECT i.id, i.asset_type, i.primary_market, i.canonical_name
        FROM reference.instruments i
        JOIN reference.instrument_aliases a ON a.instrument_id = i.id
        WHERE a.alias_type = 'TICKER' AND a.alias_value = $1
          AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
        ORDER BY a.effective_from DESC
        LIMIT 1
        """,
        ticker,
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

    ``reference.instruments`` has no natural unique key, so the alias table
    (UNIQUE on alias_type/alias_value/market/effective_from) is the dedupe
    anchor: whichever request wins the race creates the row, the others
    resolve to the same instrument id afterwards.
    """
    await conn.execute(
        """
        INSERT INTO reference.instruments
            (asset_type, primary_market, canonical_name, lifecycle_status)
        VALUES ($1, $2, $3, 'ACTIVE')
        ON CONFLICT DO NOTHING
        """,
        asset_type,
        primary_market,
        name,
    )
    instrument_id = await conn.fetchval(
        """
        SELECT id FROM reference.instruments
        WHERE canonical_name = $1 AND asset_type = $2
        ORDER BY created_at LIMIT 1
        """,
        name,
        asset_type,
    )
    if instrument_id is None:  # pragma: no cover — dedupe anchor raced nothing
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
        ON CONFLICT (alias_type, alias_value, market, effective_from)
        DO NOTHING
        """,
        instrument_id,
        ticker,
        primary_market,
    )
    resolved = await conn.fetchval(
        """
        SELECT i.id
        FROM reference.instruments i
        JOIN reference.instrument_aliases a ON a.instrument_id = i.id
        WHERE a.alias_type = 'TICKER' AND a.alias_value = $1
        ORDER BY a.effective_from DESC
        LIMIT 1
        """,
        ticker,
    )
    return str(resolved if resolved is not None else instrument_id)


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


async def insert_buy_event(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
    instrument_id: str,
    quantity: Decimal,
    avg_cost: Decimal,
    trade_date: date,
    idempotency_key: str,
) -> str:
    """Append a CONFIRMED BUY event with INSTRUMENT + CASH legs."""
    event_id = str(
        await conn.fetchval(
            """
            INSERT INTO ledger.ledger_events
                (tenant_id, account_id, event_type, status, trade_date,
                 idempotency_key, schema_version)
            VALUES ($1, $2, 'BUY', 'CONFIRMED', $3, $4, 1)
            RETURNING id
            """,
            tenant_id,
            account_id,
            trade_date,
            idempotency_key,
        )
    )
    cash_amount = -(quantity * avg_cost).quantize(Decimal("0.00000001"))
    await conn.executemany(
        """
        INSERT INTO ledger.ledger_legs
            (tenant_id, event_id, leg_type, instrument_id, currency,
             quantity, amount, unit_price)
        VALUES ($1, $2, $3, $4, 'CNY', $5, $6, $7)
        """,
        [
            (tenant_id, event_id, "INSTRUMENT", instrument_id, quantity, None, avg_cost),
            (tenant_id, event_id, "CASH", None, None, cash_amount, None),
        ],
    )
    return event_id


async def insert_cash_in_event(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
    amount: Decimal,
    trade_date: date,
    idempotency_key: str,
) -> str:
    """Append a CONFIRMED CASH_IN event (manual-holding principal injection).

    Pairs with the BUY's CASH leg so a manually entered holding stays cash
    neutral while the injected principal keeps ``principal`` (hence
    ``cumulative_pnl``) accurate for accounts without real cash-flow events.
    """
    event_id = str(
        await conn.fetchval(
            """
            INSERT INTO ledger.ledger_events
                (tenant_id, account_id, event_type, status, trade_date,
                 idempotency_key, schema_version)
            VALUES ($1, $2, 'CASH_IN', 'CONFIRMED', $3, $4, 1)
            RETURNING id
            """,
            tenant_id,
            account_id,
            trade_date,
            idempotency_key,
        )
    )
    await conn.execute(
        """
        INSERT INTO ledger.ledger_legs
            (tenant_id, event_id, leg_type, currency, amount)
        VALUES ($1, $2, 'CASH', 'CNY', $3)
        """,
        tenant_id,
        event_id,
        amount.quantize(Decimal("0.00000001")),
    )
    return event_id


async def insert_cash_out_event(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
    amount: Decimal,  # 负数（流出）
    trade_date: date,
    idempotency_key: str,
) -> str:
    """Append a CONFIRMED CASH_OUT event (manual-holding principal withdrawal).

    Pairs with the clear/SELL leg so removing a manually entered holding keeps
    cash neutral while ``principal`` (hence ``cumulative_pnl``) reflects the
    capital actually withdrawn.
    """
    event_id = str(
        await conn.fetchval(
            """
            INSERT INTO ledger.ledger_events
                (tenant_id, account_id, event_type, status, trade_date,
                 idempotency_key, schema_version)
            VALUES ($1, $2, 'CASH_OUT', 'CONFIRMED', $3, $4, 1)
            RETURNING id
            """,
            tenant_id,
            account_id,
            trade_date,
            idempotency_key,
        )
    )
    await conn.execute(
        """
        INSERT INTO ledger.ledger_legs
            (tenant_id, event_id, leg_type, currency, amount)
        VALUES ($1, $2, 'CASH', 'CNY', $3)
        """,
        tenant_id,
        event_id,
        amount.quantize(Decimal("0.00000001")),
    )
    return event_id


async def insert_adjustment_event(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
    instrument_id: str,
    quantity_delta: Decimal,  # 正=加仓/建仓，负=减仓/清仓
    unit_price: Decimal | None,
    cash_amount: Decimal | None,  # 配平现金（负=扣现金，正=回收现金）
    trade_date: date,
    idempotency_key: str,
) -> str:
    """Append a CONFIRMED ADJUSTMENT event with optional legs.

    A negative ``quantity_delta`` clears holdings through the ledger replay's
    SELL branch (proportional cost reduction); the paired CASH leg keeps the
    account cash reconciliation coherent.
    """
    event_id = str(
        await conn.fetchval(
            """
            INSERT INTO ledger.ledger_events
                (tenant_id, account_id, event_type, status, trade_date,
                 idempotency_key, schema_version)
            VALUES ($1, $2, 'ADJUSTMENT', 'CONFIRMED', $3, $4, 1)
            RETURNING id
            """,
            tenant_id,
            account_id,
            trade_date,
            idempotency_key,
        )
    )
    rows = []
    if quantity_delta != 0:
        rows.append(
            (
                tenant_id,
                event_id,
                "INSTRUMENT",
                instrument_id,
                quantity_delta,
                None,
                unit_price,
            )
        )
    if cash_amount is not None:
        rows.append(
            (tenant_id, event_id, "CASH", None, None, cash_amount, None)
        )
    if rows:
        await conn.executemany(
            """
            INSERT INTO ledger.ledger_legs
                (tenant_id, event_id, leg_type, instrument_id, currency,
                 quantity, amount, unit_price)
            VALUES ($1, $2, $3, $4, 'CNY', $5, $6, $7)
            """,
            rows,
        )
    return event_id


async def current_position(
    conn: asyncpg.Connection, tenant_id: str, symbol: str
) -> asyncpg.Record | None:
    """Latest open position for one ticker symbol (account+instrument)."""
    return await conn.fetchrow(
        """
        SELECT DISTINCT ON (ps.account_id, ps.instrument_id)
            ps.account_id, ps.instrument_id, ps.quantity, ps.avg_cost
        FROM ledger.position_snapshots ps
        JOIN reference.instruments i ON i.id = ps.instrument_id
        JOIN reference.instrument_aliases a ON a.instrument_id = i.id
        WHERE ps.tenant_id = $1
          AND a.alias_type = 'TICKER' AND a.alias_value = $2
          AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
        ORDER BY ps.account_id, ps.instrument_id, ps.as_of DESC
        LIMIT 1
        """,
        tenant_id,
        symbol,
    )


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
