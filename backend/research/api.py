"""Authenticated research read APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.research import service
from modules.research.service import DecisionService

router = APIRouter(prefix="/research", tags=["research"])


class DecisionRequest(BaseModel):
    action: str = Field(pattern="^(ACCEPTED|IGNORED|DEFERRED)$")
    reportId: UUID | None = None
    recommendationId: UUID | None = None
    reason: str | None = Field(default=None, max_length=1000)
    correction: str | None = Field(default=None, max_length=1000)
    previousDecisionId: UUID | None = None


class DecisionRetractRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@router.get("/recommendations/latest")
async def latest_recommendations(
    ctx: TenantContext = Depends(get_tenant_context),
    pool: Pool = Depends(get_pool),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Read already-published recommendations; never generate on GET."""
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_latest_recommendations(conn, ctx.tenant_id)
    return ok(
        data,
        request_id=request_id,
        as_of=now_shanghai(),
        quality_status=data["qualityStatus"],
    )


@router.post("/decisions", status_code=201)
async def record_decision(
    body: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ctx: TenantContext = Depends(get_tenant_context),
    pool: Pool = Depends(get_pool),
    request_id: str = Depends(get_request_id),
) -> dict:
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await DecisionService().record_decision(
            conn,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            decision_action=body.action,
            idempotency_key=idempotency_key,
            report_id=str(body.reportId) if body.reportId else None,
            recommendation_id=str(body.recommendationId) if body.recommendationId else None,
            reason=body.reason,
            correction=body.correction,
            previous_decision_id=(
                str(body.previousDecisionId) if body.previousDecisionId else None
            ),
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/decisions")
async def list_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    pool: Pool = Depends(get_pool),
    request_id: str = Depends(get_request_id),
) -> dict:
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await DecisionService().list_decisions(conn, ctx.tenant_id, limit=limit)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/decisions/{decision_id}/retract")
async def retract_decision(
    decision_id: str,
    body: DecisionRetractRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    pool: Pool = Depends(get_pool),
    request_id: str = Depends(get_request_id),
) -> dict:
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await DecisionService().retract_decision(
            conn,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            decision_id=decision_id,
            reason=body.reason,
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())
