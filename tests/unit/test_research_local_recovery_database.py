"""Synthetic maintenance in the explicitly owned queue-control test cluster.

No real guard, OS reboot, model process, or deployed database is involved.
"""

from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.jobs import research_local_recovery as recovery
from backend.macro import research_debug_repository as queue

DSN = os.environ.get("RESEARCH_QUEUE_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned queue106 cluster required"),
]


async def connect(role="admin"):
    target = urlsplit(DSN)
    assert (target.hostname, target.port, target.path, target.username) == (
        "127.0.0.1",
        29035,
        "/wealthsail_queue_control_test",
        "queue_test_admin",
    )
    if role == "admin":
        return await asyncpg.connect(DSN, timeout=5)
    conn = await asyncpg.connect(
        f"postgresql://queue_test_{role}_login@127.0.0.1:29035/wealthsail_queue_control_test",
        timeout=5,
    )
    if role == "worker":
        await conn.execute("SET ROLE wealthsail_worker")
    return conn


@asynccontextmanager
async def fixture(monkeypatch, *, terminal=True):
    admin = await connect()
    worker = await connect("worker")
    tenant, actor, request, batch, run, token = (uuid4() for _ in range(6))
    now = await admin.fetchval("SELECT clock_timestamp()")
    acquired = now - timedelta(minutes=4)
    try:
        async with admin.transaction():
            await admin.execute(
                "INSERT INTO identity.tenants(id,name) VALUES($1,'local reboot fixture')",
                tenant,
            )
            await admin.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'local reboot fixture')",
                actor,
            )
            await admin.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
                tenant,
                actor,
            )
            await admin.execute(
                """INSERT INTO research.daily_research_refresh_requests
              (id,tenant_id,user_id,command_key,requested_at,request_mode,status,started_at)
              VALUES($1,$2,$3,$4,$5,'MANUAL','RUNNING',$5)""",
                request,
                tenant,
                actor,
                uuid4(),
                acquired - timedelta(minutes=1),
            )
            await admin.execute(
                """INSERT INTO market.alpha_public_batches
              (id,run_date,claimed_at,completed_at,status,run_mode,debug_request_id)
              VALUES($1,$2,$3,$3,'READY','MANUAL',$4)""",
                batch,
                acquired.date(),
                acquired - timedelta(seconds=2),
                request,
            )
            await admin.execute(
                """INSERT INTO research.daily_research_runs
              (run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet,run_mode,debug_request_id,source_batch_id)
              VALUES($1,$2,$3,$3,'local-fixture','local-fixture','{}','MANUAL',$4,$5)""",
                run,
                acquired.date(),
                acquired - timedelta(seconds=1),
                request,
                batch,
            )
            await admin.execute(
                "INSERT INTO research.daily_research_model_guards(run_id,owner_token,acquired_at) VALUES($1,$2,$3)",
                run,
                token,
                acquired,
            )
            if terminal:
                await admin.execute(
                    "INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class) VALUES($1,'FAILED',$2,'FixtureTimeout')",
                    run,
                    acquired + timedelta(seconds=1),
                )
        row = await worker.fetchrow(recovery._GUARD_SELECT, run)
        monkeypatch.setattr(recovery, "_platform_is_windows", lambda: True)
        monkeypatch.setattr(
            recovery,
            "windows_boot_evidence",
            lambda: {
                "bootAt": acquired + timedelta(seconds=90),
                "checkedAt": now,
                "source": "NT_SYSTEM_TIME_OF_DAY",
                "uptimeMs": 1,
            },
        )
        settings = SimpleNamespace(worker_database_url=DSN, codex_runner_url="")
        arguments = dict(
            run_id=run,
            expected_snapshot=recovery.guard_snapshot(row),
            confirm_run=run,
            tenant_id=tenant,
            user_id=actor,
            attest_local_origin=True,
            attest_no_clock_change=True,
        )
        yield admin, worker, now, row, settings, arguments
    finally:
        await worker.close()
        # Exact synthetic identities only; no global cleanup or real material.
        async with admin.transaction():
            await admin.execute(
                "DELETE FROM research.local_research_recovery_audit WHERE run_id=$1",
                run,
            )
            await admin.execute(
                "DELETE FROM research.daily_research_model_guards WHERE run_id=$1", run
            )
            await admin.execute(
                "UPDATE research.daily_research_refresh_requests SET batch_id=NULL,report_run_id=NULL,status='FAILED',completed_at=GREATEST(clock_timestamp(),requested_at,started_at) WHERE tenant_id=$1",
                tenant,
            )
            await admin.execute(
                "DELETE FROM research.daily_research_results WHERE run_id=$1", run
            )
            await admin.execute(
                "DELETE FROM research.daily_research_runs WHERE run_id=$1", run
            )
            await admin.execute(
                "DELETE FROM market.alpha_public_batches WHERE id=$1", batch
            )
            await admin.execute(
                "DELETE FROM research.daily_research_refresh_commands WHERE tenant_id=$1",
                tenant,
            )
            await admin.execute(
                "DELETE FROM research.daily_research_refresh_requests WHERE tenant_id=$1",
                tenant,
            )
            await admin.execute(
                "DELETE FROM identity.tenant_members WHERE tenant_id=$1", tenant
            )
            await admin.execute("DELETE FROM identity.tenants WHERE id=$1", tenant)
            await admin.execute("DELETE FROM identity.profiles WHERE id=$1", actor)
        await admin.close()


