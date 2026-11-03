"""Disposable PostgreSQL: derived repairs never mutate original failed research."""

from contextlib import asynccontextmanager
from datetime import timedelta
from importlib import import_module
import json
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.macro import research_acceptance_repository as acceptance
from tests.unit.test_research_acceptance_database import outcome
from tests.unit.test_manual_research_database import make_run
from tests.unit.test_research_history_repair_repository import result

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned repair101 DB required"),
]


@asynccontextmanager
async def fixture(*, manual=True, committed=False):
    target = urlsplit(DSN)
    assert target.hostname in {"127.0.0.1", "localhost", "::1"} and target.port == 29034
    assert target.path in {
        "/wealthsail_acceptance_repair101_v1",
        "/wealthsail_acceptance_repair101_v2",
    }
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    tenants, actors, viewer = [uuid4(), uuid4()], [uuid4(), uuid4(), uuid4()], uuid4()
    now = await conn.fetchval("SELECT clock_timestamp()-interval '2 days'")
    try:
        for tenant in tenants:
            await conn.execute(
                "INSERT INTO identity.tenants(id,name) VALUES($1,'history repair fixture')",
                tenant,
            )
        for actor, tenant, role in [
            (actors[0], tenants[0], "OWNER"),
            (actors[1], tenants[0], "ADMIN"),
            (actors[2], tenants[1], "OWNER"),
            (viewer, tenants[0], "VIEWER"),
        ]:
            await conn.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'history repair fixture')",
                actor,
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,$3)",
                tenant,
                actor,
                role,
            )
        if manual:
            _, _, run_id = await make_run(conn, tenants[0], actors[0], now=now)
        else:
            run_id = await conn.fetchval(
                "INSERT INTO research.daily_research_runs(slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet) VALUES(($1 AT TIME ZONE 'Asia/Shanghai')::date,$1,$1,'repair-fixture','repair-fixture','{}') RETURNING run_id",
                now,
            )
        await acceptance.enqueue_research_archive(
            conn,
            run_id,
            {"asOf": now.isoformat()},
            {"original": "frozen"},
            {},
            now,
            outcome(run_id, now),
            recorded_at=now,
        )
        claim = await acceptance.claim_research_archive(conn, run_id=run_id)
        await acceptance.persist_research_archive(conn, claim)
        await conn.execute(
            "INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class) VALUES($1,'FAILED',$2,'ResearchValidationError')",
            run_id,
            now,
        )
        if committed:
            await tx.commit()
        yield conn, run_id, now, tenants, actors, viewer
    finally:
        if not committed:
            await tx.rollback()
        else:
            await conn.execute("RESET ROLE")
            await conn.execute(
                "DELETE FROM research.research_history_repair_events WHERE derivation_id IN(SELECT id FROM research.research_history_repair_derivations WHERE source_run_id=$1)",
                run_id,
            )
            await conn.execute(
                "WITH removed_derivations AS (DELETE FROM research.research_history_repair_derivations WHERE source_run_id=$1),removed_attempts AS (DELETE FROM research.research_history_repair_attempts WHERE source_run_id=$1) SELECT 1",
                run_id,
            )
            for table in (
                "research_validation_replay_attempts",
                "research_validation_archive_queue",
                "research_validation_records",
                "research_validation_artifacts",
                "daily_research_results",
            ):
                await conn.execute(
                    f"DELETE FROM research.{table} WHERE run_id=$1", run_id
                )
            await conn.execute(
                "UPDATE research.daily_research_refresh_requests SET status='FAILED',completed_at=GREATEST(requested_at,started_at),batch_id=NULL,report_run_id=NULL WHERE tenant_id=ANY($1::uuid[])",
                tenants,
            )
            await conn.execute(
                "DELETE FROM research.daily_research_runs WHERE run_id=$1", run_id
            )
            await conn.execute(
                "DELETE FROM market.alpha_public_batches WHERE debug_request_id IN(SELECT id FROM research.daily_research_refresh_requests WHERE tenant_id=ANY($1::uuid[]))",
                tenants,
            )
            await conn.execute(
                "DELETE FROM research.daily_research_refresh_commands WHERE tenant_id=ANY($1::uuid[])",
                tenants,
            )
            await conn.execute(
                "DELETE FROM research.daily_research_refresh_requests WHERE tenant_id=ANY($1::uuid[])",
                tenants,
            )
            await conn.execute(
                "DELETE FROM identity.tenant_members WHERE tenant_id=ANY($1::uuid[])",
                tenants,
            )
            await conn.execute(
                "DELETE FROM identity.tenants WHERE id=ANY($1::uuid[])", tenants
            )
            await conn.execute(
                "DELETE FROM identity.profiles WHERE id=ANY($1::uuid[])",
                [*actors, viewer],
            )
        await conn.close()


