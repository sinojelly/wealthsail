"""Portfolio API: /api/v1/portfolio/summary · positions · nav-trend · search · add."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.portfolio import service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/summary")
async def portfolio_summary(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """资产看板总览：总资产、现金、持仓市值、当日/累计收益、配置与质量状态。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_summary(conn, ctx.tenant_id, as_of)
    return ok(
        data,
        request_id=request_id,
        as_of=as_of,
        quality_status=data["qualityStatus"],
    )


@router.get("/positions")
async def portfolio_positions(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """当前持仓列表（含质量状态），按市值降序。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_positions(conn, ctx.tenant_id)
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/nav-trend")
async def portfolio_nav_trend(
    days: int = Query(default=60, ge=1, le=365, description="返回最近 N 个交易日"),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """资产净值走势（日频）。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_nav_trend(conn, ctx.tenant_id, days=days)
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/search")
async def portfolio_search(
    keyword: str = Query(..., min_length=1, max_length=32, description="股票/ETF/基金名称或代码"),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """搜索可添加的标的：本地主数据 + AkShare 实时（降级安全）。

    返回: { items: [{symbol, name, market, type: STOCK|ETF|FUND}], total }
    """
    as_of = now_shanghai()
    data = await service.search_instruments(pool, ctx, keyword)
    return ok(data, request_id=request_id, as_of=as_of)


class PositionCreateRequest(BaseModel):
    """用户手动添加持仓请求体。"""

    symbol: str = Field(..., description="6 位证券代码（如 600519 / 510300 / 110022）")
    name: str | None = Field(None, description="标的名称（搜索带入，可选）")
    assetType: Literal["STOCK", "ETF", "FUND"] = Field(..., description="资产类型")
    quantity: Decimal = Field(..., gt=0, description="数量/份额")
    avgCost: Decimal = Field(..., ge=0, description="成本价（每股/每份）")
    buyDate: date | None = Field(None, description="买入日期，默认今天")
    accountId: str | None = Field(None, description="账户 id（可选，默认按类型自动选择/创建）")
    idempotencyKey: str | None = Field(None, max_length=128, description="幂等键（可选，防重复提交）")


class PositionUpdateRequest(BaseModel):
    """编辑持仓请求体（替换式：数量与成本价整体覆盖）。"""

    quantity: Decimal = Field(..., gt=0, description="最新数量/份额")
    avgCost: Decimal = Field(..., ge=0, description="最新成本价")
    buyDate: date | None = Field(None, description="调整日期，默认今天")
    idempotencyKey: str | None = Field(None, max_length=128, description="幂等键（可选）")


@router.patch("/positions/{symbol}")
async def portfolio_update_position(
    symbol: str,
    payload: PositionUpdateRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """编辑持仓（数量/成本价）：清旧建新账本事件 + 重算投影。"""
    as_of = now_shanghai()
    data = await service.update_position(pool, ctx, symbol, payload)
    return ok(
        data,
        request_id=request_id,
        as_of=as_of,
        quality_status=data["position"]["quality"],
    )


@router.delete("/positions/{symbol}")
async def portfolio_delete_position(
    symbol: str,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """删除持仓：清空账本持仓 + 本金退出，重算投影。"""
    as_of = now_shanghai()
    data = await service.delete_position(pool, ctx, symbol)
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/positions", status_code=201)
async def portfolio_add_position(
    payload: PositionCreateRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """添加一笔持仓：写不可变账本（BUY）+ 重算今日投影，自动关联行情。

    返回: { position: {symbol, name, type, quantity, avgCost, price,
                        marketValue, pnl, pnlRate, quality, accountId,
                        buyDate, eventId} }
    """
    as_of = now_shanghai()
    data = await service.add_position(pool, ctx, payload)
    return ok(
        data,
        request_id=request_id,
        as_of=as_of,
        quality_status=data["position"]["quality"],
    )
