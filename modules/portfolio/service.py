"""持仓、账户与现金流领域服务（modules/portfolio）。

自包含的数据库驱动服务：注入 asyncpg 连接池（及可选的外部标的数据 provider），
不依赖 backend.*。不可变账本语义贯穿全部写路径——持仓的添加/编辑/删除分别
落为 BUY / (REVERSAL + BUY) / REVERSAL 事件，任何已确认事件不可原地修改；
每次写操作后在同一事务内重放 CONFIRMED 事件重建当日快照。

职责：
  * 持仓管理：添加、编辑（冲销重录）、删除（冲销）、列表（含最新行情与市值）
  * 持仓搜索：本地主数据优先，外部行情源（AkShare）实时补充，失败静默降级
  * 账户管理：创建、更新、归档资金账户
  * 现金流：存入/取出/费用/分红/利息（利息以 ADJUSTMENT + 现金腿表达）
  * 快照重算：position_snapshots 与 account_nav_snapshots 当日投影重建

金额一律 Decimal；行情价缺失时按 fail-closed 语义标记 UNAVAILABLE，绝不
用请求时刻的假想价格冒充。
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Awaitable, Callable, Protocol

import asyncpg

from modules.database import AsyncConnection, AsyncPool, tenant_transaction
from modules.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError

logger = logging.getLogger("wealthsail.modules.portfolio")

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
QTY = Decimal("0.0000")

# 对外资产类型（前端）→ 内部 asset_type（与 003 迁移 CHECK 对齐）
PUBLIC_ASSET_TYPE: dict[str, str] = {
    "STOCK": "CN_STOCK",
    "ETF": "EXCHANGE_FUND",
    "FUND": "OFF_EXCHANGE_FUND",
}
INTERNAL_ASSET_TYPE: dict[str, str] = {
    value: key for key, value in PUBLIC_ASSET_TYPE.items()
}
ASSET_CATEGORY: dict[str, str] = {
    "CN_STOCK": "A股",
    "EXCHANGE_FUND": "场内ETF",
    "OFF_EXCHANGE_FUND": "场外基金",
    "BOND": "债券",
    "GOLD_SPOT": "黄金",
}

_SYMBOL_RE = re.compile(r"^(\d{6})(?:\.(?:SH|SZ|BJ))?$")


def _d(value: object) -> Decimal:
    """把 NUMERIC/None 规整为 Decimal，绝不经过 float。"""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f"unexpected numeric type: {type(value).__name__}")


def normalize_symbol(symbol: str) -> str:
    """剥离交易所后缀并校验 6 位代码。"""
    match = _SYMBOL_RE.match((symbol or "").strip().upper())
    if not match:
        raise ValidationError(
            "证券代码须为 6 位数字（如 600519 / 510300 / 110022）"
        )
    return match.group(1)


def _primary_market(asset_type: str, symbol: str) -> str:
    """按资产类型与代码推断主市场（与 backend.portfolio.service 一致）。"""
    if asset_type == "OFF_EXCHANGE_FUND":
        return "FUND_CN"
    if symbol.startswith(("4", "8", "92")):
        return "BSE"
    if symbol[0] in "569":
        return "SSE"
    return "SZSE"


# ---------------------------------------------------------------------------
# 投影重放（纯函数，与 backend/market/repository.py 的语义一致）
# ---------------------------------------------------------------------------
@dataclass
class HoldingProjection:
    """按账户+标的重放后的开放持仓。"""

    account_id: str
    instrument_id: str
    quantity: Decimal = Decimal("0.0000")
    cost_basis: Decimal = Decimal("0.00")

    @property
    def avg_cost(self) -> Decimal:
        if self.quantity <= 0:
            return Decimal("0.0000")
        return (self.cost_basis / self.quantity).quantize(Decimal("0.0001"))


def rebuild_confirmed_holdings(
    events: list[dict[str, Any]],
) -> dict[tuple[str, str], HoldingProjection]:
    """重放已过滤的 CONFIRMED 事件腿，得到 (account_id, instrument_id) → 持仓。

    Args:
        events: [{account_id, event_type, legs: [{leg_type, instrument_id,
            quantity, amount, unit_price}]}]，按 occurred_at 升序。

    Returns:
        净数量 > 0 的持仓映射（零持仓自动消失）。

    Raises:
        ValueError: 账本重放将产生负持仓（账本一致性错误）。
    """
    holdings: dict[tuple[str, str], HoldingProjection] = {}
    for event in events:
        legs = event.get("legs") or []
        if not isinstance(legs, list):
            raise ValueError("ledger event legs must be a JSON array")
        instrument_legs = [leg for leg in legs if leg.get("leg_type") == "INSTRUMENT"]
        for leg in instrument_legs:
            instrument_id = str(leg["instrument_id"])
            account_id = str(event["account_id"])
            key = (account_id, instrument_id)
            state = holdings.setdefault(
                key,
                HoldingProjection(account_id=account_id, instrument_id=instrument_id),
            )
            quantity = _d(leg.get("quantity"))
            if quantity > 0:
                if event.get("event_type") != "DIVIDEND_REINVEST":
                    cash_cost = sum(
                        (
                            abs(_d(part.get("amount")))
                            for part in legs
                            if part.get("leg_type") in {"CASH", "FEE", "TAX"}
                            and _d(part.get("amount")) < 0
                        ),
                        ZERO,
                    )
                    if cash_cost == 0:
                        cash_cost = quantity * _d(leg.get("unit_price"))
                    state.cost_basis += cash_cost
                state.quantity += quantity
            elif quantity < 0:
                sold = abs(quantity)
                if sold > state.quantity:
                    raise ValueError("confirmed ledger would create a negative position")
                remaining = state.quantity - sold
                state.cost_basis = (
                    state.cost_basis * remaining / state.quantity
                    if state.quantity > 0
                    else ZERO
                )
                state.quantity = remaining
    return {key: state for key, state in holdings.items() if state.quantity > 0}


@dataclass(frozen=True)
class AccountNavValuation:
    """账户净资产估值（fail-closed：存在未定价持仓则 total_nav 为 None）。"""

    total_nav: Decimal | None
    known_value: Decimal
    coverage_pct: Decimal
    unpriced_position_count: int
    quality: str


def derive_account_nav(
    *,
    cash_balance: Decimal,
    principal: Decimal,
    priced_positions_value: Decimal,
    total_position_count: int,
    priced_position_count: int,
) -> AccountNavValuation:
    """按已定价覆盖率推导账户净资产与质量状态。"""
    known_value = (cash_balance + priced_positions_value).quantize(CENT)
    unpriced = max(total_position_count - priced_position_count, 0)
    coverage = (
        Decimal("100.00")
        if total_position_count == 0
        else (Decimal(priced_position_count) / Decimal(total_position_count) * Decimal("100")).quantize(
            Decimal("0.01")
        )
    )
    return AccountNavValuation(
        total_nav=known_value if unpriced == 0 else None,
        known_value=known_value,
        coverage_pct=coverage,
        unpriced_position_count=unpriced,
        quality="FRESH" if unpriced == 0 else "ESTIMATED",
    )


# ---------------------------------------------------------------------------
# 外部标的数据 Provider（可注入，便于测试）
# ---------------------------------------------------------------------------
class InstrumentSearchProvider(Protocol):
    """外部实时标的数据源（AkShare 等），失败必须返回空列表而非抛出。"""

    async def search(self, keyword: str) -> list[dict[str, str]]: ...


class _AkShareProvider:
    """AkShare 实时标的搜索的 Provider 适配（任何失败静默降级）。"""

    async def search(self, keyword: str) -> list[dict[str, str]]:
        try:
            import akshare as ak  # noqa: PLC0415
        except ImportError:
            return []

        kw = keyword.strip()
        results: dict[str, dict[str, str]] = {}

        async def _load(cache_key: str, fetcher, asset_type: str, market_fn) -> None:
            try:
                df = await asyncio.to_thread(fetcher)
                if df is None or df.empty or not {"代码", "名称"}.issubset(df.columns):
                    return
                for _, row in df.iterrows():
                    code = str(row["代码"]).strip().zfill(6)
                    if not code or not (kw in code or kw.casefold() in str(row["名称"]).casefold()):
                        continue
                    results.setdefault(
                        f"{market_fn(code)}:{code}:{asset_type}",
                        {
                            "symbol": code,
                            "name": str(row["名称"]).strip(),
                            "market": market_fn(code),
                            "type": asset_type,
                            "source": "live",
                        },
                    )
            except Exception:  # noqa: BLE001 — 搜索失败静默降级到本地主数据
                logger.debug("外部标的搜索 %s 失败（已降级）", cache_key)

        def _market(code: str) -> str:
            return _primary_market("CN_STOCK", code)

        await asyncio.gather(
            _load("stock_zh_a_spot_em", lambda: ak.stock_zh_a_spot_em(), "STOCK", _market),
            _load("fund_etf_spot_em", lambda: ak.fund_etf_spot_em(), "ETF", _market),
            _load("fund_name_em", lambda: ak.fund_name_em(), "FUND", lambda _c: "FUND_CN"),
        )
        return list(results.values())


class PortfolioService:
    """持仓、账户与现金流服务。"""

    def __init__(
        self,
        pool: AsyncPool,
        *,
        search_provider: InstrumentSearchProvider | None = None,
    ) -> None:
        self.pool = pool
        self._search_provider: InstrumentSearchProvider = (
            search_provider or _AkShareProvider()
        )

    # -- 审计 ---------------------------------------------------------------
    @staticmethod
    async def _write_audit(
        conn: AsyncConnection,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO audit.audit_log
                (tenant_id, actor_type, actor_id, action, resource_type,
                 resource_id, correlation_id, details)
            VALUES ($1, 'USER', $2, $3, $4, $5, $6, $7)
            """,
            tenant_id,
            actor_id,
            action,
            resource_type,
            resource_id,
            None,
            (details or {}),
        )

    # -- 持仓搜索 -------------------------------------------------------------
    async def search_instruments(self, actor: TenantContext, keyword: str) -> dict:
        """按名称/代码搜索可添加标的：本地主数据 + 外部实时（降级安全）。"""
        kw = (keyword or "").strip()
        if not kw:
            raise ValidationError("搜索关键词不能为空")

        merged: dict[str, dict[str, str]] = {}
        async with tenant_transaction(self.pool, actor.user_id, actor.tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT i.id, i.asset_type, i.canonical_name, i.primary_market,
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
                  AND (
                      a.alias_value ILIKE $1
                      OR i.canonical_name ILIKE $1
                  )
                LIMIT 50
                """,
                f"%{kw}%",
            )
            for row in rows:
                if not row["ticker"]:
                    continue
                identity = (
                    f"{row['primary_market']}:{row['ticker']}:"
                    f"{INTERNAL_ASSET_TYPE.get(row['asset_type'], 'STOCK')}"
                )
                merged[identity] = {
                    "symbol": row["ticker"],
                    "name": row["canonical_name"],
                    "market": row["primary_market"],
                    "type": INTERNAL_ASSET_TYPE.get(row["asset_type"], "STOCK"),
                    "source": "local",
                }

        # 外部源补充（AkShare 实时快照；失败静默，不阻塞主流程）
        try:
            for item in await self._search_provider.search(kw):
                identity = f"{item['market']}:{item['symbol']}:{item['type']}"
                merged.setdefault(identity, item)
        except Exception:  # noqa: BLE001 — 外部源异常绝不阻断搜索
            logger.warning("外部标的搜索异常，仅返回本地结果: %s", kw)

        items = sorted(merged.values(), key=lambda x: (x["type"], x["symbol"]))[:20]
        return {"items": items, "total": len(items)}

    # -- 持仓管理 -------------------------------------------------------------
    async def add_position(
        self,
        actor: TenantContext,
        *,
        account_id: str,
        symbol: str,
        asset_type: str,
        quantity: Decimal,
        unit_cost: Decimal,
        trade_date: date,
        idempotency_key: str,
        name: str | None = None,
        fee: Decimal = ZERO,
    ) -> dict:
        """添加一笔买入持仓（BUY 事件 + 现金/费用腿 + 快照重算）。

        Raises:
            ValidationError: 参数非法或账户不属于租户。
            ConflictError: 幂等键冲突且内容不一致。
            NotFoundError: 标的无法从任何数据源验证。
        """
        symbol = normalize_symbol(symbol)
        internal_type = PUBLIC_ASSET_TYPE.get(asset_type)
        if internal_type is None:
            raise ValidationError("仅支持 STOCK / ETF / FUND")
        quantity = _d(quantity)
        unit_cost = _d(unit_cost)
        fee = _d(fee)
        if quantity <= 0:
            raise ValidationError("数量必须大于 0")
        if unit_cost < 0:
            raise ValidationError("成本价不能为负")
        if fee < 0:
            raise ValidationError("手续费不能为负")
        _validate_idempotency_key(idempotency_key)

        async with tenant_transaction(self.pool, actor.user_id, actor.tenant_id) as conn:
            existing = await self._find_event_by_key(conn, actor.tenant_id, idempotency_key)
            if existing is not None:
                return self._replay_response(existing, actor)

            instrument = await self._resolve_instrument(
                conn, actor, symbol=symbol, internal_type=internal_type, name=name
            )
            await self._require_active_account(conn, actor.tenant_id, account_id)

            event_id = await self._insert_buy_event(
                conn,
                actor=actor,
                account_id=account_id,
                instrument_id=instrument["id"],
                quantity=quantity,
                unit_cost=unit_cost,
                fee=fee,
                trade_date=trade_date,
                idempotency_key=idempotency_key,
                description=f"买入持仓 {symbol}",
            )
            await self._recompute_projections(conn, actor.tenant_id)
            await self._write_audit(
                conn,
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action="PORTFOLIO_POSITION_ADDED",
                resource_type="ledger.ledger_events",
                resource_id=event_id,
                details={"symbol": symbol, "assetType": asset_type},
            )

        logger.info("持仓已添加: tenant=%s symbol=%s qty=%s", actor.tenant_id, symbol, quantity)
        return {
            "eventId": event_id,
            "symbol": symbol,
            "name": instrument["name"],
            "assetType": asset_type,
            "quantity": fmt_qty(quantity),
            "unitCost": fmt_money(unit_cost),
            "tradeDate": trade_date.isoformat(),
            "accountId": account_id,
            "status": "CONFIRMED",
        }

    async def edit_position(
        self,
        actor: TenantContext,
        *,
        event_id: str,
        quantity: Decimal,
        unit_cost: Decimal,
        idempotency_key: str,
        fee: Decimal = ZERO,
    ) -> dict:
        """编辑一笔已确认买入：冲销原事件（REVERSAL）后重录新 BUY。

        不可变账本下没有原地修改：经济事实以追加方式表达。
        """
        quantity = _d(quantity)
        unit_cost = _d(unit_cost)
        fee = _d(fee)
        if quantity <= 0:
            raise ValidationError("数量必须大于 0")
        if unit_cost < 0:
            raise ValidationError("成本价不能为负")
        if fee < 0:
            raise ValidationError("手续费不能为负")
        _validate_idempotency_key(idempotency_key)

        async with tenant_transaction(self.pool, actor.user_id, actor.tenant_id) as conn:
            existing = await self._find_event_by_key(conn, actor.tenant_id, idempotency_key)
            if existing is not None:
                return self._replay_response(existing, actor)

            original = await self._load_buy_event(conn, actor.tenant_id, event_id)
            reversal_id = await self._insert_reversal(
                conn,
                actor=actor,
                original=original,
                idempotency_key=idempotency_key,
                description=f"冲销买入 {original['ticker']} 后重录",
            )
            new_id = await self._insert_buy_event(
                conn,
                actor=actor,
                account_id=str(original["account_id"]),
                instrument_id=str(original["instrument_id"]),
                quantity=quantity,
                unit_cost=unit_cost,
                fee=fee,
                trade_date=original["trade_date"],
                idempotency_key=f"{idempotency_key}:rebuy",
                description=f"重录买入 {original['ticker']}",
            )
            await self._recompute_projections(conn, actor.tenant_id)
            await self._write_audit(
                conn,
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action="PORTFOLIO_POSITION_EDITED",
                resource_type="ledger.ledger_events",
                resource_id=event_id,
                details={"reversalEventId": reversal_id, "newEventId": new_id},
            )

        logger.info("持仓已编辑: tenant=%s event=%s", actor.tenant_id, event_id)
        return {
            "reversalEventId": reversal_id,
            "eventId": new_id,
            "status": "CONFIRMED",
        }

    async def delete_position(
        self,
        actor: TenantContext,
        *,
        event_id: str,
        idempotency_key: str,
    ) -> dict:
        """删除一笔买入持仓：以 REVERSAL 事件冲销，不做物理删除。"""
        _validate_idempotency_key(idempotency_key)

        async with tenant_transaction(self.pool, actor.user_id, actor.tenant_id) as conn:
            existing = await self._find_event_by_key(conn, actor.tenant_id, idempotency_key)
            if existing is not None:
                return self._replay_response(existing, actor)

            original = await self._load_buy_event(conn, actor.tenant_id, event_id)
            reversal_id = await self._insert_reversal(
                conn,
                actor=actor,
                original=original,
                idempotency_key=idempotency_key,
                description=f"删除持仓 {original['ticker']}",
            )
            await self._recompute_projections(conn, actor.tenant_id)
            await self._write_audit(
                conn,
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action="PORTFOLIO_POSITION_DELETED",
                resource_type="ledger.ledger_events",
                resource_id=event_id,
                details={"reversalEventId": reversal_id},
            )

        logger.info("持仓已删除: tenant=%s event=%s", actor.tenant_id, event_id)
        return {"reversalEventId": reversal_id, "status": "CONFIRMED"}

    async def list_positions(
        self, conn: AsyncConnection, actor: TenantContext
    ) -> dict:
        """最新持仓列表（含行情、市值、盈亏与质量状态），按市值降序。

        Args:
            conn: 调用方租户事务内的连接（复用同一事务）。
        """
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (ps.account_id, ps.instrument_id)
                ps.account_id, ps.instrument_id, ps.as_of, ps.quantity,
                ps.avg_cost, ps.market_price, ps.market_value,
                ps.unrealized_pnl, ps.data_quality_status,
                i.canonical_name, i.asset_type, i.primary_market,
                COALESCE(a.alias_value, '') AS ticker
            FROM ledger.position_snapshots ps
            JOIN reference.instruments i ON i.id = ps.instrument_id
            LEFT JOIN LATERAL (
                SELECT alias_value
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
            actor.tenant_id,
        )
        total_value = sum(
            (_d(r["market_value"]) for r in rows if r["market_value"] is not None),
            ZERO,
        )
        items: list[dict[str, Any]] = []
        for row in sorted(
            rows,
            key=lambda r: (
                _d(r["market_value"]) if r["market_value"] is not None else Decimal("-Infinity")
            ),
            reverse=True,
        ):
            quantity = _d(row["quantity"])
            avg_cost = _d(row["avg_cost"])
            price = _d(row["market_price"]) if row["market_price"] is not None else None
            market_value = _d(row["market_value"]) if row["market_value"] is not None else None
            pnl = _d(row["unrealized_pnl"]) if row["unrealized_pnl"] is not None else None
            if market_value is None and price is not None:
                market_value = (quantity * price).quantize(CENT)
            if pnl is None and market_value is not None:
                pnl = market_value - (quantity * avg_cost)
            cost_value = quantity * avg_cost
            pnl_rate = pct(pnl, cost_value) if pnl is not None and cost_value else None
            items.append(
                {
                    "symbol": row["ticker"],
                    "name": row["canonical_name"],
                    "type": ASSET_CATEGORY.get(row["asset_type"], row["asset_type"]),
                    "quantity": fmt_qty(quantity),
                    "avgCost": fmt_money(avg_cost),
                    "price": fmt_money(price) if price is not None else None,
                    "marketValue": fmt_money(market_value) if market_value is not None else None,
                    "weight": (
                        fmt_money(pct(market_value, total_value))
                        if market_value is not None and total_value
                        else None
                    ),
                    "pnl": fmt_money(pnl) if pnl is not None else None,
                    "pnlRate": fmt_money(pnl_rate) if pnl_rate is not None else None,
                    "quality": row["data_quality_status"],
                    "accountId": str(row["account_id"]),
                    "asOf": row["as_of"].isoformat(),
                }
            )
        return {
            "items": items,
            "total": len(items),
            "totalMarketValue": fmt_money(total_value) if total_value else "0.00",
            "currency": "CNY",
        }

    # -- 账户管理 -------------------------------------------------------------
    async def create_account(
        self,
        actor: TenantContext,
        *,
        name: str,
        account_type: str,
        base_currency: str = "CNY",
    ) -> dict:
        """创建资金账户（券商/基金平台/手工）。"""
        clean_name = (name or "").strip()
        if not clean_name or len(clean_name) > 64:
            raise ValidationError("账户名称必填且不超过 64 个字符")
        if account_type not in {"BROKERAGE", "FUND_PLATFORM", "MANUAL"}:
            raise ValidationError("账户类型仅支持 BROKERAGE / FUND_PLATFORM / MANUAL")
        if len(base_currency) != 3 or not base_currency.isalpha():
            raise ValidationError("币种须为 3 位字母代码")

        async with tenant_transaction(self.pool, actor.user_id, actor.tenant_id) as conn:
            account_id = await conn.fetchval(
                """
                INSERT INTO identity.accounts
                    (tenant_id, account_type, name, base_currency, status)
                VALUES ($1, $2, $3, $4, 'ACTIVE')
                RETURNING id
                """,
                actor.tenant_id,
                account_type,
                clean_name,
                base_currency.upper(),
            )
            await self._write_audit(
                conn,
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action="ACCOUNT_CREATED",
                resource_type="identity.accounts",
                resource_id=str(account_id),
                details={"accountType": account_type},
            )

        logger.info("账户已创建: tenant=%s account=%s", actor.tenant_id, account_id)
        return {"accountId": str(account_id), "name": clean_name, "status": "ACTIVE"}

    async def update_account(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
        *,
        account_id: str,
        name: str | None = None,
    ) -> dict:
        """更新账户名称（须在调用方租户事务内）。"""
        if name is None:
            raise ValidationError("没有需要更新的字段")
        clean_name = (name or "").strip()
        if not clean_name or len(clean_name) > 64:
            raise ValidationError("账户名称必填且不超过 64 个字符")

        row = await conn.fetchrow(
            """
            UPDATE identity.accounts
               SET name = $1, updated_at = now(), version = version + 1
             WHERE id = $2 AND tenant_id = $3 AND status = 'ACTIVE'
            RETURNING id, name
            """,
            clean_name,
            account_id,
            actor.tenant_id,
        )
        if row is None:
            raise NotFoundError("账户不存在、已归档或不属于当前租户")
        await self._write_audit(
            conn,
            tenant_id=actor.tenant_id,
            actor_id=actor.user_id,
            action="ACCOUNT_UPDATED",
            resource_type="identity.accounts",
            resource_id=str(row["id"]),
            details={"name": clean_name},
        )
        return {"accountId": str(row["id"]), "name": row["name"]}

    async def archive_account(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
        *,
        account_id: str,
    ) -> dict:
        """归档资金账户：存在非零现金或未平仓持仓时拒绝归档。"""
        row = await conn.fetchrow(
            """
            SELECT a.id, a.name,
                   COALESCE(nav.cash_balance, 0) AS cash_balance,
                   COALESCE(ps.quantity, 0) AS open_quantity
            FROM identity.accounts a
            LEFT JOIN LATERAL (
                SELECT cash_balance
                FROM ledger.account_nav_snapshots
                WHERE tenant_id = a.tenant_id AND account_id = a.id
                ORDER BY as_of DESC
                LIMIT 1
            ) nav ON TRUE
            LEFT JOIN LATERAL (
                SELECT SUM(quantity) AS quantity
                FROM ledger.position_snapshots
                WHERE tenant_id = a.tenant_id AND account_id = a.id
                ORDER BY as_of DESC
                LIMIT 1
            ) ps ON TRUE
            WHERE a.id = $1 AND a.tenant_id = $2 AND a.status = 'ACTIVE'
            """,
            account_id,
            actor.tenant_id,
        )
        if row is None:
            raise NotFoundError("账户不存在、已归档或不属于当前租户")
        if _d(row["cash_balance"]) != 0 or _d(row["open_quantity"]) != 0:
            raise ForbiddenError("账户仍有资金或持仓，不能归档")

        updated = await conn.fetchrow(
            """
            UPDATE identity.accounts
               SET status = 'CLOSED', closed_on = CURRENT_DATE,
                   updated_at = now(), version = version + 1
             WHERE id = $1 AND tenant_id = $2 AND status = 'ACTIVE'
            RETURNING id, status
            """,
            account_id,
            actor.tenant_id,
        )
        if updated is None:
            raise NotFoundError("账户不存在、已归档或不属于当前租户")
        await self._write_audit(
            conn,
            tenant_id=actor.tenant_id,
            actor_id=actor.user_id,
            action="ACCOUNT_ARCHIVED",
            resource_type="identity.accounts",
            resource_id=str(updated["id"]),
            details={"closedOn": date.today().isoformat()},
        )
        return {"accountId": str(updated["id"]), "status": updated["status"]}

    async def list_accounts(
        self, conn: AsyncConnection, actor: TenantContext
    ) -> dict:
        """当前租户的账户列表（含最近现金余额）。"""
        rows = await conn.fetch(
            """
            SELECT a.id, a.account_type, a.name, a.base_currency, a.status,
                   a.opened_on, a.closed_on,
                   COALESCE(nav.cash_balance, 0) AS cash_balance
            FROM identity.accounts a
            LEFT JOIN LATERAL (
                SELECT cash_balance
                FROM ledger.account_nav_snapshots
                WHERE tenant_id = a.tenant_id AND account_id = a.id
                ORDER BY as_of DESC
                LIMIT 1
            ) nav ON TRUE
            WHERE a.tenant_id = $1
            ORDER BY a.status = 'ACTIVE' DESC, a.created_at ASC
            """,
            actor.tenant_id,
        )
        return {
            "items": [
                {
                    "accountId": str(r["id"]),
                    "name": r["name"],
                    "accountType": r["account_type"],
                    "baseCurrency": r["base_currency"],
                    "status": r["status"],
                    "openedOn": r["opened_on"].isoformat(),
                    "closedOn": r["closed_on"].isoformat() if r["closed_on"] else None,
                    "cashBalance": fmt_money(_d(r["cash_balance"])),
                }
                for r in rows
            ],
            "total": len(rows),
        }

    # -- 现金流 ---------------------------------------------------------------
    async def record_cash_in(
        self,
        actor: TenantContext,
        *,
        account_id: str,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "资金入账",
    ) -> dict:
        """记录资金存入（CASH_IN 事件，正现金腿）。"""
        return await self._record_cash_event(
            actor,
            account_id=account_id,
            amount=amount,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            event_type="CASH_IN",
            leg_type="CASH",
            description=description,
            action="CASH_IN_RECORDED",
        )

    async def record_cash_out(
        self,
        actor: TenantContext,
        *,
        account_id: str,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "资金出账",
    ) -> dict:
        """记录资金取出（CASH_OUT 事件，负现金腿）。"""
        return await self._record_cash_event(
            actor,
            account_id=account_id,
            amount=amount,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            event_type="CASH_OUT",
            leg_type="CASH",
            description=description,
            action="CASH_OUT_RECORDED",
        )

    async def record_fee(
        self,
        actor: TenantContext,
        *,
        account_id: str,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "费用支出",
    ) -> dict:
        """记录独立费用（FEE 事件，费用腿）。"""
        return await self._record_cash_event(
            actor,
            account_id=account_id,
            amount=amount,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            event_type="FEE",
            leg_type="FEE",
            description=description,
            action="FEE_RECORDED",
        )

    async def record_dividend(
        self,
        actor: TenantContext,
        *,
        account_id: str,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "现金分红",
    ) -> dict:
        """记录现金分红到账（DIVIDEND_CASH 事件，正现金腿）。"""
        return await self._record_cash_event(
            actor,
            account_id=account_id,
            amount=amount,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            event_type="DIVIDEND_CASH",
            leg_type="CASH",
            description=description,
            action="DIVIDEND_RECORDED",
        )

    async def record_interest(
        self,
        actor: TenantContext,
        *,
        account_id: str,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "利息收入",
    ) -> dict:
        """记录利息收入（账本无 INTEREST 类型，以 ADJUSTMENT + 正现金腿表达）。"""
        return await self._record_cash_event(
            actor,
            account_id=account_id,
            amount=amount,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            event_type="ADJUSTMENT",
            leg_type="CASH",
            description=description,
            action="INTEREST_RECORDED",
        )

    async def _record_cash_event(
        self,
        actor: TenantContext,
        *,
        account_id: str,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        event_type: str,
        leg_type: str,
        description: str,
        action: str,
    ) -> dict:
        """统一的现金流事件写入（含幂等与快照重算）。"""
        amount = _d(amount)
        if amount <= 0:
            raise ValidationError("金额必须大于 0")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValidationError("Idempotency-Key 必填且不能超过 128 个字符")

        async with tenant_transaction(self.pool, actor.user_id, actor.tenant_id) as conn:
            existing = await self._find_event_by_key(conn, actor.tenant_id, idempotency_key)
            if existing is not None:
                return self._replay_response(existing, actor)
            await self._require_active_account(conn, actor.tenant_id, account_id)

            signed = amount if event_type in {"CASH_IN", "DIVIDEND_CASH", "ADJUSTMENT"} else -amount
            event_id = await self._insert_simple_event(
                conn,
                actor=actor,
                account_id=account_id,
                event_type=event_type,
                trade_date=trade_date,
                idempotency_key=idempotency_key,
                description=description,
                leg_type=leg_type,
                amount=signed,
            )
            await self._recompute_projections(conn, actor.tenant_id)
            await self._write_audit(
                conn,
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=action,
                resource_type="ledger.ledger_events",
                resource_id=event_id,
                details={"amount": str(amount), "accountId": account_id},
            )

        logger.info("现金流事件已记录: tenant=%s type=%s amount=%s", actor.tenant_id, event_type, amount)
        return {
            "eventId": event_id,
            "eventType": event_type,
            "amount": fmt_money(amount),
            "tradeDate": trade_date.isoformat(),
            "accountId": account_id,
            "status": "CONFIRMED",
        }

    # -- 快照重算 -------------------------------------------------------------
    async def recompute_snapshots(self, actor: TenantContext) -> dict:
        """重放账本并重建当日持仓与账户净资产快照（手动触发入口）。"""
        async with tenant_transaction(self.pool, actor.user_id, actor.tenant_id) as conn:
            positions = await self._refresh_positions(conn, actor.tenant_id)
            navs = await self._refresh_account_nav(conn, actor.tenant_id)
        logger.info(
            "快照重算完成: tenant=%s positions=%d navs=%d",
            actor.tenant_id, positions, navs,
        )
        return {"positionsUpdated": positions, "accountsUpdated": navs}

    # -- 私有实现 -------------------------------------------------------------
    async def _resolve_instrument(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
        *,
        symbol: str,
        internal_type: str,
        name: str | None,
    ) -> dict[str, str]:
        """解析标的：本地主数据优先；缺失时经外部数据源验证后落主数据。"""
        expected_market = _primary_market(internal_type, symbol)
        row = await conn.fetchrow(
            """
            SELECT i.id, i.asset_type, i.canonical_name, i.primary_market
            FROM reference.instruments i
            LEFT JOIN LATERAL (
                SELECT alias_value
                FROM reference.instrument_aliases
                WHERE instrument_id = i.id AND alias_type = 'TICKER'
                  AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
                ORDER BY effective_from DESC
                LIMIT 1
            ) a ON TRUE
            WHERE a.alias_value = $1 AND i.asset_type = $2
            LIMIT 1
            """,
            symbol,
            internal_type,
        )
        if row is not None:
            if row["asset_type"] != internal_type:
                raise ValidationError(f"标的 {symbol} 的资产类型与主数据不一致")
            return {
                "id": str(row["id"]),
                "name": row["canonical_name"],
                "asset_type": internal_type,
                "market": row["primary_market"],
            }

        # 主数据缺失：外部源验证（精确匹配代码+类型），成功则登记主数据
        candidates = await self._search_provider.search(symbol)
        public_type = INTERNAL_ASSET_TYPE[internal_type]
        exact = next(
            (
                c
                for c in candidates
                if c["symbol"] == symbol and c["type"] == public_type
            ),
            None,
        )
        if exact is None:
            raise NotFoundError(
                "暂时无法从行情源验证该标的，请稍后重试或等待主数据同步"
            )
        verified_name = (name or "").strip() or exact["name"]
        instrument_id = await conn.fetchval(
            """
            INSERT INTO reference.instruments
                (id, asset_type, primary_market, trading_currency,
                 canonical_name, lifecycle_status)
            VALUES (gen_random_uuid(), $1, $2, 'CNY', $3, 'ACTIVE')
            RETURNING id
            """,
            internal_type,
            expected_market,
            verified_name,
        )
        await conn.execute(
            """
            INSERT INTO reference.instrument_aliases
                (instrument_id, alias_type, alias_value, market)
            VALUES ($1, 'TICKER', $2, $3)
            """,
            instrument_id,
            symbol,
            expected_market,
        )
        return {
            "id": str(instrument_id),
            "name": verified_name,
            "asset_type": internal_type,
            "market": expected_market,
        }

    async def _require_active_account(
        self, conn: AsyncConnection, tenant_id: str, account_id: str
    ) -> None:
        account_id = await conn.fetchval(
            """
            SELECT id FROM identity.accounts
            WHERE id = $1 AND tenant_id = $2 AND status = 'ACTIVE'
            """,
            account_id,
            tenant_id,
        )
        if account_id is None:
            raise NotFoundError("账户不存在、已归档或不属于当前租户")

    @staticmethod
    async def _find_event_by_key(
        conn: AsyncConnection, tenant_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            SELECT id AS event_id, event_type, account_id, trade_date, description
            FROM ledger.ledger_events
            WHERE tenant_id = $1 AND idempotency_key = $2
            """,
            tenant_id,
            idempotency_key,
        )
        return dict(row) if row is not None else None

    @staticmethod
    async def _load_buy_event(
        conn: AsyncConnection, tenant_id: str, event_id: str
    ) -> dict[str, Any]:
        """读取可冲销的 BUY 事件（须为确认态且未被逆转）。"""
        row = await conn.fetchrow(
            """
            SELECT ev.id, ev.account_id, ev.trade_date, ev.description,
                   leg.instrument_id,
                   COALESCE(a.alias_value, '') AS ticker
            FROM ledger.ledger_events ev
            JOIN ledger.ledger_legs leg
              ON leg.tenant_id = ev.tenant_id AND leg.event_id = ev.id
             AND leg.leg_type = 'INSTRUMENT'
            LEFT JOIN LATERAL (
                SELECT alias_value
                FROM reference.instrument_aliases
                WHERE instrument_id = leg.instrument_id
                  AND alias_type = 'TICKER'
                  AND (effective_to IS NULL OR effective_to >= ev.trade_date)
                ORDER BY effective_from DESC
                LIMIT 1
            ) a ON TRUE
            WHERE ev.id = $1 AND ev.tenant_id = $2
              AND ev.event_type = 'BUY' AND ev.status = 'CONFIRMED'
              AND NOT EXISTS (
                  SELECT 1 FROM ledger.ledger_events r
                  WHERE r.tenant_id = ev.tenant_id
                    AND r.status = 'CONFIRMED'
                    AND r.event_type = 'REVERSAL'
                    AND r.reverses_event_id = ev.id
              )
            LIMIT 1
            """,
            event_id,
            tenant_id,
        )
        if row is None:
            raise NotFoundError("买入事件不存在、未确认或已被冲销")
        return dict(row)

    async def _insert_buy_event(
        self,
        conn: AsyncConnection,
        *,
        actor: TenantContext,
        account_id: str,
        instrument_id: str,
        quantity: Decimal,
        unit_cost: Decimal,
        fee: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str,
    ) -> str:
        """写 BUY 事件及其 INSTRUMENT/CASH/FEE 腿（单事务内）。"""
        event_id = await conn.fetchval(
            """
            INSERT INTO ledger.ledger_events
                (tenant_id, account_id, event_type, status, trade_date,
                 idempotency_key, description)
            VALUES ($1, $2, 'BUY', 'CONFIRMED', $3, $4, $5)
            RETURNING id
            """,
            actor.tenant_id,
            account_id,
            trade_date,
            idempotency_key,
            description,
        )
        legs: list[tuple] = [
            (
                actor.tenant_id,
                event_id,
                "INSTRUMENT",
                instrument_id,
                quantity,
                None,
                unit_cost,
            ),
            (
                actor.tenant_id,
                event_id,
                "CASH",
                None,
                None,
                -(quantity * unit_cost),
                None,
            ),
        ]
        if fee > 0:
            legs.append((actor.tenant_id, event_id, "FEE", None, None, -fee, None))
        await conn.executemany(
            """
            INSERT INTO ledger.ledger_legs
                (tenant_id, event_id, leg_type, instrument_id, quantity, amount, unit_price)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            legs,
        )
        return str(event_id)

    async def _insert_simple_event(
        self,
        conn: AsyncConnection,
        *,
        actor: TenantContext,
        account_id: str,
        event_type: str,
        trade_date: date,
        idempotency_key: str,
        description: str,
        leg_type: str,
        amount: Decimal,
    ) -> str:
        """写单腿现金流事件（CASH/FEE/TAX 腿）。"""
        event_id = await conn.fetchval(
            """
            INSERT INTO ledger.ledger_events
                (tenant_id, account_id, event_type, status, trade_date,
                 idempotency_key, description)
            VALUES ($1, $2, $3, 'CONFIRMED', $4, $5, $6)
            RETURNING id
            """,
            actor.tenant_id,
            account_id,
            event_type,
            trade_date,
            idempotency_key,
            description,
        )
        await conn.execute(
            """
            INSERT INTO ledger.ledger_legs
                (tenant_id, event_id, leg_type, currency, amount)
            VALUES ($1, $2, $3, 'CNY', $4)
            """,
            actor.tenant_id,
            event_id,
            leg_type,
            amount,
        )
        return str(event_id)

    async def _insert_reversal(
        self,
        conn: AsyncConnection,
        *,
        actor: TenantContext,
        original: dict[str, Any],
        idempotency_key: str,
        description: str,
    ) -> str:
        """写 REVERSAL 事件（无腿；重放时排除该类型且跳过被逆转事件）。"""
        reversal_id = await conn.fetchval(
            """
            INSERT INTO ledger.ledger_events
                (tenant_id, account_id, event_type, status, trade_date,
                 idempotency_key, description, reverses_event_id)
            VALUES ($1, $2, 'REVERSAL', 'CONFIRMED', $3, $4, $5, $6)
            RETURNING id
            """,
            actor.tenant_id,
            original["account_id"],
            original["trade_date"],
            idempotency_key,
            description,
            original["id"],
        )
        return str(reversal_id)

    @staticmethod
    def _replay_response(existing: dict[str, Any], actor: TenantContext) -> dict:
        """幂等重放：同一幂等键的重复请求返回既有事件（不重复记账）。"""
        logger.info("幂等重放: tenant=%s key 已存在 event=%s", actor.tenant_id, existing["event_id"])
        return {
            "eventId": existing["event_id"],
            "eventType": existing["event_type"],
            "accountId": str(existing["account_id"]),
            "tradeDate": existing["trade_date"].isoformat(),
            "status": "CONFIRMED",
            "replayed": True,
        }

    async def _recompute_projections(
        self, conn: AsyncConnection, tenant_id: str
    ) -> None:
        """重放账本并重建当日持仓与账户 NAV 快照（与写入同一事务）。"""
        as_of_date = datetime.now().astimezone().date()
        await self._refresh_positions(conn, tenant_id, as_of_date=as_of_date)
        await self._refresh_account_nav(conn, tenant_id, as_of_date=as_of_date)

    async def _refresh_positions(
        self, conn: AsyncConnection, tenant_id: str, *, as_of_date: date | None = None
    ) -> int:
        """重建指定日期的持仓快照（先删当日投影再确定性重放）。"""
        as_of_date = as_of_date or datetime.now().astimezone().date()
        events = await conn.fetch(
            """
            SELECT ev.account_id, ev.event_type,
                   jsonb_agg(
                       jsonb_build_object(
                           'leg_type', leg.leg_type,
                           'instrument_id', leg.instrument_id,
                           'quantity', leg.quantity,
                           'amount', leg.amount,
                           'unit_price', leg.unit_price
                       ) ORDER BY leg.id
                   ) AS legs
            FROM ledger.ledger_events ev
            JOIN ledger.ledger_legs leg
              ON leg.tenant_id = ev.tenant_id AND leg.event_id = ev.id
            WHERE ev.tenant_id = $1
              AND ev.status = 'CONFIRMED'
              AND ev.trade_date <= $2
              AND ev.event_type <> 'REVERSAL'
              AND NOT EXISTS (
                  SELECT 1 FROM ledger.ledger_events reversal
                  WHERE reversal.tenant_id = ev.tenant_id
                    AND reversal.status = 'CONFIRMED'
                    AND reversal.event_type = 'REVERSAL'
                    AND reversal.reverses_event_id = ev.id
              )
            GROUP BY ev.id, ev.account_id, ev.event_type, ev.occurred_at
            ORDER BY ev.occurred_at, ev.id
            """,
            tenant_id,
            as_of_date,
        )
        holdings = rebuild_confirmed_holdings([dict(r) for r in events])
        quotes = await self._latest_observation_quotes(
            conn, [h.instrument_id for h in holdings.values()]
        )

        # 确定性重放：删除该日旧投影，避免已平仓持仓残留
        await conn.execute(
            "DELETE FROM ledger.position_snapshots WHERE tenant_id = $1 AND as_of = $2",
            tenant_id,
            as_of_date,
        )
        updated = 0
        for holding in holdings.values():
            quote = quotes.get(holding.instrument_id)
            price = quote.get("price") if quote else None
            quality = quote.get("quality", "UNAVAILABLE") if quote else "UNAVAILABLE"
            quantity = holding.quantity
            avg_cost = holding.avg_cost
            market_value = (
                (quantity * price).quantize(CENT) if price is not None else None
            )
            unrealized_pnl = (
                (market_value - quantity * avg_cost).quantize(CENT)
                if market_value is not None
                else None
            )
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
                holding.account_id,
                holding.instrument_id,
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

    async def _refresh_account_nav(
        self, conn: AsyncConnection, tenant_id: str, *, as_of_date: date | None = None
    ) -> int:
        """重建当日账户净资产快照（现金/本金来自账本，非上日投影）。"""
        as_of_date = as_of_date or datetime.now().astimezone().date()
        accounts = await conn.fetch(
            """
            SELECT id AS account_id
            FROM identity.accounts
            WHERE tenant_id = $1 AND status = 'ACTIVE'
            """,
            tenant_id,
        )
        updated = 0
        for account in accounts:
            account_id = str(account["account_id"])
            cash_row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(leg.amount), 0) AS cash_balance,
                       COALESCE(SUM(leg.amount)
                                FILTER (WHERE ev.event_type IN ('CASH_IN', 'CASH_OUT')), 0)
                       AS principal
                FROM ledger.ledger_legs leg
                JOIN ledger.ledger_events ev
                  ON ev.tenant_id = leg.tenant_id AND ev.id = leg.event_id
                WHERE leg.tenant_id = $1 AND ev.account_id = $2
                  AND leg.leg_type = 'CASH'
                  AND ev.status = 'CONFIRMED'
                  AND NOT EXISTS (
                      SELECT 1 FROM ledger.ledger_events reversal
                      WHERE reversal.tenant_id = ev.tenant_id
                        AND reversal.status = 'CONFIRMED'
                        AND reversal.event_type = 'REVERSAL'
                        AND reversal.reverses_event_id = ev.id
                  )
                """,
                tenant_id,
                account_id,
            )
            cash_balance = _d((cash_row or {}).get("cash_balance"))
            principal = _d((cash_row or {}).get("principal"))

            positions_row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(market_value) FILTER (WHERE market_value IS NOT NULL), 0)
                       AS positions_value,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE market_value IS NOT NULL) AS priced_count
                FROM ledger.position_snapshots
                WHERE tenant_id = $1 AND account_id = $2 AND as_of = $3
                """,
                tenant_id,
                account_id,
                as_of_date,
            )
            positions_row = positions_row or {}
            positions_value = _d(positions_row.get("positions_value"))
            valuation = derive_account_nav(
                cash_balance=cash_balance,
                principal=principal,
                priced_positions_value=positions_value,
                total_position_count=int(positions_row.get("total") or 0),
                priced_position_count=int(positions_row.get("priced_count") or 0),
            )
            total_nav = valuation.total_nav

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
                _d(prev_row["total_nav"])
                if prev_row and prev_row["total_nav"] is not None
                else total_nav
            )
            daily_pnl = (
                (total_nav - prev_total).quantize(CENT)
                if total_nav is not None and prev_total is not None
                else None
            )
            cumulative_pnl = (
                (total_nav - principal).quantize(CENT)
                if total_nav is not None
                else None
            )

            await conn.execute(
                """
                INSERT INTO ledger.account_nav_snapshots
                    (tenant_id, account_id, as_of, cash_balance,
                     positions_market_value, total_nav, daily_pnl, cumulative_pnl,
                     data_quality_status, valuation_coverage_pct,
                     unpriced_position_count, known_value)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (tenant_id, account_id, as_of) DO UPDATE
                SET cash_balance = EXCLUDED.cash_balance,
                    positions_market_value = EXCLUDED.positions_market_value,
                    total_nav = EXCLUDED.total_nav,
                    daily_pnl = EXCLUDED.daily_pnl,
                    cumulative_pnl = EXCLUDED.cumulative_pnl,
                    data_quality_status = EXCLUDED.data_quality_status,
                    valuation_coverage_pct = EXCLUDED.valuation_coverage_pct,
                    unpriced_position_count = EXCLUDED.unpriced_position_count,
                    known_value = EXCLUDED.known_value
                """,
                tenant_id,
                account_id,
                as_of_date,
                cash_balance,
                positions_value if valuation.unpriced_position_count == 0 else None,
                total_nav,
                daily_pnl,
                cumulative_pnl,
                valuation.quality,
                valuation.coverage_pct,
                valuation.unpriced_position_count,
                valuation.known_value,
            )
            updated += 1
        return updated

    async def _latest_observation_quotes(
        self, conn: AsyncConnection, instrument_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """每个标的最新的行情观测（CLOSE/ESTIMATED_NAV 优先于 NAV）。"""
        if not instrument_ids:
            return {}
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (subject_id) subject_id, field_code, value, quality_status
            FROM market.market_observations
            WHERE subject_type = 'INSTRUMENT' AND subject_id = ANY($1::uuid[])
            ORDER BY subject_id,
                     CASE field_code
                         WHEN 'CLOSE' THEN 0
                         WHEN 'ESTIMATED_NAV' THEN 0
                         WHEN 'NAV' THEN 1
                         ELSE 9
                     END,
                     observed_at DESC
            """,
            list(instrument_ids),
        )
        quotes: dict[str, dict[str, Any]] = {}
        for row in rows:
            quality = str(row["quality_status"])
            value = _d(row["value"]) if row["value"] is not None else None
            if value is not None and quality == "UNAVAILABLE":
                quality = "STALE"
            quotes[str(row["subject_id"])] = {
                "field": row["field_code"],
                "price": value if quality != "UNAVAILABLE" else None,
                "quality": quality,
            }
        return quotes


# ---------------------------------------------------------------------------
# 格式化工具（与 backend.common.formatting 等价）
# ---------------------------------------------------------------------------
def fmt_money(value: Decimal | None) -> str:
    """金额 → 两位小数十进制字符串。"""
    if value is None:
        return "0.00"
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def fmt_qty(value: Decimal) -> str:
    """数量 → 四位小数十进制字符串。"""
    return str(value.quantize(QTY, rounding=ROUND_HALF_UP))


def pct(part: Decimal | None, whole: Decimal | None) -> Decimal | None:
    """百分比（0.12 → 12.00）；分母无效时返回 None。"""
    if part is None or whole in (None, 0):
        return None
    return (part / whole * Decimal("100")).quantize(Decimal("0.01"))


def _validate_idempotency_key(idempotency_key: str) -> None:
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError("Idempotency-Key 必填且不能超过 128 个字符")


# 供类型提示复用（避免循环 import）
from modules.identity.service import TenantContext  # noqa: E402,F401
