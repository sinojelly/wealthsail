"""Portfolio service: position/NAV aggregation and PnL computation.

Financial precision invariant: every amount is Decimal; the API layer
serializes them as decimal strings. No float is used anywhere in this module.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

import asyncpg

from backend.common.database import tenant_transaction
from backend.common.formatting import fmt_money, fmt_qty, merge_quality, pct
from backend.dependencies import SHANGHAI_TZ, now_shanghai, write_audit
from backend.errors import (
    DataUnavailableError,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError_,
)
from backend.integrations import market_data as md
from backend.market import repository as market_repository
from backend.portfolio import repository

# Asset type → frontend allocation category (A股 / 场内ETF / 场外基金 / 现金).
ASSET_CATEGORY: dict[str, str] = {
    "CN_STOCK": "A股",
    "EXCHANGE_FUND": "场内ETF",
    "OFF_EXCHANGE_FUND": "场外基金",
    "BOND": "债券",
    "GOLD_SPOT": "黄金",
}

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _d(value: object) -> Decimal:
    """Coerce a NUMERIC/None value to Decimal, never through float."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f"unexpected numeric type: {type(value).__name__}")


async def get_summary(conn: asyncpg.Connection, tenant_id: str, as_of: datetime) -> dict:
    """Aggregate the latest per-account NAV snapshots into the dashboard summary."""
    navs = await repository.latest_nav_per_account(conn, tenant_id)
    if not navs:
        raise DataUnavailableError(
            "当前租户暂无资产快照数据，请先完成期初导入",
            retryable=False,
        )

    complete = all(r["total_nav"] is not None for r in navs)
    total_asset = (
        sum((_d(r["total_nav"]) for r in navs), ZERO) if complete else None
    )
    known_value = sum(
        (_d(r.get("known_value") if hasattr(r, "get") else r["known_value"]) for r in navs),
        ZERO,
    )
    cash = sum((_d(r["cash_balance"]) for r in navs), ZERO)
    positions_value = (
        sum((_d(r["positions_market_value"]) for r in navs), ZERO)
        if all(r["positions_market_value"] is not None for r in navs)
        else None
    )
    daily_pnl = (
        sum((_d(r["daily_pnl"]) for r in navs), ZERO)
        if all(r["daily_pnl"] is not None for r in navs)
        else None
    )
    cumulative_pnl = (
        sum((_d(r["cumulative_pnl"]) for r in navs), ZERO)
        if all(r["cumulative_pnl"] is not None for r in navs)
        else None
    )
    quality = merge_quality(r["data_quality_status"] for r in navs)
    coverage = min(_d(r.get("valuation_coverage_pct", Decimal("100"))) for r in navs)
    unpriced_count = sum(int(r.get("unpriced_position_count", 0) or 0) for r in navs)

    positions = await repository.latest_positions(conn, tenant_id)
    allocation = _build_allocation(positions, cash, total_asset)

    return {
        "totalAsset": fmt_money(total_asset) if total_asset is not None else None,
        "knownValue": fmt_money(known_value),
        "valuationCoveragePct": fmt_money(coverage),
        "unpricedPositionCount": unpriced_count,
        "currency": "CNY",
        "cashBalance": fmt_money(cash),
        "positionsMarketValue": (
            fmt_money(positions_value) if positions_value is not None else None
        ),
        "dailyPnl": fmt_money(daily_pnl) if daily_pnl is not None else None,
        # A reliable daily return requires cash-flow-adjusted/TWR data. Until
        # that projection is available, do not publish a fabricated ratio.
        "dailyPnlRate": None,
        "cumulativePnl": (
            fmt_money(cumulative_pnl) if cumulative_pnl is not None else None
        ),
        "cumulativePnlRate": (
            fmt_money(pct(cumulative_pnl, total_asset - cumulative_pnl))
            if cumulative_pnl is not None and total_asset is not None
            else None
        ),
        "allocation": allocation,
        "qualityStatus": quality,
        # A combined portfolio is only as current as its oldest account
        # snapshot. Returning the request clock would overstate freshness.
        "asOf": min(r["as_of"] for r in navs),
        "positionCount": len(positions),
    }


