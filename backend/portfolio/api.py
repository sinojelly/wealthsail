"""Portfolio API: /api/v1/portfolio/summary · positions · nav-trend · search · add."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query
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
    keyword: str = Query(..., min_length=2, max_length=32, description="股票/ETF/基金名称或代码"),
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
    """用户创建期初持仓导入草稿的请求体。"""

    symbol: str = Field(..., description="6 位证券代码（如 600519 / 510300 / 110022）")
    name: str | None = Field(None, description="标的名称（搜索带入，可选）")
    assetType: Literal["STOCK", "ETF", "FUND"] = Field(..., description="资产类型")
    quantity: Decimal = Field(..., gt=0, description="数量/份额")
    avgCost: Decimal = Field(..., ge=0, description="成本价（每股/每份）")
    buyDate: date | None = Field(None, description="基线日期，默认今天")
    accountId: str | None = Field(None, description="账户 id（可选，默认按类型自动选择/创建）")


@router.post("/imports", status_code=201)
async def portfolio_create_import(
    payload: PositionCreateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """创建一个可预览的期初持仓草稿，不写入任何已确认经济事实。

    标的名称和市场由服务端主数据/Provider 重新核验，不采信表单展示文本。
    返回 DRAFT 后，客户端必须要求用户明确确认，才能生成期初 ADJUSTMENT。
    """
    as_of = now_shanghai()
    data = await service.create_position_import(
        pool,
        ctx,
        payload,
        idempotency_key=idempotency_key,
    )
    return ok(data, request_id=request_id, as_of=as_of, quality_status="FRESH")


@router.post("/imports/{batch_id}/confirm")
async def portfolio_confirm_import(
    batch_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """确认期初组合基线并生成带批次来源的 ADJUSTMENT；不会伪造交易或现金流。"""
    as_of = now_shanghai()
    data = await service.confirm_position_import(
        pool,
        ctx,
        batch_id,
        idempotency_key=idempotency_key,
    )
    return ok(data, request_id=request_id, as_of=as_of, quality_status="FRESH")
