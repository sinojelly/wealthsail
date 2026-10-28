"""Authenticated HTTP endpoints for the shared fund-screener cache."""

from __future__ import annotations

from typing import Literal

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


@router.get("/quotes")
async def latest_fund_quotes(
    codes: str = Query(pattern=r"^[0-9]{6}(,[0-9]{6}){0,31}$", max_length=223),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Refresh display quotes separately; no research-plan mutation or provider I/O in RLS."""
    from backend.funds.latest_quotes import latest_quote_cache

    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        trading_day = await conn.fetchval(
            "SELECT is_trading_day FROM reference.trading_calendars WHERE market='SSE' AND calendar_date=$1",
            now.date(),
        )
    data = await latest_quote_cache.get(codes.split(","), now, trading_day=trading_day)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/search")
async def search_funds(
    keyword: str = Query(min_length=1, max_length=100),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Search cached fund ranges; an empty result is a normal outcome.  An
    exact six-digit code miss auto-enqueues one durable Worker fetch job."""

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.search(conn, keyword, tenant_id=ctx.tenant_id)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/browse")
async def browse_funds(
    category: str = Query(min_length=2, max_length=20),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Browse cached funds by quick-filter category; cache-only, no fetching."""

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.browse(conn, category)
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


@router.get("/{fund_code}/nav-history")
async def fund_nav_history(
    fund_code: str,
    period: Literal["6m", "1y", "2y", "all"] = Query(default="2y"),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Read disclosed NAV; insufficient depth queues bounded Worker work."""
    from backend.funds.nav_history import get_nav_history

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await get_nav_history(
            conn,
            fund_code,
            period,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            now=now_shanghai(),
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())
