"""Authenticated trade-fill commands backed by the canonical ledger module."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from modules.errors import AuthRequiredError
from modules.identity.service import verify_password
from modules.ledger.repository import LedgerRepository

router = APIRouter(prefix="/trades", tags=["trades"])


class TradeFillDraftRequest(BaseModel):
    accountId: UUID
    instrumentId: UUID
    side: Literal["BUY", "SELL"]
    quantity: Decimal = Field(gt=0)
    unitPrice: Decimal = Field(gt=0)
    fee: Decimal = Field(default=Decimal("0"), ge=0)
    tradeDate: date
    decisionId: UUID | None = None


class TradeFillConfirmRequest(BaseModel):
    password: str = Field(min_length=1)
    description: str | None = Field(default=None, max_length=500)


@router.post("/fills", status_code=201)
async def create_fill_draft(
    body: TradeFillDraftRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await LedgerRepository().create_trade_fill_draft(
            conn,
            tenant_id=ctx.tenant_id,
            account_id=str(body.accountId),
            instrument_id=str(body.instrumentId),
            side=body.side,
            quantity=body.quantity,
            unit_price=body.unitPrice,
            fee=body.fee,
            trade_date=body.tradeDate,
            decision_id=str(body.decisionId) if body.decisionId else None,
            idempotency_key=idempotency_key,
            created_by=ctx.user_id,
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/fills/{fill_id}/confirm")
async def confirm_fill(
    fill_id: str,
    body: TradeFillConfirmRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        password_hash = await conn.fetchval(
            "SELECT password_hash FROM identity.profiles WHERE id=$1", ctx.user_id
        )
        if not verify_password(body.password, password_hash):
            raise AuthRequiredError("重新验证口令失败")
        data = await LedgerRepository().confirm_trade_fill(
            conn,
            tenant_id=ctx.tenant_id,
            fill_id=fill_id,
            idempotency_key=idempotency_key,
            confirmed_by=ctx.user_id,
            description=body.description,
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.get("/fills")
async def list_fills(
    limit: int = Query(default=50, ge=1, le=200),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await LedgerRepository().list_trade_fills(conn, ctx.tenant_id, limit=limit)
    return ok(data, request_id=request_id, as_of=now_shanghai())
