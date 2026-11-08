"""Explicit local Windows maintenance, never an automatic guard expiry.

Run ``python -m backend.jobs.research_local_recovery --run-id UUID`` to inspect.
Legacy reboot recovery requires an active OWNER's exact-run attestation that
the original invocation belonged to this machine and its clock was not changed.
Stop normal services and investigate every unrelated RUNNING item first. This
command does not kill processes, rerun a model, or modify immutable run/results.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
import os
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from backend.common.database import create_pool, system_connection
from backend.config import get_settings

BOOT_MARGIN = timedelta(seconds=60)
CLOCK_MARGIN = timedelta(seconds=30)
QUEUE_WINDOW = timedelta(minutes=5)

_GUARD_SELECT = """SELECT g.run_id,g.owner_token,g.acquired_at,g.released_at,g.exit_evidence,
 r.dispatched_at,r.run_mode,r.debug_request_id,r.source_batch_id,
 q.request_mode,q.tenant_id,q.status AS request_status,q.started_at,q.batch_id,q.report_run_id,
 b.run_mode AS source_mode,b.debug_request_id AS source_request_id,
 t.status AS result_status,t.recorded_at
 FROM research.daily_research_model_guards g
 JOIN research.daily_research_runs r ON r.run_id=g.run_id
 LEFT JOIN research.daily_research_refresh_requests q ON q.id=r.debug_request_id
 LEFT JOIN market.alpha_public_batches b ON b.id=r.source_batch_id
 LEFT JOIN research.daily_research_results t ON t.run_id=r.run_id
 WHERE g.run_id=$1"""


class LocalRecoveryDenied(RuntimeError):
    """A closed reason code safe for the maintenance terminal and API logs."""


def _require(condition, code):
    if not condition:
        raise LocalRecoveryDenied(code)


def _platform_is_windows():
    return os.name == "nt"


def _aware(value):
    return isinstance(value, datetime) and value.utcoffset() is not None


def require_local_database(settings):
    """No DNS aliases, DSN endpoint overrides, remote runners or pooler hosts."""
    _require(_platform_is_windows(), "WINDOWS_REQUIRED")
    _require(not getattr(settings, "codex_runner_url", ""), "REMOTE_RUNNER_CONFIGURED")
    try:
        parts = urlsplit(settings.worker_database_url)
        address = ipaddress.ip_address(parts.hostname or "")
        valid = (
            parts.scheme in {"postgres", "postgresql"}
            and address.is_loopback
            and bool(parts.path.strip("/"))
        )
        # libpq/asyncpg query parameters must not override the parsed authority.
        valid = (
            valid
            and not parts.fragment
            and not any(
                key.lower()
                in {
                    "host",
                    "hostaddr",
                    "port",
                    "service",
                    "options",
                    "target_session_attrs",
                }
                for key, _ in parse_qsl(parts.query, keep_blank_values=True)
            )
        )
        _ = parts.port
    except (ValueError, TypeError, AttributeError):
        valid = False
    _require(valid, "LOOPBACK_DATABASE_REQUIRED")


def windows_boot_evidence():
    """Read the kernel boot clock, not a caller-supplied time or process PID.

    Kernel wall times also rely on the maintenance operator's explicit no-clock-
    adjustment attestation. A reboot, not suspend/hibernate or worker restart,
    must be newer than the old DB guard by the conservative margin.
    """
    _require(_platform_is_windows(), "WINDOWS_REQUIRED")
    import ctypes
    from ctypes import wintypes

    class TimeOfDay(ctypes.Structure):
        _fields_ = [
            ("boot", ctypes.c_longlong),
            ("current", ctypes.c_longlong),
            ("bias", ctypes.c_longlong),
            ("zone", wintypes.ULONG),
            ("reserved", wintypes.ULONG),
            ("boot_bias", ctypes.c_ulonglong),
            ("sleep_bias", ctypes.c_ulonglong),
        ]

    nt = ctypes.WinDLL("ntdll")
    query = nt.NtQuerySystemInformation
    query.argtypes = [
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    query.restype = wintypes.LONG
    value, length = TimeOfDay(), wintypes.ULONG()
    _require(
        query(3, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(length)) == 0,
        "KERNEL_BOOT_UNAVAILABLE",
    )
    _require(
        length.value >= 32 and 0 < value.boot < value.current, "KERNEL_BOOT_UNAVAILABLE"
    )
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetTickCount64.argtypes = []
    kernel.GetTickCount64.restype = ctypes.c_ulonglong
    uptime = kernel.GetTickCount64()
    _require(
        abs((value.current - value.boot) // 10000 - uptime) <= 300000,
        "KERNEL_BOOT_CLOCK_CONFLICT",
    )
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return {
        "bootAt": epoch + timedelta(microseconds=value.boot // 10),
        "checkedAt": epoch + timedelta(microseconds=value.current // 10),
        "source": "NT_SYSTEM_TIME_OF_DAY",
        "uptimeMs": uptime,
    }


def guard_snapshot(row):
    _require(row is not None and _aware(row["acquired_at"]), "GUARD_NOT_FOUND")
    material = ":".join(
        (
            str(row["run_id"]),
            str(row["owner_token"]),
            row["acquired_at"].astimezone(timezone.utc).isoformat(),
        )
    )
    return hashlib.sha256(
        ("research-guard-snapshot-v1:" + material).encode()
    ).hexdigest()


async def inspect_local_guard(conn, run_id):
    """Read only; never reveal the guard owner token in a terminal/DTO."""
    row = await conn.fetchrow(_GUARD_SELECT, UUID(str(run_id)))
    _require(row is not None, "GUARD_NOT_FOUND")
    evidence = row["exit_evidence"]
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError:
            evidence = None
    code = evidence.get("evidenceState") if isinstance(evidence, dict) else None
    if code not in {
        "LOCAL_RUNNING",
        "LOCAL_EXIT_CONFIRMED",
        "LOCAL_EXIT_UNCONFIRMED",
        "LOCAL_EVIDENCE_NOT_RETAINED",
        "LOCAL_PLATFORM_UNSUPPORTED",
    }:
        code = "LOCAL_EVIDENCE_NOT_RETAINED"
    return {
        "runId": str(row["run_id"]),
        "acquiredAt": row["acquired_at"].isoformat(),
        "released": row["released_at"] is not None,
        "snapshot": guard_snapshot(row),
        "evidenceState": code,
        "canRelease": False,
        "automaticRecovery": False,
        "maintenanceCommand": "python -m backend.jobs.research_local_recovery --help",
    }


def _validate_origin(row, tenant, boot, now):
    _require(row["released_at"] is None, "GUARD_ALREADY_RELEASED")
    _require(
        _aware(row["dispatched_at"])
        and row["dispatched_at"] <= row["acquired_at"] < boot,
        "GUARD_CLOCK_CONFLICT",
    )
    request = row["debug_request_id"]
    if request is not None:
        _require(
            row["run_mode"] in {"MANUAL", "DEBUG"}
            and row["request_mode"] == row["run_mode"]
            and row["tenant_id"] == tenant
            and row["source_batch_id"] is not None
            and row["source_mode"] == row["run_mode"]
            and row["source_request_id"] == request,
            "ORIGINAL_REQUEST_SCOPE_CONFLICT",
        )
        _require(
            row["batch_id"] in {None, row["source_batch_id"]}
            and row["report_run_id"] in {None, row["run_id"]},
            "ORIGINAL_REQUEST_LINK_CONFLICT",
        )
        if row["request_status"] == "RUNNING":
            _require(
                _aware(row["started_at"])
                and row["started_at"] <= row["dispatched_at"]
                and row["started_at"] < boot,
                "ORIGINAL_REQUEST_CLOCK_CONFLICT",
            )
    else:
        _require(row["run_mode"] == "SCHEDULED", "ORIGINAL_REQUEST_SCOPE_CONFLICT")
    _require(
        row["recorded_at"] is None
        or (
            _aware(row["recorded_at"])
            and row["dispatched_at"] <= row["recorded_at"] <= now
        ),
        "RESULT_CLOCK_CONFLICT",
    )


async def recover_legacy_local_reboot(
    conn,
    settings,
    *,
    run_id,
    expected_snapshot,
    confirm_run,
    tenant_id,
    user_id,
    attest_local_origin=False,
    attest_no_clock_change=False,
):
    """Manually release one old local guard under session-wide dispatch exclusion.

    No received lifecycle/proof object or caller-provided clock authorizes this
    path. The OWNER's local-origin and clock declarations are recorded as human
    attestations, separately from the kernel boot evidence.
    """
    require_local_database(settings)
    # A savepoint is insufficient: the advisory seal must outlive the actual
    # commit of both the guard CAS and durable enqueue cutoff.
    _require(not conn.is_in_transaction(), "DEDICATED_MAINTENANCE_CONNECTION_REQUIRED")
    run, tenant, user = UUID(str(run_id)), UUID(str(tenant_id)), UUID(str(user_id))
    _require(UUID(str(confirm_run)) == run, "EXACT_RUN_CONFIRMATION_REQUIRED")
    _require(
        attest_local_origin is True and attest_no_clock_change is True,
        "LOCAL_ORIGIN_AND_CLOCK_ATTESTATION_REQUIRED",
    )
    _require(
        isinstance(expected_snapshot, str) and len(expected_snapshot) == 64,
        "EXACT_GUARD_SNAPSHOT_REQUIRED",
    )
    boot = windows_boot_evidence()
    _require(
        boot.get("source") == "NT_SYSTEM_TIME_OF_DAY"
        and _aware(boot.get("bootAt"))
        and _aware(boot.get("checkedAt")),
        "KERNEL_BOOT_UNAVAILABLE",
    )
    locked = await conn.fetchval(
        "SELECT pg_try_advisory_lock(hashtext('research-model-dispatch'))"
    )
    _require(locked is True, "DISPATCH_MAINTENANCE_BUSY")
    try:
        async with conn.transaction():
            now = await conn.fetchval("SELECT clock_timestamp()")
            _require(
                _aware(now)
                and abs(now - boot["checkedAt"]) <= CLOCK_MARGIN
                and boot["bootAt"] < now,
                "KERNEL_DATABASE_CLOCK_CONFLICT",
            )
            row = await conn.fetchrow(_GUARD_SELECT + " FOR UPDATE OF g", run)
            _require(
                row is not None
                and hmac.compare_digest(guard_snapshot(row), expected_snapshot),
                "EXACT_GUARD_SNAPSHOT_CHANGED",
            )
            _require(
                boot["bootAt"] > row["acquired_at"] + BOOT_MARGIN,
                "REBOOT_NOT_AFTER_GUARD",
            )
            _require(
                await conn.fetchval(
                    """SELECT EXISTS(SELECT 1 FROM identity.tenant_members m
              JOIN identity.tenants t ON t.id=m.tenant_id WHERE m.tenant_id=$1 AND m.user_id=$2
              AND m.status='ACTIVE' AND m.role='OWNER' AND t.status='ACTIVE')""",
                    tenant,
                    user,
                ),
                "ACTIVE_LOCAL_OWNER_REQUIRED",
            )
            # Enqueue producers use a different actor lock. Block phantom old
            # queue inserts through commit. NOWAIT avoids deadlocking a worker
            # that updated a queue row before waiting for the dispatch lock.
            try:
                await conn.execute("SELECT research.lock_local_research_maintenance()")
            except Exception as error:
                if getattr(error, "sqlstate", None) == "55P03":
                    raise LocalRecoveryDenied("MAINTENANCE_TABLE_BUSY") from None
                raise
            row = await conn.fetchrow(_GUARD_SELECT + " FOR UPDATE OF g", run)
            _require(
                row is not None
                and hmac.compare_digest(guard_snapshot(row), expected_snapshot),
                "EXACT_GUARD_SNAPSHOT_CHANGED",
            )
            _validate_origin(row, tenant, boot["bootAt"], now)
            running = await conn.fetch(
                "SELECT id FROM research.daily_research_refresh_requests WHERE status='RUNNING' ORDER BY id FOR UPDATE"
            )
            _require(
                all(q["id"] == row["debug_request_id"] for q in running),
                "UNRELATED_REQUEST_RUNNING",
            )
            pipelines = await conn.fetch(
                "SELECT id FROM research.daily_research_pipelines WHERE state='RUNNING' ORDER BY id FOR UPDATE"
            )
            _require(not pipelines, "PIPELINE_RUNNING")
            recovered = (
                row["request_status"] == "RUNNING"
                and row["debug_request_id"] is not None
            )
            if recovered:
                changed = await conn.execute(
                    """UPDATE research.daily_research_refresh_requests SET status=$2,completed_at=$3,
                  error_class=$4,batch_id=$5,report_run_id=$6 WHERE id=$1 AND status='RUNNING'
                  AND started_at=$7 AND request_mode=$8 AND tenant_id=$9
                  AND (batch_id IS NULL OR batch_id=$5) AND (report_run_id IS NULL OR report_run_id=$6)""",
                    row["debug_request_id"],
                    "FAILED" if row["result_status"] == "FAILED" else "UNCERTAIN",
                    now,
                    "LocalRebootRecovered",
                    row["source_batch_id"],
                    run,
                    row["started_at"],
                    row["run_mode"],
                    tenant,
                )
                _require(changed == "UPDATE 1", "ORIGINAL_REQUEST_CAS_FAILED")
            expired = await conn.execute(
                """UPDATE research.daily_research_refresh_requests SET status='EXPIRED',completed_at=$1,error_class='LocalRebootQueueExpired'
              WHERE status='QUEUED' AND started_at IS NULL AND batch_id IS NULL AND report_run_id IS NULL
              AND (requested_at<=$2 OR requested_at<=$3)""",
                now,
                row["acquired_at"],
                now - QUEUE_WINDOW,
            )
            _require(
                not await conn.fetchval(
                    """SELECT EXISTS(SELECT 1 FROM research.daily_research_refresh_requests
              WHERE status='QUEUED' AND (requested_at<=$1 OR requested_at<=$2))""",
                    row["acquired_at"],
                    now - QUEUE_WINDOW,
                ),
                "OLD_QUEUE_NOT_TERMINAL",
            )
            proof = {
                "kind": "LOCAL_WINDOWS_REBOOT",
                "bootAt": boot["bootAt"].isoformat(),
                "kernelCheckedAt": boot["checkedAt"].isoformat(),
                "source": boot["source"],
                "guardSnapshot": expected_snapshot,
                "operatorAttestedLocalOrigin": True,
                "operatorAttestedNoClockChange": True,
                "queueCutoffAt": max(
                    row["acquired_at"], now - QUEUE_WINDOW
                ).isoformat(),
            }
            released = await conn.fetchval(
                """UPDATE research.daily_research_model_guards SET released_at=$4,release_proof=$5::jsonb
              WHERE run_id=$1 AND owner_token=$2 AND acquired_at=$3 AND released_at IS NULL RETURNING run_id""",
                run,
                row["owner_token"],
                row["acquired_at"],
                now,
                json.dumps(proof),
            )
            _require(released == run, "EXACT_GUARD_CAS_FAILED")
            count = int(expired.rsplit(" ", 1)[-1])
            await conn.execute(
                """INSERT INTO research.local_research_recovery_audit
              (run_id,guard_snapshot,acquired_at,tenant_id,user_id,released_at,proof,expired_queued,recovered_request,queue_cutoff_at)
              VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10)""",
                run,
                expected_snapshot,
                row["acquired_at"],
                tenant,
                user,
                now,
                json.dumps(proof),
                count,
                recovered,
                max(row["acquired_at"], now - QUEUE_WINDOW),
            )
        return {
            "status": "RELEASED",
            "evidenceKind": "LOCAL_WINDOWS_REBOOT",
            "expiredQueued": count,
            "recoveredRequest": recovered,
        }
    finally:
        await conn.fetchval(
            "SELECT pg_advisory_unlock(hashtext('research-model-dispatch'))"
        )


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run-id", required=True, type=UUID)
    result.add_argument(
        "--action", choices=("inspect", "legacy-local-reboot"), default="inspect"
    )
    result.add_argument("--expect-snapshot")
    result.add_argument("--confirm-run", type=UUID)
    result.add_argument("--tenant-id", type=UUID)
    result.add_argument("--owner-user-id", type=UUID)
    result.add_argument(
        "--attest-local-origin",
        action="store_true",
        help="OWNER maintenance declaration: the old invocation ran on THIS Windows machine; never infer this from current settings",
    )
    result.add_argument(
        "--attest-no-clock-change",
        action="store_true",
        help="OWNER declaration: no wall-clock correction since guard acquisition; boot wall time is otherwise insufficient",
    )
    return result


async def main(argv=None):
    args = parser().parse_args(argv)
    settings = get_settings()
    require_local_database(settings)
    pool = await create_pool(
        settings.worker_database_url,
        min_size=1,
        max_size=1,
        runtime_role="wealthsail_worker",
    )
    try:
        async with system_connection(pool) as conn:
            if args.action == "inspect":
                async with conn.transaction(readonly=True):
                    value = await inspect_local_guard(conn, args.run_id)
            else:
                _require(
                    all(
                        (
                            args.confirm_run,
                            args.expect_snapshot,
                            args.tenant_id,
                            args.owner_user_id,
                        )
                    ),
                    "EXACT_RUN_OWNER_AND_SNAPSHOT_REQUIRED",
                )
                value = await recover_legacy_local_reboot(
                    conn,
                    settings,
                    run_id=args.run_id,
                    expected_snapshot=args.expect_snapshot,
                    confirm_run=args.confirm_run,
                    tenant_id=args.tenant_id,
                    user_id=args.owner_user_id,
                    attest_local_origin=args.attest_local_origin,
                    attest_no_clock_change=args.attest_no_clock_change,
                )
        print(json.dumps(value, ensure_ascii=False))
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except LocalRecoveryDenied as error:
        print(json.dumps({"status": "REFUSED", "reasonCode": str(error)}))
        raise SystemExit(2) from None
    except Exception:
        print(json.dumps({"status": "FAILED", "reasonCode": "LOCAL_RECOVERY_FAILED"}))
        raise SystemExit(1) from None
