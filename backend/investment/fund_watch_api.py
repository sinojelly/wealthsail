"""Actor-specific visibility and lifecycle commands for tracked/held funds."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from backend.common.database import tenant_transaction
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.investment.fund_watch import set_watch_status

router = APIRouter(prefix="/investment/fund-watch", tags=["investment"])


class FundWatchStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fund_code: str | None = Field(default=None, alias="fundCode", pattern=r"^[0-9]{6}$")
    instrument_id: UUID | None = Field(default=None, alias="instrumentId")
    fund_market: str = Field(
        default="FUND_CN",
        alias="fundMarket",
        min_length=1,
        max_length=32,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    enabled: StrictBool

    @model_validator(mode="after")
    def exact_identity(self):
        if (self.fund_code is None) == (self.instrument_id is None):
            raise ValueError("exactly one fundCode or instrumentId is required")
        return self


@router.post("/status")
async def set_status(
    body: FundWatchStatusRequest,
    pool=Depends(get_pool),
    ctx=Depends(get_tenant_context),
    request_id=Depends(get_request_id),
):
    now = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await set_watch_status(
            conn,
            ctx.tenant_id,
            ctx.user_id,
            body.fund_code,
            body.fund_market,
            body.enabled,
            now,
            instrument_id=body.instrument_id,
        )
    return ok(data, request_id=request_id, as_of=now)
