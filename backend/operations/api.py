"""Operations API: /api/v1/operations/jobs · freshness · outbox · audit.

OWNER/ADMIN only — the RLS policies (migration 008) and the role check below
both enforce this; the check gives a clean FORBIDDEN before hitting the DB.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context, now_shanghai
from backend.errors import ok
from backend.operations import repository

router = APIRouter(prefix="/operations", tags=["operations"])


def _require_owner_admin(ctx: TenantContext) -> None:
    ctx.require_roles("OWNER", "ADMIN")


@router.get("/jobs")
async def operations_jobs(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """作业定义与最近运行状态（含耗时与失败类别）。"""
    _require_owner_admin(ctx)
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        jobs = await repository.list_jobs(conn, ctx.tenant_id)
        runs = await repository.list_job_runs(conn, ctx.tenant_id, limit=30)
    data = {
        "jobs": [
            {
                "id": str(j["id"]),
                "jobName": j["job_name"],
                "cronExpr": j["cron_expr"],
                "reportSession": j["report_session"],
                "isEnabled": j["is_enabled"],
            }
            for j in jobs
        ],
        "runs": [
            {
                "id": str(r["id"]),
                "jobId": str(r["job_id"]) if r["job_id"] else None,
                "jobKey": r["job_key"],
                "startedAt": r["started_at"],
                "finishedAt": r["finished_at"],
                "status": r["status"],
                "errorClass": r["error_class"],
                "durationSeconds": (
                    round((r["finished_at"] - r["started_at"]).total_seconds(), 1)
                    if r["finished_at"] and r["started_at"]
                    else None
                ),
            }
            for r in runs
        ],
    }
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/freshness")
async def operations_freshness(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """数据新鲜度：净值与持仓快照的最新日期与 FRESH 覆盖。"""
    _require_owner_admin(ctx)
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await repository.freshness(conn, ctx.tenant_id)
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/outbox")
async def operations_outbox(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """消息投递队列：状态计数与最近消息（脱敏，不含 payload）。"""
    _require_owner_admin(ctx)
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await repository.outbox_status(conn, ctx.tenant_id)
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/audit")
async def operations_audit(
    limit: int = Query(default=50, ge=1, le=200),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """不可变审计日志（脱敏，不含 details）。"""
    _require_owner_admin(ctx)
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        rows = await repository.list_audit(conn, ctx.tenant_id, limit=limit)
    data = [
        {
            "id": str(r["id"]),
            "actorType": r["actor_type"],
            "actorId": r["actor_id"],
            "action": r["action"],
            "resourceType": r["resource_type"],
            "resourceId": r["resource_id"],
            "correlationId": r["correlation_id"],
            "createdAt": r["created_at"],
        }
        for r in rows
    ]
    return ok(data, request_id=request_id, as_of=as_of)
