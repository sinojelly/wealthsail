"""Disposable PostgreSQL coverage for immutable queue bundles and fenced recovery."""

import asyncio
from datetime import timedelta
import json

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.macro import research_acceptance_repository as repo
from tests.unit.test_research_acceptance_database import fixture, outcome, DSN

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned acceptance DB required"),
]


async def enqueue(conn, run_id, now):
    return await repo.enqueue_research_archive(
        conn,
        run_id,
        {"PRIVATE_PACKET": True},
        "PRIVATE_RAW",
        {},
        now,
        outcome(run_id, now),
        recorded_at=now,
    )


async def test_pending_receipt_is_tenant_safe_and_admin_can_inspect_original_frozen_materials():
    async with fixture(manual=True) as (conn, run_id, now, tenants, actors, viewer):
        await enqueue(conn, run_id, now)
        await apply_tenant_context(conn, str(viewer), str(tenants[0]))
        pending = await repo.read_validation_summary(conn, run_id, now)
        assert (
            pending["archiveState"] == "AUDIT_PENDING" and pending["canReplay"] is False
        )
        assert "PRIVATE" not in json.dumps(pending)
        assert "recoverableMaterials" not in pending
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetch(
                    "SELECT frozen_bundle FROM research.research_validation_archive_queue"
                )
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        detail = await repo.read_validation_summary(conn, run_id, now, admin=True)
        assert detail["recoverableMaterials"] == {kind: True for kind in repo.KINDS}
        raw = await repo.read_artifacts_admin(conn, run_id)
        assert raw["items"][1]["content"] == "PRIVATE_RAW"
        await apply_tenant_context(conn, str(actors[1]), str(tenants[1]))
        assert await repo.read_validation_summary(conn, run_id, now) is None
        assert (
            await conn.fetchval(
                "SELECT research.read_validation_artifacts_admin($1,true)", run_id
            )
            is None
        )


async def test_recovery_preserves_oversize_hash_and_immutable_bundle_and_fences_old_lease():
    async with fixture() as (conn, run_id, now, *_):
        await repo.enqueue_research_archive(
            conn,
            run_id,
            {},
            None,
            {},
            now,
            outcome(run_id, now),
            raw_metadata={
                "sha256": "c" * 64,
                "byteLength": 3_000_000,
                "omissionReason": "SIZE_LIMIT",
            },
            recorded_at=now,
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        first = await repo.claim_research_archive(conn, now, run_id=run_id)
        assert first["attempts"] == 1
        assert await repo.claim_research_archive(conn, now, run_id=run_id) is None
        second = await repo.claim_research_archive(
            conn, now + timedelta(seconds=121), run_id=run_id
        )
        assert second["attempts"] == 2
        assert (
            await repo.finish_research_archive(
                conn, first, now + timedelta(seconds=122), status="ARCHIVED"
            )
            is None
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE research.research_validation_archive_queue SET frozen_bundle='{}' WHERE run_id=$1",
                    run_id,
                )
        await repo.persist_research_archive(conn, second, now + timedelta(seconds=122))
        frozen = await conn.fetchval(
            "SELECT raw_output FROM research.research_validation_artifacts WHERE run_id=$1",
            run_id,
        )
        assert json.loads(frozen) == {
            "content": None,
            "sha256": "c" * 64,
            "byteLength": 3_000_000,
            "omissionReason": "SIZE_LIMIT",
        }
        assert (
            await conn.fetchval(
                "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
            == "ARCHIVED"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.research_validation_records WHERE run_id=$1",
                run_id,
            )
            == 1
        )


async def test_last_crashed_lease_is_explicitly_unrecoverable_and_queue_has_no_public_raw_grant():
    async with fixture() as (conn, run_id, now, *_):
        await enqueue(conn, run_id, now)
        await conn.execute(
            "UPDATE research.research_validation_archive_queue SET status='ARCHIVING',attempts=8,lease_token=gen_random_uuid(),lease_expires_at=$2 WHERE run_id=$1",
            run_id,
            now,
        )
        assert await repo.claim_research_archive(conn, now, run_id=run_id) is None
        assert (
            await conn.fetchval(
                "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
            == "UNRECOVERABLE"
        )
        assert await conn.fetchval(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='research.research_validation_archive_queue'::regclass"
        )
        for column in ("frozen_bundle",):
            assert not await conn.fetchval(
                "SELECT has_column_privilege('wealthsail_api','research.research_validation_archive_queue',$1,'SELECT')",
                column,
            )
        assert not await conn.fetchval(
            "SELECT has_column_privilege('wealthsail_worker','research.research_validation_archive_queue','frozen_bundle','UPDATE')"
        )


async def test_locked_exhausted_lease_does_not_block_claiming_another_run():
    from tests.unit.test_manual_research_database import (
        fixture as committed_fixture,
        make_run,
        NOW,
    )

    async with committed_fixture(committed=True) as (conn, tenant, actor, other):
        _, _, exhausted = await make_run(conn, tenant, actor, now=NOW)
        _, _, waiting = await make_run(conn, tenant, other, now=NOW)
        await enqueue(conn, exhausted, NOW)
        await enqueue(conn, waiting, NOW)
        await conn.execute(
            "UPDATE research.research_validation_archive_queue SET status='ARCHIVING',attempts=8,lease_token=gen_random_uuid(),lease_expires_at=$2 WHERE run_id=$1",
            exhausted,
            NOW,
        )
        blocker = await asyncpg.connect(DSN)
        try:
            async with blocker.transaction():
                await blocker.fetchrow(
                    "SELECT run_id FROM research.research_validation_archive_queue WHERE run_id=$1 FOR UPDATE",
                    exhausted,
                )
                async with conn.transaction():
                    await conn.execute("SET LOCAL ROLE wealthsail_worker")
                    claimed = await asyncio.wait_for(
                        repo.claim_research_archive(conn, NOW + timedelta(seconds=2)), 1
                    )
                    assert claimed["run_id"] == waiting
                    assert (
                        await conn.fetchval(
                            "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                            exhausted,
                        )
                        == "ARCHIVING"
                    )
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE wealthsail_worker")
                assert (
                    await repo.claim_research_archive(
                        conn, NOW + timedelta(seconds=3), run_id=exhausted
                    )
                    is None
                )
                assert (
                    await conn.fetchval(
                        "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                        exhausted,
                    )
                    == "UNRECOVERABLE"
                )
        finally:
            await blocker.close()