def repository():
    return import_module("backend.macro.research_history_repair_repository")


def fake_runner(monkeypatch, run_id, now, *, blocked=False):
    async def repair(payload):
        value = result(run_id)
        value["validatedAt"] = now.isoformat()
        value["originalOutcome"]["validatedAt"] = now.isoformat()
        if blocked:
            value.update(safetyBlocked=True, historicalPublishAllowed=False)
        return value

    monkeypatch.setattr(
        "backend.macro.research_acceptance_replay.run_offline_repair", repair
    )


async def test_101_tables_have_force_rls_and_no_direct_runtime_write_or_private_read():
    async with fixture() as (conn, *_):
        for table in (
            "research_history_repair_derivations",
            "research_history_repair_events",
            "research_history_repair_attempts",
        ):
            row = await conn.fetchrow(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass($1)",
                "research." + table,
            )
            assert row and row["relrowsecurity"] and row["relforcerowsecurity"]
            for role in ("wealthsail_api", "wealthsail_worker"):
                for privilege in ("INSERT", "UPDATE", "DELETE"):
                    assert not await conn.fetchval(
                        "SELECT has_table_privilege($1,$2,$3)",
                        role,
                        "research." + table,
                        privilege,
                    )
        assert not await conn.fetchval(
            "SELECT has_column_privilege('wealthsail_api','research.research_history_repair_derivations','private_result','SELECT')"
        )


async def test_independent_review_and_explicit_tenant_history_publish_preserve_original(
    monkeypatch,
):
    repo = repository()
    async with fixture() as (conn, run_id, now, tenants, actors, viewer):
        fake_runner(monkeypatch, run_id, now)
        original = await conn.fetchval(
            "SELECT outcome FROM research.research_validation_records WHERE run_id=$1 AND kind='INITIAL'",
            run_id,
        )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        key = uuid4()
        draft = await repo.generate_history_repair(
            conn, run_id, actors[0], tenants[0], key, now + timedelta(days=2)
        )
        same = await repo.generate_history_repair(
            conn, run_id, actors[0], tenants[0], key, now + timedelta(days=2)
        )
        assert draft["id"] == same["id"] and draft["state"] == "DRAFT"
        assert draft["originalOutcome"]["decision"] == "PARTIAL"
        with pytest.raises(Exception) as denied:
            async with conn.transaction():
                await repo.review_history_repair(
                    conn,
                    UUID(draft["id"]),
                    actors[0],
                    tenants[0],
                    uuid4(),
                    "APPROVE",
                    now,
                )
        assert "独立" in str(denied.value) or "creator" in str(denied.value).lower()
        await apply_tenant_context(conn, str(viewer), str(tenants[0]))
        assert (
            await repo.read_history_repair(
                conn, UUID(draft["id"]), now + timedelta(days=3)
            )
            is None
        )
        await apply_tenant_context(conn, str(actors[1]), str(tenants[0]))
        approved = await repo.review_history_repair(
            conn, UUID(draft["id"]), actors[1], tenants[0], uuid4(), "APPROVE", now
        )
        assert approved["state"] == "APPROVED"
        published = await repo.publish_history_repair(
            conn, UUID(draft["id"]), actors[1], tenants[0], uuid4(), now
        )
        assert published["state"] == "PUBLISHED" and published["actionable"] is False
        await apply_tenant_context(conn, str(viewer), str(tenants[0]))
        public = await repo.read_history_repair(
            conn, UUID(draft["id"]), now + timedelta(days=3)
        )
        assert public["currentFreshness"] == "EXPIRED" and "PRIVATE" not in str(public)
        await apply_tenant_context(conn, str(actors[2]), str(tenants[1]))
        assert (
            await repo.read_history_repair(
                conn, UUID(draft["id"]), now + timedelta(days=3), admin=True
            )
            is None
        )
        await conn.execute("RESET ROLE")
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_results WHERE run_id=$1",
                run_id,
            )
            == "FAILED"
        )
        assert (
            await conn.fetchval(
                "SELECT outcome FROM research.research_validation_records WHERE run_id=$1 AND kind='INITIAL'",
                run_id,
            )
            == original
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.research_validation_records WHERE run_id=$1",
                run_id,
            )
            == 1
        )


