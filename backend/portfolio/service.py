"""Portfolio service: position/NAV aggregation and PnL computation.

Financial precision invariant: every amount is Decimal; the API layer
serializes them as decimal strings. No float is used anywhere in this module.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import asyncpg

from backend.common.formatting import fmt_money, fmt_qty, merge_quality, pct
from backend.dependencies import SHANGHAI_TZ
from backend.errors import DataUnavailableError
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

    total_asset = sum((_d(r["total_nav"]) for r in navs), ZERO)
    cash = sum((_d(r["cash_balance"]) for r in navs), ZERO)
    positions_value = sum((_d(r["positions_market_value"]) for r in navs), ZERO)
    daily_pnl = sum((_d(r["daily_pnl"]) for r in navs), ZERO)
    cumulative_pnl = sum((_d(r["cumulative_pnl"]) for r in navs), ZERO)
    quality = merge_quality(r["data_quality_status"] for r in navs)

    positions = await repository.latest_positions(conn, tenant_id)
    allocation = _build_allocation(positions, cash, total_asset)

    return {
        "totalAsset": fmt_money(total_asset),
        "currency": "CNY",
        "cashBalance": fmt_money(cash),
        "positionsMarketValue": fmt_money(positions_value),
        "dailyPnl": fmt_money(daily_pnl),
        "dailyPnlRate": fmt_money(pct(daily_pnl, total_asset - daily_pnl)),
        "cumulativePnl": fmt_money(cumulative_pnl),
        "cumulativePnlRate": fmt_money(pct(cumulative_pnl, total_asset - cumulative_pnl)),
        "allocation": allocation,
        "qualityStatus": quality,
        "asOf": as_of,
        "positionCount": len(positions),
    }


def _build_allocation(
    positions: list[asyncpg.Record], cash: Decimal, total_asset: Decimal
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
            "value": fmt_money(pct(amount, total_asset)),
        }
        for category, amount in sorted(
            by_category.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


async def get_positions(conn: asyncpg.Connection, tenant_id: str) -> list[dict]:
    """Latest holdings with market value, weight, PnL and quality status."""
    rows = await repository.latest_positions(conn, tenant_id)
    navs = await repository.latest_nav_per_account(conn, tenant_id)
    total_asset = sum((_d(r["total_nav"]) for r in navs), ZERO)

    result: list[dict] = []
    for row in rows:
        quantity = _d(row["quantity"])
        avg_cost = _d(row["avg_cost"])
        price = row["market_price"]
        market_value = _d(row["market_value"])
        pnl = row["unrealized_pnl"]

        if row["market_value"] is None and price is not None:
            market_value = (quantity * _d(price)).quantize(CENT)
        if row["unrealized_pnl"] is None:
            pnl = market_value - (quantity * avg_cost)
        cost_value = quantity * avg_cost
        pnl_rate = pct(_d(pnl), cost_value) if cost_value else ZERO

        result.append(
            {
                "symbol": row["ticker"],
                "name": row["canonical_name"],
                "type": ASSET_CATEGORY.get(row["asset_type"], row["asset_type"]),
                "quantity": fmt_qty(quantity),
                "price": fmt_money(_d(price)) if price is not None else None,
                "marketValue": fmt_money(market_value),
                "weight": fmt_money(pct(market_value, total_asset)),
                "pnl": fmt_money(_d(pnl)),
                "pnlRate": fmt_money(pnl_rate),
                "quality": merge_quality([row["data_quality_status"]]),
                "accountId": str(row["account_id"]),
            }
        )
    result.sort(key=lambda p: p["marketValue"], reverse=True)
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
            "totalNav": fmt_money(_d(r["total_nav"])),
            "dailyPnl": fmt_money(_d(r["daily_pnl"])),
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
