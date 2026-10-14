"""Current selected market and the authenticated user's private decision facts."""

from uuid import UUID
from fastapi import APIRouter, Depends, Query
from backend.common.database import tenant_transaction
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.investment import opportunity_service

router = APIRouter(tags=["investment"])


@router.get("/investment/opportunity-context")
async def opportunity_context(
    direction_id: str | None = Query(
        default=None, alias="directionId", min_length=1, max_length=100
    ),
    index_id: str | None = Query(
        default=None, alias="indexId", min_length=1, max_length=100
    ),
    instrument_id: UUID | None = Query(default=None, alias="instrumentId"),
    fund_code: str | None = Query(
        default=None, alias="fundCode", min_length=1, max_length=30
    ),
    fund_market: str | None = Query(
        default=None, alias="fundMarket", min_length=1, max_length=30
    ),
    months: int = Query(default=36, ge=1, le=120),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await opportunity_service.get_context(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            direction_id=direction_id,
            index_id=index_id,
            instrument_id=instrument_id,
            fund_code=fund_code,
            fund_market=fund_market,
            months=months,
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())