def _build_allocation(
    positions: list[asyncpg.Record], cash: Decimal, total_asset: Decimal | None
) -> list[dict]:
    """Allocation by asset category (A股/场内ETF/场外基金/现金 …) with weights."""
    by_category: dict[str, Decimal] = {}
    for row in positions:
        cat = ASSET_CATEGORY.get(row["asset_type"], "其他")
        by_category[cat] = by_category.get(cat, ZERO) + _d(row["market_value"])
    by_category["现金"] = cash

    return [
        {
            "category": category,
            "amount": fmt_money(amount),
            "value": fmt_money(pct(amount, total_asset)) if total_asset is not None else None,
        }
        for category, amount in sorted(
            by_category.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


async def get_positions(conn: asyncpg.Connection, tenant_id: str) -> list[dict]:
    """Latest holdings with market value, weight, PnL and quality status."""
    rows = await repository.latest_positions(conn, tenant_id)
    navs = await repository.latest_nav_per_account(conn, tenant_id)
    total_asset = (
        sum((_d(r["total_nav"]) for r in navs), ZERO)
        if all(r["total_nav"] is not None for r in navs)
        else None
    )

    result: list[dict] = []
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            _d(row["market_value"])
            if row["market_value"] is not None
            else Decimal("-Infinity")
        ),
        reverse=True,
    )
    for row in sorted_rows:
        quantity = _d(row["quantity"])
        avg_cost = _d(row["avg_cost"])
        price = row["market_price"]
        market_value = _d(row["market_value"]) if row["market_value"] is not None else None
        pnl = row["unrealized_pnl"]

        if market_value is None and price is not None:
            market_value = (quantity * _d(price)).quantize(CENT)
        if pnl is None and market_value is not None:
            pnl = market_value - (quantity * avg_cost)
        cost_value = quantity * avg_cost
        pnl_rate = pct(_d(pnl), cost_value) if pnl is not None and cost_value else None

        result.append(
            {
                "symbol": row["ticker"],
                "name": row["canonical_name"],
                "type": ASSET_CATEGORY.get(row["asset_type"], row["asset_type"]),
                "quantity": fmt_qty(quantity),
                "avgCost": fmt_money(avg_cost),
                "price": fmt_money(_d(price)) if price is not None else None,
                "marketValue": fmt_money(market_value) if market_value is not None else None,
                "weight": (
                    fmt_money(pct(market_value, total_asset))
                    if market_value is not None and total_asset is not None
                    else None
                ),
                "pnl": fmt_money(_d(pnl)) if pnl is not None else None,
                "pnlRate": fmt_money(pnl_rate) if pnl_rate is not None else None,
                "quality": merge_quality([row["data_quality_status"]]),
                "accountId": str(row["account_id"]),
            }
        )
    return result


async def get_nav_trend(
    conn: asyncpg.Connection, tenant_id: str, days: int = 60
) -> dict:
    """Daily NAV trend (points) — benchmark left null until index data exists."""
    rows = await repository.nav_history(conn, tenant_id, limit=days)
    if not rows:
        raise DataUnavailableError(
            "暂无净值历史数据", retryable=False, field_errors=[]
        )
    points = [
        {
            "date": r["as_of"].isoformat(),
            "totalNav": (
                fmt_money(_d(r["total_nav"])) if r["total_nav"] is not None else None
            ),
            "dailyPnl": (
                fmt_money(_d(r["daily_pnl"])) if r["daily_pnl"] is not None else None
            ),
            "cashBalance": fmt_money(_d(r["cash_balance"])),
        }
        for r in rows
    ]
    return {
        "points": points,
        "benchmark": None,
        "currency": "CNY",
        "timezone": str(SHANGHAI_TZ),
    }


# ---------------------------------------------------------------------------
# 手动添加持仓：标的搜索 / 录入 / 投影重算
# ---------------------------------------------------------------------------
# 对外资产类型（前端）→ 内部 asset_type
PUBLIC_ASSET_TYPE: dict[str, str] = {
    "STOCK": "CN_STOCK",
    "ETF": "EXCHANGE_FUND",
    "FUND": "OFF_EXCHANGE_FUND",
}
INTERNAL_ASSET_TYPE: dict[str, str] = {
    value: key for key, value in PUBLIC_ASSET_TYPE.items()
}

_SYMBOL_RE = re.compile(r"^(\d{6})")


def normalize_symbol(symbol: str) -> str:
    """Strip exchange suffixes and validate the 6-digit code."""
    match = _SYMBOL_RE.match(symbol.strip().upper())
    if not match:
        raise ValidationError_(
            "证券代码须为 6 位数字（如 600519 / 510300 / 110022）",
            field_errors=[{"field": "symbol", "message": "须为 6 位数字代码"}],
        )
    return match.group(1)