@pytest.mark.parametrize("terminal", [False, True])
async def test_real_worker_exact_cas_audit_queue_cutoff_and_no_immutable_changes(
    monkeypatch, terminal
):
    async with fixture(monkeypatch, terminal=terminal) as (
        admin,
        worker,
        now,
        row,
        settings,
        args,
    ):
        original_run = await admin.fetchrow(
            "SELECT * FROM research.daily_research_runs WHERE run_id=$1", row["run_id"]
        )
        original_result = await admin.fetchrow(
            "SELECT * FROM research.daily_research_results WHERE run_id=$1",
            row["run_id"],
        )
        queued = uuid4()
        await admin.execute(
            "INSERT INTO research.daily_research_refresh_requests(id,tenant_id,user_id,command_key,requested_at,request_mode) VALUES($1,$2,$3,$4,$5,'MANUAL')",
            queued,
            args["tenant_id"],
            args["user_id"],
            uuid4(),
            row["acquired_at"],
        )
        result = await recovery.recover_legacy_local_reboot(worker, settings, **args)
        assert (
            result["status"] == "RELEASED"
            and result["expiredQueued"] >= 1
            and result["recoveredRequest"]
        )
        proof = json.loads(
            await admin.fetchval(
                "SELECT release_proof FROM research.daily_research_model_guards WHERE run_id=$1",
                row["run_id"],
            )
        )
        assert proof["kind"] == "LOCAL_WINDOWS_REBOOT" and "exitConfirmed" not in proof
        assert (
            await admin.fetchrow(
                "SELECT * FROM research.daily_research_runs WHERE run_id=$1",
                row["run_id"],
            )
            == original_run
        )
        assert (
            await admin.fetchrow(
                "SELECT * FROM research.daily_research_results WHERE run_id=$1",
                row["run_id"],
            )
            == original_result
        )
        request = await admin.fetchrow(
            "SELECT * FROM research.daily_research_refresh_requests WHERE id=$1",
            row["debug_request_id"],
        )
        assert request["status"] == ("FAILED" if terminal else "UNCERTAIN")
        assert (
            request["report_run_id"] == row["run_id"]
            and request["batch_id"] == row["source_batch_id"]
        )
        assert (
            await admin.fetchval(
                "SELECT status FROM research.daily_research_refresh_requests WHERE id=$1",
                queued,
            )
            == "EXPIRED"
        )
        audit = await worker.fetchrow(
            "SELECT * FROM research.local_research_recovery_audit WHERE run_id=$1",
            row["run_id"],
        )
        assert (
            audit["guard_snapshot"] == args["expected_snapshot"]
            and audit["queue_cutoff_at"] <= audit["released_at"]
        )
        assert now - audit["queue_cutoff_at"] < timedelta(minutes=5), (
            "cutoff must reject a request that has not naturally expired"
        )
        # A producer that only INSERTs its pre-maintenance timestamp after the
        # session seal was removed still cannot get a model slot.
        delayed = uuid4()
        await admin.execute(
            "INSERT INTO research.daily_research_refresh_requests(id,tenant_id,user_id,command_key,requested_at,request_mode) VALUES($1,$2,$3,$4,$5,'MANUAL')",
            delayed,
            args["tenant_id"],
            args["user_id"],
            uuid4(),
            now - timedelta(minutes=1),
        )
        # This is younger than 5 minutes: only the durable maintenance cutoff
        # can reject the delayed producer, not the ordinary queue TTL.
        await admin.execute(
            "UPDATE research.daily_research_refresh_requests SET requested_at=$2 WHERE id=$1",
            delayed,
            audit["queue_cutoff_at"],
        )
        assert (
            await queue.claim_refresh(
                worker, now + timedelta(seconds=1), allowed_modes=["MANUAL"]
            )
            is None
        )
        assert (
            await admin.fetchval(
                "SELECT status FROM research.daily_research_refresh_requests WHERE id=$1",
                delayed,
            )
            == "EXPIRED"
        )


