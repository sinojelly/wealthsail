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
from backend.reports import intraday
from backend.reports import pre_market_personalized
from backend.reports.service import generate_report

logger = logging.getLogger("wealthsail.runner")

# Internal dispatch phases for the split 08:30 / 08:35 pre-market report.
PRE_MARKET_PHASES = ("PRE_MARKET_MACRO", "PRE_MARKET_PERSONALIZED")

# Map an internal dispatch phase back to the public session stored on the
# workflow.jobs row, so recovery and runner lookups stay migration-free.
PUBLIC_SESSION_BY_PHASE = {
    "PRE_MARKET_MACRO": "PRE_MARKET",
    "PRE_MARKET_PERSONALIZED": "PRE_MARKET",
}


@dataclass(frozen=True)
class JobLease:
    run_id: str
    lease_token: str


def business_job_key(
    job_id: str, phase: str, now: datetime, *, user_id: str | None = None
) -> str:
    """Stable idempotency key for one logical report occurrence.

    Fixed sessions run once per trade date.  INTRADAY is intentionally a
    sequence of five-minute reports, so its key includes the logical bucket;
    repeated scheduler ticks inside the same bucket remain idempotent.  The
    split personalized pre-market phase appends the audience UUID so each
    member gets an independent terminal key.
    """
    key = f"job:{job_id}:{now.date().isoformat()}:{phase}"
    if phase == "INTRADAY":
        bucket_minute = now.minute - (now.minute % 5)
        key = f"{key}:{now.hour:02d}{bucket_minute:02d}"
    if user_id is not None:
        key = f"{key}:{user_id}"
    return key


def parse_business_job_key(key: str) -> tuple[str, str | None]:
    """Reverse ``business_job_key``: phase plus optional audience UUID.

    Recovery reads the persisted key and must never fall back to the legacy
    tenant-wide ``PRE_MARKET`` path, so an unknown shape fails closed.
    """
    parts = key.split(":")
    if len(parts) < 4 or parts[0] != "job":
        raise ValueError(f"invalid business job key: {key}")
    phase = parts[3]
    audience_user_id = parts[4] if len(parts) >= 5 else None
    return phase, audience_user_id


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


async def run_recoverable_jobs(
    pool: Pool,
    settings: Settings,
    *,
    limit: int = 100,
) -> dict[str, str]:
    """Retry recent report runs that survived a Worker crash or failed safely.

    The scan reuses each persisted ``job_key``.  This closes the gap where
    startup recovery changed an expired RUNNING row to PENDING but no future
    fixed scheduler tick would ever claim it.  Recovery is deliberately
    bounded by age and attempt count so stale financial reports are not
    published hours later without an operator decision.
    """
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT j.id, j.tenant_id, j.job_name, j.report_session, jr.job_key
              FROM workflow.job_runs jr
              JOIN workflow.jobs j
                ON j.id = jr.job_id AND j.tenant_id = jr.tenant_id
             WHERE jr.status IN ('PENDING', 'FAILED')
               AND COALESCE(jr.job_type, 'REPORT_SESSION') = 'REPORT_SESSION'
               AND COALESCE(jr.next_retry_at, now()) <= now()
               AND jr.started_at >=
                   now() - make_interval(mins => $1)
               AND jr.attempt_count < $2
               AND j.is_enabled = TRUE
             ORDER BY COALESCE(jr.next_retry_at, jr.started_at), jr.id
             LIMIT $3
            """,
            settings.job_recovery_window_minutes,
            settings.job_recovery_max_attempts,
            limit,
        )

    outcomes: dict[str, str] = {}
    for job in rows:
        key = str(job["job_key"])
        session = str(job["report_session"])
        try:
            outcomes[key] = await _execute_with_lease(pool, settings, job, key, session)
        except Exception as exc:  # noqa: BLE001 -- isolate recoverable runs
            logger.exception("recovery failed for %s", key)
            outcomes[key] = f"FAILED:{exc.__class__.__name__}"
    return outcomes


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
                   lease_token=NULL, lease_owner=NULL, lease_until=NULL,
                   next_retry_at = CASE
                       WHEN $3 = 'FAILED' THEN now() + interval '30 seconds'
                       ELSE NULL
                   END
             WHERE id=$1 AND lease_token = $2 AND status = 'RUNNING'
            """,
            run_id,
            lease_token,
            status,
            error_class,
        )
    return result.endswith("1")


