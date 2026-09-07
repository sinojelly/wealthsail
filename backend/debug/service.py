"""Controlled one-shot report generation for tenant administrators."""

from __future__ import annotations

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import now_shanghai
from backend.errors import DataUnavailableError, NotFoundError, ValidationError_
from backend.jobs import scheduler as job_scheduler
from backend.operations import repository as operations_repository
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

    Most report types render synchronously and let the Worker send the Outbox
    row. ``FUND_1430`` is different: it first enqueues a durable Worker run so
    real provider data can be refreshed before the report is rendered.

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

    if session == "FUND_1430":
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            queued = await operations_repository.enqueue_manual_fund_estimate(
                conn,
                ctx.tenant_id,
                target_user_id=target,
            )
        if queued["reason"] == "JOB_NOT_CONFIGURED":
            raise DataUnavailableError("当前租户尚未启用基金 14:30 标准任务")
        run = queued["run"]
        return {
            "reportId": None,
            "outboxId": None,
            "runId": run["id"],
            "status": run["status"],
        }

    if session == "PRE_MARKET":
        report_variant = _PRE_MARKET_PHASE_BY_QUERY.get(phase, "PRE_MARKET_MACRO")
        if report_variant == "PRE_MARKET_PERSONALIZED":
            return await _trigger_personalized(pool, ctx, settings)
        # The automatic scheduler already skips weekends and exchange holidays.
        # Manual debug intentionally remains available on those days, but its
        # report must disclose that the market facts are carried forward.
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            non_trading_debug = not await job_scheduler.is_trading_day(
                conn, now_shanghai()
            )
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
            pre_market_non_trading_debug=non_trading_debug,
            trigger_audit_action="DEBUG_REPORT_TRIGGERED",
            trigger_audit_details={
                "targetUserId": target,
                "sharedDraftRebuilt": True,
                "nonTradingDayCarryForward": non_trading_debug,
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


async def get_report_trigger_run(
    pool: Pool,
    ctx: TenantContext,
    run_id: str,
) -> dict[str, str | None]:
    """Return one debug FUND_1430 run owned by the authenticated operator."""
    ctx.require_roles("OWNER", "ADMIN")
    await _require_active_operator(pool, ctx)
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT run.id, run.status, run.error_class,
                   report.id AS report_id
              FROM workflow.job_runs run
              LEFT JOIN LATERAL (
                    SELECT id
                      FROM reporting.reports
                     WHERE tenant_id = run.tenant_id
                       AND job_run_id = run.id
                     ORDER BY created_at DESC, id DESC
                     LIMIT 1
              ) report ON TRUE
             WHERE run.tenant_id = $1
               AND run.id = $2::uuid
               AND run.job_type IS NULL
               AND run.payload ->> 'targetUserId' = $3::text
            """,
            ctx.tenant_id,
            run_id,
            ctx.user_id,
        )
        if row is None:
            raise NotFoundError("报告刷新任务不存在")
        report_id = str(row["report_id"]) if row["report_id"] is not None else None
        outbox_id = (
            await repository.get_report_outbox_id(
                conn,
                ctx.tenant_id,
                report_id,
                ctx.user_id,
            )
            if report_id is not None
            else None
        )
    return {
        "runId": str(row["id"]),
        "status": str(row["status"]),
        "reportId": report_id,
        "outboxId": outbox_id,
        "errorClass": row["error_class"],
    }


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
