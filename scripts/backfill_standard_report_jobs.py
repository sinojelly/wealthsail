"""Safely backfill one tenant's four canonical report jobs.

This is a migration-role operation, not an application endpoint. The default
mode is a read-only preflight. ``--apply`` requires exact database-target,
tenant-id and tenant-name confirmations, writes one audit record, and only
repairs an empty or exact canonical subset. Disabled, modified and duplicate
job configurations are never overwritten.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

if __package__:
    from scripts.migrate import safe_target_name
    from scripts.standard_report_jobs import (
        STANDARD_REPORT_JOBS,
        is_complete_canonical_schedule,
        missing_canonical_jobs,
        standard_job_arrays,
    )
else:
    from migrate import safe_target_name
    from standard_report_jobs import (
        STANDARD_REPORT_JOBS,
        is_complete_canonical_schedule,
        missing_canonical_jobs,
        standard_job_arrays,
    )


@dataclass(frozen=True)
class BackfillRequest:
    tenant_id: UUID
    tenant_name: str

    def __post_init__(self) -> None:
        normalized_name = self.tenant_name.strip()
        if not normalized_name:
            raise ValueError("tenant name is required")
        object.__setattr__(self, "tenant_name", normalized_name)


def _result(
    request: BackfillRequest,
    status: str,
    *,
    existing_sessions: tuple[str, ...] = (),
    inserted_sessions: tuple[str, ...] = (),
) -> dict[str, Any]:
    all_sessions = tuple(job.session for job in STANDARD_REPORT_JOBS)
    return {
        "status": status,
        "tenantId": str(request.tenant_id),
        "tenantName": request.tenant_name,
        "jobCount": len(STANDARD_REPORT_JOBS),
        "sessions": list(all_sessions),
        "existingSessions": list(existing_sessions),
        "missingSessions": [
            session for session in all_sessions if session not in existing_sessions
        ],
        "insertedSessions": list(inserted_sessions),
    }


async def _load_jobs(conn: asyncpg.Connection, tenant_id: UUID) -> list[Any]:
    return list(
        await conn.fetch(
            """
            SELECT id, job_name, cron_expr, report_session, is_enabled
              FROM workflow.jobs
             WHERE tenant_id = $1
               AND report_session IN (
                   'PRE_MARKET', 'INTRADAY', 'FUND_1430', 'POST_MARKET'
               )
             ORDER BY report_session, created_at, id
             FOR UPDATE
            """,
            tenant_id,
        )
    )


async def insert_standard_report_jobs(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    jobs=STANDARD_REPORT_JOBS,
) -> None:
    """Insert selected canonical jobs; callers own transaction and checks."""

    names, crons, sessions = standard_job_arrays(tuple(jobs))
    await conn.execute(
        """
        INSERT INTO workflow.jobs(
            tenant_id, job_name, cron_expr, report_session, is_enabled
        )
        SELECT $1, definition.job_name, definition.cron_expr,
               definition.report_session, TRUE
          FROM unnest($2::text[], $3::text[], $4::text[])
               AS definition(job_name, cron_expr, report_session)
        """,
        tenant_id,
        names,
        crons,
        sessions,
    )


async def backfill_standard_report_jobs(
    conn: asyncpg.Connection,
    request: BackfillRequest,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Preflight or atomically complete an exact canonical schedule subset."""

    async with conn.transaction():
        tenant = await conn.fetchrow(
            """
            SELECT id, name, status
              FROM identity.tenants
             WHERE id = $1 AND status = 'ACTIVE'
             FOR UPDATE
            """,
            request.tenant_id,
        )
        if tenant is None or tenant["name"] != request.tenant_name:
            raise RuntimeError("tenant identity did not match an exact ACTIVE tenant")

        jobs = await _load_jobs(conn, request.tenant_id)
        missing_jobs = missing_canonical_jobs(jobs)
        if missing_jobs is None:
            raise RuntimeError(
                "existing standard report job state requires manual review"
            )
        existing_sessions = tuple(
            job.session for job in STANDARD_REPORT_JOBS if job not in missing_jobs
        )
        if not missing_jobs:
            return _result(
                request,
                "ALREADY_APPLIED",
                existing_sessions=existing_sessions,
            )

        if not apply:
            return _result(
                request,
                "READY",
                existing_sessions=existing_sessions,
            )

        await insert_standard_report_jobs(conn, request.tenant_id, missing_jobs)
        inserted_sessions = tuple(job.session for job in missing_jobs)
        result = _result(
            request,
            "APPLIED",
            existing_sessions=existing_sessions,
            inserted_sessions=inserted_sessions,
        )
        await conn.execute(
            """
            INSERT INTO audit.audit_log(
                tenant_id, actor_type, actor_id, action, resource_type,
                resource_id, details
            ) VALUES (
                $1, 'SYSTEM_WORKER', 'migration-admin',
                'REPORT_JOBS_BACKFILLED', 'identity.tenants', $2::text, $3::jsonb
            )
            """,
            request.tenant_id,
            str(request.tenant_id),
            json.dumps(
                {
                    "operation": "MANUAL_STANDARD_JOB_BACKFILL",
                    "tenantName": request.tenant_name,
                    "sessions": result["sessions"],
                    "existingSessions": result["existingSessions"],
                    "insertedSessions": result["insertedSessions"],
                    "jobs": [
                        {
                            "jobName": job.name,
                            "cronExpr": job.cron,
                            "reportSession": job.session,
                        }
                        for job in STANDARD_REPORT_JOBS
                    ],
                },
                ensure_ascii=False,
            ),
        )

        readback = await _load_jobs(conn, request.tenant_id)
        if not is_complete_canonical_schedule(readback):
            raise RuntimeError(
                "standard report job readback failed; transaction aborted"
            )
        return result


