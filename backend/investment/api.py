from fastapi import APIRouter, Depends, Query

from backend.common.database import tenant_transaction
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.investment import service
from backend.investment.schemas import ScreenQuery

router = APIRouter(tags=["investment"])


async def _read(operation, pool, ctx, request_id, *args):
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await operation(conn, *args)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/investment/market-context")
async def market_context(pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    return await _read(service.market_context, pool, ctx, request_id)


@router.get("/investment/directions")
async def directions(pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    return await _read(service.directions, pool, ctx, request_id)


@router.get("/investment/directions/{direction_id}/indices")
async def indices(direction_id: str, pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    return await _read(service.indices, pool, ctx, request_id, direction_id)


@router.get("/investment/search")
async def search(keyword: str = Query(min_length=1, max_length=100), pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    return await _read(service.search, pool, ctx, request_id, keyword)


@router.get("/funds/screener")
async def screen(query: ScreenQuery = Query(), pool=Depends(get_pool), ctx=Depends(get_tenant_context), request_id=Depends(get_request_id)):
    return await _read(service.screen, pool, ctx, request_id, query)
