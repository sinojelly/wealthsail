"""OWNER/ADMIN-only one-shot report trigger API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.common.database import Pool
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ok
from backend.debug.service import trigger_report_once


ReportSession = Literal["PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"]
# Split pre-market debug phase selector (?phase=...).
DebugPhase = Literal["MACRO", "PERSONALIZED", "ALERT"]

router = APIRouter(prefix="/debug/reports", tags=["debug"])


@router.post("/trigger/{session}")
async def trigger_report(
    session: ReportSession,
    user_id: UUID | None = Query(default=None),
    phase: DebugPhase | None = Query(default=None),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Force one report version without creating a scheduler job.

    ``PRE_MARKET`` runs the shared 08:30 macro brief by default; ``?phase=PERSONALIZED&user_id=<uuid>``
    runs one member's 08:35 report instead. ``?session=INTRADAY&phase=ALERT&user_id=<uuid>``
    runs one member's condition-triggered intraday alert (no-op when no threshold
    is breached or the 30-minute cooldown is active).
    """
    ctx.require_roles("OWNER", "ADMIN")
    data = await trigger_report_once(
        pool,
        ctx,
        settings,
        session,
        target_user_id=str(user_id) if user_id is not None else ctx.user_id,
        phase=phase,
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())
