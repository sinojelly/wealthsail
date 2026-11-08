"""Queue RPCs and claim races in an explicitly isolated local PostgreSQL cluster."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.errors import ForbiddenError, NotFoundError, VersionConflictError
from backend.macro import research_debug_repository as repo

DSN = os.environ.get("RESEARCH_QUEUE_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated queue-control PostgreSQL required"),
]


async def connect(role=None):
    parsed = urlsplit(DSN)
    assert parsed.hostname == "127.0.0.1" and parsed.port == 29035
    assert parsed.path == "/wealthsail_queue_control_test"
    if role:
        parsed = parsed._replace(netloc=f"queue_test_{role}_login@127.0.0.1:29035")
    conn = await asyncpg.connect(urlunsplit(parsed))
    if role == "worker":
        await conn.execute("SET ROLE wealthsail_worker")
    return conn


@asynccontextmanager
async def actors():
    conn = await connect()
    tenant, owner, other, admin = uuid4(), uuid4(), uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'isolated queue test')", tenant
    )
    for user, role in [(owner, "OWNER"), (other, "OWNER"), (admin, "ADMIN")]:
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'queue fixture')",
            user,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,$3)",
            tenant,
            user,
            role,
        )
    try:
        yield conn, tenant, owner, other, admin
    finally:
        await conn.execute(
            "UPDATE research.daily_research_refresh_requests SET status='FAILED',completed_at=GREATEST(clock_timestamp(),requested_at,started_at) WHERE tenant_id=$1 AND status IN ('RUNNING','QUEUED')",
            tenant,
        )
        await conn.close()


async def insert_request(conn, tenant, user, *, at=None):
    return await conn.fetchrow(
        "INSERT INTO research.daily_research_refresh_requests(tenant_id,user_id,command_key,requested_at,request_mode) VALUES($1,$2,$3,$4,'MANUAL') RETURNING *",
        tenant,
        user,
        uuid4(),
        at or datetime.now(timezone.utc),
    )


async def scoped(conn, tenant, user):
    await apply_tenant_context(conn, str(user), str(tenant))


async def test_owner_cancel_preserves_command_and_never_creates_execution_or_usage():
    async with actors() as (admin, tenant, owner, _, _):
        api, worker = await connect("api"), await connect("worker")
        now, key = datetime.now(timezone.utc), uuid4()
        before = await admin.fetchrow(
            "SELECT (SELECT count(*) FROM research.daily_research_runs) AS runs,(SELECT count(*) FROM research.daily_research_model_guards) AS guards"
        )
        try:
            async with api.transaction():
                await scoped(api, tenant, owner)
                queued = await repo.enqueue_refresh(
                    api, tenant, owner, key, now, request_mode="MANUAL"
                )
                cancelled = await repo.cancel_refresh(
                    api, tenant, owner, queued["id"], now
                )
                again = await repo.cancel_refresh(
                    api, tenant, owner, queued["id"], now + timedelta(seconds=1)
                )
                replay = await repo.enqueue_refresh(
                    api,
                    tenant,
                    owner,
                    key,
                    now + timedelta(seconds=2),
                    request_mode="MANUAL",
                )
            assert (
                cancelled["status"]
                == again["status"]
                == replay["status"]
                == "CANCELLED"
            )
            assert again["completedAt"] == cancelled["completedAt"]
            assert (
                cancelled["startedAt"]
                is cancelled["batchId"]
                is cancelled["reportRunId"]
                is None
            )
            assert (
                await repo.claim_refresh(
                    worker, datetime.now(timezone.utc), allowed_modes=["MANUAL"]
                )
                is None
            )
            after = await admin.fetchrow(
                "SELECT (SELECT count(*) FROM research.daily_research_runs) AS runs,(SELECT count(*) FROM research.daily_research_model_guards) AS guards"
            )
            assert after == before
        finally:
            await api.close()
            await worker.close()


async def test_api_role_has_no_general_update_or_guard_access_and_rpc_requires_current_owner():
    async with actors() as (admin, tenant, owner, other, manager):
        row = await insert_request(admin, tenant, owner)
        api = await connect("api")
        try:
            for user, expected in [(other, NotFoundError), (manager, ForbiddenError)]:
                async with api.transaction():
                    await scoped(api, tenant, user)
                    with pytest.raises(expected):
                        async with api.transaction():
                            await repo.cancel_refresh(
                                api, tenant, user, row["id"], datetime.now(timezone.utc)
                            )
            async with api.transaction():
                await scoped(api, tenant, owner)
                for sql in [
                    "UPDATE research.daily_research_refresh_requests SET status='RUNNING'",
                    "SELECT owner_token FROM research.daily_research_model_guards",
                ]:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with api.transaction():
                            await api.execute(sql)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with api.transaction():
                        await api.fetchval(
                            "SELECT research.expire_queued_research_refresh($1,$2,$3)",
                            tenant,
                            other,
                            datetime.now(timezone.utc),
                        )
                await admin.execute(
                    "UPDATE identity.tenant_members SET status='SUSPENDED' WHERE tenant_id=$1 AND user_id=$2",
                    tenant,
                    owner,
                )
                with pytest.raises(ForbiddenError):
                    async with api.transaction():
                        await repo.cancel_refresh(
                            api, tenant, owner, row["id"], datetime.now(timezone.utc)
                        )
        finally:
            await api.close()


async def test_queue_expires_while_guard_stays_occupied_without_consuming_a_slot():
    async with actors() as (admin, tenant, owner, _, _):
        now = datetime.now(timezone.utc)
        row = await insert_request(admin, tenant, owner, at=now - timedelta(minutes=6))
        run, token = uuid4(), uuid4()
        await admin.execute(
            """WITH slot AS (SELECT COALESCE(max(slot_date),date '2000-01-01')+1 AS day FROM research.daily_research_runs)
        INSERT INTO research.daily_research_runs(run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet)
        SELECT $1,day,day+time '07:00' AT TIME ZONE 'Asia/Shanghai',day+time '07:00' AT TIME ZONE 'Asia/Shanghai','fixture','fixture','{}'::jsonb FROM slot""",
            run,
        )
        await admin.execute(
            "INSERT INTO research.daily_research_model_guards(run_id,owner_token,acquired_at) VALUES($1,$2,$3)",
            run,
            token,
            now,
        )
        worker, api = await connect("worker"), await connect("api")
        try:
            assert (
                await repo.claim_refresh(worker, now, allowed_modes=["MANUAL"]) is None
            )
            saved = await admin.fetchrow(
                "SELECT * FROM research.daily_research_refresh_requests WHERE id=$1",
                row["id"],
            )
            assert saved["status"] == "EXPIRED" and saved["started_at"] is None
            assert (
                await admin.fetchval(
                    "SELECT released_at FROM research.daily_research_model_guards WHERE run_id=$1",
                    run,
                )
                is None
            )
            fresh = await insert_request(admin, tenant, owner)
            async with api.transaction():
                await scoped(api, tenant, owner)
                dto = await repo.read_refresh(
                    api, tenant, owner, fresh["id"], datetime.now(timezone.utc)
                )
            assert dto["blockedReasonCode"] == "MODEL_GUARD_ACTIVE" and dto["canCancel"]
            assert set(dto).isdisjoint({"ownerToken", "pid", "blockingRunId"})
        finally:
            await admin.execute(
                "UPDATE research.daily_research_model_guards SET released_at=clock_timestamp() WHERE run_id=$1",
                run,
            )
            await worker.close()
            await api.close()


async def test_worker_claim_wins_cancel_race_without_cancelling_started_work():
    async with actors() as (admin, tenant, owner, _, _):
        row = await insert_request(admin, tenant, owner)
        worker, api = await connect("worker"), await connect("api")
        transaction = worker.transaction()
        await transaction.start()
        try:
            claimed = await repo.claim_refresh(
                worker, datetime.now(timezone.utc), allowed_modes=["MANUAL"]
            )
            assert claimed["id"] == row["id"]

            async def cancel():
                async with api.transaction():
                    await scoped(api, tenant, owner)
                    return await repo.cancel_refresh(
                        api, tenant, owner, row["id"], datetime.now(timezone.utc)
                    )

            pending = asyncio.create_task(cancel())
            await asyncio.sleep(0.05)
            assert not pending.done()
            await transaction.commit()
            with pytest.raises(VersionConflictError):
                await asyncio.wait_for(pending, 3)
            saved = await admin.fetchrow(
                "SELECT status,started_at FROM research.daily_research_refresh_requests WHERE id=$1",
                row["id"],
            )
            assert saved["status"] == "RUNNING" and saved["started_at"] is not None
        finally:
            if worker.is_in_transaction():
                await transaction.rollback()
            await worker.close()
            await api.close()


async def test_cancel_wins_claim_race_and_a_later_claim_does_not_revive_it():
    async with actors() as (admin, tenant, owner, _, _):
        row = await insert_request(admin, tenant, owner)
        worker, api = await connect("worker"), await connect("api")
        try:
            async with api.transaction():
                await scoped(api, tenant, owner)
                result = await repo.cancel_refresh(
                    api, tenant, owner, row["id"], datetime.now(timezone.utc)
                )
                assert result["status"] == "CANCELLED"
                assert (
                    await repo.claim_refresh(
                        worker, datetime.now(timezone.utc), allowed_modes=["MANUAL"]
                    )
                    is None
                )
            assert (
                await repo.claim_refresh(
                    worker, datetime.now(timezone.utc), allowed_modes=["MANUAL"]
                )
                is None
            )
        finally:
            await worker.close()
            await api.close()


async def test_actor_poll_persists_expiration_without_a_running_worker():
    async with actors() as (admin, tenant, owner, _, _):
        row = await insert_request(
            admin, tenant, owner, at=datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        api = await connect("api")
        try:
            async with api.transaction():
                await scoped(api, tenant, owner)
                dto = await repo.read_refresh(
                    api, tenant, owner, row["id"], datetime.now(timezone.utc)
                )
            assert dto["status"] == "EXPIRED" and not dto["canCancel"]
            assert (
                await admin.fetchval(
                    "SELECT status FROM research.daily_research_refresh_requests WHERE id=$1",
                    row["id"],
                )
                == "EXPIRED"
            )
        finally:
            await api.close()


async def test_admin_cannot_use_a_future_rpc_clock_to_cancel_a_recent_queue():
    async with actors() as (admin, tenant, _, _, manager):
        row = await insert_request(admin, tenant, manager)
        api = await connect("api")
        try:
            async with api.transaction():
                await scoped(api, tenant, manager)
                expired = await api.fetchval(
                    "SELECT research.expire_queued_research_refresh($1,$2,$3)",
                    tenant,
                    manager,
                    datetime.now(timezone.utc) + timedelta(days=2),
                )
            assert expired == 0
            assert (
                await admin.fetchval(
                    "SELECT status FROM research.daily_research_refresh_requests WHERE id=$1",
                    row["id"],
                )
                == "QUEUED"
            )
        finally:
            await api.close()
