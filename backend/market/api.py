"""Market API: 市场热点与市场情绪指数。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.integrations.market_data import get_hot_stocks
from backend.market import service

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/hot")
async def market_hot(
    _ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """市场热点：全市场实时涨跌幅排行前 10。

    返回: { items: [{symbol, name, price, change_pct, volume, turnover}], updatedAt }
    降级链: AkShare 全市场快照 → 东财 clist → 新浪候选池。
    """
    as_of = now_shanghai()
    items = await get_hot_stocks(limit=10)
    return ok(
        {"items": items, "updatedAt": as_of},
        request_id=request_id,
        as_of=as_of,
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
    return ok(data, request_id=request_id, as_of=as_of)
