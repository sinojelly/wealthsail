from fastapi import APIRouter, Depends, Query

from backend.common.database import tenant_transaction
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.investment import service
from backend.investment.schemas import ScreenQuery
from backend.investment.schemas import RefreshRequest
from uuid import UUID

router = APIRouter(tags=["investment"])


async def _read(operation, pool, ctx, request_id, *args):
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await operation(conn, *args)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/investment/market-context")
async def market_context(
    months: int = Query(default=36, ge=1, le=120),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(service.market_context, pool, ctx, request_id, months)


@router.get("/investment/directions")
async def directions(
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(service.directions, pool, ctx, request_id)


@router.get("/investment/directions/{direction_id}/indices")
async def indices(
    direction_id: str,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(service.indices, pool, ctx, request_id, direction_id)


@router.get("/investment/search")
async def search(
    keyword: str = Query(min_length=1, max_length=100),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(service.search, pool, ctx, request_id, keyword)


@router.get("/funds/screener")
async def screen(
    query: ScreenQuery = Query(),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(service.screen, pool, ctx, request_id, query)


@router.get("/investment/indices/{index_id}/analysis")
async def index_analysis(
    index_id: str,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(service.analysis, pool, ctx, request_id, "index", index_id)


@router.get("/investment/fund-ranges/{fund_code}/analysis")
async def nav_analysis(
    fund_code: str,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    from backend.funds.service import get_range
    from backend.investment.theme_graph import cached_nav_analysis

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        nav = await cached_nav_analysis(conn, fund_code, now_shanghai())
        facts = await get_range(conn, fund_code)
    if nav:
        return ok({**nav, "range": facts}, request_id=request_id, as_of=now_shanghai())
    return ok(
        {
            "state": "UNSUPPORTED"
            if facts["status"] == "AVAILABLE"
            else facts["status"],
            "score": None,
            "indicators": {},
            "points": [],
            "reasons": [],
            "range": facts,
            "asOf": facts.get("latestNavDate"),
            "source": facts.get("dataSource"),
            "seriesKind": "ACCUMULATED_NAV",
        },
        request_id=request_id,
        as_of=now_shanghai(),
    )


@router.get("/investment/instruments/{instrument_id}/analysis")
async def fund_analysis(
    instrument_id: UUID,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(
        service.analysis, pool, ctx, request_id, "fund", str(instrument_id)
    )


@router.post("/investment/refresh", status_code=202)
async def refresh(
    body: RefreshRequest,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(
        service.enqueue_refresh, pool, ctx, request_id, ctx.tenant_id, body
    )


@router.get("/investment/refresh/{run_id}")
async def refresh_status(
    run_id: UUID,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    return await _read(
        service.refresh_status, pool, ctx, request_id, ctx.tenant_id, str(run_id)
    )
