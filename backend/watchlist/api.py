"""Authenticated self-service watchlist HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.watchlist import service


router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class AddWatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)


@router.get("")
async def get_watchlist(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.list_watchlist(conn, ctx.user_id)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("")
async def add_watchlist(
    body: AddWatchlistRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.add_watchlist_item(conn, ctx, body.symbol)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.delete("/{symbol}")
async def remove_watchlist(
    symbol: str,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.remove_watchlist_item(conn, ctx, symbol)
    return ok(data, request_id=request_id, as_of=now_shanghai())
