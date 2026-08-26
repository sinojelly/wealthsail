"""Market API: 市场热点与市场情绪指数。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.common.database import Pool
from backend.config import Settings
from backend.dependencies import get_pool, get_request_id, get_settings_dep, now_shanghai
from backend.errors import ok
from backend.integrations.market_data import get_hot_stocks
from backend.market import service

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/hot")
async def market_hot(
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
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """今日市场情绪指数：涨跌家数、涨停/跌停、成交额、北向净流入（可选）。

    实时抓取（AkShare 全市场快照）并落库；抓取失败回退库内最近一条。
    返回: { tradeDate, totalUp, totalDown, totalFlat, limitUp, limitDown,
            turnover(亿元), northFlow(亿元|null), sentimentScore(0-100), source }
    """
    as_of = now_shanghai()
    data = await service.get_market_sentiment(pool, settings)
    return ok(data, request_id=request_id, as_of=as_of)
