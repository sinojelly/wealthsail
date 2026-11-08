"""Durable operator requests; model/source attempts keep their own immutable slots."""

from datetime import datetime, timedelta
import json
import re

from asyncpg import InsufficientPrivilegeError

from backend.errors import (
    ForbiddenError,
    NotFoundError,
    ValidationError_,
    VersionConflictError,
)
from backend.macro.daily_repository import local
from backend.macro.research_diagnostics import sanitize_failure_diagnostics
from backend.macro.model_dispatch_repository import (
    ResearchModelBusy,
    lock_model_dispatch,
)

EXECUTION_WINDOW = timedelta(minutes=35)
QUEUE_WINDOW = timedelta(minutes=5)
REQUEST_MODES = frozenset({"DEBUG", "MANUAL"})
EXECUTION_STAGES = (
    "COLLECTING",
    "WAITING_MODEL_SLOT",
    "MODEL_RUNNING",
    "VERIFYING_OUTPUT",
    "REPORT_VALIDATION",
    "PERSISTING",
)
QUEUE_BLOCK_REASONS = frozenset(
    {"MODEL_GUARD_ACTIVE", "REQUEST_RUNNING", "WAITING_WORKER"}
)
LOCAL_EXIT_EVIDENCE_STATES = frozenset(
    {
        "LOCAL_RUNNING",
        "LOCAL_EXIT_CONFIRMED",
        "LOCAL_EXIT_UNCONFIRMED",
        "LOCAL_EVIDENCE_NOT_RETAINED",
        "LOCAL_PLATFORM_UNSUPPORTED",
    }
)
_QUEUE_CUTOFF_SQL = "(SELECT COALESCE(max(queue_cutoff_at),'-infinity'::timestamptz) FROM research.local_research_recovery_audit)"

