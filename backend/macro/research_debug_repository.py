"""Durable operator requests; model/source attempts keep their own immutable slots."""

from datetime import timedelta
import re

from backend.errors import ForbiddenError, NotFoundError, ValidationError_
from backend.macro.daily_repository import local
from backend.macro.research_diagnostics import sanitize_failure_diagnostics
from backend.macro.model_dispatch_repository import (
    ResearchModelBusy,
    lock_model_dispatch,
)

EXECUTION_WINDOW = timedelta(minutes=35)
REQUEST_MODES = frozenset({"DEBUG", "MANUAL"})

_READ_REQUEST = """SELECT q.*,t.status AS report_status,
 t.failure_stage,t.failure_reason_code,t.failure_rule_id,t.failure_item_index,t.failure_industry_codes
 FROM research.daily_research_refresh_requests q
 LEFT JOIN research.daily_research_runs r ON r.run_id=q.report_run_id
   AND r.debug_request_id=q.id AND r.run_mode=q.request_mode
   AND r.source_batch_id=q.batch_id AND r.dispatched_at>=q.started_at
   AND r.dispatched_at<=q.completed_at
 LEFT JOIN research.daily_research_results t ON t.run_id=r.run_id
   AND t.status=q.status AND t.recorded_at<=q.completed_at"""


def _modes(values):
    if not isinstance(values, (list, tuple, set, frozenset)) or any(
        not isinstance(value, str) or value not in REQUEST_MODES for value in values
    ):
        raise ValueError("invalid research request modes")
    return list(dict.fromkeys(values))


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
    proof = (
        row
        if terminal
        and status in {"FAILED", "UNCERTAIN"}
        and row.get("report_run_id")
        and row.get("report_status") == status
        else {}
    )
    diagnostics = sanitize_failure_diagnostics(
        stage=proof.get("failure_stage"),
        reason_code=proof.get("failure_reason_code"),
        rule_id=proof.get("failure_rule_id"),
        item_index=proof.get("failure_item_index"),
        missing_industry_codes=proof.get("failure_industry_codes"),
    )
    return {
        "id": str(row["id"]),
        "requestMode": row.get("request_mode", "DEBUG"),
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
        "failureStage": diagnostics["failure_stage"],
        "failureReasonCode": diagnostics["failure_reason_code"],
        "failureRuleId": diagnostics["failure_rule_id"],
        "failureItemIndex": diagnostics["failure_item_index"],
        "failureIndustryCodes": diagnostics.get("failure_industry_codes"),
    }


async def active_operator(conn, tenant_id, user_id):
    return await conn.fetchval(
        """SELECT EXISTS(SELECT 1 FROM identity.tenant_members m
        JOIN identity.tenants t ON t.id=m.tenant_id AND t.status='ACTIVE'
        WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.status='ACTIVE' AND m.role IN ('OWNER','ADMIN'))""",
        tenant_id,
        user_id,
    )


