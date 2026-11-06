"""Owned loopback PostgreSQL: live progress uses the existing ledger RLS."""

from contextlib import asynccontextmanager
import json
import os
from uuid import UUID, uuid4

import pytest

from backend.ai_usage import repository
from backend.common.database import apply_tenant_context
from backend.integrations.research_execution import ExecutionObserver
from tests.unit.test_opportunity_fund_cache_database import fixture

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("OPPORTUNITY_CACHE_TEST_DATABASE_URL"),
        reason="owned104 database required",
    ),
]


def patch_connection(monkeypatch, conn):
    @asynccontextmanager
    async def connect(*_):
        yield conn

    monkeypatch.setattr(repository, "system_connection", connect)
    monkeypatch.setattr(repository, "tenant_transaction", connect)


async def seed(conn, run, now, tenant=None, actor=None):
    task, call = uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO research.ai_usage_tasks(id,tenant_id,user_id,task_type,task_name,started_at) VALUES($1,$2,$3,'daily_research','progress fixture',$4)",
        task,
        tenant,
        actor,
        now,
    )
    await conn.execute(
        "INSERT INTO research.ai_usage_calls(id,task_id,model,call_type,started_at,details) VALUES($1,$2,'codex','research',$3,$4::jsonb)",
        call,
        task,
        now,
        json.dumps({"researchRunId": str(run), "before": {"status": "UNAVAILABLE"}}),
    )
    observer = ExecutionObserver()
    observer.feed(b'{"type":"thread.started"}\n')
    return {
        "id": str(call),
        "taskId": str(task),
        "runId": str(run),
        "progress": observer.progress(),
    }


async def test_bound_live_progress_preserves_quota_and_survives_terminal_merge(
    monkeypatch,
):
    async with fixture() as (conn, run, now, *_):
        patch_connection(monkeypatch, conn)
        data = await seed(conn, run, now)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await repository.persist(
            object(),
            None,
            None,
            "call_progress",
            {**data, "progress": {**data["progress"], "raw": "PRIVATE"}},
        )
        row = await conn.fetchrow(
            "SELECT * FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
        )
        saved = json.loads(row["details"])
        assert saved["researchProgress"]["phase"] == "MODEL_RUNNING"
        assert (
            saved["before"] == {"status": "UNAVAILABLE"}
            and "PRIVATE" not in row["details"]
        )
        await repository.persist(
            object(),
            None,
            None,
            "finish_call",
            {
                "id": data["id"],
                "finishedAt": now,
                "status": "SUCCEEDED",
                "model": "codex",
                "before": {},
                "after": {},
                "tokens": {"inputTokens": None},
            },
        )
        row = await conn.fetchrow(
            "SELECT * FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
        )
        assert repository.call_dto(row)["researchProgress"]["phase"] == "MODEL_RUNNING"
        assert repository.call_dto(row)["inputTokens"] is None


@pytest.mark.parametrize("mismatch", ["run", "task", "terminal"])
async def test_wrong_or_terminal_call_never_accepts_progress(monkeypatch, mismatch):
    async with fixture() as (conn, run, now, *_):
        patch_connection(monkeypatch, conn)
        data = await seed(conn, run, now)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await repository.persist(object(), None, None, "call_progress", data)
        original = await conn.fetchval(
            "SELECT details FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
        )
        assert "researchProgress" in original
        data["progress"] = {
            **data["progress"],
            "phase": "TURN_FAILED",
            "elapsedMs": data["progress"]["elapsedMs"] + 1,
        }
        if mismatch == "terminal":
            await conn.execute(
                "UPDATE research.ai_usage_calls SET status='UNCERTAIN',finished_at=$2 WHERE id=$1",
                UUID(data["id"]),
                now,
            )
        elif mismatch == "run":
            data["runId"] = str(uuid4())
        else:
            data["taskId"] = str(uuid4())
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await repository.persist(object(), None, None, "call_progress", data)
        details = await conn.fetchval(
            "SELECT details FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
        )
        assert details == original


async def test_progress_remains_private_at_tenant_rls_boundary(monkeypatch):
    async with fixture() as (conn, run, now, tenants, actors, *_):
        patch_connection(monkeypatch, conn)
        data = await seed(conn, run, now, tenants[0], actors[0])
        await apply_tenant_context(conn, str(actors[1]), str(tenants[1]))
        await repository.persist(
            object(), str(tenants[1]), str(actors[1]), "call_progress", data
        )
        assert (
            await conn.fetchrow(
                "SELECT * FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
            )
            is None
        )
        await conn.execute("RESET ROLE")
        assert "researchProgress" not in await conn.fetchval(
            "SELECT details FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
        )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        await repository.persist(
            object(), str(tenants[0]), str(actors[0]), "call_progress", data
        )
        assert "researchProgress" in await conn.fetchval(
            "SELECT details FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
        )


async def test_progress_cannot_move_to_a_different_observer_or_older_event(monkeypatch):
    async with fixture() as (conn, run, now, *_):
        patch_connection(monkeypatch, conn)
        data = await seed(conn, run, now)
        data["progress"]["elapsedMs"] = 100
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await repository.persist(object(), None, None, "call_progress", data)
        original = await conn.fetchval(
            "SELECT details FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
        )
        for changed in (
            {"elapsedMs": 99},
            {"startedAt": "2020-01-01T00:00:00+00:00", "elapsedMs": 101},
        ):
            await repository.persist(
                object(),
                None,
                None,
                "call_progress",
                {**data, "progress": {**data["progress"], **changed}},
            )
            assert (
                await conn.fetchval(
                    "SELECT details FROM research.ai_usage_calls WHERE id=$1",
                    UUID(data["id"]),
                )
                == original
            )


async def test_terminal_write_does_not_attach_progress_for_a_different_bound_run(
    monkeypatch,
):
    async with fixture() as (conn, run, now, *_):
        patch_connection(monkeypatch, conn)
        data = await seed(conn, run, now)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await repository.persist(
            object(),
            None,
            None,
            "finish_call",
            {
                "id": data["id"],
                "finishedAt": now,
                "status": "SUCCEEDED",
                "model": "codex",
                "before": {},
                "after": {},
                "tokens": {},
                "researchRunId": str(uuid4()),
                "researchProgress": data["progress"],
            },
        )
        saved = await conn.fetchval(
            "SELECT details FROM research.ai_usage_calls WHERE id=$1", UUID(data["id"])
        )
        assert "researchProgress" not in saved
