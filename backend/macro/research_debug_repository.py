"""Durable operator requests; model/source attempts keep their own immutable slots."""

from datetime import timedelta
import re

from backend.errors import ForbiddenError, NotFoundError, ValidationError_
from backend.macro.daily_repository import local

EXECUTION_WINDOW = timedelta(minutes=35)


def request_dto(row, now):
    stamp = local(now)
    started, completed = row.get("started_at"), row.get("completed_at")
    terminal = completed is not None and completed <= stamp
    expired = (
        started is not None and stamp - started > EXECUTION_WINDOW and not terminal
    )
    status = (
        row["status"]
        if terminal
        else "UNCERTAIN"
        if expired
        else "RUNNING"
        if started and started <= stamp
        else "QUEUED"
    )
    return {
        "id": str(row["id"]),
        "status": status,
        "requestedAt": row["requested_at"],
        "startedAt": started if started and started <= stamp else None,
        "completedAt": completed if terminal else None,
        "batchId": str(row["batch_id"]) if terminal and row.get("batch_id") else None,
        "reportRunId": str(row["report_run_id"])
        if terminal and row.get("report_run_id")
        else None,
        "errorClass": row.get("error_class")
        if terminal
        else "ExecutionWindowExpired"
        if expired
        else None,
    }


async def active_operator(conn, tenant_id, user_id):
    return await conn.fetchval(
        """SELECT EXISTS(SELECT 1 FROM identity.tenant_members m
        JOIN identity.tenants t ON t.id=m.tenant_id AND t.status='ACTIVE'
        WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.status='ACTIVE' AND m.role IN ('OWNER','ADMIN'))""",
        tenant_id,
        user_id,
    )


async def enqueue_refresh(conn, tenant_id, user_id, command_key, now):
    stamp = local(now)
    if not await active_operator(conn, tenant_id, user_id):
        raise ForbiddenError("仅当前有效管理员可以重新研究")
    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            f"research-debug:{tenant_id}:{user_id}",
        )
        existing = await conn.fetchrow(
            """SELECT q.* FROM research.daily_research_refresh_commands c
            JOIN research.daily_research_refresh_requests q ON q.id=c.request_id AND q.tenant_id=c.tenant_id AND q.user_id=c.user_id
            WHERE c.tenant_id=$1 AND c.user_id=$2 AND c.command_key=$3""",
            tenant_id,
            user_id,
            command_key,
        )
        if existing is not None:
            if existing["requested_at"] > stamp:
                raise ValidationError_("当前时钟早于已保存的研究请求")
            return request_dto(existing, stamp)
        active = await conn.fetchrow(
            """SELECT * FROM research.daily_research_refresh_requests
            WHERE tenant_id=$1 AND user_id=$2 AND requested_at<=$3
            AND (status='QUEUED' OR (status='RUNNING' AND started_at>=$4))
            ORDER BY requested_at DESC,id DESC LIMIT 1""",
            tenant_id,
            user_id,
            stamp,
            stamp - EXECUTION_WINDOW,
        )
        if active is not None:
            await _bind_command(
                conn, tenant_id, user_id, command_key, active["id"], stamp
            )
            return request_dto(active, stamp)
        row = await conn.fetchrow(
            """INSERT INTO research.daily_research_refresh_requests(tenant_id,user_id,command_key,requested_at)
            VALUES($1,$2,$3,$4) RETURNING *""",
            tenant_id,
            user_id,
            command_key,
            stamp,
        )
        await _bind_command(conn, tenant_id, user_id, command_key, row["id"], stamp)
    return request_dto(row, stamp)


async def _bind_command(conn, tenant_id, user_id, command_key, request_id, now):
    await conn.execute(
        """INSERT INTO research.daily_research_refresh_commands(tenant_id,user_id,command_key,request_id,requested_at)
        VALUES($1,$2,$3,$4,$5)""",
        tenant_id,
        user_id,
        command_key,
        request_id,
        now,
    )


async def read_refresh(conn, tenant_id, user_id, request_id, now):
    stamp = local(now)
    row = await conn.fetchrow(
        "SELECT * FROM research.daily_research_refresh_requests WHERE tenant_id=$1 AND user_id=$2 AND id=$3 AND requested_at<=$4",
        tenant_id,
        user_id,
        request_id,
        stamp,
    )
    if row is None:
        raise NotFoundError("重新研究任务不存在")
    return request_dto(row, stamp)


async def latest_refresh(conn, tenant_id, user_id, now):
    stamp = local(now)
    row = await conn.fetchrow(
        "SELECT * FROM research.daily_research_refresh_requests WHERE tenant_id=$1 AND user_id=$2 AND requested_at<=$3 ORDER BY requested_at DESC,id DESC LIMIT 1",
        tenant_id,
        user_id,
        stamp,
    )
    return request_dto(row, stamp) if row else None


async def claim_refresh(conn, now):
    stamp = local(now)
    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext('research-debug-global'))"
        )
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM research.daily_research_refresh_requests WHERE status='RUNNING' AND started_at>=$1)",
            stamp - EXECUTION_WINDOW,
        ):
            return None
        # Stale role tokens cannot dispatch a queued costly model invocation.
        await conn.execute(
            """UPDATE research.daily_research_refresh_requests q SET status='FAILED',completed_at=$1,error_class='OperatorUnavailable'
            WHERE q.status='QUEUED' AND q.requested_at<=$1 AND NOT EXISTS(
            SELECT 1 FROM identity.tenant_members m JOIN identity.tenants t ON t.id=m.tenant_id AND t.status='ACTIVE'
            WHERE m.tenant_id=q.tenant_id AND m.user_id=q.user_id AND m.status='ACTIVE' AND m.role IN ('OWNER','ADMIN'))""",
            stamp,
        )
        return await conn.fetchrow(
            """WITH candidate AS (
              SELECT id FROM research.daily_research_refresh_requests WHERE status='QUEUED' AND requested_at<=$1
              ORDER BY requested_at,id LIMIT 1 FOR UPDATE SKIP LOCKED)
            UPDATE research.daily_research_refresh_requests q SET status='RUNNING',started_at=$1
            FROM candidate c WHERE q.id=c.id RETURNING q.*""",
            stamp,
        )


async def finish_refresh(
    conn,
    request_id,
    now,
    *,
    status,
    batch_id=None,
    report_run_id=None,
    error_class=None,
):
    if status not in {"READY", "FAILED", "UNCERTAIN"}:
        raise ValueError("invalid debug terminal status")
    if status == "READY" and (
        batch_id is None or report_run_id is None or error_class is not None
    ):
        raise ValueError("READY requires a batch and report")
    if error_class is not None and not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{0,79}", error_class
    ):
        error_class = "ResearchError"
    stamp = local(now)
    await conn.execute(
        """UPDATE research.daily_research_refresh_requests q SET status=$2,completed_at=$3,batch_id=$4,report_run_id=$5,error_class=$6
        WHERE q.id=$1 AND q.status='RUNNING' AND q.started_at<=$3
          AND (q.started_at>=$7 OR EXISTS(
            SELECT 1 FROM research.daily_research_runs r
            JOIN research.daily_research_results t ON t.run_id=r.run_id
            WHERE r.run_id=$5 AND r.run_mode='DEBUG' AND r.debug_request_id=q.id
              AND r.source_batch_id=$4 AND r.dispatched_at>=q.started_at
              AND t.status=$2 AND t.recorded_at<=$3))""",
        request_id,
        status,
        stamp,
        batch_id,
        report_run_id,
        error_class,
        stamp - EXECUTION_WINDOW,
    )
