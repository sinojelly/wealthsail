"""Controlled one-shot report generation for tenant administrators."""

from __future__ import annotations

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.errors import NotFoundError, ValidationError_
from backend.reports import repository
from backend.reports import service as report_service


async def trigger_report_once(
    pool: Pool,
    ctx: TenantContext,
    settings: Settings,
    session: str,
    *,
    target_user_id: str | None = None,
) -> dict[str, str | None]:
    """Generate one report version and queue at most one user's delivery.

    This path deliberately has no dependency on scheduler jobs or job runs.
    The regular Worker still leases and sends an Outbox row when one is created.
    """
    ctx.require_roles("OWNER", "ADMIN")
    if session not in repository.REPORT_SESSIONS:
        raise ValidationError_(
            "未知报告时段",
            field_errors=[{"field": "session", "message": session}],
        )

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