def _primary_market(asset_type: str, symbol: str) -> str:
    if asset_type == "OFF_EXCHANGE_FUND":
        return "FUND_CN"
    if symbol[0] in "69":
        return "SSE"
    if symbol[0] in "02":
        return "SZSE"
    if symbol[0] in "34":
        return "BSE"
    return "SZSE"


async def _ak_search(keyword: str) -> list[dict]:
    """Real-time instrument search via AkShare (A股/ETF/场外基金), fail-safe.

    Any vendor failure silently degrades to the local instrument master —
    the search must never block the add-position flow.
    """
    try:
        import akshare as ak  # noqa: PLC0415
    except ImportError:
        return []

    kw = keyword.strip()
    results: dict[str, dict] = {}

    def _market(code: str) -> str:
        if code[0] in "69":
            return "SSE"
        if code[0] in "02":
            return "SZSE"
        if code[0] in "34":
            return "BSE"
        return "SZSE"

    async def _run(fetcher, asset_type: str, market_fn) -> None:
        try:
            df = await asyncio.wait_for(asyncio.to_thread(fetcher), timeout=15)
            if df is None or df.empty:
                return
            mask = df["代码"].astype(str).str.contains(kw, na=False) | df[
                "名称"
            ].astype(str).str.contains(kw, na=False)
            for _, row in df[mask].head(10).iterrows():
                code = str(row["代码"]).strip()
                results.setdefault(
                    code,
                    {
                        "symbol": code,
                        "name": str(row["名称"]).strip(),
                        "market": market_fn(code),
                        "type": asset_type,
                        "source": "live",
                    },
                )
        except Exception:  # noqa: BLE001 — 搜索失败静默降级到本地主数据
            return

    await asyncio.gather(
        _run(lambda: ak.stock_zh_a_spot_em(), "STOCK", _market),
        _run(lambda: ak.fund_etf_spot_em(), "ETF", _market),
        _run(lambda: ak.fund_name_em(), "FUND", lambda _code: "FUND_CN"),
    )
    return list(results.values())


async def search_instruments(
    pool: asyncpg.Pool, ctx, keyword: str
) -> dict:
    """Search stocks/ETFs/funds by Chinese name or 6-digit code.

    Local instrument master first, then AkShare realtime enrichment
    (degradation-safe); results are deduplicated by symbol.
    """
    kw = keyword.strip()
    if not kw:
        raise ValidationError_(
            "搜索关键词不能为空",
            field_errors=[{"field": "keyword", "message": "不能为空"}],
        )
    merged: dict[str, dict] = {}
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        rows = await repository.search_instruments_local(conn, kw)
        for row in rows:
            if not row["ticker"]:
                continue
            merged[row["ticker"]] = {
                "symbol": row["ticker"],
                "name": row["canonical_name"],
                "market": row["primary_market"],
                "type": INTERNAL_ASSET_TYPE.get(row["asset_type"], "STOCK"),
                "source": "local",
            }
    for item in await _ak_search(kw):
        merged.setdefault(item["symbol"], item)
    items = sorted(merged.values(), key=lambda x: (x["type"], x["symbol"]))[:20]
    return {"items": items, "total": len(items)}


async def _fetch_quote(symbol: str, asset_type: str) -> md.Observation | None:
    """Best-effort live quote for the newly added instrument."""
    try:
        if asset_type == "CN_STOCK":
            observations = await md.fetch_equity_quotes([symbol])
        elif asset_type == "EXCHANGE_FUND":
            observations = await md.fetch_etf_quotes([symbol])
        else:
            observations = await md.fetch_fund_observations([symbol])
        return next(
            (o for o in observations if o.symbol == symbol and o.value is not None),
            None,
        )
    except Exception:  # noqa: BLE001 — 行情失败不阻断录入（投影按 UNAVAILABLE 记账）
        return None


async def _recompute_projections(
    conn: asyncpg.Connection, *, tenant_id: str, extra_quotes: dict | None = None
) -> None:
    """Recompute today's position + NAV projections for the tenant.

    quotes cover every held instrument (persisted observations + extra live
    quotes for the instrument being mutated), so no other position degrades
    to UNAVAILABLE during the replay.
    """
    held_ids = await repository.holding_instrument_ids(conn, tenant_id)
    quotes = await repository.latest_observation_quotes(conn, held_ids)
    if extra_quotes:
        quotes.update(extra_quotes)
    as_of_date = now_shanghai().date()
    await market_repository.refresh_positions(
        conn, tenant_id=tenant_id, quotes=quotes, as_of_date=as_of_date
    )
    await market_repository.refresh_account_nav(
        conn, tenant_id=tenant_id, as_of_date=as_of_date
    )


