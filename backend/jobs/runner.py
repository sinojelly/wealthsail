"""Job lease runner: atomic lease acquisition and the run state machine.

Lease semantics (per docs/10 §6 and migration 004 workflow.job_runs):
  * ``UNIQUE (tenant_id, job_key)`` makes acquisition atomic — the INSERT with
    ON CONFLICT DO NOTHING either claims the lease or observes a prior claim
    (idempotent replay across crashed workers);
  * stale PENDING/RUNNING runs older than the lease timeout are failed with
    ``LeaseTimeoutRecovery`` so the next tick can re-run them;
  * the runner executes report generation through the reports service inside
    per-tenant RLS transactions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import asyncpg

from backend.common.database import Pool, system_connection
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.reports.service import generate_report

logger = logging.getLogger("wealthsail.runner")


def _job_key(job_name: str, now: datetime) -> str:
    return f"{job_name}:{now.strftime('%Y-%m-%d:%H%M%S')}"


async def claim_stale_runs(pool: Pool, settings: Settings) -> int:
    """Fail runs stuck beyond the lease timeout so they can be retried."""
    cutoff = datetime.now() - timedelta(seconds=settings.job_lease_timeout_seconds)
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.job_runs
               SET status = 'FAILED',
                   error_class = 'LeaseTimeoutRecovery',
                   finished_at = now()
             WHERE status IN ('PENDING', 'RUNNING')
               AND started_at < $1
            """,
            cutoff,
        )
    recovered = int(result.split()[-1]) if result else 0
    if recovered:
        logger.warning("recovered %d stale job runs", recovered)
    return recovered


async def run_session(
    pool: Pool,
    settings: Settings,
    session: str,
    now: datetime,
) -> dict:
    """Execute a session for every enabled tenant job.

    Returns a per-tenant summary; each tenant is isolated by its own lease and
    its own RLS transaction during report generation.
    """
    async with system_connection(pool) as conn:
        jobs = await conn.fetch(
            """
            SELECT j.id, j.tenant_id, j.job_name
            FROM workflow.jobs j
            WHERE j.report_session = $1 AND j.is_enabled = TRUE
            """,
            session,
        )

    outcomes: dict[str, str] = {}
    for job in jobs:
        tenant_id = str(job["tenant_id"])
        key = _job_key(job["job_name"], now)
        try:
            outcomes[tenant_id] = await _execute_with_lease(
                pool, settings, job, key, session
            )
        except Exception as exc:  # noqa: BLE001 — per-tenant isolation
            logger.exception("job failed for tenant %s: %s", tenant_id, session)
            outcomes[tenant_id] = f"FAILED:{exc.__class__.__name__}"
    return outcomes


async def _execute_with_lease(
    pool: Pool,
    settings: Settings,
    job: asyncpg.Record,
    key: str,
    session: str,
) -> str:
    async with system_connection(pool) as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO workflow.job_runs (tenant_id, job_id, job_key, status)
            VALUES ($1, $2, $3, 'PENDING')
            ON CONFLICT (tenant_id, job_key) DO NOTHING
            RETURNING id
            """,
            job["tenant_id"],
            job["id"],
            key,
        )
    if run_id is None:
        return "SKIPPED:already-ran"

    async with system_connection(pool) as conn:
        await conn.execute(
            "UPDATE workflow.job_runs SET status = 'RUNNING' WHERE id = $1", run_id
        )

    try:
        # Acting user for RLS claims: the tenant's OWNER (the worker has no
        # human session). RLS still constrains every query to this tenant.
        acting_user = await _tenant_owner_user(pool, str(job["tenant_id"]))
        ctx = TenantContext(
            user_id=acting_user, tenant_id=str(job["tenant_id"]), tenant_role="OWNER"
        )
        report = await generate_report(pool, ctx, settings, session)
        async with system_connection(pool) as conn:
            await conn.execute(
                """
                UPDATE workflow.job_runs
                   SET status = 'COMPLETED', finished_at = now()
                 WHERE id = $1
                """,
                run_id,
            )
        return f"COMPLETED:report={report['id']}"
    except Exception as exc:  # noqa: BLE001 — record failure and re-raise
        async with system_connection(pool) as conn:
            await conn.execute(
                """
                UPDATE workflow.job_runs
                   SET status = 'FAILED', finished_at = now(), error_class = $2
                 WHERE id = $1
                """,
                run_id,
                exc.__class__.__name__,
            )
        raise


async def _tenant_owner_user(pool: Pool, tenant_id: str) -> str:
    async with system_connection(pool) as conn:
        user_id = await conn.fetchval(
            """
            SELECT user_id
            FROM identity.tenant_members
            WHERE tenant_id = $1 AND role = 'OWNER' AND status = 'ACTIVE'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            tenant_id,
        )
    if user_id is None:
        raise RuntimeError(f"tenant {tenant_id} has no active OWNER member")
    return str(user_id)
