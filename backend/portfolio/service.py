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
