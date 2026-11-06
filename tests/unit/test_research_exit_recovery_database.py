"""Owned loopback PostgreSQL: exit proof changes only the bound guard column."""

from contextlib import asynccontextmanager
import os
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.integrations.research_lifecycle import ResearchLifecycle
from backend.jobs import research_exit_recovery as recovery
from tests.unit.test_opportunity_fund_cache_database import fixture

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("OPPORTUNITY_CACHE_TEST_DATABASE_URL"),
        reason="owned104 loopback DB required",
    ),
]


def patch_connection(monkeypatch, conn):
    state = {"borrowed": 0}

    @asynccontextmanager
    async def connect(_pool):
        state["borrowed"] += 1
        try:
            yield conn
        finally:
            state["borrowed"] -= 1

    monkeypatch.setattr(recovery, "system_connection", connect)
    return state


@pytest.mark.parametrize("confirmed", [False, True])
async def test_worker_recovers_only_matching_guard_and_never_rewrites_old_terminal(
    monkeypatch, confirmed
):
    async with fixture() as (conn, run, now, *_):
        owner = uuid4()
        await conn.execute(
            "INSERT INTO research.daily_research_model_guards(run_id,owner_token,acquired_at) VALUES($1,$2,$3)",
            run,
            owner,
            now,
        )
        await conn.execute(
            "INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class) VALUES($1,'UNCERTAIN',$2,'TimeoutError')",
            run,
            now,
        )
        original = dict(
            await conn.fetchrow(
                "SELECT * FROM research.daily_research_results WHERE run_id=$1", run
            )
        )
        state = patch_connection(monkeypatch, conn)

        async def status(_settings, identity):
            assert state["borrowed"] == 0
            assert identity == recovery.invocation_identity(run, owner)
            lifecycle = ResearchLifecycle(identity)
            lifecycle.exited(
                reaped=True, descendants_confirmed=confirmed, returncode=-9
            )
            return lifecycle.snapshot()

        monkeypatch.setattr(recovery.codex_client, "research_status", status)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        settings = SimpleNamespace(codex_runner_url="http://local-fixture.invalid")
        result = await recovery.recover_research_exits(object(), settings)
        assert result["released"] == int(confirmed)
        guard = await conn.fetchrow(
            "SELECT * FROM research.daily_research_model_guards WHERE run_id=$1", run
        )
        assert (guard["released_at"] is not None) is confirmed
        assert guard["owner_token"] == owner
        assert (
            dict(
                await conn.fetchrow(
                    "SELECT * FROM research.daily_research_results WHERE run_id=$1", run
                )
            )
            == original
        )
        if confirmed:
            assert (await recovery.recover_research_exits(object(), settings))[
                "checked"
            ] == 0


async def test_api_role_cannot_read_guard_owner_tokens(monkeypatch):
    async with fixture() as (conn, run, now, tenants, actors, *_):
        await conn.execute(
            "INSERT INTO research.daily_research_model_guards(run_id,owner_token,acquired_at) VALUES($1,$2,$3)",
            run,
            uuid4(),
            now,
        )
        patch_connection(monkeypatch, conn)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await recovery.recover_research_exits(
                    object(),
                    SimpleNamespace(codex_runner_url="http://local-fixture.invalid"),
                )
