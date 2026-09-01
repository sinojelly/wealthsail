"""Canonical validation and audited mutations for a user's watchlist."""

from __future__ import annotations

from datetime import datetime

from asyncpg import Connection

from backend.common.tenancy import TenantContext
from backend.dependencies import write_audit
from backend.errors import NotFoundError, ValidationError_
from backend.watchlist import repository


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValidationError_("标的代码不能为空")
    if len(normalized) > 32:
        raise ValidationError_("标的代码不能超过 32 个字符")
    return normalized


def _item_dto(row) -> dict:
    added_at = row["added_at"]
    return {
        "symbol": row["symbol"],
        "assetName": row["asset_name"],
        "assetType": row["asset_type"],
        "addedAt": added_at.isoformat() if isinstance(added_at, datetime) else added_at,
    }


async def list_watchlist(conn: Connection, user_id: str) -> list[dict]:
    rows = await repository.list_watchlist(conn, user_id)
    return [_item_dto(row) for row in rows]


async def add_watchlist_item(conn: Connection, ctx: TenantContext, symbol: str) -> dict:
    normalized = _normalize_symbol(symbol)
    instrument = await repository.find_active_instrument(conn, normalized)
    if instrument is None or instrument["asset_type"] is None:
        raise ValidationError_("标的未通过主数据验证")

    canonical_symbol = str(instrument["symbol"]).strip().upper()
    row = await repository.insert_watchlist_item(
        conn,
        ctx.user_id,
        canonical_symbol,
        instrument["asset_name"],
        instrument["asset_type"],
    )
    if row is None:
        row = await repository.get_watchlist_item(conn, ctx.user_id, canonical_symbol)
        if row is None:
            raise RuntimeError("watchlist conflict row disappeared")
    else:
        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action="WATCHLIST_ITEM_ADDED",
            resource_type="identity.watchlist",
            resource_id=canonical_symbol,
            details={"symbol": canonical_symbol, "assetType": instrument["asset_type"]},
        )
    return _item_dto(row)


async def remove_watchlist_item(
    conn: Connection, ctx: TenantContext, symbol: str
) -> dict:
    normalized = _normalize_symbol(symbol)
    row = await repository.delete_watchlist_item(conn, ctx.user_id, normalized)
    if row is None:
        raise NotFoundError("关注标的不存在")
    await write_audit(
        conn,
        tenant_id=ctx.tenant_id,
        actor_type="USER",
        actor_id=ctx.user_id,
        action="WATCHLIST_ITEM_REMOVED",
        resource_type="identity.watchlist",
        resource_id=row["symbol"],
        details={"symbol": row["symbol"], "assetType": row["asset_type"]},
    )
    return _item_dto(row)
