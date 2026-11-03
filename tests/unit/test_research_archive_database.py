"""Disposable PostgreSQL coverage for immutable queue bundles and fenced recovery."""

import asyncio
from datetime import timedelta
import json

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.errors import ValidationError_
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
        await conn.execute(
            "UPDATE research.research_validation_archive_queue SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1",
            run_id,
        )
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
            "UPDATE research.research_validation_archive_queue SET status='ARCHIVING',attempts=8,lease_token=gen_random_uuid(),lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1",
            run_id,
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
            "UPDATE research.research_validation_archive_queue SET status='ARCHIVING',attempts=8,lease_token=gen_random_uuid(),lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1",
            exhausted,
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


@pytest.mark.parametrize("operation", ["persist", "finish"])
async def test_expired_database_lease_cannot_be_revived_by_reusing_an_old_app_stamp(
    operation,
):
    async with fixture() as (conn, run_id, now, *_):
        await enqueue(conn, run_id, now)
        claim = await repo.claim_research_archive(conn, now, run_id=run_id)
        stale_stamp = await conn.fetchval(
            "SELECT clock_timestamp()-interval '10 minutes'"
        )
        await conn.execute(
            "UPDATE research.research_validation_archive_queue SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1",
            run_id,
        )
        if operation == "persist":
            with pytest.raises(ValidationError_, match="租约已失效"):
                async with conn.transaction():
                    await repo.persist_research_archive(conn, claim, stale_stamp)
        else:
            assert (
                await repo.finish_research_archive(
                    conn, claim, stale_stamp, status="ARCHIVED"
                )
                is None
            )
        assert (
            await conn.fetchval(
                "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
            == "ARCHIVING"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.research_validation_artifacts WHERE run_id=$1",
                run_id,
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.research_validation_records WHERE run_id=$1",
                run_id,
            )
            == 0
        )


async def test_lease_expiring_during_archive_write_rolls_back_artifacts_and_receipt(
    monkeypatch,
):
    async with fixture() as (conn, run_id, now, *_):
        await enqueue(conn, run_id, now)
        claim = await repo.claim_research_archive(conn, now, run_id=run_id)
        await conn.execute(
            "UPDATE research.research_validation_archive_queue SET lease_expires_at=clock_timestamp()+interval '120 seconds' WHERE run_id=$1",
            run_id,
        )
        stale_stamp = await conn.fetchval(
            "SELECT clock_timestamp()-interval '10 minutes'"
        )
        original_save = repo._save_frozen_artifacts

        async def expire_after_write(*args):
            result = await original_save(*args)
            await conn.execute(
                "UPDATE research.research_validation_archive_queue SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1",
                run_id,
            )
            return result

        monkeypatch.setattr(repo, "_save_frozen_artifacts", expire_after_write)
        with pytest.raises(ValidationError_, match="租约已失效"):
            async with conn.transaction():
                await repo.persist_research_archive(conn, claim, stale_stamp)
        for table in ("research_validation_artifacts", "research_validation_records"):
            assert (
                await conn.fetchval(
                    f"SELECT count(*) FROM research.{table} WHERE run_id=$1", run_id
                )
                == 0
            )
        assert (
            await conn.fetchval(
                "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
            == "ARCHIVING"
        )


async def test_claim_and_retry_deadlines_ignore_future_application_clock():
    async with fixture() as (conn, run_id, now, *_):
        await enqueue(conn, run_id, now)
        before = await conn.fetchval("SELECT clock_timestamp()")
        claim = await repo.claim_research_archive(conn, now, run_id=run_id)
        assert (
            before + timedelta(seconds=119)
            <= claim["lease_expires_at"]
            <= before + timedelta(seconds=125)
        )
        assert (
            await repo.claim_research_archive(
                conn, now + timedelta(days=1), run_id=run_id
            )
            is None
        )
        pending = await repo.finish_research_archive(
            conn, claim, now, status="AUDIT_PENDING"
        )
        assert pending["status"] == "AUDIT_PENDING"
        assert (
            await repo.claim_research_archive(
                conn, now + timedelta(days=2), run_id=run_id
            )
            is None
        )


@pytest.mark.parametrize("operation", ["persist", "finish"])
async def test_lease_is_rechecked_after_waiting_for_the_queue_row_lock(operation):
    from tests.unit.test_manual_research_database import (
        fixture as committed_fixture,
        make_run,
        NOW,
    )

    async with committed_fixture(committed=True) as (conn, tenant, actor, _other):
        _, _, run_id = await make_run(conn, tenant, actor)
        await enqueue(conn, run_id, NOW)
        claim = await repo.claim_research_archive(conn, run_id=run_id)
        stale_stamp = await conn.fetchval(
            "SELECT clock_timestamp()-interval '10 minutes'"
        )
        blocker, worker = await asyncpg.connect(DSN), await asyncpg.connect(DSN)
        pending = None
        try:
            blocking_tx = blocker.transaction()
            await blocking_tx.start()
            await blocker.fetchrow(
                "SELECT run_id FROM research.research_validation_archive_queue WHERE run_id=$1 FOR UPDATE",
                run_id,
            )

            async def attempt():
                try:
                    async with worker.transaction():
                        await worker.execute("SET LOCAL ROLE wealthsail_worker")
                        if operation == "persist":
                            return await repo.persist_research_archive(
                                worker, claim, stale_stamp
                            )
                        return await repo.finish_research_archive(
                            worker, claim, stale_stamp, status="ARCHIVED"
                        )
                except ValidationError_:
                    return None

            pending = asyncio.create_task(attempt())

            async def wait_for_lock():
                while (
                    await blocker.fetchval(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=$1",
                        worker.get_server_pid(),
                    )
                    != "Lock"
                ):
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_lock(), 2)
            await blocker.execute(
                "UPDATE research.research_validation_archive_queue SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1",
                run_id,
            )
            await blocking_tx.commit()
            assert await asyncio.wait_for(pending, 2) is None
            assert (
                await conn.fetchval(
                    "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                    run_id,
                )
                == "ARCHIVING"
            )
            for table in (
                "research_validation_artifacts",
                "research_validation_records",
            ):
                assert (
                    await conn.fetchval(
                        f"SELECT count(*) FROM research.{table} WHERE run_id=$1", run_id
                    )
                    == 0
                )
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await worker.close()
            await blocker.close()
