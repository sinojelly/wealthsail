"""Read-only actor fund references, including opt-in current historical context."""

from fastapi import APIRouter, Depends, Query

from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.investment.personal_funds import personal_funds_reference
from backend.investment.research_preview_api import readonly_tenant_transaction

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
    async with readonly_tenant_transaction(pool, ctx) as conn:
        data = await personal_funds_reference(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            now,
            include_research_mentions=include_research_mentions,
        )
    return ok(data, request_id=request_id, as_of=now)
