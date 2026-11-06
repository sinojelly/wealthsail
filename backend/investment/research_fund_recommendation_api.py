"""Read-only news-driven fund recommendations, independent of formal reports."""

from uuid import UUID

from fastapi import APIRouter, Depends

from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.investment.research_topic_api import readonly_tenant_transaction
from backend.macro.research_opportunities import read_topic_fund_recommendations

router = APIRouter(
    prefix="/investment/daily-research/topic-fund-recommendations", tags=["investment"]
)


@router.get("/{run_id}")
async def read_recommendations(
    run_id: UUID,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with readonly_tenant_transaction(pool, ctx) as conn:
        value = await read_topic_fund_recommendations(conn, run_id, now)
    return ok(value, request_id=request_id, as_of=now)
