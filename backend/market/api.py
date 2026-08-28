"""Market API: 市场热点与市场情绪指数。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.market import service

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/hot")
async def market_hot(
    ctx: TenantContext = Depends(get_tenant_context),
    pool: Pool = Depends(get_pool),
    request_id: str = Depends(get_request_id),
) -> dict:
    """市场热点：读取 Worker 已落库的全市场涨跌幅排行。

    API 请求不触发任何外部 Provider；无快照或过期快照通过质量状态显式降级。
    """
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_market_hot(conn)
    return ok(
        data,
        request_id=request_id,
        as_of=as_of,
        quality_status=data["qualityStatus"],
    )


@router.get("/sentiment")
async def market_sentiment(
    ctx: TenantContext = Depends(get_tenant_context),
    pool: Pool = Depends(get_pool),
    request_id: str = Depends(get_request_id),
) -> dict:
    """今日市场情绪指数：涨跌家数、涨停/跌停、成交额、北向净流入（可选）。

    仅读取 Worker 已落库的最近快照；API 请求不会触发 Provider 或高权限写入。
    返回: { tradeDate, totalUp, totalDown, totalFlat, limitUp, limitDown,
            turnover(亿元), northFlow(亿元|null), sentimentScore(0-100), source }
    """
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_market_sentiment(conn)
    return ok(
        data,
        request_id=request_id,
        as_of=as_of,
        quality_status=data["qualityStatus"],
    )
