"""Opt-in tests on a disposable loopback AI usage database, never .env targets."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from backend.ai_usage import repository
from backend.common.database import create_pool, tenant_transaction


def isolated_dsn():
    dsn = os.environ.get("AI_USAGE_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("disposable AI usage PostgreSQL is not configured")
    parsed = urlsplit(dsn)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port is None
        or parsed.path != "/ai_usage_isolated_test"
        or parsed.username != "postgres"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "AI usage tests require explicit loopback postgres/ai_usage_isolated_test"
        )
    return dsn


@pytest.mark.asyncio
async def test_ai_usage_migration_installs_ledger_and_overlap_function():
    conn = await asyncpg.connect(isolated_dsn())
    try:
        assert await conn.fetchval(
            "SELECT to_regclass('research.ai_usage_tasks') IS NOT NULL "
            "AND to_regclass('research.ai_usage_calls') IS NOT NULL "
            "AND to_regprocedure('research.mark_ai_usage_overlap(uuid)') IS NOT NULL"
        ), "AI usage migration has not installed its real database boundary"
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def usage_db():
    pool = await asyncpg.create_pool(isolated_dsn(), min_size=1, max_size=6)
    users, tenants = [uuid4() for _ in range(3)], [uuid4() for _ in range(2)]
    tasks = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for tenant in tenants:
                await conn.execute(
                    "INSERT INTO identity.tenants(id,name) VALUES($1,'AI usage isolated fixture')",
                    tenant,
                )
            for user in users:
                await conn.execute(
                    "INSERT INTO identity.profiles(id,nickname) VALUES($1,'AI usage isolated fixture')",
                    user,
                )
            for tenant, user, role in [
                (tenants[0], users[0], "OWNER"),
                (tenants[1], users[1], "OWNER"),
                (tenants[0], users[2], "VIEWER"),
            ]:
                await conn.execute(
                    "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,$3)",
                    tenant,
                    user,
                    role,
                )
    try:
        yield pool, users, tenants, tasks
    finally:
        # Independent ledger commits are the behavior under test. Delete only
        # this fixture's generated IDs, atomically, including global tasks.
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM research.ai_usage_calls WHERE task_id=ANY($1::uuid[])",
                    tasks,
                )
                await conn.execute(
                    "DELETE FROM research.ai_usage_tasks WHERE id=ANY($1::uuid[])",
                    tasks,
                )
                await conn.execute(
                    "DELETE FROM identity.platform_roles WHERE user_id=ANY($1::uuid[])",
                    users,
                )
                await conn.execute(
                    "DELETE FROM identity.tenant_members WHERE tenant_id=ANY($1::uuid[])",
                    tenants,
                )
                await conn.execute(
                    "DELETE FROM identity.profiles WHERE id=ANY($1::uuid[])", users
                )
                await conn.execute(
                    "DELETE FROM identity.tenants WHERE id=ANY($1::uuid[])", tenants
                )
        await pool.close()


async def start_task(fixture, member=0, *, global_task=False):
    pool, users, tenants, tasks = fixture
    task_id = uuid4()
    tasks.append(task_id)
    tenant, user = (
        (None, None)
        if global_task
        else (str(tenants[member if member < 2 else 0]), str(users[member]))
    )
    await repository.persist(
        pool,
        tenant,
        user,
        "start_task",
        {
            "id": str(task_id),
            "taskType": "REVIEW_TEST",
            "taskName": "隔离数据库任务",
            "startedAt": datetime.now(timezone.utc) - timedelta(minutes=2),
        },
    )
    return task_id, tenant, user


async def start_call(fixture, task, *, offset=0):
    pool = fixture[0]
    task_id, tenant, user = task
    call = {
        "id": str(uuid4()),
        "taskId": str(task_id),
        "model": "fixture-model",
        "callType": "completion",
        "startedAt": datetime.now(timezone.utc)
        - timedelta(minutes=1)
        + timedelta(seconds=offset),
    }
    await repository.persist(pool, tenant, user, "start_call", call)
    return call


def finish_call(call, fingerprint="isolated-shared-account"):
    before = {
        "accountFingerprint": fingerprint,
        "planType": "test",
        "observedAt": call["startedAt"].isoformat(),
        "windows": [],
        "credits": {"balance": "12.50", "unlimited": False},
    }
    finished = datetime.now(timezone.utc)
    return {
        **call,
        "finishedAt": finished,
        "status": "SUCCEEDED",
        "before": before,
        "after": {
            **before,
            "observedAt": finished.isoformat(),
            "credits": {"balance": "12.40", "unlimited": False},
        },
        "tokens": {
            "inputTokens": 100,
            "cachedTokens": 80,
            "outputTokens": 20,
            "totalTokens": 120,
        },
    }


@pytest.mark.asyncio
async def test_real_usage_ledger_survives_business_rollback(usage_db):
    pool, users, tenants, _ = usage_db
    task = await start_task(usage_db)
    async with pool.acquire() as business_conn:
        business = business_conn.transaction()
        await business.start()
        try:
            call = await start_call(usage_db, task)
            await repository.persist(
                pool, task[1], task[2], "finish_call", finish_call(call)
            )
            await repository.persist(
                pool,
                task[1],
                task[2],
                "finish_task",
                {
                    "id": str(task[0]),
                    "finishedAt": datetime.now(timezone.utc),
                    "businessStatus": "FAILED",
                    "errorClass": "ValidationError",
                },
            )
        finally:
            await business.rollback()
    async with tenant_transaction(pool, str(users[0]), str(tenants[0])) as conn:
        result = await repository.list_tasks(conn, str(tenants[0]))
    assert result["total"] == 1
    item = result["tasks"][0]
    assert item["businessStatus"] == "FAILED" and item["inferenceStatus"] == "SUCCEEDED"
    assert item["totalTokens"] == 120 and item["cachedTokens"] == 80
    assert item["calls"][0]["quota"]["credits"]["consumed"] == "0.10"


@pytest.mark.asyncio
async def test_real_usage_rls_isolates_tenants_members_and_platform_global(usage_db):
    pool, users, tenants, _ = usage_db
    owner = await start_task(usage_db)
    foreign = await start_task(usage_db, 1)
    member = await start_task(usage_db, 2)
    global_task = await start_task(usage_db, global_task=True)
    for task in (owner, foreign, member, global_task):
        await start_call(usage_db, task)
    async with tenant_transaction(pool, str(users[0]), str(tenants[0])) as conn:
        result = await repository.list_tasks(conn, str(tenants[0]), include_global=True)
        assert {row["id"] for row in result["tasks"]} == {str(owner[0]), str(member[0])}
        assert await conn.fetchval("SELECT count(*) FROM research.ai_usage_calls") == 2
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute("SELECT research.mark_ai_usage_overlap($1)", uuid4())
    async with tenant_transaction(pool, str(users[2]), str(tenants[0])) as conn:
        result = await repository.list_tasks(conn, str(tenants[0]), include_global=True)
        assert [row["id"] for row in result["tasks"]] == [str(member[0])]
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO identity.platform_roles(user_id,role,status,granted_by) VALUES($1,'PLATFORM_ADMIN','ACTIVE',$1)",
            users[0],
        )
    async with tenant_transaction(pool, str(users[0]), str(tenants[0])) as conn:
        result = await repository.list_tasks(conn, str(tenants[0]), include_global=True)
        assert {row["id"] for row in result["tasks"]} == {
            str(owner[0]),
            str(member[0]),
            str(global_task[0]),
        }
        assert await conn.fetchval("SELECT count(*) FROM research.ai_usage_calls") == 3


@pytest.mark.asyncio
async def test_real_usage_repo_marks_both_concurrent_cross_tenant_calls(usage_db):
    pool = usage_db[0]
    first, second = await start_task(usage_db), await start_task(usage_db, 1)
    calls = await asyncio.gather(
        start_call(usage_db, first), start_call(usage_db, second)
    )
    await asyncio.gather(
        *[
            repository.persist(pool, task[1], task[2], "finish_call", finish_call(call))
            for task, call in zip((first, second), calls)
        ]
    )
    async with pool.acquire() as conn:
        flags = await conn.fetch(
            "SELECT concurrent FROM research.ai_usage_calls WHERE task_id=ANY($1::uuid[])",
            [first[0], second[0]],
        )
    assert len(flags) == 2 and all(row["concurrent"] for row in flags)


@pytest.mark.asyncio
async def test_real_worker_can_record_global_usage_without_api_global_write(usage_db):
    admin_pool, users, tenants, tasks = usage_db
    task_id = uuid4()
    tasks.append(task_id)
    start = {
        "id": str(task_id),
        "taskType": "DAILY_RESEARCH",
        "taskName": "全局公开研究",
        "startedAt": datetime.now(timezone.utc) - timedelta(minutes=2),
    }
    async with tenant_transaction(admin_pool, str(users[0]), str(tenants[0])) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO research.ai_usage_tasks(id,task_type,task_name,started_at) VALUES($1,'DAILY_RESEARCH','forbidden',now())",
                    task_id,
                )
    worker_pool = await create_pool(
        isolated_dsn(), runtime_role="wealthsail_worker", min_size=1, max_size=1
    )
    try:
        await repository.persist(worker_pool, None, None, "start_task", start)
        call = {
            "id": str(uuid4()),
            "taskId": str(task_id),
            "model": "fixture-model",
            "callType": "research",
            "startedAt": datetime.now(timezone.utc) - timedelta(minutes=1),
        }
        await repository.persist(worker_pool, None, None, "start_call", call)
        await repository.persist(
            worker_pool,
            None,
            None,
            "finish_call",
            finish_call(call, "isolated-global-account"),
        )
        await repository.persist(
            worker_pool,
            None,
            None,
            "finish_task",
            {
                "id": str(task_id),
                "finishedAt": datetime.now(timezone.utc),
                "businessStatus": "SUCCEEDED",
            },
        )
    finally:
        await worker_pool.close()
    async with admin_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT t.business_status,c.status FROM research.ai_usage_tasks t JOIN research.ai_usage_calls c ON c.task_id=t.id WHERE t.id=$1",
            task_id,
        )
    assert row["business_status"] == "SUCCEEDED" and row["status"] == "SUCCEEDED"
