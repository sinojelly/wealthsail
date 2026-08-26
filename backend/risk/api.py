"""Risk API: /api/v1/risk/policies · rules."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.risk import service

router = APIRouter(prefix="/risk", tags=["risk"])


class PolicyPreviewRequest(BaseModel):
    benchmark: str = Field(min_length=1, max_length=32)
    horizonMonths: int = Field(ge=3, le=120)
    maxPosition: str
    maxDrawdown: str
    minCash: str
    maxAlertsPerDay: int = Field(ge=1, le=20)
    silenceMinutes: int = Field(ge=5, le=1440)


class PolicyConfirmRequest(BaseModel):
    draftId: str
    expectedVersion: int = Field(ge=1)
    policyHash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    password: str = Field(min_length=1)


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


@router.post("/policies/preview")
async def preview_policy(
    body: PolicyPreviewRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """创建可审计的政策预览；PREVIEW 不影响当前风险判定。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.preview_policy(
            conn, ctx, body.model_dump(), idempotency_key=idempotency_key
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/policies/confirm")
async def confirm_policy(
    body: PolicyConfirmRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """二次认证后把特定版本/哈希的 PREVIEW 原子确认为新政策。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.confirm_policy(
            conn,
            ctx,
            draft_id=body.draftId,
            expected_version=body.expectedVersion,
            policy_hash=body.policyHash,
            password=body.password,
        )
    return ok(data, request_id=request_id, as_of=as_of)
