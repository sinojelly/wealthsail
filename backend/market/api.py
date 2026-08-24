"""Market API: 市场热点（实时涨跌幅排行）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.dependencies import get_request_id, now_shanghai
from backend.errors import ok
from backend.integrations.market_data import get_hot_stocks

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
