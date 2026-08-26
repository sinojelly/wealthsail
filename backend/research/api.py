"""Authenticated research read APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.research import service

router = APIRouter(prefix="/research", tags=["research"])


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