async def test_hard_risk_block_cannot_be_overridden_by_independent_approval(
    monkeypatch,
):
    repo = repository()
    async with fixture() as (conn, run_id, now, tenants, actors, _viewer):
        fake_runner(monkeypatch, run_id, now, blocked=True)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        draft = await repo.generate_history_repair(
            conn, run_id, actors[0], tenants[0], uuid4(), now
        )
        assert draft["canReview"] is False
        assert draft["canReject"] is False
        await apply_tenant_context(conn, str(actors[1]), str(tenants[0]))
        reviewer_draft = await repo.read_history_repair(
            conn, UUID(draft["id"]), now, admin=True
        )
        assert reviewer_draft["canReview"] is False
        assert reviewer_draft["canReject"] is True
        with pytest.raises(Exception):
            async with conn.transaction():
                await repo.review_history_repair(
                    conn,
                    UUID(draft["id"]),
                    actors[1],
                    tenants[0],
                    uuid4(),
                    "APPROVE",
                    now,
                )
        rejected = await repo.review_history_repair(
            conn, UUID(draft["id"]), actors[1], tenants[0], uuid4(), "REJECT", now
        )
        assert rejected["state"] == "REJECTED"
        assert rejected["canReview"] is False and rejected["canReject"] is False
        assert rejected["canPublish"] is False


async def test_101_readback_rejects_direct_private_runtime_select():
    from scripts.migrate import _readback

    async with fixture() as (conn, *_):
        await conn.execute(
            "GRANT SELECT(private_result) ON research.research_history_repair_derivations TO wealthsail_api"
        )
        with pytest.raises(RuntimeError, match="history repair"):
            await _readback(conn)


async def test_true_readonly_preview_leaves_no_attempt_or_derived_record(monkeypatch):
    repo = repository()
    async with fixture(committed=True) as (
        owner,
        run_id,
        now,
        tenants,
        actors,
        _viewer,
    ):
        fake_runner(monkeypatch, run_id, now)
        reader = await asyncpg.connect(DSN)
        try:
            async with reader.transaction(readonly=True):
                await apply_tenant_context(reader, str(actors[0]), str(tenants[0]))
                assert await reader.fetchval("SHOW transaction_read_only") == "on"
                preview = await repo.preview_history_repair(
                    reader, run_id, actors[0], tenants[0], now
                )
                assert preview["state"] == "PREVIEW" and "id" not in preview
                assert preview["validatedAt"] == now.isoformat()
            for table in (
                "research_history_repair_attempts",
                "research_history_repair_derivations",
            ):
                assert (
                    await owner.fetchval(
                        f"SELECT count(*) FROM research.{table} WHERE source_run_id=$1",
                        run_id,
                    )
                    == 0
                )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM research.research_validation_records WHERE run_id=$1",
                    run_id,
                )
                == 1
            )
        finally:
            await reader.close()


async def test_two_global_repair_slots_are_cross_process_and_readonly():
    async with fixture(committed=True) as (
        _owner,
        run_id,
        _now,
        tenants,
        actors,
        _viewer,
    ):
        connections = [await asyncpg.connect(DSN) for _ in range(3)]
        transactions = [conn.transaction(readonly=True) for conn in connections]
        try:
            for index, (conn, tx) in enumerate(zip(connections, transactions)):
                await tx.start()
                await apply_tenant_context(
                    conn,
                    str(actors[index]),
                    str(tenants[0] if index < 2 else tenants[1]),
                )
            assert (
                await connections[0].fetchval(
                    "SELECT research.try_history_repair_locks($1)", run_id
                )
                is True
            )
            assert (
                await connections[1].fetchval(
                    "SELECT research.try_history_repair_locks($1)", uuid4()
                )
                is True
            )
            assert (
                await connections[2].fetchval(
                    "SELECT research.try_history_repair_locks($1)", uuid4()
                )
                is False
            )
            await transactions[0].rollback()
            assert (
                await connections[2].fetchval(
                    "SELECT research.try_history_repair_locks($1)", uuid4()
                )
                is True
            )
        finally:
            for conn in connections:
                await conn.close()


