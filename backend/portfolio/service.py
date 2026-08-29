"""Portfolio service: position/NAV aggregation and PnL computation.

Financial precision invariant: every amount is Decimal; the API layer
serializes them as decimal strings. No float is used anywhere in this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal

import asyncpg

from backend.common.database import tenant_transaction
from backend.common.formatting import fmt_money, fmt_qty, merge_quality, pct
from backend.dependencies import SHANGHAI_TZ, now_shanghai, write_audit
from backend.errors import (
    IdempotencyConflictError,
    NotFoundError,
    ValidationError_,
)
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


def _row_value(row: asyncpg.Record | dict, key: str, default=None):
    return row.get(key, default)


def _date_text(value) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    return format(value if isinstance(value, Decimal) else Decimal(str(value)), "f")


def _position_period_dto(row: asyncpg.Record | dict) -> tuple[dict | None, dict | None]:
    """Map persisted metric facts without recalculating financial percentages."""
    if row["asset_type"] != "OFF_EXCHANGE_FUND":
        return None, None
    calculation_status = _row_value(row, "calculation_status")
    if calculation_status is None:
        return (
            {"startedOn": None, "days": None, "method": "INSUFFICIENT_DATA"},
            {
                "high": None,
                "highDate": None,
                "low": None,
                "lowDate": None,
                "fromHighPct": None,
                "fromLowPct": None,
                "rangePositionPct": None,
                "status": "PENDING",
                "qualityReasons": ["METRICS_NOT_READY"],
                "asOf": None,
            },
        )
    return (
        {
            "startedOn": _date_text(_row_value(row, "holding_started_on")),
            "days": _row_value(row, "holding_days"),
            "method": _row_value(row, "holding_start_method"),
        },
        {
            "high": _decimal_text(_row_value(row, "period_high_nav")),
            "highDate": _date_text(_row_value(row, "period_high_date")),
            "low": _decimal_text(_row_value(row, "period_low_nav")),
            "lowDate": _date_text(_row_value(row, "period_low_date")),
            "fromHighPct": _decimal_text(_row_value(row, "from_high_pct")),
            "fromLowPct": _decimal_text(_row_value(row, "from_low_pct")),
            "rangePositionPct": _decimal_text(_row_value(row, "range_position_pct")),
            "status": str(calculation_status),
            "qualityReasons": list(_row_value(row, "quality_reasons", []) or []),
            # This is the official current-NAV disclosure date, not the API
            # request time and never an intraday/realtime label.
            "asOf": _date_text(_row_value(row, "current_nav_date")),
        },
    )


async def get_summary(
    conn: asyncpg.Connection, tenant_id: str, as_of: datetime
) -> dict:
    """Aggregate the latest per-account NAV snapshots into the dashboard summary."""
    navs = await repository.latest_nav_per_account(conn, tenant_id)
    if not navs:
        # A newly created tenant has no observed portfolio facts yet. Preserve
        # that distinction with null valuations instead of fabricating zeros or
        # reporting the valid onboarding state as a service outage.
        return {
            "totalAsset": None,
            "knownValue": "0.00",
            "valuationCoveragePct": "0.00",
            "unpricedPositionCount": 0,
            "currency": "CNY",
            "cashBalance": "0.00",
            "positionsMarketValue": None,
            "dailyPnl": None,
            "dailyPnlRate": None,
            "cumulativePnl": None,
            "cumulativePnlRate": None,
            "allocation": [],
            "qualityStatus": "MISSING",
            "asOf": None,
            "positionCount": 0,
        }

    complete = all(r["total_nav"] is not None for r in navs)
    total_asset = sum((_d(r["total_nav"]) for r in navs), ZERO) if complete else None
    known_value = sum(
        (
            _d(r.get("known_value") if hasattr(r, "get") else r["known_value"])
            for r in navs
        ),
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
            "value": fmt_money(pct(amount, total_asset))
            if total_asset is not None
            else None,
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
        market_value = (
            _d(row["market_value"]) if row["market_value"] is not None else None
        )
        pnl = row["unrealized_pnl"]

        if market_value is None and price is not None:
            market_value = (quantity * _d(price)).quantize(CENT)
        if pnl is None and market_value is not None:
            pnl = market_value - (quantity * avg_cost)
        cost_value = quantity * avg_cost
        pnl_rate = pct(_d(pnl), cost_value) if pnl is not None and cost_value else None
        holding_period, range_metrics = _position_period_dto(row)

        result.append(
            {
                "symbol": row["ticker"],
                "name": row["canonical_name"],
                "type": ASSET_CATEGORY.get(row["asset_type"], row["asset_type"]),
                "quantity": fmt_qty(quantity),
                "avgCost": fmt_money(avg_cost),
                "price": fmt_money(_d(price)) if price is not None else None,
                "marketValue": fmt_money(market_value)
                if market_value is not None
                else None,
                "weight": (
                    fmt_money(pct(market_value, total_asset))
                    if market_value is not None and total_asset is not None
                    else None
                ),
                "pnl": fmt_money(_d(pnl)) if pnl is not None else None,
                "pnlRate": fmt_money(pnl_rate) if pnl_rate is not None else None,
                "quality": merge_quality([row["data_quality_status"]]),
                "accountId": str(row["account_id"]),
                "holdingPeriod": holding_period,
                "rangeMetrics": range_metrics,
            }
        )
    return result


async def get_nav_trend(
    conn: asyncpg.Connection, tenant_id: str, days: int = 60
) -> dict:
    """Daily NAV trend (points) — benchmark left null until index data exists."""
    rows = await repository.nav_history(conn, tenant_id, limit=days)
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
# 期初组合初始化：标的搜索 / 两阶段导入 / 投影重算
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

_SYMBOL_RE = re.compile(r"^(\d{6})(?:\.(?:SH|SZ|BJ))?$")
_SEARCH_DATASET_TTL_SECONDS = 300.0
_SEARCH_DATASET_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
_SEARCH_DATASET_LOCKS: dict[str, asyncio.Lock] = {}


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
    # 北交所现行代码以 92/8 开头，兼容历史 4 开头代码；创业板 30/31
    # 仍属于深交所，不能按首位 3 误判为北交所。
    if symbol.startswith(("4", "8", "92")):
        return "BSE"
    if symbol[0] in "569":
        return "SSE"
    return "SZSE"


async def _load_search_dataset(
    cache_key: str,
    fetcher,
    *,
    code_column: str = "代码",
    name_column: str = "名称",
) -> list[dict[str, str]]:
    """Load one Provider master dataset with per-key TTL and single-flight.

    Search terms are applied after this cache boundary. Thus ten keystrokes do
    not download the same three whole-market tables ten times.
    """
    loop = asyncio.get_running_loop()
    cached = _SEARCH_DATASET_CACHE.get(cache_key)
    if cached is not None and cached[0] > loop.time():
        return cached[1]
    lock = _SEARCH_DATASET_LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _SEARCH_DATASET_CACHE.get(cache_key)
        if cached is not None and cached[0] > loop.time():
            return cached[1]
        df = await asyncio.wait_for(asyncio.to_thread(fetcher), timeout=15)
        if (
            df is None
            or df.empty
            or not {code_column, name_column}.issubset(df.columns)
        ):
            return []
        rows = [
            {
                "symbol": str(row[code_column]).strip().zfill(6),
                "name": str(row[name_column]).strip(),
            }
            for _, row in df.iterrows()
            if str(row[code_column]).strip()
        ]
        _SEARCH_DATASET_CACHE[cache_key] = (
            loop.time() + _SEARCH_DATASET_TTL_SECONDS,
            rows,
        )
        return rows


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
        return _primary_market("CN_STOCK", code)

    async def _run(
        cache_key: str,
        fetcher,
        asset_type: str,
        market_fn,
        *,
        code_column: str = "代码",
        name_column: str = "名称",
    ) -> None:
        try:
            rows = await _load_search_dataset(
                cache_key,
                fetcher,
                code_column=code_column,
                name_column=name_column,
            )
            matched = [
                row
                for row in rows
                if kw in row["symbol"] or kw.casefold() in row["name"].casefold()
            ][:10]
            for row in matched:
                code = row["symbol"]
                results.setdefault(
                    f"{market_fn(code)}:{code}:{asset_type}",
                    {
                        "symbol": code,
                        "name": row["name"],
                        "market": market_fn(code),
                        "type": asset_type,
                        "source": "live",
                    },
                )
        except Exception:  # noqa: BLE001 — 搜索失败静默降级到本地主数据
            return

    await asyncio.gather(
        _run("stock_zh_a_spot_em", lambda: ak.stock_zh_a_spot_em(), "STOCK", _market),
        _run("fund_etf_spot_em", lambda: ak.fund_etf_spot_em(), "ETF", _market),
        _run(
            "fund_name_em",
            lambda: ak.fund_name_em(),
            "FUND",
            lambda _code: "FUND_CN",
            code_column="基金代码",
            name_column="基金简称",
        ),
    )
    return list(results.values())


async def verify_fund_master_names(product_codes: list[str]) -> dict[str, str]:
    """Return Provider-verified names for exact off-exchange fund codes.

    Missing entries represent unavailable or unknown Provider data. Callers
    can keep those source positions visible as blocking conflicts without
    trusting an uploaded display name or failing the whole preview request.
    """
    codes = set(product_codes)
    if not codes:
        return {}
    try:
        import akshare as ak  # noqa: PLC0415

        rows = await _load_search_dataset(
            "fund_name_em",
            lambda: ak.fund_name_em(),
            code_column="基金代码",
            name_column="基金简称",
        )
    except Exception:  # noqa: BLE001 - unresolved codes stay visible and blocking
        return {}
    return {
        row["symbol"]: row["name"]
        for row in rows
        if row["symbol"] in codes and row["name"]
    }


async def search_instruments(pool: asyncpg.Pool, ctx, keyword: str) -> dict:
    """Search stocks/ETFs/funds by Chinese name or 6-digit code.

    Local instrument master first, then AkShare realtime enrichment
    (degradation-safe); identity is market + symbol + asset type.
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
    for item in await _ak_search(kw):
        identity = f"{item['market']}:{item['symbol']}:{item['type']}"
        merged.setdefault(identity, item)
    items = sorted(merged.values(), key=lambda x: (x["type"], x["symbol"]))[:20]
    return {"items": items, "total": len(items)}


