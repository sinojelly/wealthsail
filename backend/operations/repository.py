"""Operations repository: sanitized operational queries.

SELECT privileges on workflow.* / audit.* are granted to `authenticated`
only for the sanitized column sets (migration 009); sensitive payloads and
audit details never leave the database through this surface.
"""

from __future__ import annotations

import asyncpg


def _run_dto(row: asyncpg.Record | dict | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "status": str(row["status"]),
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "errorClass": row["error_class"],
    }


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


async def fund_estimate_status(
    conn: asyncpg.Connection, tenant_id: str
) -> dict:
    """Sanitized FUND_1430 readiness, last run and estimate coverage."""
    calendar = await conn.fetchrow(
        """
        WITH clock AS (
            SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date AS today
        )
        SELECT clock.today,
               EXISTS (
                   SELECT 1 FROM reference.trading_calendars
                    WHERE market = 'SSE'
               ) AS calendar_configured,
               EXISTS (
                   SELECT 1 FROM reference.trading_calendars calendar
                    WHERE calendar.market = 'SSE'
                      AND calendar.calendar_date = clock.today
               ) AS today_configured,
               COALESCE(
                   (
                       SELECT calendar.is_trading_day
                         FROM reference.trading_calendars calendar
                        WHERE calendar.market = 'SSE'
                          AND calendar.calendar_date = clock.today
                   ),
                   EXTRACT(ISODOW FROM clock.today) BETWEEN 1 AND 5
               ) AS is_trading_day,
               COALESCE(
                   (
                       SELECT max(calendar.calendar_date)
                         FROM reference.trading_calendars calendar
                        WHERE calendar.market = 'SSE'
                          AND calendar.is_trading_day = TRUE
                          AND calendar.calendar_date <= clock.today
                   ),
                   (
                       SELECT max(
                                  (observation.as_of AT TIME ZONE 'Asia/Shanghai')::date
                              )
                         FROM market.market_observations observation
                        WHERE observation.field_code = 'NAV'
                          AND observation.value IS NOT NULL
                          AND observation.as_of <= now()
                   )
               ) AS latest_trading_date
          FROM clock
        """
    )
    run = await conn.fetchrow(
        """
        SELECT id, status, started_at, finished_at, error_class
          FROM workflow.job_runs
         WHERE tenant_id = $1
           AND job_key LIKE 'manual:FUND_1430:%'
         ORDER BY started_at DESC, id DESC
         LIMIT 1
        """,
        tenant_id,
    )
    coverage = await conn.fetchrow(
        """
        WITH latest_positions AS (
            SELECT DISTINCT ON (position.account_id, position.instrument_id)
                   position.instrument_id, position.quantity
              FROM ledger.position_snapshots position
              JOIN reference.instruments instrument
                ON instrument.id = position.instrument_id
             WHERE position.tenant_id = $1
               AND instrument.asset_type = 'OFF_EXCHANGE_FUND'
             ORDER BY position.account_id, position.instrument_id,
                      position.as_of DESC
        ), held_funds AS (
            SELECT DISTINCT instrument_id
              FROM latest_positions
             WHERE quantity > 0
        ), latest_estimates AS (
            SELECT held.instrument_id,
                   estimate.value, estimate.quality_status, estimate.as_of
              FROM held_funds held
              LEFT JOIN LATERAL (
                  SELECT observation.value, observation.quality_status,
                         observation.as_of
                   FROM market.market_observations observation
                   WHERE observation.subject_id = held.instrument_id
                     AND observation.field_code = 'ESTIMATED_NAV'
                     AND (observation.as_of AT TIME ZONE 'Asia/Shanghai')::date =
                         (now() AT TIME ZONE 'Asia/Shanghai')::date
                   ORDER BY observation.as_of DESC,
                            observation.observed_at DESC,
                            observation.revision DESC
                   LIMIT 1
              ) estimate ON TRUE
        )
        SELECT count(*) AS held_fund_count,
               count(*) FILTER (
                   WHERE value IS NOT NULL AND quality_status = 'ESTIMATED'
               ) AS estimated_count,
               count(*) FILTER (
                   WHERE value IS NULL OR quality_status IS DISTINCT FROM 'ESTIMATED'
               ) AS unavailable_count,
               max(as_of) AS latest_as_of
          FROM latest_estimates
        """,
        tenant_id,
    )
    calendar = calendar or {}
    coverage = coverage or {}
    calendar_configured = bool(calendar.get("calendar_configured", False))
    today_configured = bool(calendar.get("today_configured", False))
    calendar_status = (
        "READY"
        if calendar_configured and today_configured
        else "DEGRADED"
        if calendar_configured
        else "MISSING"
    )
    is_trading_day = bool(calendar.get("is_trading_day", False))
    return {
        "session": "FUND_1430",
        "canRun": is_trading_day,
        "unavailableReason": None if is_trading_day else "NON_TRADING_DAY",
        "calendar": {
            "status": calendar_status,
            "today": calendar.get("today"),
            "isTradingDay": is_trading_day,
            "latestTradingDate": calendar.get("latest_trading_date"),
        },
        "run": _run_dto(run),
        "coverage": {
            "heldFundCount": int(coverage.get("held_fund_count", 0) or 0),
            "estimatedCount": int(coverage.get("estimated_count", 0) or 0),
            "unavailableCount": int(coverage.get("unavailable_count", 0) or 0),
            "latestAsOf": coverage.get("latest_as_of"),
        },
    }


async def enqueue_manual_fund_estimate(
    conn: asyncpg.Connection, tenant_id: str
) -> dict:
    """Create one durable FUND_1430 request, deduplicated transactionally."""
    await conn.execute(
        """
        SELECT pg_advisory_xact_lock(
            hashtextextended('manual-fund-estimate:' || $1::text, 0)
        )
        """,
        tenant_id,
    )
    existing = await conn.fetchrow(
        """
        SELECT id, status, started_at, finished_at, error_class
          FROM workflow.job_runs
         WHERE tenant_id = $1
           AND job_key LIKE 'manual:FUND_1430:%'
           AND status IN ('PENDING', 'RUNNING', 'FAILED')
           AND (status <> 'FAILED' OR started_at >= now() - interval '30 minutes')
         ORDER BY started_at DESC, id DESC
         LIMIT 1
        """,
        tenant_id,
    )
    if existing is not None:
        return {"created": False, "run": _run_dto(existing), "reason": None}

    job = await conn.fetchrow(
        """
        SELECT id
          FROM workflow.jobs
         WHERE tenant_id = $1
           AND report_session = 'FUND_1430'
           AND is_enabled = TRUE
         ORDER BY created_at ASC, id ASC
         LIMIT 1
        """,
        tenant_id,
    )
    if job is None:
        return {"created": False, "run": None, "reason": "JOB_NOT_CONFIGURED"}

    created = await conn.fetchrow(
        """
        INSERT INTO workflow.job_runs
            (tenant_id, job_id, job_key, status, started_at,
             job_type, payload, available_at)
        VALUES
            ($1, $2, 'manual:FUND_1430:' || gen_random_uuid()::text,
             'PENDING', now(), NULL, '{}'::jsonb, now())
        RETURNING id, status, started_at, finished_at, error_class
        """,
        tenant_id,
        job["id"],
    )
    return {"created": True, "run": _run_dto(created), "reason": None}


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
            "unknown": counts.get("UNKNOWN", 0),
            "dead": counts.get("DEAD", 0),
            "cancelled": counts.get("CANCELLED", 0),
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