async def _heartbeat_loop(pool: Pool, lease: JobLease, lease_seconds: int) -> None:
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
    phase: str,
    now: datetime,
) -> dict:
    """Execute one internal dispatch phase for every enabled tenant job.

    The split personalized pre-market phase fans out once per active tenant
    member; each member's failure is isolated so one empty/blocked member does
    not abort the rest.
    """
    public_session = PUBLIC_SESSION_BY_PHASE.get(phase, phase)
    async with system_connection(pool) as conn:
        jobs = await conn.fetch(
            """
            SELECT j.id, j.tenant_id, j.job_name
            FROM workflow.jobs j
            WHERE j.report_session = $1 AND j.is_enabled = TRUE
            """,
            public_session,
        )

    outcomes: dict[str, str] = {}
    for job in jobs:
        tenant_id = str(job["tenant_id"])
        # Both the split personalized pre-market report and the condition-
        # triggered INTRADAY alert are evaluated per active tenant member, so
        # each member's own holdings and 30-minute cooldown stay isolated.
        if phase in ("PRE_MARKET_PERSONALIZED", "INTRADAY"):
            members = await _active_tenant_members(pool, tenant_id)
            for user_id in members:
                label = f"{tenant_id}:{user_id}"
                key = business_job_key(str(job["id"]), phase, now, user_id=user_id)
                try:
                    outcomes[label] = await _execute_with_lease(
                        pool, settings, job, key, public_session
                    )
                except Exception as exc:  # noqa: BLE001 -- isolate members
                    logger.exception("personalized job failed for %s: %s", label, phase)
                    outcomes[label] = f"FAILED:{exc.__class__.__name__}"
            continue
        key = business_job_key(str(job["id"]), phase, now)
        try:
            outcomes[tenant_id] = await _execute_with_lease(
                pool, settings, job, key, public_session
            )
        except Exception as exc:  # noqa: BLE001 -- isolate tenants
            logger.exception("job failed for tenant %s: %s", tenant_id, phase)
            outcomes[tenant_id] = f"FAILED:{exc.__class__.__name__}"
    return outcomes


async def _execute_with_lease(
    pool: Pool,
    settings: Settings,
    job: asyncpg.Record,
    key: str,
    public_session: str,
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
        phase, audience_user_id = parse_business_job_key(key)
        tenant_id = str(job["tenant_id"])
        if phase == "PRE_MARKET_PERSONALIZED":
            # Members with neither active positions nor a watchlist have no
            # personal facts to report; they keep only the shared macro brief.
            member_ctx = TenantContext(
                user_id=audience_user_id,
                tenant_id=tenant_id,
                tenant_role="OWNER",
            )
            report = await pre_market_personalized.generate_pre_market_personalized(
                pool, member_ctx, settings, job_run_id=lease.run_id
            )
            if report is None:
                if not await finish_job_run(
                    pool,
                    lease.run_id,
                    lease.lease_token,
                    status="SKIPPED",
                    error_class="NO_PERSONAL_CONTEXT",
                ):
                    raise RuntimeError("job lease lost before completion")
                return "SKIPPED:NO_PERSONAL_CONTEXT"
        elif phase == "INTRADAY":
            # Condition-triggered: only generates/delivers when a threshold is
            # breached and the 30-minute per-user cooldown has elapsed.
            member_user = audience_user_id or await _tenant_owner_user(pool, tenant_id)
            member_ctx = TenantContext(
                user_id=member_user,
                tenant_id=tenant_id,
                tenant_role="OWNER",
            )
            report = await intraday.maybe_generate_intraday_alert(
                pool, member_ctx, settings, job_run_id=lease.run_id
            )
            if report is None:
                if not await finish_job_run(
                    pool,
                    lease.run_id,
                    lease.lease_token,
                    status="SKIPPED",
                    error_class="INTRADAY_NO_ALERT",
                ):
                    raise RuntimeError("job lease lost before completion")
                return "SKIPPED:INTRADAY_NO_ALERT"
        else:
            acting_user = await _tenant_owner_user(pool, tenant_id)
            ctx = TenantContext(
                user_id=acting_user,
                tenant_id=tenant_id,
                tenant_role="OWNER",
            )
            report = await generate_report(
                pool,
                ctx,
                settings,
                public_session,
                report_variant=phase if phase in PRE_MARKET_PHASES else "STANDARD",
                audience_user_id=None,
                delivery_user_id=None,
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


async def _active_tenant_members(pool: Pool, tenant_id: str) -> list[str]:
    """ACTIVE tenant members who each receive their own 08:35 report."""
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT user_id
            FROM identity.tenant_members
            WHERE tenant_id = $1 AND status = 'ACTIVE'
            ORDER BY created_at ASC
            """,
            tenant_id,
        )
    return [str(row["user_id"]) for row in rows]
