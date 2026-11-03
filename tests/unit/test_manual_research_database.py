"""Owned PostgreSQL mode isolation and durable cross-entry model mutual exclusion."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import ipaddress
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.macro import research_debug_repository as requests

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned loopback DB required"),
]
NOW = datetime(2470, 1, 1, 8, tzinfo=ZoneInfo("Asia/Shanghai"))


@asynccontextmanager
async def fixture(*, committed=False):
    target = urlsplit(DSN)
    assert (
        target.hostname == "localhost"
        or ipaddress.ip_address(target.hostname).is_loopback
    )
    assert target.path.startswith("/wealthsail_manual_research")
    conn = await asyncpg.connect(DSN)
    tenant, actor, other = uuid4(), uuid4(), uuid4()
    transaction = conn.transaction()
    await transaction.start()
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'manual research fixture')",
        tenant,
    )
    for user in (actor, other):
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'manual fixture')",
            user,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
            tenant,
            user,
        )
    if committed:
        await transaction.commit()
    try:
        yield conn, tenant, actor, other
    finally:
        if not committed:
            await transaction.rollback()
        else:
            await conn.execute("RESET ROLE")
            # A committed concurrency fixture owns all its rows; cleanup never touches another actor.
            await conn.execute(
                "DELETE FROM research.daily_research_model_guards WHERE run_id IN (SELECT run_id FROM research.daily_research_runs WHERE debug_request_id IN (SELECT id FROM research.daily_research_refresh_requests WHERE tenant_id=$1))",
                tenant,
            )
            await conn.execute(
                "UPDATE research.daily_research_refresh_requests SET batch_id=NULL,report_run_id=NULL,status='FAILED',completed_at=COALESCE(completed_at,requested_at) WHERE tenant_id=$1",
                tenant,
            )
            # Immutable acceptance rows restrict deletion of their owning run.
            # Only this committed fixture's tenant/run identities are removed.
            if target.path.startswith("/wealthsail_manual_research_acceptance"):
                run_ids = [
                    row["run_id"]
                    for row in await conn.fetch(
                        "SELECT run_id FROM research.daily_research_runs WHERE debug_request_id IN(SELECT id FROM research.daily_research_refresh_requests WHERE tenant_id=$1)",
                        tenant,
                    )
                ]
                for table in (
                    "research_validation_records",
                    "research_validation_artifacts",
                ):
                    await conn.execute(
                        f"DELETE FROM research.{table} WHERE run_id=ANY($1::uuid[])",
                        run_ids,
                    )
            await conn.execute(
                "DELETE FROM research.daily_research_results WHERE run_id IN(SELECT run_id FROM research.daily_research_runs WHERE debug_request_id IN(SELECT id FROM research.daily_research_refresh_requests WHERE tenant_id=$1))",
                tenant,
            )
            await conn.execute(
                "DELETE FROM research.daily_research_runs WHERE debug_request_id IN(SELECT id FROM research.daily_research_refresh_requests WHERE tenant_id=$1)",
                tenant,
            )
            await conn.execute(
                "DELETE FROM market.alpha_public_batches WHERE debug_request_id IN(SELECT id FROM research.daily_research_refresh_requests WHERE tenant_id=$1)",
                tenant,
            )
            await conn.execute(
                "DELETE FROM research.daily_research_refresh_commands WHERE tenant_id=$1",
                tenant,
            )
            await conn.execute(
                "DELETE FROM research.daily_research_refresh_requests WHERE tenant_id=$1",
                tenant,
            )
            await conn.execute(
                "DELETE FROM identity.tenant_members WHERE tenant_id=$1", tenant
            )
            await conn.execute("DELETE FROM identity.tenants WHERE id=$1", tenant)
            await conn.execute(
                "DELETE FROM identity.profiles WHERE id=ANY($1::uuid[])", [actor, other]
            )
        await conn.close()


async def make_run(conn, tenant, actor, *, mode="MANUAL", now=NOW):
    from backend.macro.alpha_repository import claim_alpha_batch
    from backend.macro.daily_repository import claim_daily_run

    request = await requests.enqueue_refresh(
        conn, tenant, actor, uuid4(), now, request_mode=mode
    )
    identity = UUID(request["id"])
    await conn.execute(
        "UPDATE research.daily_research_refresh_requests SET status='RUNNING',started_at=$2 WHERE id=$1",
        identity,
        now,
    )
    arguments = {
        "manual_request_id" if mode == "MANUAL" else "debug_request_id": identity
    }
    batch = await claim_alpha_batch(conn, now, **arguments)
    assert batch is not None and batch["run_mode"] == mode
    run = await claim_daily_run(
        conn,
        now,
        {},
        input_hash="fixture",
        prompt_version="manual-fixture",
        source_batch_id=batch["id"],
        **arguments,
    )
    assert run is not None and run["run_mode"] == mode
    return identity, batch["id"], run["run_id"]


async def test_097_adds_frozen_request_mode_and_persistent_guard_permissions():
    async with fixture() as (conn, _tenant, _actor, _other):
        column = await conn.fetchrow(
            "SELECT column_default,is_nullable FROM information_schema.columns WHERE table_schema='research' AND table_name='daily_research_refresh_requests' AND column_name='request_mode'"
        )
        assert column is not None
        assert "DEBUG" in column["column_default"] and column["is_nullable"] == "NO"
        assert await conn.fetchval(
            "SELECT to_regclass('research.daily_research_model_guards') IS NOT NULL"
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert not await conn.fetchval(
                "SELECT has_table_privilege('wealthsail_api','research.daily_research_model_guards',$1)",
                privilege,
            )
        for privilege in ("SELECT", "INSERT"):
            assert await conn.fetchval(
                "SELECT has_table_privilege('wealthsail_worker','research.daily_research_model_guards',$1)",
                privilege,
            )
        assert not await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','research.daily_research_model_guards','UPDATE,DELETE')"
        )
        assert await conn.fetchval(
            "SELECT has_column_privilege('wealthsail_worker','research.daily_research_model_guards','released_at','UPDATE')"
        )


async def test_command_replay_is_permanent_across_modes_and_coalescing_stays_in_mode():
    async with fixture() as (conn, tenant, actor, _other):
        key = uuid4()
        manual = await requests.enqueue_refresh(
            conn, tenant, actor, key, NOW, request_mode="MANUAL"
        )
        debug = await requests.enqueue_refresh(conn, tenant, actor, uuid4(), NOW)
        assert debug["id"] != manual["id"]
        assert (
            await requests.enqueue_refresh(
                conn, tenant, actor, uuid4(), NOW, request_mode="MANUAL"
            )
        )["id"] == manual["id"]
        assert await requests.claim_refresh(conn, NOW, allowed_modes=["MANUAL"])
        await requests.finish_refresh(conn, UUID(manual["id"]), NOW, status="FAILED")
        replay = await requests.enqueue_refresh(
            conn, tenant, actor, key, NOW, request_mode="DEBUG"
        )
        assert (
            replay["id"] == manual["id"]
            and replay["requestMode"] == "MANUAL"
            and replay["status"] == "FAILED"
        )
        assert (
            await requests.enqueue_refresh(
                conn, tenant, actor, uuid4(), NOW, request_mode="MANUAL"
            )
        )["id"] != manual["id"]


async def test_manual_running_request_never_expires_into_a_new_claim_or_new_command():
    async with fixture() as (conn, tenant, actor, other):
        first = await requests.enqueue_refresh(
            conn, tenant, actor, uuid4(), NOW, request_mode="MANUAL"
        )
        await requests.enqueue_refresh(
            conn,
            tenant,
            other,
            uuid4(),
            NOW + timedelta(seconds=1),
            request_mode="MANUAL",
        )
        assert await requests.claim_refresh(conn, NOW, allowed_modes=["MANUAL"])
        later = NOW + timedelta(minutes=36)
        same = await requests.enqueue_refresh(
            conn, tenant, actor, uuid4(), later, request_mode="MANUAL"
        )
        assert same["id"] == first["id"] and same["status"] == "UNCERTAIN"
        assert (
            await requests.claim_refresh(conn, later, allowed_modes=["MANUAL"]) is None
        )
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_refresh_requests WHERE id=$1",
                UUID(first["id"]),
            )
            == "RUNNING"
        )


async def test_allowed_modes_filter_claim_and_latest_without_reinterpreting_saved_mode():
    async with fixture() as (conn, tenant, actor, _other):
        manual = await requests.enqueue_refresh(
            conn, tenant, actor, uuid4(), NOW, request_mode="MANUAL"
        )
        debug = await requests.enqueue_refresh(
            conn, tenant, actor, uuid4(), NOW + timedelta(seconds=1)
        )
        latest = await requests.latest_refresh(
            conn, tenant, actor, NOW + timedelta(seconds=2), allowed_modes=["MANUAL"]
        )
        assert latest["id"] == manual["id"]
        chosen = await requests.claim_refresh(conn, NOW + timedelta(seconds=2))
        assert str(chosen["id"]) == debug["id"] and chosen["request_mode"] == "DEBUG"


@pytest.mark.parametrize(
    "mismatch",
    [
        "batch_request_mode",
        "run_request_mode",
        "run_batch_request",
        "scheduled_batch_mode",
    ],
)
async def test_mode_request_and_batch_identity_cannot_be_mixed(mismatch):
    async with fixture() as (conn, tenant, actor, other):
        identity, batch, _run = await make_run(conn, tenant, actor)
        debug = UUID(
            (await requests.enqueue_refresh(conn, tenant, other, uuid4(), NOW))["id"]
        )
        with pytest.raises(
            (asyncpg.ForeignKeyViolationError, asyncpg.CheckViolationError)
        ):
            async with conn.transaction():
                if mismatch == "batch_request_mode":
                    await conn.execute(
                        "INSERT INTO market.alpha_public_batches(run_date,claimed_at,status,run_mode,debug_request_id) VALUES($1,$2,'CLAIMED','MANUAL',$3)",
                        NOW.date(),
                        NOW,
                        debug,
                    )
                else:
                    request = (
                        debug
                        if mismatch in {"run_request_mode", "run_batch_request"}
                        else None
                    )
                    mode = (
                        "DEBUG"
                        if mismatch == "run_batch_request"
                        else "SCHEDULED"
                        if mismatch == "scheduled_batch_mode"
                        else "MANUAL"
                    )
                    await conn.execute(
                        "INSERT INTO research.daily_research_runs(slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet,run_mode,debug_request_id,source_batch_id) VALUES($1,$2,$2,'fixture','mixed-mode','{}',$3,$4,$5)",
                        NOW.date(),
                        NOW,
                        mode,
                        request,
                        batch,
                    )


async def test_manual_attempt_does_not_consume_scheduled_slot_and_cannot_repeat():
    from backend.macro.daily_repository import claim_daily_run, consumed

    async with fixture() as (conn, tenant, actor, _other):
        identity, batch, _run = await make_run(conn, tenant, actor)
        assert not await consumed(conn, NOW)
        assert (
            await claim_daily_run(
                conn,
                NOW,
                {},
                input_hash="fixture",
                prompt_version="fixture",
                manual_request_id=identity,
                source_batch_id=batch,
            )
            is None
        )
        assert (
            await claim_daily_run(
                conn, NOW, {}, input_hash="fixture", prompt_version="fixture"
            )
            is not None
        )
        assert await consumed(conn, NOW)


async def test_finish_can_discover_only_its_own_completed_run_and_diagnosis():
    from backend.macro.daily_repository import finish_daily_run

    async with fixture() as (conn, tenant, actor, other):
        identity, _batch, run = await make_run(conn, tenant, actor)
        await conn.execute(
            "UPDATE research.daily_research_refresh_requests SET status='RUNNING',started_at=$2 WHERE id=$1",
            identity,
            NOW,
        )
        await finish_daily_run(
            conn,
            run,
            NOW,
            status="UNCERTAIN",
            error_class="TimeoutError",
            failure_stage="MODEL_EXECUTION",
            failure_reason_code="TIMEOUT",
        )
        # The consumer may be cancelled before receiving the inner run's return value.
        await requests.finish_refresh(
            conn, identity, NOW + timedelta(minutes=36), status="UNCERTAIN"
        )
        result = await requests.read_refresh(
            conn, tenant, actor, identity, NOW + timedelta(minutes=36)
        )
        assert result["reportRunId"] == str(run)
        assert (
            result["failureStage"] == "MODEL_EXECUTION"
            and result["failureReasonCode"] == "TIMEOUT"
        )
        missing = UUID(
            (
                await requests.enqueue_refresh(
                    conn, tenant, other, uuid4(), NOW, request_mode="MANUAL"
                )
            )["id"]
        )
        await conn.execute(
            "UPDATE research.daily_research_refresh_requests SET status='RUNNING',started_at=$2 WHERE id=$1",
            missing,
            NOW,
        )
        await requests.finish_refresh(conn, missing, NOW, status="FAILED")
        absent = await requests.read_refresh(conn, tenant, other, missing, NOW)
        assert absent["reportRunId"] is None and absent["failureReasonCode"] is None


@pytest.mark.parametrize("proof", ["matching", "other_request", "unfinished"])
async def test_manual_ready_requires_exact_completed_request_mode_and_source(proof):
    from backend.macro.daily_repository import finish_daily_run

    async with fixture() as (conn, tenant, actor, other):
        identity, batch, run = await make_run(conn, tenant, actor)
        await conn.execute(
            "UPDATE research.daily_research_refresh_requests SET status='RUNNING',started_at=$2 WHERE id=$1",
            identity,
            NOW,
        )
        if proof == "other_request":
            _other_id, batch, run = await make_run(conn, tenant, other)
        await conn.execute(
            "UPDATE market.alpha_public_batches SET status='READY',completed_at=$2,payload='{}' WHERE id=$1",
            batch,
            NOW,
        )
        if proof != "unfinished":
            await finish_daily_run(
                conn, run, NOW, status="READY", payload={"summary": "fixture"}
            )
        await requests.finish_refresh(
            conn,
            identity,
            NOW + timedelta(minutes=36),
            status="READY",
            batch_id=batch,
            report_run_id=run,
        )
        result = await requests.read_refresh(
            conn, tenant, actor, identity, NOW + timedelta(minutes=36)
        )
        assert result["status"] == ("READY" if proof == "matching" else "UNCERTAIN")


async def test_persistent_guard_survives_observation_timeout_and_only_matching_owner_releases():
    from backend.macro.model_dispatch_repository import (
        ResearchModelBusy,
        create_model_guard,
        lock_model_dispatch,
        release_model_guard,
    )

    from backend.macro.daily_repository import finish_daily_run

    async with fixture() as (conn, tenant, actor, other):
        request, _batch, run = await make_run(conn, tenant, actor)
        await lock_model_dispatch(conn)
        token = await create_model_guard(conn, run, NOW)
        await finish_daily_run(
            conn, run, NOW, status="UNCERTAIN", error_class="TimeoutError"
        )
        await requests.finish_refresh(conn, request, NOW, status="UNCERTAIN")
        await requests.enqueue_refresh(
            conn, tenant, other, uuid4(), NOW, request_mode="MANUAL"
        )
        assert (
            await requests.claim_refresh(
                conn, NOW + timedelta(minutes=36), allowed_modes=["MANUAL"]
            )
            is None
        )
        with pytest.raises(ResearchModelBusy):
            await lock_model_dispatch(conn)
        assert not await release_model_guard(
            conn, run, uuid4(), NOW + timedelta(minutes=36)
        )
        assert not await release_model_guard(
            conn, run, token, NOW - timedelta(seconds=1)
        )
        assert await release_model_guard(conn, run, token, NOW + timedelta(minutes=36))
        assert not await release_model_guard(
            conn, run, token, NOW + timedelta(minutes=37)
        )
        await lock_model_dispatch(conn)


async def test_scheduled_and_manual_atomic_model_claims_cannot_overlap():
    from backend.macro.model_dispatch_repository import (
        ResearchModelBusy,
        create_model_guard,
        lock_model_dispatch,
    )
    from backend.macro.alpha_repository import claim_alpha_batch
    from backend.macro.daily_repository import claim_daily_run

    marker = "concurrent-" + uuid4().hex
    now = NOW + timedelta(days=uuid4().int % 100000)
    async with fixture(committed=True) as (admin, tenant, actor, _other):
        request = UUID(
            (
                await requests.enqueue_refresh(
                    admin, tenant, actor, uuid4(), now, request_mode="MANUAL"
                )
            )["id"]
        )
        await admin.execute(
            "UPDATE research.daily_research_refresh_requests SET status='RUNNING',started_at=$2 WHERE id=$1",
            request,
            now,
        )
        batch = await claim_alpha_batch(admin, now, manual_request_id=request)

        async def attempt(mode):
            conn = await asyncpg.connect(DSN)
            try:
                async with conn.transaction():
                    await lock_model_dispatch(conn)
                    run = await claim_daily_run(
                        conn,
                        now,
                        {},
                        input_hash="fixture",
                        prompt_version=marker,
                        **(
                            {
                                "manual_request_id": request,
                                "source_batch_id": batch["id"],
                            }
                            if mode == "MANUAL"
                            else {}
                        ),
                    )
                    assert run is not None
                    await create_model_guard(conn, run["run_id"], now)
                return run["run_id"]
            except ResearchModelBusy:
                return None
            finally:
                await conn.close()

        try:
            runs = await asyncio.gather(attempt("SCHEDULED"), attempt("MANUAL"))
            assert sum(run is not None for run in runs) == 1
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM research.daily_research_runs WHERE prompt_version=$1",
                    marker,
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM research.daily_research_model_guards WHERE released_at IS NULL"
                )
                == 1
            )
        finally:
            await admin.execute(
                "DELETE FROM research.daily_research_model_guards WHERE run_id IN(SELECT run_id FROM research.daily_research_runs WHERE prompt_version=$1)",
                marker,
            )
            await admin.execute(
                "DELETE FROM research.daily_research_runs WHERE prompt_version=$1",
                marker,
            )


async def test_api_can_queue_own_manual_request_but_cannot_rewrite_mode_or_read_guard():
    from backend.common.database import apply_tenant_context
    from backend.errors import NotFoundError

    async with fixture() as (conn, tenant, actor, other):
        theirs = await requests.enqueue_refresh(
            conn, tenant, other, uuid4(), NOW, request_mode="MANUAL"
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        mine = await requests.enqueue_refresh(
            conn, tenant, actor, uuid4(), NOW, request_mode="MANUAL"
        )
        assert mine["requestMode"] == "MANUAL"
        assert (
            await requests.read_refresh(conn, tenant, actor, UUID(mine["id"]), NOW)
        )["id"] == mine["id"]
        with pytest.raises(NotFoundError):
            await requests.read_refresh(conn, tenant, other, UUID(theirs["id"]), NOW)
        for statement in (
            "SELECT * FROM research.daily_research_model_guards",
            "UPDATE research.daily_research_refresh_requests SET request_mode='DEBUG'",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(statement)


async def test_worker_can_release_guard_but_cannot_change_its_owner_or_delete_it():
    from backend.macro.model_dispatch_repository import (
        create_model_guard,
        lock_model_dispatch,
        release_model_guard,
    )

    async with fixture() as (conn, tenant, actor, _other):
        _request, _batch, run = await make_run(conn, tenant, actor)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await lock_model_dispatch(conn)
        token = await create_model_guard(conn, run, NOW)
        for statement in (
            "UPDATE research.daily_research_model_guards SET owner_token=gen_random_uuid()",
            "DELETE FROM research.daily_research_model_guards",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(statement)
        assert await release_model_guard(conn, run, token, NOW)
