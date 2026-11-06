"""AI usage ledger writes commit separately from the consuming business task."""

import json
from uuid import UUID

from backend.common.database import system_connection, tenant_transaction
from backend.ai_usage.calculation import aggregate_task, quota_delta
from backend.integrations.research_execution import sanitize_execution_progress


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _run_identity(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


async def persist(pool, tenant_id, user_id, event, data):
    boundary = (
        tenant_transaction(pool, user_id, tenant_id)
        if tenant_id
        else system_connection(pool)
    )
    async with boundary as conn:
        async with conn.transaction():
            if event == "start_task":
                await conn.execute(
                    """INSERT INTO research.ai_usage_tasks(id,tenant_id,user_id,task_type,task_name,started_at)
                    VALUES($1,$2,$3,$4,$5,$6)""",
                    UUID(data["id"]),
                    UUID(tenant_id) if tenant_id else None,
                    UUID(user_id) if user_id else None,
                    data["taskType"],
                    data["taskName"],
                    data["startedAt"],
                )
            elif event == "finish_task":
                await conn.execute(
                    "UPDATE research.ai_usage_tasks SET business_status=$2,finished_at=$3,error_class=$4 WHERE id=$1",
                    UUID(data["id"]),
                    data["businessStatus"],
                    data["finishedAt"],
                    data.get("errorClass"),
                )
            elif event == "start_call":
                run_id = (
                    _run_identity(data.get("researchRunId"))
                    if data["callType"] == "research"
                    else None
                )
                await conn.execute(
                    """INSERT INTO research.ai_usage_calls(id,task_id,started_at,model,call_type,details)
                    VALUES($1,$2,$3,$4,$5,$6::jsonb)""",
                    UUID(data["id"]),
                    UUID(data["taskId"]),
                    data["startedAt"],
                    data["model"],
                    data["callType"],
                    dump({"researchRunId": run_id} if run_id else {}),
                )
            elif event == "call_progress":
                progress = sanitize_execution_progress(data.get("progress"))
                run_id = _run_identity(data.get("runId"))
                if progress is None or run_id is None:
                    return
                await conn.execute(
                    """UPDATE research.ai_usage_calls
                    SET details=jsonb_set(details,'{researchProgress}',$3::jsonb)
                    WHERE id=$1 AND task_id=$2 AND status='RUNNING' AND finished_at IS NULL
                      AND call_type='research' AND details->>'researchRunId'=$4
                      AND (NOT details ? 'researchProgress' OR (
                        details->'researchProgress'->>'startedAt'=$5
                        AND details->'researchProgress'->'elapsedMs'<=$3::jsonb->'elapsedMs'))""",
                    UUID(data["id"]),
                    UUID(data["taskId"]),
                    dump(progress),
                    run_id,
                    progress["startedAt"],
                )
            elif event == "finish_call":
                details = {key: data[key] for key in ("before", "after", "tokens")}
                progress = sanitize_execution_progress(data.get("researchProgress"))
                run_id = _run_identity(data.get("researchRunId"))
                if progress is not None and run_id:
                    details["researchProgress"] = progress
                fingerprint = (data.get("before") or {}).get("accountFingerprint")
                if fingerprint:
                    # Acquire before any call row lock. Concurrent completions
                    # must see one another's committed sampling interval.
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        "ai-usage:" + fingerprint,
                    )
                await conn.execute(
                    """UPDATE research.ai_usage_calls SET finished_at=$2,status=$3,model=$4,error_class=$5,
                    details=details || CASE WHEN $8::text IS NULL OR details->>'researchRunId'=$8
                      THEN $6::jsonb ELSE $6::jsonb-'researchProgress' END,
                    account_fingerprint=$7 WHERE id=$1""",
                    UUID(data["id"]),
                    data["finishedAt"],
                    data["status"],
                    data["model"],
                    data.get("errorClass"),
                    dump(details),
                    fingerprint,
                    run_id,
                )
                await conn.execute(
                    "SELECT research.mark_ai_usage_overlap($1)", UUID(data["id"])
                )


def call_dto(row):
    details = decode(row.get("details")) or {}
    progress = sanitize_execution_progress(details.get("researchProgress"))
    return {
        "id": str(row["id"]),
        "model": row["model"],
        "callType": row["call_type"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "errorClass": row.get("error_class"),
        **{
            key: (details.get("tokens") or {}).get(key)
            for key in ("inputTokens", "cachedTokens", "outputTokens", "totalTokens")
        },
        "quota": quota_delta(
            details.get("before"),
            details.get("after"),
            concurrent=row.get("concurrent", False),
        ),
        **({"researchProgress": progress} if progress is not None else {}),
    }


async def list_tasks(conn, tenant_id, *, include_global=False, limit=20, offset=0):
    scope = "(tenant_id=$1 OR ($2 AND tenant_id IS NULL))"
    rows = await conn.fetch(
        f"SELECT * FROM research.ai_usage_tasks WHERE {scope} ORDER BY started_at DESC,id DESC LIMIT $3 OFFSET $4",
        UUID(str(tenant_id)),
        include_global,
        limit,
        offset,
    )
    total = await conn.fetchval(
        f"SELECT count(*) FROM research.ai_usage_tasks WHERE {scope}",
        UUID(str(tenant_id)),
        include_global,
    )
    calls = (
        await conn.fetch(
            "SELECT * FROM research.ai_usage_calls WHERE task_id=ANY($1::uuid[]) ORDER BY started_at,id",
            [row["id"] for row in rows],
        )
        if rows
        else []
    )
    items = []
    for row in rows:
        task = {
            "id": str(row["id"]),
            "taskType": row["task_type"],
            "taskName": row["task_name"],
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "businessStatus": row["business_status"],
        }
        result = aggregate_task(
            task, [call_dto(call) for call in calls if call["task_id"] == row["id"]]
        )
        for call in result["calls"]:
            call["quota"].pop("periods", None)
        items.append(result)
    return {"tasks": items, "total": total, "limit": limit, "offset": offset}


async def reconcile_stale_calls(conn, *, limit: int = 100) -> str:
    """Reconcile orphaned RUNNING calls whose parent tasks have already terminated.

    Applies a 30-second grace period for tasks with finished_at, locks target rows
    with FOR UPDATE SKIP LOCKED, orders deterministically by started_at and id, and
    re-checks target.status = 'RUNNING' in the UPDATE to eliminate concurrency race
    conditions with in-flight finish_call operations.

    Args:
        conn: asyncpg connection with research schema update privileges.
        limit: Maximum number of stale calls to reconcile in a single batch.

    Returns:
        Status string from asyncpg execute (e.g. 'UPDATE 2').
    """
    return await conn.execute(
        """
        WITH stale AS (
            SELECT c.id, t.finished_at
              FROM research.ai_usage_calls c
              JOIN research.ai_usage_tasks t ON t.id = c.task_id
             WHERE c.status = 'RUNNING'
               AND (
                   t.finished_at <= statement_timestamp() - INTERVAL '30 seconds'
                   OR (t.finished_at IS NULL AND t.business_status <> 'RUNNING')
               )
             ORDER BY c.started_at, c.id
             LIMIT $1
             FOR UPDATE OF c SKIP LOCKED
        )
        UPDATE research.ai_usage_calls target
           SET status = 'UNCERTAIN',
               finished_at = COALESCE(stale.finished_at, statement_timestamp()),
               error_class = COALESCE(target.error_class, 'ZombieCallRecovered')
          FROM stale
         WHERE target.id = stale.id
           AND target.status = 'RUNNING'
        """,
        limit,
    )
