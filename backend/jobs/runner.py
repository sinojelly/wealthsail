"""Persistent JobRun lease state machine for the four report sessions.

One stable business key represents one tenant/job/trade-date/session.  A single
upsert atomically creates a run or recovers an expired lease; completion and
failure use the lease token as a compare-and-set guard so a stale worker cannot
overwrite the new owner.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from backend.common.database import Pool, system_connection
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.reports.service import generate_report

logger = logging.getLogger("wealthsail.runner")


@dataclass(frozen=True)
class JobLease:
    run_id: str
    lease_token: str


def business_job_key(job_id: str, session: str, now: datetime) -> str:
    """Stable idempotency key for one job's business session and trade date."""
    return f"job:{job_id}:{now.date().isoformat()}:{session}"


def worker_owner() -> str:
    """Operational owner label only; authority is the unguessable lease token."""
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim_stale_runs(pool: Pool, settings: Settings) -> int:
    """Make expired RUNNING leases recoverable without inventing a new job key."""
    del settings  # the authoritative expiry is persisted in lease_until
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.job_runs
               SET status = 'PENDING',
                   error_class = 'LeaseTimeoutRecovery',
                   lease_token = NULL,
                   lease_owner = NULL,
                   lease_until = NULL,
                   next_retry_at = now(),
                   finished_at = NULL
             WHERE status = 'RUNNING'
               AND lease_until < now()
            """
        )
    recovered = int(result.split()[-1]) if result else 0
    if recovered:
        logger.warning("made %d expired job leases recoverable", recovered)
    return recovered


async def claim_job_run(
    pool: Pool,
    job,
    key: str,
    *,
    owner: str,
    lease_seconds: int,
) -> JobLease | None:
    """Atomically create or recover a run and return its compare-and-set token."""
    async with system_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO workflow.job_runs
                (tenant_id, job_id, job_key, status, started_at,
                 lease_token, lease_owner, lease_until, heartbeat_at, attempt_count)
            VALUES
                ($1, $2, $3, 'RUNNING', now(), gen_random_uuid(), $4,
                 now() + make_interval(secs => $5), now(), 1)
            ON CONFLICT (tenant_id, job_key) DO UPDATE
               SET status = 'RUNNING',
                   started_at = now(),
                   finished_at = NULL,
                   error_class = NULL,
                   lease_token = gen_random_uuid(),
                   lease_owner = EXCLUDED.lease_owner,
                   lease_until = EXCLUDED.lease_until,
                   heartbeat_at = now(),
                   attempt_count = workflow.job_runs.attempt_count + 1,
                   next_retry_at = NULL
             WHERE workflow.job_runs.status NOT IN ('COMPLETED', 'ABSTAINED', 'SKIPPED')
               AND (
                    (workflow.job_runs.status = 'RUNNING'
                     AND workflow.job_runs.lease_until < now())
                    OR
                    (workflow.job_runs.status IN ('PENDING', 'FAILED')
                     AND COALESCE(workflow.job_runs.next_retry_at, now()) <= now())
               )
            RETURNING id, lease_token
            """,
            job["tenant_id"],
            job["id"],
            key,
            owner,
            lease_seconds,
        )
    if row is None:
        return None
    return JobLease(str(row["id"]), str(row["lease_token"]))


async def heartbeat_job_run(
    pool: Pool, run_id: str, lease_token: str, lease_seconds: int
) -> bool:
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.job_runs
               SET heartbeat_at=now(),
                   lease_until=now() + make_interval(secs => $3)
             WHERE id=$1 AND lease_token=$2 AND status='RUNNING'
            """,
            run_id,
            lease_token,
            lease_seconds,
        )
    return result.endswith("1")


async def finish_job_run(
    pool: Pool,
    run_id: str,
    lease_token: str,
    *,
    status: str,
    error_class: str | None = None,
) -> bool:
    if status not in {"COMPLETED", "FAILED", "ABSTAINED", "SKIPPED"}:
        raise ValueError(f"invalid terminal job status: {status}")
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.job_runs
               SET status=$3, finished_at=now(), error_class=$4,
                   lease_token=NULL, lease_owner=NULL, lease_until=NULL
             WHERE id=$1 AND lease_token = $2 AND status = 'RUNNING'
            """,
            run_id,
            lease_token,
            status,
            error_class,
        )
    return result.endswith("1")


async def _heartbeat_loop(
    pool: Pool, lease: JobLease, lease_seconds: int
) -> None:
    interval = max(1.0, lease_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        if not await heartbeat_job_run(
            pool, lease.run_id, lease.lease_token, lease_seconds
        ):
            raise RuntimeError("job lease ownership lost")


async def run_session(
    pool: Pool,
    settings: Settings,
    session: str,
    now: datetime,
) -> dict:
    """Execute a due session for every enabled tenant job."""
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
        key = business_job_key(str(job["id"]), session, now)
        try:
            outcomes[tenant_id] = await _execute_with_lease(
                pool, settings, job, key, session
            )
        except Exception as exc:  # noqa: BLE001 -- isolate tenants
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
    lease = await claim_job_run(
        pool,
        job,
        key,
        owner=worker_owner(),
        lease_seconds=settings.job_lease_timeout_seconds,
    )
    if lease is None:
        return "SKIPPED:already-running-or-terminal"

    heartbeat = asyncio.create_task(
        _heartbeat_loop(pool, lease, settings.job_lease_timeout_seconds),
        name=f"job-heartbeat:{lease.run_id}",
    )
    try:
        acting_user = await _tenant_owner_user(pool, str(job["tenant_id"]))
        ctx = TenantContext(
            user_id=acting_user,
            tenant_id=str(job["tenant_id"]),
            tenant_role="OWNER",
        )
        report = await generate_report(
            pool,
            ctx,
            settings,
            session,
            job_run_id=lease.run_id,
            actor_type="SYSTEM_WORKER",
        )
        if not await finish_job_run(
            pool, lease.run_id, lease.lease_token, status="COMPLETED"
        ):
            raise RuntimeError("job lease lost before completion")
        return f"COMPLETED:report={report['id']}"
    except Exception as exc:  # noqa: BLE001 -- persist classified failure
        await finish_job_run(
            pool,
            lease.run_id,
            lease.lease_token,
            status="FAILED",
            error_class=exc.__class__.__name__,
        )
        raise
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


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
