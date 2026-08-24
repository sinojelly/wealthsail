"""Operations repository: sanitized operational queries.

SELECT privileges on workflow.* / audit.* are granted to `authenticated`
only for the sanitized column sets (migration 009); sensitive payloads and
audit details never leave the database through this surface.
"""

from __future__ import annotations

import asyncpg


async def list_jobs(conn: asyncpg.Connection, tenant_id: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, job_name, cron_expr, report_session, is_enabled, created_at
        FROM workflow.jobs
        WHERE tenant_id = $1
        ORDER BY created_at ASC
        """,
        tenant_id,
    )


async def list_job_runs(
    conn: asyncpg.Connection, tenant_id: str, limit: int = 30
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, job_id, job_key, started_at, finished_at, status, error_class
        FROM workflow.job_runs
        WHERE tenant_id = $1
        ORDER BY started_at DESC
        LIMIT $2
        """,
        tenant_id,
        limit,
    )


async def freshness(conn: asyncpg.Connection, tenant_id: str) -> dict:
    nav = await conn.fetchrow(
        """
        SELECT MAX(as_of) AS latest_as_of,
               COUNT(*) FILTER (WHERE data_quality_status = 'FRESH') AS fresh_count,
               COUNT(*) AS total
        FROM ledger.account_nav_snapshots
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    positions = await conn.fetchrow(
        """
        SELECT MAX(as_of) AS latest_as_of,
               COUNT(*) FILTER (WHERE data_quality_status = 'FRESH') AS fresh_count,
               COUNT(*) AS total
        FROM ledger.position_snapshots
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    nav = nav or {}
    positions = positions or {}
    return {
        "nav": {
            "latestAsOf": nav["latest_as_of"],
            "freshCount": nav["fresh_count"],
            "total": nav["total"],
        },
        "positions": {
            "latestAsOf": positions["latest_as_of"],
            "freshCount": positions["fresh_count"],
            "total": positions["total"],
        },
    }


async def outbox_status(conn: asyncpg.Connection, tenant_id: str) -> dict:
    rows = await conn.fetch(
        """
        SELECT status, COUNT(*) AS count
        FROM workflow.outbox
        WHERE tenant_id = $1
        GROUP BY status
        """,
        tenant_id,
    )
    counts = {r["status"]: r["count"] for r in rows}
    recent = await conn.fetch(
        """
        SELECT id, topic, aggregate_type, aggregate_id, available_at,
               status, attempt_count, sent_at, created_at
        FROM workflow.outbox
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        LIMIT 20
        """,
        tenant_id,
    )
    return {
        "counts": {
            "pending": counts.get("PENDING", 0),
            "sending": counts.get("SENDING", 0),
            "sent": counts.get("SENT", 0),
            "dead": counts.get("DEAD", 0),
        },
        "recent": [
            {
                "id": str(r["id"]),
                "topic": r["topic"],
                "aggregateType": r["aggregate_type"],
                "aggregateId": str(r["aggregate_id"]),
                "availableAt": r["available_at"],
                "status": r["status"],
                "attemptCount": r["attempt_count"],
                "sentAt": r["sent_at"],
                "createdAt": r["created_at"],
            }
            for r in recent
        ],
    }


async def list_audit(
    conn: asyncpg.Connection, tenant_id: str, limit: int = 50
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, actor_type, actor_id, action, resource_type, resource_id,
               correlation_id, created_at
        FROM audit.audit_log
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        tenant_id,
        limit,
    )