async def enqueue_refresh(
    conn, tenant_id, user_id, command_key, now, *, request_mode="DEBUG"
):
    if not isinstance(request_mode, str) or request_mode not in REQUEST_MODES:
        raise ValueError("invalid research request mode")
    stamp = local(now)
    if not await active_operator(conn, tenant_id, user_id):
        raise ForbiddenError("仅当前有效管理员可以重新研究")
    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            f"research-debug:{tenant_id}:{user_id}",
        )
        existing = await conn.fetchrow(
            _READ_REQUEST
            + """ JOIN research.daily_research_refresh_commands c
            ON q.id=c.request_id AND q.tenant_id=c.tenant_id AND q.user_id=c.user_id
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
            _READ_REQUEST
            + """ WHERE q.tenant_id=$1 AND q.user_id=$2 AND q.requested_at<=$3
            AND q.request_mode=$4 AND q.status IN ('QUEUED','RUNNING')
            ORDER BY q.requested_at DESC,q.id DESC LIMIT 1""",
            tenant_id,
            user_id,
            stamp,
            request_mode,
        )
        if active is not None:
            await _bind_command(
                conn, tenant_id, user_id, command_key, active["id"], stamp
            )
            return request_dto(active, stamp)
        row = await conn.fetchrow(
            """INSERT INTO research.daily_research_refresh_requests(tenant_id,user_id,command_key,requested_at,request_mode)
            VALUES($1,$2,$3,$4,$5) RETURNING *""",
            tenant_id,
            user_id,
            command_key,
            stamp,
            request_mode,
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
        _READ_REQUEST
        + " WHERE q.tenant_id=$1 AND q.user_id=$2 AND q.id=$3 AND q.requested_at<=$4",
        tenant_id,
        user_id,
        request_id,
        stamp,
    )
    if row is None:
        raise NotFoundError("重新研究任务不存在")
    return request_dto(row, stamp)


async def latest_refresh(conn, tenant_id, user_id, now, *, allowed_modes=None):
    stamp = local(now)
    modes = _modes(allowed_modes) if allowed_modes is not None else None
    if modes == []:
        return None
    row = await conn.fetchrow(
        _READ_REQUEST
        + " WHERE q.tenant_id=$1 AND q.user_id=$2 AND q.requested_at<=$3"
        + (" AND q.request_mode=ANY($4::text[])" if modes is not None else "")
        + " ORDER BY q.requested_at DESC,q.id DESC LIMIT 1",
        tenant_id,
        user_id,
        stamp,
        *([modes] if modes is not None else []),
    )
    return request_dto(row, stamp) if row else None


async def claim_refresh(conn, now, *, allowed_modes=None):
    modes = _modes(["DEBUG"] if allowed_modes is None else allowed_modes)
    if not modes:
        return None
    stamp = local(now)
    async with conn.transaction():
        try:
            await lock_model_dispatch(conn)
        except ResearchModelBusy:
            return None
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM research.daily_research_refresh_requests WHERE status='RUNNING')",
        ):
            return None
        # Stale role tokens cannot dispatch a queued costly model invocation.
        await conn.execute(
            """UPDATE research.daily_research_refresh_requests q SET status='FAILED',completed_at=$1,error_class='OperatorUnavailable'
            WHERE q.status='QUEUED' AND q.requested_at<=$1 AND q.request_mode=ANY($2::text[]) AND NOT EXISTS(
            SELECT 1 FROM identity.tenant_members m JOIN identity.tenants t ON t.id=m.tenant_id AND t.status='ACTIVE'
            WHERE m.tenant_id=q.tenant_id AND m.user_id=q.user_id AND m.status='ACTIVE' AND m.role IN ('OWNER','ADMIN'))""",
            stamp,
            modes,
        )
        return await conn.fetchrow(
            """WITH candidate AS (
              SELECT id FROM research.daily_research_refresh_requests WHERE status='QUEUED' AND requested_at<=$1
              AND request_mode=ANY($2::text[])
              ORDER BY requested_at,id LIMIT 1 FOR UPDATE SKIP LOCKED)
            UPDATE research.daily_research_refresh_requests q SET status='RUNNING',started_at=$1
            FROM candidate c WHERE q.id=c.id RETURNING q.*""",
            stamp,
            modes,
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
        """WITH proof AS (
          SELECT q.id,b.id AS batch_id,t.run_id AS report_run_id
          FROM research.daily_research_refresh_requests q
          LEFT JOIN market.alpha_public_batches b ON b.debug_request_id=q.id
            AND b.run_mode=q.request_mode AND b.claimed_at>=q.started_at AND b.claimed_at<=$3
            AND ($4::uuid IS NULL OR b.id=$4)
          LEFT JOIN research.daily_research_runs r ON r.debug_request_id=q.id
            AND r.run_mode=q.request_mode AND r.source_batch_id=b.id
            AND r.dispatched_at>=q.started_at AND r.dispatched_at<=$3
            AND ($5::uuid IS NULL OR r.run_id=$5)
          LEFT JOIN research.daily_research_results t ON t.run_id=r.run_id
            AND t.status=$2 AND t.recorded_at<=$3
          WHERE q.id=$1 AND q.status='RUNNING' AND q.started_at<=$3
            AND ($4::uuid IS NULL OR b.id IS NOT NULL)
            AND ($5::uuid IS NULL OR t.run_id IS NOT NULL)
            AND ($2<>'READY' OR (t.run_id IS NOT NULL AND b.status IN ('READY','PARTIAL') AND b.completed_at<=$3))
        ) UPDATE research.daily_research_refresh_requests q
        SET status=$2,completed_at=$3,batch_id=p.batch_id,report_run_id=p.report_run_id,error_class=$6
        FROM proof p WHERE q.id=p.id""",
        request_id,
        status,
        stamp,
        batch_id,
        report_run_id,
        error_class,
    )