async def test_other_running_request_refuses_and_keeps_guard_audit_and_original_state(
    monkeypatch,
):
    async with fixture(monkeypatch) as (admin, worker, now, row, settings, args):
        await admin.execute(
            """INSERT INTO research.daily_research_refresh_requests
          (tenant_id,user_id,command_key,requested_at,request_mode,status,started_at)
          VALUES($1,$2,$3,$4,'MANUAL','RUNNING',$4)""",
            args["tenant_id"],
            args["user_id"],
            uuid4(),
            now,
        )
        with pytest.raises(
            recovery.LocalRecoveryDenied, match="UNRELATED_REQUEST_RUNNING"
        ):
            await recovery.recover_legacy_local_reboot(worker, settings, **args)
        assert (
            await admin.fetchval(
                "SELECT released_at FROM research.daily_research_model_guards WHERE run_id=$1",
                row["run_id"],
            )
            is None
        )
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM research.local_research_recovery_audit WHERE run_id=$1",
                row["run_id"],
            )
            == 0
        )
        assert (
            await admin.fetchval(
                "SELECT status FROM research.daily_research_refresh_requests WHERE id=$1",
                row["debug_request_id"],
            )
            == "RUNNING"
        )


async def test_api_cannot_read_or_mutate_raw_audit_or_guard_evidence(monkeypatch):
    async with fixture(monkeypatch) as (_, _, _, row, _, args):
        api = await connect("api")
        try:
            async with api.transaction():
                await apply_tenant_context(
                    api, str(args["user_id"]), str(args["tenant_id"])
                )
                for sql in (
                    "SELECT * FROM research.local_research_recovery_audit",
                    "SELECT owner_token,exit_evidence,release_proof FROM research.daily_research_model_guards",
                    "UPDATE research.local_research_recovery_audit SET expired_queued=0",
                    "SELECT research.lock_local_research_maintenance()",
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with api.transaction():
                            await api.execute(sql)
        finally:
            await api.close()


async def test_fixed_table_lock_requires_worker_to_already_hold_dispatch_seal(
    monkeypatch,
):
    async with fixture(monkeypatch) as (_, worker, _, _, _, _):
        with pytest.raises(
            asyncpg.InsufficientPrivilegeError, match="DISPATCH_SEAL_REQUIRED"
        ):
            async with worker.transaction():
                await worker.execute(
                    "SELECT research.lock_local_research_maintenance()"
                )


async def test_busy_enqueue_table_refuses_without_deadlock_and_releases_only_maintenance_seal(
    monkeypatch,
):
    async with fixture(monkeypatch) as (admin, worker, _, row, settings, args):
        async with admin.transaction():
            await admin.execute(
                "UPDATE research.daily_research_refresh_requests SET error_class='FixtureHolding' WHERE id=$1",
                row["debug_request_id"],
            )
            with pytest.raises(
                recovery.LocalRecoveryDenied, match="MAINTENANCE_TABLE_BUSY"
            ):
                await recovery.recover_legacy_local_reboot(worker, settings, **args)
            assert (
                await admin.fetchval(
                    "SELECT released_at FROM research.daily_research_model_guards WHERE run_id=$1",
                    row["run_id"],
                )
                is None
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM research.local_research_recovery_audit WHERE run_id=$1",
                    row["run_id"],
                )
                == 0
            )
            try:
                assert (
                    await admin.fetchval(
                        "SELECT pg_try_advisory_lock(hashtext('research-model-dispatch'))"
                    )
                    is True
                )
            finally:
                await admin.fetchval(
                    "SELECT pg_advisory_unlock(hashtext('research-model-dispatch'))"
                )
