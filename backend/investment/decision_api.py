"""Explicitly opted-in personal research monitors; no external delivery."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from backend.common.database import tenant_transaction
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.investment import decision_repository as repository

router = APIRouter(tags=["investment"])


class MonitorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)
    direction_id: str | None = Field(
        default=None, alias="directionId", min_length=1, max_length=100
    )
    index_id: str | None = Field(
        default=None, alias="indexId", min_length=1, max_length=100
    )
    instrument_id: UUID | None = Field(default=None, alias="instrumentId")
    fund_code: str | None = Field(
        default=None, alias="fundCode", min_length=1, max_length=32
    )
    fund_market: str | None = Field(
        default=None, alias="fundMarket", min_length=1, max_length=32
    )
    enabled: StrictBool


@router.post("/investment/condition-monitors")
async def set_monitor(
    body: MonitorRequest,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await repository.set_monitor(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            body.model_dump(exclude={"enabled"}),
            body.enabled,
            now,
        )
    return ok(data, request_id=request_id, as_of=now)


@router.get("/investment/condition-monitors")
async def list_monitors(
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await repository.list_monitors(conn, ctx.tenant_id, ctx.user_id)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/investment/condition-notices")
async def list_notices(
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await repository.list_notices(conn, ctx.tenant_id, ctx.user_id)
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/investment/condition-notices/{notice_id}/ack")
async def ack_notice(
    notice_id: UUID,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await repository.ack_notice(
            conn, ctx.tenant_id, ctx.user_id, notice_id, now
        )
    return ok(data, request_id=request_id, as_of=now)
