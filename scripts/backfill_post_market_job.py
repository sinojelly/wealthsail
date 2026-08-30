"""Safely backfill one tenant's canonical POST_MARKET report job.

This is a migration-role operation, not an application endpoint. The default
mode is a read-only preflight. ``--apply`` requires exact database-target,
tenant-id and tenant-name confirmations, writes one audit record, and refuses
to overwrite any existing or ambiguous POST_MARKET configuration.
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

# The documented ``python scripts/backfill_post_market_job.py`` form puts the
# scripts directory at sys.path[0]. Keep that form and package imports working.
if __package__:
    from scripts.migrate import safe_target_name
else:
    from migrate import safe_target_name


POST_MARKET_JOB_NAME = "post_market_review"
POST_MARKET_CRON = "0 30 15 * * 1-5"
POST_MARKET_SESSION = "POST_MARKET"


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
    job_id: object | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "tenantId": str(request.tenant_id),
        "tenantName": request.tenant_name,
        "reportSession": POST_MARKET_SESSION,
        "jobName": POST_MARKET_JOB_NAME,
        "cronExpr": POST_MARKET_CRON,
    }
    if job_id is not None:
        result["jobId"] = str(job_id)
    return result


def _is_canonical(job: Any) -> bool:
    return (
        job["job_name"] == POST_MARKET_JOB_NAME
        and job["cron_expr"] == POST_MARKET_CRON
        and job["report_session"] == POST_MARKET_SESSION
        and job["is_enabled"] is True
    )


async def _load_jobs(
    conn: asyncpg.Connection,
    tenant_id: UUID,
) -> list[Any]:
    return list(
        await conn.fetch(
            """
            SELECT id, job_name, cron_expr, report_session, is_enabled
              FROM workflow.jobs
             WHERE tenant_id = $1
               AND report_session = 'POST_MARKET'
             ORDER BY created_at, id
            """,
            tenant_id,
        )
    )


async def backfill_post_market_job(
    conn: asyncpg.Connection,
    request: BackfillRequest,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Preflight or atomically add one exact POST_MARKET job.

    Locking the tenant row serializes retries of this operator command. Any
    pre-existing POST_MARKET row is treated as intentional configuration: an
    exact canonical row is idempotent, while every other state requires human
    review rather than an automatic update.
    """

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
        if jobs:
            if len(jobs) == 1 and _is_canonical(jobs[0]):
                return _result(
                    request,
                    "ALREADY_APPLIED",
                    job_id=jobs[0]["id"],
                )
            raise RuntimeError(
                "existing POST_MARKET job state is unsafe; manual review required"
            )

        if not apply:
            return _result(request, "READY")

        job_id = await conn.fetchval(
            """
            INSERT INTO workflow.jobs(
                tenant_id, job_name, cron_expr, report_session, is_enabled
            ) VALUES ($1, $2, $3, 'POST_MARKET', TRUE)
            RETURNING id
            """,
            request.tenant_id,
            POST_MARKET_JOB_NAME,
            POST_MARKET_CRON,
        )
        await conn.execute(
            """
            INSERT INTO audit.audit_log(
                tenant_id, actor_type, actor_id, action, resource_type,
                resource_id, details
            ) VALUES (
                $1, 'SYSTEM_WORKER', 'migration-admin',
                'REPORT_JOB_BACKFILLED', 'workflow.jobs', $2::text, $3::jsonb
            )
            """,
            request.tenant_id,
            str(job_id),
            json.dumps(
                {
                    "operation": "MANUAL_BACKFILL",
                    "tenantName": request.tenant_name,
                    "reportSession": POST_MARKET_SESSION,
                    "jobName": POST_MARKET_JOB_NAME,
                    "cronExpr": POST_MARKET_CRON,
                }
            ),
        )

        # Re-read inside the transaction. A malformed or duplicate result
        # raises and rolls back both the job and its audit record.
        readback = await _load_jobs(conn, request.tenant_id)
        if len(readback) != 1 or not _is_canonical(readback[0]):
            raise RuntimeError("POST_MARKET job readback failed; transaction aborted")
        return _result(request, "APPLIED", job_id=job_id)


def validate_apply_confirmations(
    request: BackfillRequest,
    *,
    target: str,
    confirm_target: str | None,
    confirm_tenant_id: str | None,
    confirm_tenant_name: str | None,
) -> None:
    if confirm_target != target:
        raise SystemExit(f"target confirmation mismatch; expected --confirm-target {target}")
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

    request = BackfillRequest(
        tenant_id=args.tenant_id,
        tenant_name=args.tenant_name,
    )
    target = safe_target_name(dsn)
    if args.apply:
        validate_apply_confirmations(
            request,
            target=target,
            confirm_target=args.confirm_target,
            confirm_tenant_id=args.confirm_tenant_id,
            confirm_tenant_name=args.confirm_tenant_name,
        )

    conn = await asyncpg.connect(dsn)
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
                 WHERE version = 33
                   AND filename = '033_personal_tenant_report_jobs.sql'
            )
            """
        )
        if not migration_ready:
            raise SystemExit("migration 033 must be applied before the backfill")
        return await backfill_post_market_job(conn, request, apply=args.apply)
    finally:
        await conn.close()


def main() -> None:
    result = asyncio.run(_run(_parser().parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
