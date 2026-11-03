"""Real role-bound admission budgets and non-blocking cross-process locks."""

import asyncio
from datetime import timedelta
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.errors import DataUnavailableError, RateLimitedError
from backend.macro import research_acceptance_repository as repo
from backend.macro.research_acceptance_replay import OfflineReplayError
from tests.unit.test_research_acceptance_database import fixture, initial, DSN
from tests.unit.test_research_acceptance_database import outcome

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned acceptance DB required"),
]


async def test_failed_attempts_consume_actor_and_run_budgets_but_same_key_is_free(
    monkeypatch,
):
    runner = AsyncMock(side_effect=OfflineReplayError("TIMEOUT"))
    monkeypatch.setattr(
        "backend.macro.research_acceptance_replay.run_offline_validation", runner
    )
    async with fixture() as (conn, run_id, now, tenants, actors, viewer):
        await initial(conn, run_id, now)
        await conn.execute(
            "UPDATE identity.tenant_members SET role='OWNER' WHERE tenant_id=$1 AND user_id=$2",
            tenants[0],
            viewer,
        )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        key = uuid4()
        for index in range(10):
            result = await repo.replay_validation(
                conn, run_id, actors[0], tenants[0], key if index == 0 else uuid4(), now
            )
            assert isinstance(result.error, DataUnavailableError)
        assert runner.await_count == 10
        same = await repo.replay_validation(
            conn, run_id, actors[0], tenants[0], key, now + timedelta(days=1)
        )
        assert isinstance(same.error, DataUnavailableError) and runner.await_count == 10
        limited = await repo.replay_validation(
            conn, run_id, actors[0], tenants[0], uuid4(), now
        )
        assert isinstance(limited.error, RateLimitedError) and "每人10次" in str(
            limited.error
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.research_validation_replay_attempts WHERE run_id=$1",
                run_id,
            )
            == 10
        )
        await apply_tenant_context(conn, str(actors[1]), str(tenants[1]))
        for _ in range(10):
            assert isinstance(
                (
                    await repo.replay_validation(
                        conn, run_id, actors[1], tenants[1], uuid4(), now
                    )
                ).error,
                DataUnavailableError,
            )
        await apply_tenant_context(conn, str(viewer), str(tenants[0]))
        limited = await repo.replay_validation(
            conn, run_id, viewer, tenants[0], uuid4(), now
        )
        assert isinstance(limited.error, RateLimitedError) and "20 MiB" in str(
            limited.error
        )
        assert runner.await_count == 20


@pytest.mark.parametrize("lock_kind", ["actor", "run"])
async def test_try_advisory_locks_reject_concurrent_work_without_waiting(lock_kind):
    async with fixture() as (conn, run_id, now, tenants, actors, _viewer):
        await initial(conn, run_id, now)
        other = await asyncpg.connect(DSN)
        try:
            async with other.transaction():
                identity = actors[0] if lock_kind == "actor" else run_id
                await other.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                    f"acceptance:{lock_kind}:{identity}",
                )
                await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
                result = await asyncio.wait_for(
                    repo.replay_validation(
                        conn, run_id, actors[0], tenants[0], uuid4(), now
                    ),
                    1,
                )
                assert isinstance(result.error, RateLimitedError)
                assert "正在执行" in str(result.error)
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM research.research_validation_replay_attempts WHERE run_id=$1",
                        run_id,
                    )
                    == 0
                )
        finally:
            await other.close()


async def test_api_cannot_insert_quota_rows_directly_or_use_a_different_actor():
    async with fixture() as (conn, run_id, now, tenants, actors, viewer):
        await initial(conn, run_id, now)
        await apply_tenant_context(conn, str(viewer), str(tenants[0]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval(
                    "SELECT research.reserve_validation_replay($1,$2,NULL)",
                    run_id,
                    uuid4(),
                )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO research.research_validation_replay_attempts(run_id,actor_id,actor_tenant_id,command_key,status) VALUES($1,$2,$3,$4,'RUNNING')",
                    run_id,
                    actors[1],
                    tenants[1],
                    uuid4(),
                )
        row = json.loads(
            await conn.fetchval(
                "SELECT research.reserve_validation_replay($1,$2,$3)",
                run_id,
                uuid4(),
                "a" * 64,
            )
        )
        assert row["actor_id"] == str(actors[0]) and row["actor_tenant_id"] == str(
            tenants[0]
        )


async def test_migration_readback_rejects_private_pending_bundle_select_grant():
    from scripts.migrate import _readback

    async with fixture() as (conn, *_):
        await conn.execute(
            "GRANT SELECT(frozen_bundle) ON research.research_validation_archive_queue TO wealthsail_api"
        )
        with pytest.raises(RuntimeError, match="acceptance"):
            await _readback(conn)


@pytest.mark.parametrize("archive_state", ["AUDIT_PENDING", "ARCHIVING", "UNRECOVERABLE"])
async def test_replay_waits_for_archive_and_does_not_charge_or_start_child(monkeypatch, archive_state):
    async with fixture() as (conn, run_id, now, tenants, actors, _viewer):
        await repo.enqueue_research_archive(conn, run_id, {}, "{}", {}, now, outcome(run_id, now), recorded_at=now)
        if archive_state == "ARCHIVING":
            await repo.claim_research_archive(conn, now, run_id=run_id)
        elif archive_state == "UNRECOVERABLE":
            await conn.execute("UPDATE research.research_validation_archive_queue SET status='UNRECOVERABLE' WHERE run_id=$1", run_id)
        runner = AsyncMock(return_value=outcome(run_id, now))
        monkeypatch.setattr("backend.macro.research_acceptance_replay.run_offline_validation", runner)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        result = await repo.replay_validation(conn, run_id, actors[0], tenants[0], uuid4(), now)
        assert isinstance(result, repo.ReplayFailure) and isinstance(result.error, DataUnavailableError)
        assert str(result.error) == "验收归档待恢复，暂不能离线重放"
        assert await conn.fetchval("SELECT count(*) FROM research.research_validation_replay_attempts WHERE run_id=$1", run_id) == 0
        runner.assert_not_awaited()
