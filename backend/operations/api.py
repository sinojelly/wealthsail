"""Operations API: /api/v1/operations/jobs · freshness · outbox · audit.

OWNER/ADMIN only — the RLS policies (migration 008) and the role check below
both enforce this; the check gives a clean FORBIDDEN before hitting the DB.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query
import asyncpg

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
    now_shanghai,
    write_audit,
)
from backend.errors import DataUnavailableError, ValidationError_, ok
from backend.operations import repository

router = APIRouter(prefix="/operations", tags=["operations"])
logger = logging.getLogger(__name__)


@router.get("/ai-usage")
async def operations_ai_usage(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    view: Literal["all", "tasks", "account"] = Query(default="all"),
    refresh_account: bool = Query(default=False),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    from backend.ai_usage import repository as usage_repository
    from backend.integrations import codex_client

    _require_owner_admin(ctx)
    limitations = ["ACCOUNT_DELTA_ESTIMATE", "HISTORY_FROM_INSTALLATION"]
    data = {}
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        platform = bool(
            await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM identity.platform_roles
            WHERE user_id::text=$1 AND role='PLATFORM_ADMIN' AND status='ACTIVE')""",
                ctx.user_id,
            )
        )
        # A missing migration is actionable and never implies zero usage.
        if view != "account":
            try:
                async with conn.transaction():
                    data = await usage_repository.list_tasks(
                        conn,
                        ctx.tenant_id,
                        include_global=platform,
                        limit=limit,
                        offset=offset,
                    )
            except asyncpg.UndefinedTableError:
                data = {"tasks": [], "total": 0, "limit": limit, "offset": offset}
                limitations.append("USAGE_LEDGER_NOT_MIGRATED")
    if view == "tasks":
        return ok(
            {**data, "limitations": limitations},
            request_id=request_id,
            as_of=now_shanghai(),
        )
    account = {
        "status": "RESTRICTED",
        "provider": "codex",
        "email": None,
        "planType": None,
        "authType": None,
        "observedAt": None,
        "windows": [],
        "credits": None,
        "limitations": ["PLATFORM_ADMIN_REQUIRED"],
    }
    if platform:
        started = time.monotonic()
        try:
            raw = (
                await codex_client.account(settings, refresh=True)
                if refresh_account
                else await codex_client.account(settings)
            )
            account = {key: raw.get(key, default) for key, default in account.items()}
        except Exception as exc:
            logger.warning(
                "codex_account stage=operations_api error_class=%s duration_ms=%.1f",
                type(exc).__name__,
                (time.monotonic() - started) * 1000,
            )
            account.update(status="UNAVAILABLE", limitations=["ACCOUNT_READ_FAILED"])
    return ok(
        {"account": account}
        if view == "account"
        else {**data, "account": account, "limitations": limitations},
        request_id=request_id,
        as_of=now_shanghai(),
    )


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


@router.get("/fund-estimate")
async def operations_fund_estimate(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """基金 14:30 估值运行条件、最近任务与覆盖状态。"""
    _require_owner_admin(ctx)
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await repository.fund_estimate_status(conn, ctx.tenant_id)
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/fund-estimate/run")
async def operations_run_fund_estimate(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """提交持久化 FUND_1430 请求；Provider I/O 只由 Worker 执行。"""
    _require_owner_admin(ctx)
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        status = await repository.fund_estimate_status(conn, ctx.tenant_id)
        if not status["canRun"]:
            raise ValidationError_(
                "今天不是交易日，无需生成盘中估值；当前使用最近官方净值"
            )
        result = await repository.enqueue_manual_fund_estimate(conn, ctx.tenant_id)
        if result["reason"] == "JOB_NOT_CONFIGURED":
            raise DataUnavailableError("当前租户尚未启用基金 14:30 标准任务")
        if result["created"]:
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="FUND_ESTIMATE_RUN_REQUESTED",
                resource_type="workflow.job_runs",
                resource_id=result["run"]["id"],
                correlation_id=request_id,
                details={"session": "FUND_1430"},
            )
    return ok(result, request_id=request_id, as_of=as_of)


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
