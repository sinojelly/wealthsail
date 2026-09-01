"""Controlled one-shot report generation for tenant administrators."""

from __future__ import annotations

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import write_audit
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
        actor_type="DEBUG_USER",
        force=True,
        delivery_user_id=target,
    )
    report_id = str(report["id"])

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        outbox_id = await repository.get_report_outbox_id(
            conn, ctx.tenant_id, report_id, target
        )
        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action="DEBUG_REPORT_TRIGGERED",
            resource_type="reporting.reports",
            resource_id=report_id,
            details={
                "session": session,
                "targetUserId": target,
                "outboxId": outbox_id,
            },
        )
    return {"reportId": report_id, "outboxId": outbox_id}