async def add_position(pool: asyncpg.Pool, ctx, payload) -> dict:
    """Record a user-entered holding: BUY ledger event + projection rebuild.

    The holding is appended to the immutable ledger (CONFIRMED BUY with
    INSTRUMENT + CASH legs), then today's position/NAV projections are
    recomputed from the ledger with the best known quotes — so the new
    position survives any later market-refresh replay.
    """
    symbol = normalize_symbol(payload.symbol)
    internal_type = PUBLIC_ASSET_TYPE.get(payload.assetType)
    if internal_type is None:
        raise ValidationError_(
            f"不支持的资产类型: {payload.assetType}（仅支持 STOCK / ETF / FUND）",
            field_errors=[{"field": "assetType", "message": "仅支持 STOCK/ETF/FUND"}],
        )
    try:
        quantity = Decimal(str(payload.quantity))
        avg_cost = Decimal(str(payload.avgCost))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValidationError_(
            "数量与成本价必须是有效数字",
            field_errors=[
                {"field": "quantity", "message": "必须是数字"},
                {"field": "avgCost", "message": "必须是数字"},
            ],
        ) from exc
    if quantity <= 0:
        raise ValidationError_(
            "数量必须大于 0", field_errors=[{"field": "quantity", "message": "必须大于 0"}]
        )
    if avg_cost < 0:
        raise ValidationError_(
            "成本价不能为负", field_errors=[{"field": "avgCost", "message": "不能为负"}]
        )
    trade_date = payload.buyDate or now_shanghai().date()
    name = (payload.name or "").strip() or symbol

    # 行情抓取是网络 IO，必须在租户事务之外执行。
    quote = await _fetch_quote(symbol, internal_type)

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        inst = await repository.find_instrument_by_ticker(conn, symbol)
        if inst is not None:
            instrument_id = str(inst["id"])
            if inst["asset_type"] != internal_type:
                raise ValidationError_(
                    f"标的 {symbol} 已存在（类型为 "
                    f"{INTERNAL_ASSET_TYPE.get(inst['asset_type'], inst['asset_type'])}），"
                    "请核对后重试",
                    field_errors=[{"field": "assetType", "message": "与已存在标的类型不一致"}],
                )
        else:
            instrument_id = await repository.upsert_instrument(
                conn,
                ticker=symbol,
                name=name,
                asset_type=internal_type,
                primary_market=_primary_market(internal_type, symbol),
            )

        if payload.accountId:
            owned = await conn.fetchval(
                """
                SELECT id FROM identity.accounts
                WHERE id = $1 AND tenant_id = $2 AND status = 'ACTIVE'
                """,
                payload.accountId,
                ctx.tenant_id,
            )
            if owned is None:
                raise ValidationError_(
                    "账户不存在或不属于当前租户",
                    field_errors=[{"field": "accountId", "message": "账户无效"}],
                )
            account_id = str(owned)
        else:
            account_id = await repository.pick_or_create_account(
                conn, tenant_id=ctx.tenant_id, asset_type=internal_type
            )

        idempotency_key = payload.idempotencyKey or f"buy:{symbol}:{uuid.uuid4().hex}"
        try:
            # 本金注入配平：手动录入的持仓不是现金流水，等额 CASH_IN 保持
            # 现金中性，同时让 principal（→ cumulative_pnl）反映真实投入。
            await repository.insert_cash_in_event(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                amount=quantity * avg_cost,
                trade_date=trade_date,
                idempotency_key=f"cashin:{idempotency_key}",
            )
            event_id = await repository.insert_buy_event(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                instrument_id=instrument_id,
                quantity=quantity,
                avg_cost=avg_cost,
                trade_date=trade_date,
                idempotency_key=idempotency_key,
            )
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise IdempotencyConflictError(
                "该持仓添加请求已提交过（幂等键重复），请勿重复提交"
            ) from exc

        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action="POSITION_ADDED",
            resource_type="ledger.ledger_events",
            resource_id=event_id,
            details={
                "symbol": symbol,
                "quantity": str(quantity),
                "avgCost": str(avg_cost),
                "tradeDate": trade_date.isoformat(),
            },
        )

        # 重算今日投影：quotes 覆盖租户全部持仓（库内观测 + 新标的实时价），
        # 避免 refresh_positions 将其他持仓降级为 UNAVAILABLE。
        extra_quotes = (
            {instrument_id: {"price": quote.value, "quality": quote.quality_status}}
            if quote is not None
            else None
        )
        await _recompute_projections(
            conn, tenant_id=ctx.tenant_id, extra_quotes=extra_quotes
        )

    price = quote.value if quote is not None else None
    market_value = (
        (quantity * price).quantize(Decimal("0.01")) if price is not None else None
    )
    pnl = (
        (market_value - quantity * avg_cost).quantize(Decimal("0.01"))
        if market_value is not None
        else None
    )
    cost_value = quantity * avg_cost
    pnl_rate = pct(pnl, cost_value) if pnl is not None and cost_value else None
    return {
        "position": {
            "symbol": symbol,
            "name": name,
            "type": payload.assetType,
            "quantity": fmt_qty(quantity),
            "avgCost": fmt_money(avg_cost),
            "price": fmt_money(price) if price is not None else None,
            "marketValue": fmt_money(market_value) if market_value is not None else None,
            "pnl": fmt_money(pnl) if pnl is not None else None,
            "pnlRate": fmt_money(pnl_rate) if pnl_rate is not None else None,
            "quality": quote.quality_status if quote is not None else "UNAVAILABLE",
            "accountId": account_id,
            "buyDate": trade_date.isoformat(),
            "eventId": event_id,
        }
    }


