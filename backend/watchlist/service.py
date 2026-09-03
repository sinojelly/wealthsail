"""Canonical validation and audited mutations for a user's watchlist."""

from __future__ import annotations

from datetime import datetime

from asyncpg import Connection

from backend.common.tenancy import TenantContext
from backend.dependencies import write_audit
from backend.errors import NotFoundError, ValidationError_
from backend.watchlist import repository

# 公开资产类型 → reference.instruments.asset_type 内部枚举。
_PUBLIC_TO_DB = {
    "STOCK": "CN_STOCK",
    "ETF": "EXCHANGE_FUND",
    "FUND": "OFF_EXCHANGE_FUND",
}
_DB_TO_PUBLIC = {
    "CN_STOCK": "STOCK",
    "EXCHANGE_FUND": "ETF",
    "OFF_EXCHANGE_FUND": "FUND",
}


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValidationError_("标的代码不能为空")
    if len(normalized) > 32:
        raise ValidationError_("标的代码不能超过 32 个字符")
    return normalized


def _resolve_primary_market(
    asset_type: str, market: str | None, symbol: str
) -> str | None:
    """把搜索/请求里的市场解析成 instruments.primary_market 合法值。"""
    candidate = (market or "").strip().upper()
    if candidate in {"SSE", "SZSE", "BSE", "FUND_CN", "SGE"}:
        return candidate
    if asset_type == "FUND":
        # 场外基金统一登记到 FUND_CN 市场。
        return "FUND_CN"
    if asset_type == "ETF":
        # 场内 ETF 按代码段推断交易所（5xxxxx→上交所，1xxxxx→深交所）；
        # 未知前缀则要求调用方显式提供 market。
        if symbol.startswith("5"):
            return "SSE"
        if symbol.startswith("1"):
            return "SZSE"
        return None
    return candidate or None


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


async def _fetch_live_candidates(symbol: str) -> list[dict]:
    """实时权威候选（基金/ETF/股票数据集），仅用于主数据缺失时的兜底登记。"""
    from backend.portfolio import service as portfolio_service

    return await portfolio_service.instrument_live_candidates(symbol)


async def add_watchlist_item(
    conn: Connection,
    ctx: TenantContext,
    symbol: str,
    *,
    name: str | None = None,
    asset_type: str | None = None,
    market: str | None = None,
    currency: str = "CNY",
) -> dict:
    """Add a watch item; symbols missing from the master are auto-registered.

    优先采用客户端（搜索结果）携带的 ``name``/``asset_type``/``market``；
    未携带时用实时数据集兜底：唯一候选直接登记，多类型歧义（如 000001
    既是股票又是基金）则给出明确错误，绝不猜。登记失败抛明确业务错误。
    """
    normalized = _normalize_symbol(symbol)
    instrument = await repository.find_active_instrument(conn, normalized)
    if instrument is None or instrument["asset_type"] is None:
        public_type = (asset_type or "").upper()
        db_type = _PUBLIC_TO_DB.get(public_type)
        resolved_name = name
        # 客户端未提供类型/名称时，尝试按实时数据集精确解析唯一候选。
        if db_type is None or not resolved_name:
            candidates = await _fetch_live_candidates(normalized)
            candidates = [
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and candidate.get("type") in _PUBLIC_TO_DB
            ]
            if not candidates:
                raise ValidationError_(
                    "标的未通过主数据验证；如需关注基金，请从搜索结果携带名称与类型后重试"
                )
            distinct = sorted(
                {(candidate["type"], candidate["market"]) for candidate in candidates}
            )
            if len(distinct) > 1:
                kinds = "、".join(f"{kind}({market})" for kind, market in distinct)
                raise ValidationError_(
                    f"标的代码 {normalized} 同时匹配多种类型（{kinds}），"
                    "无法自动登记，请从搜索结果选择具体标的后再关注"
                )
            chosen = candidates[0]
            public_type = chosen["type"]
            db_type = _PUBLIC_TO_DB[public_type]
            resolved_name = str(chosen.get("name") or "")
            market = str(chosen.get("market") or market or "")
        if db_type is None or not (resolved_name or "").strip():
            raise ValidationError_(
                "标的未通过主数据验证；缺少可用于自动登记的名称与类型"
            )
        primary_market = _resolve_primary_market(public_type, market, normalized)
        if primary_market is None:
            raise ValidationError_(
                f"标的未通过主数据验证，且无法确定其市场（market={market or '未提供'}），"
                "请从搜索结果携带 market 后重试"
            )
        try:
            created = await repository.ensure_follow_instrument(
                conn,
                symbol=normalized,
                name=resolved_name.strip(),
                asset_type=db_type,
                market=primary_market,
                currency=currency,
            )
        except Exception as exc:  # noqa: BLE001 — 把底层登记错误转成明确业务错误
            raise ValidationError_(
                f"自动登记主数据失败，未能添加关注：{exc.__class__.__name__}"
            ) from exc
        if created is None:
            raise RuntimeError("自动登记主数据后未返回记录")
        instrument = {
            "symbol": str(created["symbol"] or normalized).upper(),
            "asset_name": str(created["asset_name"] or name),
            "asset_type": _DB_TO_PUBLIC.get(str(created["asset_type"]), public_type),
        }

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
