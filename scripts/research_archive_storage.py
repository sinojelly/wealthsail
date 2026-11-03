"""Read-only archive capacity check and bounded redundancy plan; never reclaim data.

Use an explicit operator DSN and target confirmation. A NAS scheduler can use
exit codes 0/1/2/3 for OK/warning/critical/unknown without any message delivery.
"""

import argparse
import asyncio
import json
import os
import shutil

import asyncpg

from scripts.migrate import safe_target_name

TABLES = (
    "research.research_validation_archive_queue",
    "research.research_validation_artifacts",
    "research.research_validation_records",
    "research.research_validation_replay_attempts",
)
EXIT_CODES = {"OK": 0, "WARNING": 1, "CRITICAL": 2, "UNKNOWN": 3}


def validate_limits(retention_days, candidate_limit, warn_bytes, critical_bytes):
    if not 30 <= retention_days <= 3650 or not 1 <= candidate_limit <= 20:
        raise ValueError("retention must be 30..3650 days and candidate limit 1..20")
    if not 0 < warn_bytes < critical_bytes:
        raise ValueError("capacity thresholds must be positive and increasing")


async def scan_archive_storage(conn, *, retention_days=90, candidate_limit=20):
    validate_limits(retention_days, candidate_limit, 1, 2)
    rows = await conn.fetch(
        "SELECT name,pg_total_relation_size(to_regclass(name)) AS bytes FROM unnest($1::text[]) AS names(name)",
        list(TABLES),
    )
    if len(rows) != len(TABLES) or any(row["bytes"] is None for row in rows):
        raise ValueError("archive schema is incomplete")
    tables = {row["name"]: row["bytes"] for row in rows}
    states = await conn.fetch(
        "SELECT status,count(*) AS count FROM research.research_validation_archive_queue GROUP BY status"
    )
    oldest = await conn.fetchval(
        """SELECT COALESCE(max(GREATEST(0,extract(epoch FROM clock_timestamp()-created_at))),0)
        FROM research.research_validation_archive_queue WHERE status IN ('AUDIT_PENDING','ARCHIVING')"""
    )
    # Bound the sample before touching frozen JSON. Pending, active, failed,
    # recent and incomplete copies are never presented as reclaim candidates.
    candidates = await conn.fetch(
        """WITH aged AS MATERIALIZED (
          SELECT run_id,updated_at,frozen_bundle FROM research.research_validation_archive_queue
          WHERE status='ARCHIVED' AND updated_at<=clock_timestamp()-make_interval(days=>$1)
          ORDER BY updated_at,run_id LIMIT $2)
        SELECT q.run_id,q.updated_at,pg_column_size(q.frozen_bundle) AS bundle_bytes
        FROM aged q JOIN research.research_validation_artifacts a USING(run_id)
          JOIN research.research_validation_records r ON r.run_id=q.run_id AND r.kind='INITIAL'
        WHERE q.frozen_bundle->'outcome'=r.outcome AND NOT EXISTS (
          SELECT 1 FROM (VALUES ('packet',a.packet),('raw_output',a.raw_output),('source_checks',a.source_checks)) AS d(kind,actual)
          WHERE jsonb_typeof(d.actual->'content') IS DISTINCT FROM 'string'
            OR d.actual->>'sha256' IS NULL OR d.actual->>'byteLength' IS NULL
            OR d.actual->>'omissionReason' IS NOT NULL
            OR q.frozen_bundle->'artifacts'->d.kind->>'sha256' IS DISTINCT FROM d.actual->>'sha256'
            OR q.frozen_bundle->'artifacts'->d.kind->>'byteLength' IS DISTINCT FROM d.actual->>'byteLength'
            OR q.frozen_bundle->'artifacts'->d.kind->>'omissionReason' IS NOT NULL)
        ORDER BY q.updated_at,q.run_id""",
        retention_days,
        candidate_limit,
    )
    return {
        "tables": tables,
        "queue": {row["status"]: row["count"] for row in states},
        "oldestPendingSeconds": float(oldest),
        "retentionDays": retention_days,
        "candidateLimit": candidate_limit,
        "redundancyPlan": [
            {
                "runId": str(row["run_id"]),
                "archivedAt": row["updated_at"].isoformat(),
                "storedBundleBytes": row["bundle_bytes"],
            }
            for row in candidates
        ],
        "planOnly": True,
    }