def validate_apply_confirmations(
    request: BackfillRequest,
    *,
    target: str,
    confirm_target: str | None,
    confirm_tenant_id: str | None,
    confirm_tenant_name: str | None,
) -> None:
    if confirm_target != target:
        raise SystemExit(
            f"target confirmation mismatch; expected --confirm-target {target}"
        )
    if (confirm_tenant_id or "").strip() != str(request.tenant_id):
        raise SystemExit(
            "tenant id confirmation mismatch; --confirm-tenant-id must match exactly"
        )
    if confirm_tenant_name != request.tenant_name:
        raise SystemExit(
            "tenant name confirmation mismatch; --confirm-tenant-name must match exactly"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-target")
    parser.add_argument("--confirm-tenant-id")
    parser.add_argument("--confirm-tenant-name")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    dsn = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("MIGRATION_DATABASE_URL is required; no default is allowed")

    request = BackfillRequest(args.tenant_id, args.tenant_name)
    target = safe_target_name(dsn)
    if args.apply:
        validate_apply_confirmations(
            request,
            target=target,
            confirm_target=args.confirm_target,
            confirm_tenant_id=args.confirm_tenant_id,
            confirm_tenant_name=args.confirm_tenant_name,
        )

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        current_user = await conn.fetchval("SELECT current_user")
        if current_user in {
            "wealthsail_api",
            "wealthsail_api_login",
            "wealthsail_worker",
            "wealthsail_worker_login",
        }:
            raise SystemExit("a migration/admin database role is required")
        migration_ready = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM public.wealthsail_schema_migrations
                 WHERE version = 40
                   AND filename = '040_report_job_session_uniqueness.sql'
            )
            """
        )
        if not migration_ready:
            raise SystemExit("migration 040 must be applied before the backfill")
        return await backfill_standard_report_jobs(conn, request, apply=args.apply)
    finally:
        await conn.close()


def main() -> None:
    result = asyncio.run(_run(_parser().parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