async def test_repair_failures_and_old_replays_share_total_byte_budget_and_same_key_is_free(
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from backend.errors import RateLimitedError
    from backend.macro.research_acceptance_replay import OfflineReplayError

    repo = repository()
    runner = AsyncMock(side_effect=OfflineReplayError("TIMEOUT"))
    monkeypatch.setattr(
        "backend.macro.research_acceptance_replay.run_offline_repair", runner
    )
    async with fixture() as (conn, run_id, now, tenants, actors, _viewer):
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        first = json.loads(
            await conn.fetchval(
                "SELECT research.reserve_validation_replay($1,$2,NULL)", run_id, uuid4()
            )
        )
        assert first["status"] == "RUNNING"
        keys = [uuid4() for _ in range(4)]
        for key in keys:
            failure = await repo.generate_history_repair(
                conn, run_id, actors[0], tenants[0], key, now
            )
            assert isinstance(failure, repo.ReplayFailure)
        assert runner.await_count == 4
        await repo.generate_history_repair(
            conn, run_id, actors[0], tenants[0], keys[0], now
        )
        assert runner.await_count == 4
        limited = await repo.generate_history_repair(
            conn, run_id, actors[0], tenants[0], uuid4(), now
        )
        assert isinstance(limited.error, RateLimitedError) and "20 MiB" in str(
            limited.error
        )
        for _ in range(3):
            assert (
                json.loads(
                    await conn.fetchval(
                        "SELECT research.reserve_validation_replay($1,$2,NULL)",
                        run_id,
                        uuid4(),
                    )
                )["status"]
                == "RUNNING"
            )
        assert (
            json.loads(
                await conn.fetchval(
                    "SELECT research.reserve_validation_replay($1,$2,NULL)",
                    run_id,
                    uuid4(),
                )
            )["status"]
            == "RUN_LIMIT"
        )


async def test_actor_daily_quota_counts_both_kinds_of_offline_work():
    async with fixture() as (conn, run_id, _now, tenants, actors, _viewer):
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        for _ in range(10):
            reserved = json.loads(
                await conn.fetchval(
                    "SELECT research.reserve_validation_replay($1,$2,NULL)",
                    run_id,
                    uuid4(),
                )
            )
            assert reserved["status"] == "RUNNING"
        assert (
            json.loads(
                await conn.fetchval(
                    "SELECT research.reserve_history_repair($1,$2,NULL)",
                    run_id,
                    uuid4(),
                )
            )["status"]
            == "ACTOR_LIMIT"
        )


async def test_actual_jsonb_combined_candidate_and_receipt_size_cannot_exceed_four_mib():
    async with fixture() as (conn, run_id, now, tenants, actors, _viewer):
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        reserved = json.loads(
            await conn.fetchval(
                "SELECT research.reserve_history_repair($1,$2,NULL)", run_id, uuid4()
            )
        )
        private = result(run_id)
        private.update(validatedAt=now.isoformat(), padding="x" * 3_600_000)
        safe = {"sourceRunId": str(run_id), "padding": "x" * 700_000}
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.fetchval(
                    "SELECT research.save_history_repair($1,$2::jsonb,$3::jsonb,$4)",
                    UUID(reserved["id"]),
                    json.dumps(private),
                    json.dumps(safe),
                    "a" * 64,
                )


async def test_private_details_are_admin_only_and_public_summaries_never_carry_raw_prices(
    monkeypatch,
):
    from backend.errors import ForbiddenError

    repo = repository()
    async with fixture() as (conn, run_id, now, tenants, actors, viewer):
        fake_runner(monkeypatch, run_id, now)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        draft = await repo.generate_history_repair(
            conn, run_id, actors[0], tenants[0], uuid4(), now
        )
        assert "PRIVATE_PRICE" not in str(draft) and "PRIVATE_RAW" not in str(draft)
        details = await repo.read_history_repair_details(conn, UUID(draft["id"]), now)
        assert (
            details["privateResult"]["formalReport"]["valueDirections"][0]["plans"][0][
                "price"
            ]
            == "PRIVATE_PRICE"
        )
        assert details["provenance"]["initialRecordId"]
        await apply_tenant_context(conn, str(viewer), str(tenants[0]))
        with pytest.raises(ForbiddenError):
            async with conn.transaction():
                await repo.read_history_repair_details(conn, UUID(draft["id"]), now)
        await apply_tenant_context(conn, str(actors[2]), str(tenants[1]))
        assert (
            await repo.read_history_repair_details(conn, UUID(draft["id"]), now) is None
        )