def _position_response(
    *,
    symbol: str,
    name: str,
    asset_type: str,
    quantity: Decimal,
    avg_cost: Decimal,
    price: Decimal | None,
    quality: str,
    account_id: str,
    trade_date: date,
    event_id: str,
) -> dict:
    """Build the uniform position payload shared by add/update/delete flows."""
    market_value = (
        (quantity * price).quantize(Decimal("0.01")) if price is not None else None
    )
    pnl = (
        (market_value - quantity * avg_cost).quantize(Decimal("0.01"))
        if market_value is not None
        else None
    )
    cost_value = quantity * avg_cost
    pnl_rate = pct(pnl, cost_value) if pnl is not None and cost_value else None
    return {
        "symbol": symbol,
        "name": name,
        "type": asset_type,
        "quantity": fmt_qty(quantity),
        "avgCost": fmt_money(avg_cost),
        "price": fmt_money(price) if price is not None else None,
        "marketValue": fmt_money(market_value) if market_value is not None else None,
        "pnl": fmt_money(pnl) if pnl is not None else None,
        "pnlRate": fmt_money(pnl_rate) if pnl_rate is not None else None,
        "quality": quality,
        "accountId": account_id,
        "buyDate": trade_date.isoformat(),
        "eventId": event_id,
    }


