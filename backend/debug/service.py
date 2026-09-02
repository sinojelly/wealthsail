"""Controlled one-shot report generation for tenant administrators."""

from __future__ import annotations

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.errors import NotFoundError, ValidationError_
from backend.reports import pre_market_personalized
from backend.reports import repository
from backend.reports import service as report_service

# Split-phase values accepted via the `?phase=` query parameter.
_PRE_MARKET_PHASE_BY_QUERY = {
    "MACRO": "PRE_MARKET_MACRO",
    "PERSONALIZED": "PRE_MARKET_PERSONALIZED",
}


async def trigger_report_once(
    pool: Pool,
    ctx: TenantContext,
    settings: Settings,
    session: str,
    *,
    target_user_id: str | None = None,
    phase: str | None = None,
) -> dict[str, str | None]:
    """Generate one report version and queue at most one user's delivery.

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

    if session == "PRE_MARKET":
        report_variant = _PRE_MARKET_PHASE_BY_QUERY.get(phase, "PRE_MARKET_MACRO")
        if report_variant == "PRE_MARKET_PERSONALIZED":
            return await _trigger_personalized(pool, ctx, settings, target_user_id)
        report = await report_service.generate_report(
            pool,
            ctx,
            settings,
            session,
            actor_type="USER",
            force=True,
            report_variant="PRE_MARKET_MACRO",
            trigger_audit_action="DEBUG_REPORT_TRIGGERED",
            trigger_audit_details={"targetUserId": target_user_id or ctx.user_id},
        )
        report_id = str(report["id"])
        outbox_id = report.pop("_deliveryOutboxId", None)
        return {"reportId": report_id, "outboxId": outbox_id}

    target = target_user_id or ctx.user_id
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
            target,
        )
    if not active_member:
        raise NotFoundError("目标用户不是当前租户的活动成员")

    report = await report_service.generate_report(
        pool,
        ctx,
        settings,
        session,
        # audit.audit_log intentionally has a closed actor_type vocabulary;
        # DEBUG_REPORT_TRIGGERED below distinguishes this controlled action.
        actor_type="USER",
        force=True,
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
    target_user_id: str | None,
) -> dict[str, str | None]:
    """Run one member's 08:35 report via the controlled debug trigger."""
    target = target_user_id or ctx.user_id
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
            target,
        )
    if not active_member:
        raise NotFoundError("目标用户不是当前租户的活动成员")

    member_ctx = TenantContext(
        user_id=target, tenant_id=ctx.tenant_id, tenant_role="OWNER"
    )
    report = await pre_market_personalized.generate_pre_market_personalized(
        pool, member_ctx, settings
    )
    if report is None:
        return {"reportId": None, "outboxId": None}
    report_id = str(report["id"])
    outbox_id = report.pop("_deliveryOutboxId", None)
    return {"reportId": report_id, "outboxId": outbox_id}
