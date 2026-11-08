"""Final cost barriers against a disposable, explicitly selected loopback cluster."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import os
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.macro.alpha_repository import claim_alpha_batch, finish_alpha_batch
from backend.macro.daily_repository import claim_daily_run

DSN = os.environ.get("RESEARCH_QUEUE_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned queue105 loopback DB required"),
]


@asynccontextmanager
async def fixture(*, committed=False):
    uri = urlsplit(DSN)
    assert (uri.hostname, uri.port, uri.path, uri.username) == (
        "127.0.0.1",
        29035,
        "/wealthsail_queue_control_test",
        "queue_test_admin",
    )
    conn = await asyncpg.connect(DSN, timeout=5)
    tenant, actor = uuid4(), uuid4()
    transaction = conn.transaction()
    await transaction.start()
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'claim gate fixture')", tenant
    )
    await conn.execute(
        "INSERT INTO identity.profiles(id,nickname) VALUES($1,'claim gate fixture')",
        actor,
    )
    await conn.execute(
        "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
        tenant,
        actor,
    )
    if committed:
        await transaction.commit()
    try:
        yield conn, tenant, actor
    finally:
        if not committed:
            await transaction.rollback()
        else:
            await conn.execute("RESET ROLE")
            await conn.execute(
                "DELETE FROM research.daily_research_model_guards WHERE run_id IN(SELECT run_id FROM research.daily_research_runs WHERE debug_request_id IN(SELECT id FROM research.daily_research_refresh_requests WHERE tenant_id=$1))",
                tenant,
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
                "DELETE FROM research.daily_research_refresh_requests WHERE tenant_id=$1",
                tenant,
            )
            await conn.execute(
                "DELETE FROM identity.tenant_members WHERE tenant_id=$1", tenant
            )
            await conn.execute("DELETE FROM identity.tenants WHERE id=$1", tenant)
            await conn.execute("DELETE FROM identity.profiles WHERE id=$1", actor)
        await conn.close()


async def seed(conn, tenant, actor, mode, state, *, started=None):
    await conn.execute("RESET ROLE")
    now = await conn.fetchval("SELECT clock_timestamp()")
    status = (
        state if state in {"QUEUED", "CANCELLED", "EXPIRED", "FAILED"} else "RUNNING"
    )
    start = (
        started
        if started is not None
        else (
            now + timedelta(seconds=2)
            if state == "FUTURE_START"
            else now - timedelta(minutes=35)
            if state == "EXECUTION_EXPIRED"
            else now - timedelta(minutes=1)
            if status in {"RUNNING", "FAILED"}
            else None
        )
    )
    requested = (
        min(now - timedelta(minutes=2), start) if start else now - timedelta(minutes=2)
    )
    completed = now if status in {"CANCELLED", "EXPIRED", "FAILED"} else None
    error = (
        "CancelledBeforeStart"
        if status == "CANCELLED"
        else "QueueWaitExpired"
        if status == "EXPIRED"
        else None
    )
    request = uuid4()
    await conn.execute(
        """INSERT INTO research.daily_research_refresh_requests
      (id,tenant_id,user_id,command_key,requested_at,request_mode,status,started_at,completed_at,error_class)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        request,
        tenant,
        actor,
        uuid4(),
        requested,
        mode,
        status,
        start,
        completed,
        error,
    )
    batch = uuid4()
    await conn.execute(
        """INSERT INTO market.alpha_public_batches(id,run_date,claimed_at,completed_at,status,run_mode,debug_request_id)
      VALUES($1,$2,$3,$3,'READY',$4,$5)""",
        batch,
        now.date(),
        max(start or requested, now - timedelta(seconds=1)),
        mode,
        request,
    )
    return request, batch, now


async def invoke(conn, mode, target, request, batch, now):
    argument = {
        "manual_request_id" if mode == "MANUAL" else "debug_request_id": request
    }
    if target == "alpha":
        return await claim_alpha_batch(conn, now, **argument)
    return await claim_daily_run(
        conn,
        now,
        {},
        input_hash="fixture",
        prompt_version="fixture",
        source_batch_id=batch,
        **argument,
    )


@pytest.mark.parametrize("mode", ["DEBUG", "MANUAL"])
@pytest.mark.parametrize("target", ["alpha", "daily"])
@pytest.mark.parametrize(
    "state",
    ["QUEUED", "CANCELLED", "EXPIRED", "FAILED", "FUTURE_START", "EXECUTION_EXPIRED"],
)
async def test_nonexecuting_or_out_of_window_requests_cannot_insert_new_cost_record(
    mode, target, state
):
    async with fixture() as (conn, tenant, actor):
        request, batch, now = await seed(conn, tenant, actor, mode, state)
        if target == "alpha":
            await conn.execute(
                "DELETE FROM market.alpha_public_batches WHERE id=$1", batch
            )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        assert await invoke(conn, mode, target, request, batch, now) is None
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.daily_research_runs WHERE debug_request_id=$1",
                request,
            )
            == 0
        )


