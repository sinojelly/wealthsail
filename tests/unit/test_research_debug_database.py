"""Debug dispatch identities, PostgreSQL concurrency and actor RLS in an owned DB."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.errors import ForbiddenError, NotFoundError
from backend.macro import research_debug_repository as repo
from backend.macro.alpha_repository import (
    claim_alpha_batch,
    finish_alpha_batch,
    read_alpha_public,
)
from backend.macro.daily_repository import (
    claim_daily_run,
    consumed,
    finish_daily_run,
    read_daily_research,
)

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned debug research DB required"),
]
NOW = datetime(2600, 1, 1, 7, tzinfo=ZoneInfo("Asia/Shanghai"))


@asynccontextmanager
async def fixture(*, committed=False):
    parts = urlsplit(DSN)
    assert (
        parts.hostname == "127.0.0.1"
        and parts.port == 55434
        and parts.path.startswith("/wealthsail_review07_validation_v")
    )
    conn = await asyncpg.connect(DSN)
    tenant, owner, other = uuid4(), uuid4(), uuid4()
    transaction = conn.transaction()
    await transaction.start()
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'debug fixture')", tenant
    )
    for actor in (owner, other):
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'debug fixture')",
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
        yield conn, tenant, owner, other
    finally:
        if not committed:
            await transaction.rollback()
        else:
            await conn.execute("RESET ROLE")
            await conn.execute(
                "UPDATE research.daily_research_refresh_requests SET batch_id=NULL,report_run_id=NULL,status='FAILED',completed_at=COALESCE(completed_at,requested_at) WHERE tenant_id=$1",
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
                "DELETE FROM identity.profiles WHERE id=ANY($1::uuid[])", [owner, other]
            )
        await conn.close()


async def enqueue(conn, tenant, actor, key=None, now=NOW):
    return await repo.enqueue_refresh(conn, tenant, actor, key or uuid4(), now)


async def batch_and_report(conn, request, stamp=NOW):
    batch = await claim_alpha_batch(conn, stamp, debug_request_id=request)
    await finish_alpha_batch(
        conn, batch["id"], stamp, "READY", {"catalysts": [{"id": str(request)}]}
    )
    report = await claim_daily_run(
        conn,
        stamp,
        {},
        input_hash="fixture",
        prompt_version="fixture",
        debug_request_id=request,
        source_batch_id=batch["id"],
    )
    await finish_daily_run(
        conn, report["run_id"], stamp, status="READY", payload={"summary": str(request)}
    )
    return batch, report


async def test_same_actor_active_request_reused_and_completed_command_never_replayed():
    async with fixture() as (conn, tenant, owner, other):
        key = uuid4()
        first = await enqueue(conn, tenant, owner, key)
        concurrent_key = uuid4()
        assert (await enqueue(conn, tenant, owner, concurrent_key))["id"] == first["id"]
        claim = await repo.claim_refresh(conn, NOW)
        assert claim["id"] == UUID(first["id"])
        await repo.finish_refresh(
            conn, claim["id"], NOW, status="FAILED", error_class="FixtureFailure"
        )
        assert (await enqueue(conn, tenant, owner, key))["id"] == first["id"]
        assert (await enqueue(conn, tenant, owner, concurrent_key))["id"] == first["id"]
        second = await enqueue(conn, tenant, owner)
        assert second["id"] != first["id"] and second["status"] == "QUEUED"


async def test_debug_sources_are_mandatory_and_cannot_use_another_requests_batch():
    async with fixture() as (conn, tenant, owner, other):
        first = await enqueue(conn, tenant, owner)
        second = await enqueue(conn, tenant, other)
        first_id, second_id = map(UUID, (first["id"], second["id"]))
        batch = await claim_alpha_batch(conn, NOW, debug_request_id=first_id)
        for batch_id in (None, batch["id"]):
            with pytest.raises(
                (asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)
            ):
                async with conn.transaction():
                    await claim_daily_run(
                        conn,
                        NOW,
                        {},
                        input_hash="fixture",
                        prompt_version="fixture",
                        debug_request_id=second_id,
                        source_batch_id=batch_id,
                    )


async def test_same_day_debug_versions_do_not_consume_scheduled_slot_and_failed_latest_keeps_ready():
    async with fixture() as (conn, tenant, owner, other):
        initial = await enqueue(conn, tenant, owner)
        first_id = UUID(initial["id"])
        claimed = await repo.claim_refresh(conn, NOW)
        batch, report = await batch_and_report(conn, first_id)
        await repo.finish_refresh(
            conn,
            claimed["id"],
            NOW,
            status="READY",
            batch_id=batch["id"],
            report_run_id=report["run_id"],
        )
        assert not await consumed(conn, NOW)
        second = await enqueue(conn, tenant, owner, now=NOW + timedelta(minutes=1))
        second_id = UUID(second["id"])
        batch2 = await claim_alpha_batch(
            conn, NOW + timedelta(minutes=1), debug_request_id=second_id
        )
        run2 = await claim_daily_run(
            conn,
            NOW + timedelta(minutes=1),
            {},
            input_hash="new",
            prompt_version="new",
            debug_request_id=second_id,
            source_batch_id=batch2["id"],
        )
        await finish_daily_run(
            conn,
            run2["run_id"],
            NOW + timedelta(minutes=1),
            status="FAILED",
            error_class="FixtureFailure",
        )
        dto = await read_daily_research(conn, NOW + timedelta(minutes=2))
        assert dto["runId"] == str(run2["run_id"]) and dto["state"] == "FAILED"
        assert (
            dto["reportRunId"] == str(report["run_id"])
            and dto["reportRunMode"] == "DEBUG"
        )
        assert (
            dto["sourceBatchId"] == str(batch["id"])
            and dto["scheduleState"] == "NOT_DUE"
        )
        assert await claim_alpha_batch(conn, NOW, debug_request_id=first_id) is None
        assert (
            await claim_daily_run(
                conn,
                NOW,
                {},
                input_hash="again",
                prompt_version="again",
                debug_request_id=first_id,
                source_batch_id=batch["id"],
            )
            is None
        )


async def test_exact_debug_batch_never_returns_other_success_or_future_completion():
    async with fixture() as (conn, tenant, owner, other):
        first = await enqueue(conn, tenant, owner)
        first_id = UUID(first["id"])
        await batch_and_report(conn, first_id)
        second = await enqueue(conn, tenant, other)
        second_id = UUID(second["id"])
        batch = await claim_alpha_batch(conn, NOW, debug_request_id=second_id)
        await finish_alpha_batch(
            conn,
            batch["id"],
            NOW + timedelta(minutes=1),
            "READY",
            {"catalysts": [{"id": "future"}]},
        )
        dto = await read_alpha_public(conn, NOW, debug_request_id=second_id)
        assert dto["catalysts"] == [] and dto["collectionState"] == "COLLECTING"
        dto = await read_alpha_public(
            conn, NOW + timedelta(minutes=1), debug_request_id=second_id
        )
        assert dto["catalysts"] == [{"id": "future"}] and dto["batchId"] == str(
            batch["id"]
        )


async def test_operator_rls_hides_other_requests_and_forbids_terminal_or_metadata_writes():
    async with fixture() as (conn, tenant, owner, other):
        mine = await enqueue(conn, tenant, owner)
        theirs = await enqueue(conn, tenant, other)
        await apply_tenant_context(conn, str(owner), str(tenant))
        assert (await repo.read_refresh(conn, tenant, owner, UUID(mine["id"]), NOW))[
            "id"
        ] == mine["id"]
        with pytest.raises(NotFoundError):
            await repo.read_refresh(conn, tenant, other, UUID(theirs["id"]), NOW)
        for statement in (
            "UPDATE research.daily_research_refresh_requests SET status='FAILED'",
            "DELETE FROM research.daily_research_refresh_requests",
            "INSERT INTO market.fund_research_nav_histories(fund_code,retrieved_at,available_at,payload) VALUES('000051',now(),now(),'{}')",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(statement)
        for table in (
            "research.daily_research_runs",
            "research.daily_research_results",
            "market.fund_research_nav_histories",
        ):
            assert not await conn.fetchval(
                "SELECT has_table_privilege('wealthsail_worker',$1,'UPDATE')", table
            )
            assert not await conn.fetchval(
                "SELECT has_table_privilege('wealthsail_worker',$1,'DELETE')", table
            )


async def test_claim_revalidates_operator_and_expired_running_request_is_never_reclaimed():
    async with fixture() as (conn, tenant, owner, other):
        invalid = await enqueue(conn, tenant, owner)
        valid = await enqueue(conn, tenant, other)
        await conn.execute(
            "UPDATE identity.tenant_members SET role='VIEWER' WHERE tenant_id=$1 AND user_id=$2",
            tenant,
            owner,
        )
        selected = await repo.claim_refresh(conn, NOW)
        assert str(selected["id"]) == valid["id"]
        assert await repo.claim_refresh(conn, NOW + timedelta(minutes=1)) is None
        assert await repo.claim_refresh(conn, NOW + timedelta(minutes=36)) is None
        assert (
            await repo.read_refresh(
                conn, tenant, other, selected["id"], NOW + timedelta(minutes=36)
            )
        )["status"] == "UNCERTAIN"
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_refresh_requests WHERE id=$1",
                UUID(invalid["id"]),
            )
            == "FAILED"
        )


async def test_two_workers_cannot_claim_two_debug_requests_concurrently():
    async with fixture(committed=True) as (conn, tenant, owner, other):
        await enqueue(conn, tenant, owner)
        await enqueue(conn, tenant, other)

        async def claim():
            worker = await asyncpg.connect(DSN)
            try:
                await worker.execute("SET ROLE wealthsail_worker")
                return await repo.claim_refresh(worker, NOW)
            finally:
                await worker.close()

        outcomes = await asyncio.gather(claim(), claim())
        assert sum(item is not None for item in outcomes) == 1


async def test_closed_tenant_cannot_enqueue_and_queued_task_loses_dispatch_authority():
    async with fixture() as (conn, tenant, owner, other):
        initial = await enqueue(conn, tenant, owner)
        await conn.execute(
            "UPDATE identity.tenants SET status='CLOSED' WHERE id=$1", tenant
        )
        with pytest.raises(ForbiddenError):
            await enqueue(conn, tenant, other)
        assert await repo.claim_refresh(conn, NOW) is None
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_refresh_requests WHERE id=$1",
                UUID(initial["id"]),
            )
            == "FAILED"
        )


async def test_api_role_can_enqueue_own_request_and_persist_coalesced_command_key():
    async with fixture() as (conn, tenant, owner, other):
        await apply_tenant_context(conn, str(owner), str(tenant))
        first = await enqueue(conn, tenant, owner)
        replay_key = uuid4()
        assert (await enqueue(conn, tenant, owner, replay_key))["id"] == first["id"]
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        claim = await repo.claim_refresh(conn, NOW)
        await repo.finish_refresh(
            conn, claim["id"], NOW, status="FAILED", error_class="ProviderFailure"
        )
        await apply_tenant_context(conn, str(owner), str(tenant))
        assert (await enqueue(conn, tenant, owner, replay_key))["id"] == first["id"]
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.daily_research_refresh_requests"
            )
            == 1
        )


async def test_clock_regression_cannot_claim_another_request_while_future_started_run_exists():
    async with fixture() as (conn, tenant, owner, other):
        first = await enqueue(conn, tenant, owner)
        await enqueue(conn, tenant, other, now=NOW + timedelta(minutes=1))
        claimed = await repo.claim_refresh(conn, NOW + timedelta(minutes=10))
        assert str(claimed["id"]) == first["id"]
        assert await repo.claim_refresh(conn, NOW + timedelta(minutes=2)) is None
