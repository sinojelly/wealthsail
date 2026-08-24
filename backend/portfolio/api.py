"""Portfolio API: /api/v1/portfolio/summary · positions · nav-trend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

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