@pytest.mark.parametrize("mode", ["DEBUG", "MANUAL"])
async def test_legitimate_claimed_work_can_collect_then_dispatch_once(mode):
    async with fixture() as (conn, tenant, actor):
        request, previous, now = await seed(conn, tenant, actor, mode, "RUNNING")
        await conn.execute(
            "DELETE FROM market.alpha_public_batches WHERE id=$1", previous
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        argument = {
            "manual_request_id" if mode == "MANUAL" else "debug_request_id": request
        }
        batch = await claim_alpha_batch(conn, now, **argument)
        assert batch is not None
        assert (
            await claim_daily_run(
                conn,
                now,
                {},
                input_hash="fixture",
                prompt_version="fixture",
                source_batch_id=batch["id"],
                **argument,
            )
            is None
        )
        await finish_alpha_batch(conn, batch["id"], now, "READY", {})
        run = await claim_daily_run(
            conn,
            now,
            {},
            input_hash="fixture",
            prompt_version="fixture",
            source_batch_id=batch["id"],
            **argument,
        )
        assert run is not None
        assert (
            await claim_daily_run(
                conn,
                now,
                {},
                input_hash="fixture",
                prompt_version="fixture",
                source_batch_id=batch["id"],
                **argument,
            )
            is None
        )
        assert await claim_alpha_batch(conn, now, **argument) is None


async def wait_for_lock(observer, process_id):
    for _ in range(40):
        if await observer.fetchval(
            "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
            process_id,
        ):
            return
        await asyncio.sleep(0.025)
    raise AssertionError("claim did not wait on the original request row")


@pytest.mark.parametrize("target", ["alpha", "daily"])
async def test_row_locked_before_cancel_commit_cannot_insert_after_cancellation_wins(
    target,
):
    async with fixture(committed=True) as (owner, tenant, actor):
        request, batch, now = await seed(owner, tenant, actor, "DEBUG", "QUEUED")
        if target == "alpha":
            await owner.execute(
                "DELETE FROM market.alpha_public_batches WHERE id=$1", batch
            )
        contender = await asyncpg.connect(DSN, timeout=5)
        pending = None
        transaction = owner.transaction()
        await transaction.start()
        try:
            await owner.fetchval(
                "SELECT id FROM research.daily_research_refresh_requests WHERE id=$1 FOR UPDATE",
                request,
            )
            await contender.execute("SET ROLE wealthsail_worker")
            # Keep alpha preparation out of contender, whose role cannot DELETE.
            if target == "alpha":
                pending = asyncio.create_task(
                    claim_alpha_batch(contender, now, debug_request_id=request)
                )
            else:
                pending = asyncio.create_task(
                    claim_daily_run(
                        contender,
                        now,
                        {},
                        input_hash="fixture",
                        prompt_version="fixture",
                        source_batch_id=batch,
                        debug_request_id=request,
                    )
                )
            await wait_for_lock(owner, contender.get_server_pid())
            await owner.execute(
                "UPDATE research.daily_research_refresh_requests SET status='CANCELLED',completed_at=clock_timestamp(),error_class='CancelledBeforeStart' WHERE id=$1",
                request,
            )
            await transaction.commit()
            assert await asyncio.wait_for(pending, 3) is None
        finally:
            if not transaction._state.name == "COMMITTED":
                await transaction.rollback()
            if pending and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await contender.close()


@pytest.mark.parametrize("target", ["alpha", "daily"])
async def test_post_lock_real_deadline_cannot_be_bypassed_by_old_caller_timestamp(
    target,
):
    async with fixture(committed=True) as (owner, tenant, actor):
        started = await owner.fetchval(
            "SELECT clock_timestamp()-interval '35 minutes'+interval '400 milliseconds'"
        )
        request, batch, now = await seed(
            owner, tenant, actor, "MANUAL", "RUNNING", started=started
        )
        if target == "alpha":
            await owner.execute(
                "DELETE FROM market.alpha_public_batches WHERE id=$1", batch
            )
        contender = await asyncpg.connect(DSN, timeout=5)
        transaction = owner.transaction()
        await transaction.start()
        pending = None
        try:
            await owner.fetchval(
                "SELECT id FROM research.daily_research_refresh_requests WHERE id=$1 FOR UPDATE",
                request,
            )
            await contender.execute("SET ROLE wealthsail_worker")
            operation = (
                claim_alpha_batch(contender, now, manual_request_id=request)
                if target == "alpha"
                else claim_daily_run(
                    contender,
                    now,
                    {},
                    input_hash="fixture",
                    prompt_version="fixture",
                    source_batch_id=batch,
                    manual_request_id=request,
                )
            )
            pending = asyncio.create_task(operation)
            await wait_for_lock(owner, contender.get_server_pid())
            await asyncio.sleep(0.6)
            await transaction.commit()
            assert await asyncio.wait_for(pending, 3) is None
        finally:
            if not transaction._state.name == "COMMITTED":
                await transaction.rollback()
            if pending and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await contender.close()
