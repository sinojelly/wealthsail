"""AI usage ledger writes commit separately from the consuming business task."""

import json
from uuid import UUID

from backend.common.database import system_connection, tenant_transaction
from backend.ai_usage.calculation import aggregate_task, quota_delta


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


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
                await conn.execute(
                    """INSERT INTO research.ai_usage_calls(id,task_id,started_at,model,call_type)
                    VALUES($1,$2,$3,$4,$5)""",
                    UUID(data["id"]),
                    UUID(data["taskId"]),
                    data["startedAt"],
                    data["model"],
                    data["callType"],
                )
            elif event == "finish_call":
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
                    details=$6::jsonb,account_fingerprint=$7 WHERE id=$1""",
                    UUID(data["id"]),
                    data["finishedAt"],
                    data["status"],
                    data["model"],
                    data.get("errorClass"),
                    dump({key: data[key] for key in ("before", "after", "tokens")}),
                    fingerprint,
                )
                await conn.execute(
                    "SELECT research.mark_ai_usage_overlap($1)", UUID(data["id"])
                )


def call_dto(row):
    details = decode(row.get("details")) or {}
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


async def reconcile_stale_calls(conn) -> str:
    """Reconcile orphaned RUNNING calls whose parent tasks have already terminated.

    Args:
        conn: asyncpg connection with research schema update privileges.

    Returns:
        Status string from asyncpg execute (e.g. 'UPDATE 2').
    """
    return await conn.execute(
        """
        UPDATE research.ai_usage_calls c
           SET status = 'UNCERTAIN',
               finished_at = COALESCE(t.finished_at, statement_timestamp()),
               error_class = COALESCE(c.error_class, 'ZombieCallRecovered')
          FROM research.ai_usage_tasks t
         WHERE c.task_id = t.id
           AND c.status = 'RUNNING'
           AND (t.finished_at IS NOT NULL OR t.business_status <> 'RUNNING')
        """
    )

