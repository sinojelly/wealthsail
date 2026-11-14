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
from backend.investment.personal_funds import personal_funds_reference, personal_nav_refresh_codes
from backend.funds.nav_history import ensure_nav_histories

router = APIRouter(prefix="/investment/personal-funds-reference", tags=["investment"])


@router.get("")
async def reference(
    include_research_mentions: bool = Query(
        default=False, alias="includeResearchMentions"
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
            include_research_mentions=False,
            allow_external_fetch=False,
        )
    return ok(data, request_id=request_id, as_of=now)


@router.post("/refresh")
async def refresh_reference(
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    """Explicit provider refresh with short RLS transactions around DB work."""
    now = now_shanghai()
    def transaction():
        return tenant_transaction(pool, ctx.user_id, ctx.tenant_id)

    # Resolve actor-owned codes before provider work; never hold a pool slot
    # while waiting for another transaction or an external history source.
    async with transaction() as conn:
        codes = await personal_nav_refresh_codes(conn, ctx.tenant_id, ctx.user_id, now)
    prepared = await ensure_nav_histories(
        None, codes, now, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
        provider_budget=max(1, len(codes)), allow_external_fetch=True,
        transaction_factory=transaction,
    )
    now = prepared["asOf"]
    async with transaction() as conn:
        data = await personal_funds_reference(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            now,
            include_research_mentions=False,
            allow_external_fetch=False,
        )
    data["navSync"] = {"policy": "EXPLICIT_REFRESH", "states": prepared["refresh"]}
    for fund in data.get("funds", []):
        history = fund.get("history")
        state = prepared["refresh"].get(fund.get("fundCode"))
        if isinstance(history, dict) and state:
            history["refresh"] = state
    return ok(data, request_id=request_id, as_of=now)
