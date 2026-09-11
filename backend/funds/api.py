"""Authenticated HTTP endpoints for the shared fund-screener cache."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.funds import service


router = APIRouter(prefix="/funds", tags=["funds"])


@router.get("/search")
async def search_funds(
    keyword: str = Query(min_length=1, max_length=100),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Search cached fund ranges only; an empty result is a normal outcome."""

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.search(conn, keyword)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/{fund_code}/range")
async def fund_range(
    fund_code: str,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Read one accumulated-NAV range; absence is returned as NOT_COLLECTED."""

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_range(conn, fund_code)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/{fund_code}/fetch", status_code=status.HTTP_202_ACCEPTED)
async def fetch_fund_range(
    fund_code: str,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Queue an on-demand range refresh. Provider I/O runs only in the Worker."""

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.enqueue_fetch(
            conn, tenant_id=ctx.tenant_id, fund_code=fund_code
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())