async def recompute_projections(
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


async def _resolve_verified_instrument(
    pool: asyncpg.Pool,
    ctx,
    symbol: str,
    asset_type: str,
) -> dict:
    """Resolve canonical instrument data without trusting form display fields."""
    expected_market = _primary_market(asset_type, symbol)
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        existing = await repository.find_instrument_by_ticker(
            conn,
            symbol,
            primary_market=expected_market,
            asset_type=asset_type,
        )
    if existing is not None:
        if existing["asset_type"] != asset_type:
            raise ValidationError_(
                f"标的 {symbol} 的资产类型与主数据不一致",
                field_errors=[{"field": "assetType", "message": "与主数据不一致"}],
            )
        return {
            "id": str(existing["id"]),
            "symbol": symbol,
            "name": existing["canonical_name"],
            "asset_type": asset_type,
            "market": existing["primary_market"],
        }

    expected_public_type = INTERNAL_ASSET_TYPE[asset_type]
    candidates = await _ak_search(symbol)
    exact = next(
        (
            candidate
            for candidate in candidates
            if candidate["symbol"] == symbol
            and candidate["type"] == expected_public_type
        ),
        None,
    )
    if exact is None:
        raise ValidationError_(
            "暂时无法从行情源验证该标的，请稍后重试或等待主数据同步",
            field_errors=[{"field": "symbol", "message": "未通过权威数据源验证"}],
        )
    return {
        "id": None,
        "symbol": symbol,
        "name": exact["name"],
        "asset_type": asset_type,
        "market": exact["market"],
    }


def _position_import_content_hash(
    *,
    symbol: str,
    asset_type: str,
    quantity: Decimal,
    unit_cost: Decimal,
    trade_date,
    account_id: str | None,
) -> str:
    """Hash every command field that may alter the resulting economic baseline."""
    canonical = json.dumps(
        {
            "symbol": symbol,
            "assetType": asset_type,
            "quantity": str(quantity),
            "unitCost": str(unit_cost),
            "tradeDate": trade_date.isoformat(),
            "accountId": account_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _position_import_replay_response(row) -> dict:
    """Reconstruct the original DRAFT creation response from persisted input."""
    public_type = INTERNAL_ASSET_TYPE.get(str(row["asset_type"]))
    if public_type is None:
        raise ValidationError_("历史初始化草稿包含不支持的资产类型")
    return {
        "batchId": str(row["batch_id"]),
        "status": "DRAFT",
        "version": 1,
        "item": {
            "itemId": str(row["item_id"]),
            "symbol": str(row["ticker"]),
            "name": str(row["canonical_name"]),
            "assetType": public_type,
            "quantity": fmt_qty(Decimal(str(row["quantity"]))),
            "unitCost": fmt_money(Decimal(str(row["unit_cost"]))),
            "tradeDate": row["trade_date"].isoformat(),
            "accountId": str(row["account_id"]),
            "validationStatus": str(row["validation_status"]),
        },
    }


def _assert_same_position_import(row, source_hash: str) -> None:
    if str(row["source_content_hash"]) != source_hash:
        raise IdempotencyConflictError(
            "Idempotency-Key 已用于不同的期初持仓内容，请刷新后重试"
        )


async def create_position_import(
    pool: asyncpg.Pool,
    ctx,
    payload,
    *,
    idempotency_key: str,
) -> dict:
    """Create a reviewable position-import draft; do not publish ledger facts."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    symbol = normalize_symbol(payload.symbol)
    internal_type = PUBLIC_ASSET_TYPE.get(payload.assetType)
    if internal_type is None:
        raise ValidationError_("仅支持 STOCK / ETF / FUND")
    quantity = Decimal(str(payload.quantity))
    unit_cost = Decimal(str(payload.avgCost))
    if quantity <= 0 or unit_cost < 0:
        raise ValidationError_("数量必须大于 0，成本价不能为负")
    trade_date = payload.buyDate or now_shanghai().date()
    source_hash = _position_import_content_hash(
        symbol=symbol,
        asset_type=payload.assetType,
        quantity=quantity,
        unit_cost=unit_cost,
        trade_date=trade_date,
        account_id=payload.accountId,
    )

    # Check before any Provider call or write: a transport retry should work
    # even when the external market-data source has subsequently degraded.
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        previous = await repository.find_position_import_by_idempotency_key(
            conn,
            tenant_id=ctx.tenant_id,
            idempotency_key=idempotency_key,
        )
    if previous is not None:
        _assert_same_position_import(previous, source_hash)
        return _position_import_replay_response(previous)

    verified = await _resolve_verified_instrument(pool, ctx, symbol, internal_type)

    try:
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            current = await repository.find_instrument_by_ticker(
                conn,
                symbol,
                primary_market=verified["market"],
                asset_type=internal_type,
            )
            instrument_id = (
                str(current["id"])
                if current is not None
                else await repository.upsert_instrument(
                    conn,
                    ticker=symbol,
                    name=verified["name"],
                    asset_type=internal_type,
                    primary_market=verified["market"],
                )
            )
            if payload.accountId:
                account_id = await conn.fetchval(
                    """
                    SELECT id FROM identity.accounts
                    WHERE id = $1 AND tenant_id = $2 AND status = 'ACTIVE'
                    """,
                    payload.accountId,
                    ctx.tenant_id,
                )
                if account_id is None:
                    raise ValidationError_("账户不存在或不属于当前租户")
                account_id = str(account_id)
            else:
                account_id = await repository.pick_or_create_account(
                    conn, tenant_id=ctx.tenant_id, asset_type=internal_type
                )
            draft = await repository.create_import_draft(
                conn,
                tenant_id=ctx.tenant_id,
                account_id=account_id,
                created_by=ctx.user_id,
                instrument_id=instrument_id,
                quantity=quantity,
                unit_cost=unit_cost,
                trade_date=trade_date,
                idempotency_key=idempotency_key,
                source_content_hash=source_hash,
            )
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="PORTFOLIO_IMPORT_DRAFT_CREATED",
                resource_type="ledger.portfolio_import_batches",
                resource_id=draft["batch_id"],
                details={"itemId": draft["item_id"], "symbol": symbol},
            )
    except asyncpg.exceptions.UniqueViolationError:
        # A concurrent retry may win the unique key after our early read.
        # Re-read outside the aborted transaction and replay only if the
        # content hash proves that this is the same command.
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            previous = await repository.find_position_import_by_idempotency_key(
                conn,
                tenant_id=ctx.tenant_id,
                idempotency_key=idempotency_key,
            )
        if previous is None:
            raise
        _assert_same_position_import(previous, source_hash)
        return _position_import_replay_response(previous)

    return {
        "batchId": draft["batch_id"],
        "status": "DRAFT",
        "version": 1,
        "item": {
            "itemId": draft["item_id"],
            "symbol": symbol,
            "name": verified["name"],
            "assetType": payload.assetType,
            "quantity": fmt_qty(quantity),
            "unitCost": fmt_money(unit_cost),
            "tradeDate": trade_date.isoformat(),
            "accountId": account_id,
            "validationStatus": "VALID",
        },
    }


async def confirm_position_import(
    pool: asyncpg.Pool,
    ctx,
    batch_id: str,
    *,
    idempotency_key: str,
) -> dict:
    """Confirm a reviewed draft and rebuild projections in one transaction."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        confirmed = await repository.confirm_import_batch(
            conn,
            tenant_id=ctx.tenant_id,
            batch_id=batch_id,
            idempotency_key=idempotency_key,
        )
        if not confirmed:
            raise NotFoundError("初始化草稿不存在")
        if confirmed.get("status") == "INVALID":
            raise ValidationError_("草稿包含未通过校验的项目，不能确认")
        if confirmed.get("status") not in (None, "CONFIRMED"):
            raise ValidationError_("初始化草稿当前状态不允许确认")
        if not confirmed.get("already_confirmed"):
            await recompute_projections(conn, tenant_id=ctx.tenant_id)
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="PORTFOLIO_IMPORT_CONFIRMED",
                resource_type="ledger.portfolio_import_batches",
                resource_id=batch_id,
                details={"eventIds": confirmed.get("event_ids", [])},
            )
    return {
        "batchId": batch_id,
        "status": "CONFIRMED",
        "version": confirmed["version"],
        "eventId": confirmed.get("event_id"),
    }
