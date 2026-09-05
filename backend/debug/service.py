"""Controlled one-shot report generation for tenant administrators."""

from __future__ import annotations

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.errors import NotFoundError, ValidationError_
from backend.reports import intraday
from backend.reports import pre_market_personalized
from backend.reports import repository
from backend.reports import service as report_service

# Split-phase values accepted via the `?phase=` query parameter.
_PRE_MARKET_PHASE_BY_QUERY = {
    "MACRO": "PRE_MARKET_MACRO",
    "PERSONALIZED": "PRE_MARKET_PERSONALIZED",
}
_ALLOWED_PHASES_BY_SESSION: dict[str, set[str | None]] = {
    "PRE_MARKET": {None, "MACRO", "PERSONALIZED"},
    "INTRADAY": {None, "ALERT"},
    "FUND_1430": {None},
    "POST_MARKET": {None},
}


async def trigger_report_once(
    pool: Pool,
    ctx: TenantContext,
    settings: Settings,
    session: str,
    *,
    phase: str | None = None,
) -> dict[str, str | None]:
    """Generate a new report version and queue only the operator's delivery.

    This path deliberately has no dependency on scheduler jobs or job runs.
    The regular Worker still leases and sends an Outbox row when one is created.

    For ``PRE_MARKET`` the optional ``phase`` selects the shared macro brief
    (default) or one member's personalized report.  A member with no personal
    facts returns ``{reportId: None, outboxId: None}`` without writing a report.
    """
    ctx.require_roles("OWNER", "ADMIN")
    if session not in repository.REPORT_SESSIONS:
        raise ValidationError_(
            "未知报告时段",
            field_errors=[{"field": "session", "message": session}],
        )
    if phase not in _ALLOWED_PHASES_BY_SESSION[session]:
        raise ValidationError_(
            f"{session} 不支持 phase={phase}",
            field_errors=[
                {
                    "field": "phase",
                    "message": "报告时段与变体不匹配",
                }
            ],
        )

    await _require_active_operator(pool, ctx)
    target = ctx.user_id

    if session == "PRE_MARKET":
        report_variant = _PRE_MARKET_PHASE_BY_QUERY.get(phase, "PRE_MARKET_MACRO")
        if report_variant == "PRE_MARKET_PERSONALIZED":
            return await _trigger_personalized(pool, ctx, settings)
        report = await report_service.generate_report(
            pool,
            ctx,
            settings,
            session,
            actor_type="USER",
            force=True,
            report_variant="PRE_MARKET_MACRO",
            delivery_user_id=target,
            # Re-run the current deterministic renderer over the governed
            # source snapshot, without recollecting or overwriting the cache.
            rebuild_shared_draft=True,
            trigger_audit_action="DEBUG_REPORT_TRIGGERED",
            trigger_audit_details={
                "targetUserId": target,
                "sharedDraftRebuilt": True,
            },
        )
        report_id = str(report["id"])
        outbox_id = report.pop("_deliveryOutboxId", None)
        return {"reportId": report_id, "outboxId": outbox_id}

    if session == "INTRADAY" and phase == "ALERT":
        return await _trigger_intraday_alert(pool, ctx, settings)

    report = await report_service.generate_report(
        pool,
        ctx,
        settings,
        session,
        # audit.audit_log intentionally has a closed actor_type vocabulary;
        # DEBUG_REPORT_TRIGGERED below distinguishes this controlled action.
        actor_type="USER",
        force=True,
        # STANDARD report content stays tenant-wide, while this debug
        # operation creates an Outbox row only for its authenticated operator.
        delivery_user_id=target,
        trigger_audit_action="DEBUG_REPORT_TRIGGERED",
        trigger_audit_details={"targetUserId": target},
    )
    report_id = str(report["id"])
    outbox_id = report.pop("_deliveryOutboxId", None)
    return {"reportId": report_id, "outboxId": outbox_id}


async def _trigger_personalized(
    pool: Pool,
    ctx: TenantContext,
    settings: Settings,
) -> dict[str, str | None]:
    """Run the authenticated operator's 08:35 report as a new version."""
    report = await pre_market_personalized.generate_pre_market_personalized(
        pool,
        ctx,
        settings,
        force=True,
        trigger_audit_action="DEBUG_REPORT_TRIGGERED",
        trigger_audit_details={"targetUserId": ctx.user_id},
    )
    if report is None:
        return {"reportId": None, "outboxId": None}
    report_id = str(report["id"])
    outbox_id = report.pop("_deliveryOutboxId", None)
    return {"reportId": report_id, "outboxId": outbox_id}


async def _trigger_intraday_alert(
    pool: Pool,
    ctx: TenantContext,
    settings: Settings,
) -> dict[str, str | None]:
    """Re-evaluate the operator's alert and bypass only its delivery cooldown."""
    report = await intraday.maybe_generate_intraday_alert(
        pool,
        ctx,
        settings,
        ignore_cooldown=True,
        actor_type="USER",
        trigger_audit_action="DEBUG_REPORT_TRIGGERED",
        trigger_audit_details={"targetUserId": ctx.user_id},
    )
    if report is None:
        return {"reportId": None, "outboxId": None}
    report_id = str(report["id"])
    outbox_id = report.pop("_deliveryOutboxId", None)
    return {"reportId": report_id, "outboxId": outbox_id}


async def _require_active_operator(pool: Pool, ctx: TenantContext) -> None:
    """Reject a stale tenant token whose membership is no longer active."""
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        active_member = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM identity.tenant_members
                 WHERE tenant_id = $1
                   AND user_id = $2
                   AND status = 'ACTIVE'
            )
            """,
            ctx.tenant_id,
            ctx.user_id,
        )
    if not active_member:
        raise NotFoundError("目标用户不是当前租户的活动成员")