_READ_REQUEST = """SELECT q.*,clock_timestamp() AS observed_at,t.status AS report_status,
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
        started is not None and stamp - started >= EXECUTION_WINDOW and not terminal
    )
    queue_expired = (
        started is None and stamp >= row["requested_at"] + QUEUE_WINDOW and not terminal
    )
    status = (
        row["status"]
        if terminal
        else "EXPIRED"
        if queue_expired
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
    observation = row.get("queue_observation") or {}
    observation = observation if isinstance(observation, dict) else {}
    queued = status == "QUEUED"
    queue_end = (
        started if started and started <= stamp else completed if terminal else stamp
    )
    stage_updated = _visible_stamp(row.get("stage_updated_at"), stamp)
    stage = row.get("execution_stage")
    stage = (
        stage
        if started and started <= stamp and stage_updated and stage in EXECUTION_STAGES
        else None
    )
    blocked = observation.get("blockedReasonCode")
    blocked = (
        blocked
        if isinstance(blocked, str) and blocked in QUEUE_BLOCK_REASONS
        else "WAITING_WORKER"
    )
    position = observation.get("queuePosition")
    blocked_since = (
        _visible_stamp(observation.get("blockedSince"), stamp) if queued else None
    )
    evidence_state = observation.get("modelExitEvidenceState")
    evidence_checked = _visible_stamp(
        observation.get("modelExitEvidenceCheckedAt"), stamp
    )
    if not (
        queued
        and blocked == "MODEL_GUARD_ACTIVE"
        and isinstance(evidence_state, str)
        and evidence_state in LOCAL_EXIT_EVIDENCE_STATES
        and evidence_checked
        and blocked_since
        and evidence_checked >= blocked_since
    ):
        evidence_state = evidence_checked = None
    return {
        "id": str(row["id"]),
        "requestMode": row.get("request_mode", "DEBUG"),
        "status": status,
        "requestedAt": row["requested_at"],
        "startedAt": started if started and started <= stamp else None,
        "completedAt": completed if terminal else None,
        "batchId": str(row["batch_id"])
        if terminal and status not in {"CANCELLED", "EXPIRED"} and row.get("batch_id")
        else None,
        "reportRunId": str(row["report_run_id"])
        if terminal
        and status not in {"CANCELLED", "EXPIRED"}
        and row.get("report_run_id")
        else None,
        "errorClass": row.get("error_class")
        if terminal
        else "QueueWaitExpired"
        if queue_expired
        else "ExecutionWindowExpired"
        if expired
        else None,
        "failureStage": diagnostics["failure_stage"],
        "failureReasonCode": diagnostics["failure_reason_code"],
        "failureRuleId": diagnostics["failure_rule_id"],
        "failureItemIndex": diagnostics["failure_item_index"],
        "failureIndustryCodes": diagnostics.get("failure_industry_codes"),
        "queueExpiresAt": row["requested_at"] + QUEUE_WINDOW,
        "queueWaitSeconds": max(
            0, int((queue_end - row["requested_at"]).total_seconds())
        ),
        "blockedReasonCode": blocked if queued else None,
        "blockedSince": blocked_since,
        "queuePosition": position
        if queued and type(position) is int and position > 0
        else None,
        "canCancel": queued and observation.get("canCancel") is True,
        "modelExitEvidenceState": evidence_state,
        "modelExitEvidenceCheckedAt": evidence_checked,
        "executionStage": stage,
        "stageUpdatedAt": stage_updated if stage else None,
    }


def _visible_stamp(value, now):
    try:
        value = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        return local(value) if value.tzinfo is not None and value <= now else None
    except (ValueError, TypeError):
        return None


async def _expire_actor_queue(conn, tenant_id, user_id, now):
    """Persist deadlines even when the worker is stopped, through a scoped RPC."""
    try:
        return await conn.fetchval(
            "SELECT research.expire_queued_research_refresh($1,$2,$3)",
            tenant_id,
            user_id,
            now,
        )
    except InsufficientPrivilegeError:
        raise ForbiddenError("当前成员已无研究请求权限") from None


async def _observed_dto(conn, row, now):
    observed_at = row.get("observed_at")
    if isinstance(observed_at, datetime) and observed_at.utcoffset() is not None:
        now = max(now, observed_at)
    if request_dto(row, now)["status"] != "QUEUED":
        return request_dto(row, now)
    try:
        observed = await conn.fetchval(
            "SELECT research.research_refresh_queue_observation($1,$2,$3,$4)",
            row["tenant_id"],
            row["user_id"],
            row["id"],
            now,
        )
    except InsufficientPrivilegeError:
        raise ForbiddenError("当前成员已无研究请求权限") from None
    if isinstance(observed, str):
        try:
            observed = json.loads(observed)
        except ValueError:
            observed = None
    return request_dto({**dict(row), "queue_observation": observed}, now)


async def active_operator(conn, tenant_id, user_id):
    return await conn.fetchval(
        """SELECT EXISTS(SELECT 1 FROM identity.tenant_members m
        JOIN identity.tenants t ON t.id=m.tenant_id AND t.status='ACTIVE'
        WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.status='ACTIVE' AND m.role IN ('OWNER','ADMIN'))""",
        tenant_id,
        user_id,
    )


async def cancel_refresh(conn, tenant_id, user_id, request_id, now):
    """Cancel only an unstarted request; a lost claim race is an explicit conflict.

    The SECURITY DEFINER RPC repeats current OWNER/scope checks and takes the
    same row lock as worker claim. API roles receive no general UPDATE grant.
    """
    owner = await conn.fetchval(
        """SELECT EXISTS(SELECT 1 FROM identity.tenant_members m
        JOIN identity.tenants t ON t.id=m.tenant_id AND t.status='ACTIVE'
        WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.status='ACTIVE' AND m.role='OWNER')""",
        tenant_id,
        user_id,
    )
    if not owner:
        raise ForbiddenError("仅当前有效所有者可取消尚未执行的研究")
    stamp = local(now)
    try:
        row = await conn.fetchrow(
            "SELECT q.*,clock_timestamp() AS observed_at FROM research.cancel_queued_research_refresh($1,$2,$3,$4) q",
            tenant_id,
            user_id,
            request_id,
            stamp,
        )
    except InsufficientPrivilegeError:
        raise ForbiddenError("当前成员已无研究取消权限") from None
    if row is None:
        raise NotFoundError("重新研究任务不存在")
    if row["status"] != "CANCELLED":
        raise VersionConflictError("研究已开始或已结束，仅可取消尚未执行的排队请求")
    observed_at = row.get("observed_at")
    if isinstance(observed_at, datetime) and observed_at.utcoffset() is not None:
        stamp = max(stamp, observed_at)
    return request_dto(row, stamp)


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
        await _expire_actor_queue(conn, tenant_id, user_id, stamp)
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
            return await _observed_dto(conn, existing, stamp)
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
            return await _observed_dto(conn, active, stamp)
        row = await conn.fetchrow(
            """INSERT INTO research.daily_research_refresh_requests(tenant_id,user_id,command_key,requested_at,request_mode)
            VALUES($1,$2,$3,$4,$5) RETURNING *,clock_timestamp() AS observed_at""",
            tenant_id,
            user_id,
            command_key,
            stamp,
            request_mode,
        )
        await _bind_command(conn, tenant_id, user_id, command_key, row["id"], stamp)
    return await _observed_dto(conn, row, stamp)


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
    await _expire_actor_queue(conn, tenant_id, user_id, stamp)
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
    return await _observed_dto(conn, row, stamp)


async def latest_refresh(conn, tenant_id, user_id, now, *, allowed_modes=None):
    stamp = local(now)
    modes = _modes(allowed_modes) if allowed_modes is not None else None
    if modes == []:
        return None
    await _expire_actor_queue(conn, tenant_id, user_id, stamp)
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
    return await _observed_dto(conn, row, stamp) if row else None


async def claim_refresh(conn, now, *, allowed_modes=None):
    modes = _modes(["DEBUG"] if allowed_modes is None else allowed_modes)
    if not modes:
        return None
    stamp = local(now)
    async with conn.transaction():
        # This must precede the model lock: an occupied/orphan guard is never a
        # reason to leave a request QUEUED forever, nor authority to free it.
        await conn.execute(
            f"""UPDATE research.daily_research_refresh_requests
            SET status='EXPIRED',completed_at=GREATEST($1,clock_timestamp()),
              error_class=CASE WHEN requested_at<=$3 OR requested_at+interval '5 minutes'<=clock_timestamp()
                THEN 'QueueWaitExpired' ELSE 'QueueInvalidatedByMaintenance' END
            WHERE status='QUEUED' AND started_at IS NULL
              AND (requested_at<=$3 OR requested_at+interval '5 minutes'<=clock_timestamp()
                OR requested_at<={_QUEUE_CUTOFF_SQL})
              AND request_mode=ANY($2::text[])""",
            stamp,
            modes,
            stamp - QUEUE_WINDOW,
        )
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
            f"""WITH candidate AS (
              SELECT id FROM research.daily_research_refresh_requests WHERE status='QUEUED' AND started_at IS NULL
              AND requested_at<=$1 AND requested_at>$3
              AND batch_id IS NULL AND report_run_id IS NULL
              AND requested_at>{_QUEUE_CUTOFF_SQL}
              AND request_mode=ANY($2::text[])
              ORDER BY requested_at,id LIMIT 1 FOR UPDATE SKIP LOCKED)
            UPDATE research.daily_research_refresh_requests q SET status='RUNNING',started_at=$1,
              execution_stage='COLLECTING',stage_updated_at=$1
            FROM candidate c WHERE q.id=c.id AND q.status='QUEUED' AND q.started_at IS NULL
              AND q.requested_at>$3 AND q.requested_at+interval '5 minutes'>GREATEST($1,clock_timestamp())
              AND q.requested_at>{_QUEUE_CUTOFF_SQL}
              AND q.batch_id IS NULL AND q.report_run_id IS NULL RETURNING q.*""",
            stamp,
            modes,
            stamp - QUEUE_WINDOW,
        )


async def set_refresh_stage(conn, request_id, now, stage):
    """Advance visible RUNNING phases without rewriting terminal requests."""
    if not isinstance(stage, str) or stage not in EXECUTION_STAGES:
        raise ValueError("invalid research execution stage")
    return (
        await conn.fetchval(
            """UPDATE research.daily_research_refresh_requests SET execution_stage=$3,stage_updated_at=$2
        WHERE id=$1 AND status='RUNNING' AND started_at<=$2
          AND (stage_updated_at IS NULL OR stage_updated_at<=$2)
          AND COALESCE(array_position($4::text[],execution_stage),0)<=array_position($4::text[],$3)
        RETURNING id""",
            request_id,
            local(now),
            stage,
            list(EXECUTION_STAGES),
        )
        is not None
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
