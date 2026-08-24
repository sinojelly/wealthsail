"""Risk API: /api/v1/risk/policies · rules."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.risk import service

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/policies")
async def risk_policies(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """当前生效投资政策、版本历史与风险预算（回撤/单一仓位/现金比例）。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_policies(conn, ctx.tenant_id)
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/rules")
async def risk_rules(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """风控规则列表与基于当前组合的预算合规检查（OK/WARNING/VIOLATION）。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_rules(conn, ctx.tenant_id)
    return ok(data, request_id=request_id, as_of=as_of)