async def update_position(pool: asyncpg.Pool, ctx, symbol: str, payload) -> dict:
    """Edit a holding: clear old ledger state, rebuild with the new values.

    Expressed as ledger events (clear + rebuild with paired principal
    in/out), so the edit survives market-refresh replays and keeps cash
    neutral with ``principal`` tracking the actual capital invested.
    """
    symbol = normalize_symbol(symbol)
    try:
        quantity = Decimal(str(payload.quantity))
        avg_cost = Decimal(str(payload.avgCost))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValidationError_(
            "数量与成本价必须是有效数字",
            field_errors=[
                {"field": "quantity", "message": "必须是数字"},
                {"field": "avgCost", "message": "必须是数字"},
            ],
        ) from exc
    if quantity <= 0:
        raise ValidationError_(
            "数量必须大于 0", field_errors=[{"field": "quantity", "message": "必须大于 0"}]
        )
    if avg_cost < 0:
        raise ValidationError_(
            "成本价不能为负", field_errors=[{"field": "avgCost", "message": "不能为负"}]
        )
    trade_date = payload.buyDate or now_shanghai().date()

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        current = await repository.current_position(conn, ctx.tenant_id, symbol)
        if current is None:
            raise NotFoundError(f"未找到持仓 {symbol}，请先添加")
        account_id = str(current["account_id"])
        instrument_id = str(current["instrument_id"])
        old_quantity = Decimal(str(current["quantity"]))
        old_avg_cost = Decimal(str(current["avg_cost"]))
        old_cost = (old_quantity * old_avg_cost).quantize(Decimal("0.01"))
        new_cost = (quantity * avg_cost).quantize(Decimal("0.01"))
        name = await conn.fetchval(
            "SELECT canonical_name FROM reference.instruments WHERE id = $1",
            instrument_id,
        ) or symbol
        asset_type = await conn.fetchval(
            "SELECT asset_type FROM reference.instruments WHERE id = $1",
            instrument_id,
        )

        base_key = payload.idempotencyKey or f"update:{symbol}:{uuid.uuid4().hex}"
        try:
            # 1) 清空旧持仓（负 delta 走重放 SELL 分支归零成本；CASH 腿回收现金）
            await repository.insert_adjustment_event(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                instrument_id=instrument_id,
                quantity_delta=-old_quantity,
                unit_price=None,
                cash_amount=old_cost,
                trade_date=trade_date,
                idempotency_key=f"{base_key}:clear",
            )
            # 2) 本金退出配平（principal 随投入变化）
            await repository.insert_cash_out_event(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                amount=-old_cost,
                trade_date=trade_date,
                idempotency_key=f"{base_key}:cashout",
            )
            # 3) 重建新持仓
            await repository.insert_adjustment_event(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                instrument_id=instrument_id,
                quantity_delta=quantity,
                unit_price=avg_cost,
                cash_amount=-new_cost,
                trade_date=trade_date,
                idempotency_key=f"{base_key}:rebuild",
            )
            # 4) 本金注入配平（现金保持中性）
            await repository.insert_cash_in_event(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                amount=new_cost,
                trade_date=trade_date,
                idempotency_key=f"{base_key}:cashin",
            )
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise IdempotencyConflictError(
                "该编辑请求已提交过（幂等键重复），请勿重复提交"
            ) from exc

        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action="POSITION_UPDATED",
            resource_type="ledger.ledger_events",
            resource_id=f"{base_key}:rebuild",
            details={
                "symbol": symbol,
                "oldQuantity": str(old_quantity),
                "oldAvgCost": str(old_avg_cost),
                "quantity": str(quantity),
                "avgCost": str(avg_cost),
                "tradeDate": trade_date.isoformat(),
            },
        )
        await _recompute_projections(conn, tenant_id=ctx.tenant_id)
        quotes = await repository.latest_observation_quotes(conn, [instrument_id])
        quote = quotes.get(instrument_id)

    return {
        "position": _position_response(
            symbol=symbol,
            name=name,
            asset_type=INTERNAL_ASSET_TYPE.get(str(asset_type), "STOCK"),
            quantity=quantity,
            avg_cost=avg_cost,
            price=quote["price"] if quote else None,
            quality=quote["quality"] if quote else "UNAVAILABLE",
            account_id=account_id,
            trade_date=trade_date,
            event_id=f"{base_key}:rebuild",
        )
    }


async def delete_position(pool: asyncpg.Pool, ctx, symbol: str) -> dict:
    """Remove a holding: clear ledger state + principal withdrawal."""
    symbol = normalize_symbol(symbol)
    trade_date = now_shanghai().date()

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        current = await repository.current_position(conn, ctx.tenant_id, symbol)
        if current is None:
            raise NotFoundError(f"未找到持仓 {symbol}")
        account_id = str(current["account_id"])
        instrument_id = str(current["instrument_id"])
        old_quantity = Decimal(str(current["quantity"]))
        old_avg_cost = Decimal(str(current["avg_cost"]))
        old_cost = (old_quantity * old_avg_cost).quantize(Decimal("0.01"))
        name = await conn.fetchval(
            "SELECT canonical_name FROM reference.instruments WHERE id = $1",
            instrument_id,
        ) or symbol

        base_key = f"delete:{symbol}:{uuid.uuid4().hex}"
        await repository.insert_adjustment_event(
            conn,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            instrument_id=instrument_id,
            quantity_delta=-old_quantity,
            unit_price=None,
            cash_amount=old_cost,
            trade_date=trade_date,
            idempotency_key=f"{base_key}:clear",
        )
        await repository.insert_cash_out_event(
            conn,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            amount=-old_cost,
            trade_date=trade_date,
            idempotency_key=f"{base_key}:cashout",
        )
        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action="POSITION_REMOVED",
            resource_type="ledger.ledger_events",
            resource_id=f"{base_key}:clear",
            details={
                "symbol": symbol,
                "quantity": str(old_quantity),
                "avgCost": str(old_avg_cost),
            },
        )
        await _recompute_projections(conn, tenant_id=ctx.tenant_id)

    return {
        "removed": {
            "symbol": symbol,
            "name": name,
            "quantity": fmt_qty(old_quantity),
        }
    }
