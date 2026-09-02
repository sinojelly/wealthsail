"""Persistence operations for self-owned watchlist items."""

from __future__ import annotations

from asyncpg import Connection


async def find_active_instrument(conn: Connection, symbol: str):
    """Resolve a ticker to the authoritative active instrument metadata."""
    return await conn.fetchrow(
        """
        SELECT alias.alias_value AS symbol, instrument.canonical_name AS asset_name,
               CASE instrument.asset_type
                 WHEN 'CN_STOCK' THEN 'STOCK'
                 WHEN 'EXCHANGE_FUND' THEN 'ETF'
                 WHEN 'OFF_EXCHANGE_FUND' THEN 'FUND'
               END AS asset_type
        FROM reference.instrument_aliases alias
        JOIN reference.instruments instrument ON instrument.id = alias.instrument_id
        WHERE alias.alias_type='TICKER'
          AND upper(alias.alias_value)=upper($1)
          AND instrument.lifecycle_status='ACTIVE'
          AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
        ORDER BY alias.effective_from DESC
        LIMIT 1
        """,
        symbol,
    )


async def ensure_follow_instrument(
    conn: Connection,
    *,
    symbol: str,
    name: str,
    asset_type: str,
    market: str,
    currency: str = "CNY",
):
    """Register a missing fund/ETF in the reference master (SECURITY DEFINER).

    ``asset_type`` must be the internal enum value (``OFF_EXCHANGE_FUND`` /
    ``EXCHANGE_FUND``) and ``market`` one accepted by the instruments CHECK
    constraint (e.g. ``FUND_CN`` for OTC funds).  Returns the canonical
    master row or ``None``.
    """
    return await conn.fetchrow(
        """
        SELECT * FROM reference.ensure_watchlist_instrument($1, $2, $3, $4, $5)
        """,
        symbol.strip().upper(),
        name,
        asset_type,
        market,
        currency,
    )


async def list_watchlist(conn: Connection, user_id: str):
    return await conn.fetch(
        """
        SELECT symbol, asset_name, asset_type, added_at
        FROM identity.watchlist
        WHERE user_id = $1
        ORDER BY added_at DESC, symbol
        """,
        user_id,
    )


async def insert_watchlist_item(
    conn: Connection, user_id: str, symbol: str, asset_name: str, asset_type: str
):
    return await conn.fetchrow(
        """
        INSERT INTO identity.watchlist (user_id, symbol, asset_name, asset_type)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id, symbol) DO NOTHING
        RETURNING symbol, asset_name, asset_type, added_at
        """,
        user_id,
        symbol,
        asset_name,
        asset_type,
    )


async def get_watchlist_item(conn: Connection, user_id: str, symbol: str):
    return await conn.fetchrow(
        """
        SELECT symbol, asset_name, asset_type, added_at
        FROM identity.watchlist
        WHERE user_id = $1 AND symbol = $2
        """,
        user_id,
        symbol,
    )


async def delete_watchlist_item(conn: Connection, user_id: str, symbol: str):
    return await conn.fetchrow(
        """
        DELETE FROM identity.watchlist
        WHERE user_id = $1 AND symbol = $2
        RETURNING symbol, asset_name, asset_type, added_at
        """,
        user_id,
        symbol,
    )