def capacity_report(
    snapshot, *, disk=None, warn_bytes=1024**3, critical_bytes=2 * 1024**3
):
    validate_limits(90, 20, warn_bytes, critical_bytes)
    signals, level = [], 0
    total = sum(snapshot["tables"].values())
    if total >= warn_bytes:
        signals.append("ARCHIVE_DATABASE_CAPACITY")
        level = 2 if total >= critical_bytes else 1
    if snapshot.get("queue", {}).get("UNRECOVERABLE", 0):
        signals.append("ARCHIVE_UNRECOVERABLE")
        level = max(level, 1)
    age = snapshot.get("oldestPendingSeconds", 0)
    if age >= 4 * 3600:
        signals.append("ARCHIVE_BACKLOG_AGE")
        level = max(level, 2 if age >= 24 * 3600 else 1)
    volume = {"state": "NOT_CHECKED"}
    if disk is not None:
        if disk["total"] <= 0 or not 0 <= disk["free"] <= disk["total"]:
            volume = {"state": "UNKNOWN"}
            signals.append("NAS_VOLUME_UNKNOWN")
            level = 3
        else:
            free = disk["free"] / disk["total"] * 100
            volume = {
                "state": "CHECKED",
                "totalBytes": disk["total"],
                "freeBytes": disk["free"],
                "freePct": round(free, 2),
            }
            if free <= 20:
                signals.append("NAS_VOLUME_CAPACITY")
                level = max(level, 2 if free <= 10 else 1)
    return {
        **snapshot,
        "databaseBytes": total,
        "volume": volume,
        "severity": ("OK", "WARNING", "CRITICAL", "UNKNOWN")[level],
        "signals": signals,
    }


async def inspect_database(
    dsn, confirm_target, *, retention_days=90, candidate_limit=20
):
    validate_limits(retention_days, candidate_limit, 1, 2)
    if safe_target_name(dsn) != confirm_target:
        raise ValueError("database target confirmation mismatch")
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5)
    try:
        async with conn.transaction(readonly=True, isolation="repeatable_read"):
            await conn.execute("SET LOCAL statement_timeout = '5s'")
            return await scan_archive_storage(
                conn, retention_days=retention_days, candidate_limit=candidate_limit
            )
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-target",
        required=True,
        help="Exact host[:port]/database; credentials are never printed",
    )
    parser.add_argument("--retention-days", type=int, default=90)
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--warn-db-bytes", type=int, default=1024**3)
    parser.add_argument("--critical-db-bytes", type=int, default=2 * 1024**3)
    parser.add_argument(
        "--volume-path",
        help="Explicit NAS PostgreSQL data/backup volume path visible to this process",
    )
    args = parser.parse_args()
    try:
        validate_limits(
            args.retention_days,
            args.candidate_limit,
            args.warn_db_bytes,
            args.critical_db_bytes,
        )
        dsn = os.environ.get("RESEARCH_STORAGE_DATABASE_URL", "")
        if not dsn:
            raise ValueError("explicit storage database URL is required")
        snapshot = asyncio.run(
            inspect_database(
                dsn,
                args.confirm_target,
                retention_days=args.retention_days,
                candidate_limit=args.candidate_limit,
            )
        )
        disk = shutil.disk_usage(args.volume_path) if args.volume_path else None
        report = capacity_report(
            snapshot,
            disk={"total": disk.total, "free": disk.free} if disk else None,
            warn_bytes=args.warn_db_bytes,
            critical_bytes=args.critical_db_bytes,
        )
    except Exception as error:  # noqa: BLE001 -- operator output must not contain DSNs or SQL evidence
        report = {
            "severity": "UNKNOWN",
            "errorClass": type(error).__name__,
            "planOnly": True,
        }
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return EXIT_CODES[report["severity"]]


if __name__ == "__main__":
    raise SystemExit(main())
