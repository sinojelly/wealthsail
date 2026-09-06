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
from backend.debug.service import get_report_trigger_run, trigger_report_once


ReportSession = Literal["PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"]
# Split pre-market debug phase selector (?phase=...).
DebugPhase = Literal["MACRO", "PERSONALIZED", "ALERT"]

router = APIRouter(prefix="/debug/reports", tags=["debug"])


@router.get("/runs/{run_id}")
async def report_trigger_run_status(
    run_id: UUID,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Poll an operator-scoped background FUND_1430 refresh."""
    ctx.require_roles("OWNER", "ADMIN")
    data = await get_report_trigger_run(pool, ctx, str(run_id))
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/trigger/{session}")
async def trigger_report(
    session: ReportSession,
    phase: DebugPhase | None = Query(default=None),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Force one report version, or enqueue a FUND_1430 provider refresh.

    ``PRE_MARKET`` runs the shared 08:30 macro brief by default;
    ``?phase=PERSONALIZED`` runs the authenticated operator's 08:35 report.
    ``INTRADAY?phase=ALERT`` re-evaluates that same user's current alert facts.
    ``FUND_1430`` is asynchronous because its Worker run refreshes real market
    data before rendering; clients poll ``/runs/{runId}`` for completion.
    Report content may remain tenant-wide, but manual delivery is always
    restricted to the authenticated operator.
    """
    ctx.require_roles("OWNER", "ADMIN")
    data = await trigger_report_once(
        pool,
        ctx,
        settings,
        session,
        phase=phase,
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())
