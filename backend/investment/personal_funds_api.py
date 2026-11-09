"""Actor fund references with authenticated public NAV cache repair."""

from fastapi import APIRouter, Depends, Query

from backend.common.database import tenant_transaction

from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.investment.personal_funds import personal_funds_reference

router = APIRouter(prefix="/investment/personal-funds-reference", tags=["investment"])


@router.get("")
async def reference(
    include_research_mentions: bool = Query(
        default=True, alias="includeResearchMentions"
    ),
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await personal_funds_reference(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            now,
            include_research_mentions=include_research_mentions,
        )
    return ok(data, request_id=request_id, as_of=now)
