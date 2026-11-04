"""Real PostgreSQL topic pagination, source scope and archive failure isolation."""

from datetime import timedelta
from importlib import import_module
import json
import os

import pytest

from backend.common.database import apply_tenant_context
from backend.errors import NotFoundError, ValidationError_
from backend.macro import research_acceptance_repository as acceptance
from tests.unit.test_research_history_repair_database import fixture
from tests.unit.test_research_topic_repository import registry

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
        reason="owned102 database required",
    ),
]


def repository():
    return import_module("backend.macro.research_topic_repository")


async def store(conn, run_id, now, count=30):
    value = registry(run_id, count)
    value["validatedAt"] = now.isoformat()
    return await repository().save_topic_registry(conn, run_id, value, now)


@pytest.mark.parametrize("limit", [12, 20, 30])
async def test_all_topics_are_saved_and_keyset_pages_have_no_duplicates(limit):
    async with fixture() as (conn, run_id, now, tenants, actors, _):
        snapshot = await store(conn, run_id, now)
        assert snapshot == await store(conn, run_id, now)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        items, cursor = [], None
        while True:
            page = await repository().read_topic_registry(
                conn, run_id, now, limit=limit, cursor=cursor
            )
            assert page["actionable"] is False and page["totalCount"] == 30
            assert len(page["items"]) <= limit
            items.extend(row["topicId"] for row in page["items"])
            cursor = page["nextCursor"]
            if not page["hasMore"]:
                break
        assert len(items) == len(set(items)) == 30
        assert "plans" not in json.dumps(page)
        assert page["coverage"]["countsByStatus"]["PENDING_REVIEW"] == 30


async def test_worker_writer_saves_all72_topics_and_filter_cannot_reuse_other_cursor():
    async with fixture() as (conn, run_id, now, tenants, actors, _):
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await store(conn, run_id, now, 72)
        await conn.execute("RESET ROLE")
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        first = await repository().read_topic_registry(conn, run_id, limit=30)
        assert first["totalCount"] == 72 and len(first["items"]) == 30
        with pytest.raises(ValidationError_):
            await repository().read_topic_registry(
                conn,
                run_id,
                limit=30,
                cursor=first["nextCursor"],
                status="PENDING_REVIEW",
            )


async def test_optional_verification_coverage_roundtrips_without_implying_execution():
    async with fixture() as (conn, run_id, now, tenants, actors, _):
        value = registry(run_id, 1)
        value["validatedAt"] = now.isoformat()
        measured = {
            "structureCompleteScopeCount": 10,
            "executionVerifiedScopeCount": 0,
            "evidenceVerifiedCheckCount": 7,
            "executionVerifiedCheckCount": 0,
            "strictDirectionCount": 1,
            "autoPublishBlockerCount": 2,
            "strictRevalidated": True,
            "autoPublishAllowed": False,
            "executionMatchStatus": "EXECUTION_UNMATCHED",
            "executionPlannedTaskCount": 50,
            "executionMatchedTaskCount": 49,
            "executionObservedQueryCount": 51,
            "executionUnmatchedQueryHashCount": 2,
            "executionPendingDirectionCount": 1,
            "executionReceiptTransportReason": "RECEIPT_SUMMARIZED_SIZE_LIMIT",
        }
        value["coverage"].update(measured)
        await repository().save_topic_registry(conn, run_id, value, now)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))

        page = await repository().read_topic_registry(conn, run_id)

        assert {key: page["coverage"][key] for key in measured} == measured
        assert page["coverage"]["searchExecutionVerified"] is False
        assert page["actionable"] is False


async def test_topics_keep_original_initial_and_run_result_and_reject_snapshot_overwrite():
    async with fixture() as (conn, run_id, now, *_):
        original = await conn.fetchval(
            "SELECT outcome FROM research.research_validation_records WHERE run_id=$1",
            run_id,
        )
        await store(conn, run_id, now)
        with pytest.raises(ValidationError_):
            async with conn.transaction():
                await store(conn, run_id, now, 12)
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_results WHERE run_id=$1",
                run_id,
            )
            == "FAILED"
        )
        assert (
            await conn.fetchval(
                "SELECT outcome FROM research.research_validation_records WHERE run_id=$1",
                run_id,
            )
            == original
        )


async def test_manual_topic_pages_are_tenant_scoped_but_scheduled_safe_pages_are_shared():
    for manual in (True, False):
        async with fixture(manual=manual) as (
            conn,
            run_id,
            now,
            tenants,
            actors,
            viewer,
        ):
            await store(conn, run_id, now)
            await apply_tenant_context(conn, str(viewer), str(tenants[0]))
            assert (await repository().read_topic_registry(conn, run_id))[
                "registryAvailable"
            ] is True
            await apply_tenant_context(conn, str(actors[2]), str(tenants[1]))
            if manual:
                with pytest.raises(NotFoundError):
                    await repository().read_topic_registry(conn, run_id)
            else:
                assert (await repository().read_topic_registry(conn, run_id))[
                    "totalCount"
                ] == 30


async def test_topic_tables_are_force_rls_and_only_controlled_worker_can_write():
    async with fixture() as (conn, *_):
        for name in ("research_topic_snapshots", "research_topic_entries"):
            assert await conn.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=$1::regclass",
                "research." + name,
            )
            for role in ("wealthsail_api", "wealthsail_worker"):
                assert not await conn.fetchval(
                    "SELECT has_table_privilege($1,$2,'INSERT') OR has_table_privilege($1,$2,'UPDATE') OR has_table_privilege($1,$2,'DELETE')",
                    role,
                    "research." + name,
                )
        assert not await conn.fetchval(
            "SELECT has_function_privilege('wealthsail_api','research.save_topic_registry(uuid,jsonb)','EXECUTE')"
        )


async def test_archive_registry_failure_rolls_back_artifacts_receipt_but_keeps_formal_result(
    monkeypatch,
):
    async with fixture() as (conn, run_id, now, *_):
        # Remove this fixture's completed audit, retaining its original terminal result.
        await conn.execute(
            "DELETE FROM research.research_validation_archive_queue WHERE run_id=$1",
            run_id,
        )
        await conn.execute(
            "DELETE FROM research.research_validation_records WHERE run_id=$1", run_id
        )
        await conn.execute(
            "DELETE FROM research.research_validation_artifacts WHERE run_id=$1", run_id
        )
        await conn.execute(
            "UPDATE research.daily_research_results SET status='READY',payload='{}',error_class=NULL WHERE run_id=$1",
            run_id,
        )
        value = registry(run_id, 12)
        value["validatedAt"] = now.isoformat()
        from tests.unit.test_research_acceptance_database import outcome

        await acceptance.enqueue_research_archive(
            conn,
            run_id,
            {"asOf": now.isoformat()},
            {"sources": []},
            {},
            now,
            outcome(run_id, now),
            topic_registry=value,
        )
        claim = await acceptance.claim_research_archive(conn, run_id=run_id)

        async def failed(*args):
            raise ValidationError_("topic storage failed")

        monkeypatch.setattr(repository(), "save_topic_registry", failed)
        with pytest.raises(ValidationError_):
            async with conn.transaction():
                await acceptance.persist_research_archive(conn, claim)
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_results WHERE run_id=$1",
                run_id,
            )
            == "READY"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.research_validation_records WHERE run_id=$1",
                run_id,
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


async def test_size_only_omission_still_archives_initial_and_does_not_revoke_ready():
    async with fixture() as (conn, run_id, now, tenants, actors, _):
        await conn.execute(
            "DELETE FROM research.research_validation_archive_queue WHERE run_id=$1",
            run_id,
        )
        await conn.execute(
            "DELETE FROM research.research_validation_records WHERE run_id=$1", run_id
        )
        await conn.execute(
            "DELETE FROM research.research_validation_artifacts WHERE run_id=$1", run_id
        )
        await conn.execute(
            "UPDATE research.daily_research_results SET status='READY',payload='{}',error_class=NULL WHERE run_id=$1",
            run_id,
        )
        value = registry(run_id, 1)
        value["validatedAt"] = now.isoformat()
        value["items"][0]["summary"] = "字" * 17000
        from tests.unit.test_research_acceptance_database import outcome

        await acceptance.enqueue_research_archive(
            conn,
            run_id,
            {"asOf": now.isoformat()},
            {"sources": []},
            {},
            now,
            outcome(run_id, now),
            topic_registry=value,
        )
        claim = await acceptance.claim_research_archive(conn, run_id=run_id)
        await acceptance.persist_research_archive(conn, claim)
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_results WHERE run_id=$1",
                run_id,
            )
            == "READY"
        )
        assert (
            await conn.fetchval(
                "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
            == "ARCHIVED"
        )
        summary = json.loads(
            await conn.fetchval(
                "SELECT summary FROM research.research_validation_records WHERE run_id=$1",
                run_id,
            )
        )
        assert summary["topicRegistryAvailable"] is False
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        page = await repository().read_topic_registry(conn, run_id)
        assert (
            page["registryAvailable"] is False
            and page["archiveState"] == "UNRECOVERABLE"
        )
        assert (
            page["canInspectArtifacts"] is True and "artifacts" in page["artifactsUrl"]
        )


async def test_pending_scopes_are_small_metadata_and_manual_scopes_do_not_leak_publicly():
    from backend.macro.research_topics import make_task

    async with fixture() as (conn, run_id, now, tenants, *_):
        value = registry(run_id, 1)
        value["validatedAt"] = now.isoformat()
        task = make_task(
            "TOPIC_SCOPE:" + "a" * 32,
            "FINANCING_COOLING",
            "新主题 融资",
            {
                "start": (now - timedelta(days=90)).date().isoformat(),
                "end": now.date().isoformat(),
                "lookbackDays": 90,
            },
            status="PENDING_REVIEW",
            reason_code="INDUSTRY_IDENTITY_PENDING",
        )
        task["searchTerm"] = "明确的新主题"
        value["items"][0]["pendingTasks"] = [task]
        await repository().save_topic_registry(conn, run_id, value, now)
        assert await repository().read_pending_topic_scopes(conn) == []
        pending = await repository().read_pending_topic_scopes(conn, tenants[0])
        assert pending == [
            {
                "industryId": task["industryId"],
                "sourceRunId": str(run_id),
                "window": task["window"],
                "taskId": task["taskId"],
                "reasonCode": "INDUSTRY_IDENTITY_PENDING",
                "searchTerm": "明确的新主题",
            }
        ]


async def test_topic_hash_provenance_rejects_mismatched_archive_descriptors():
    async with fixture() as (conn, run_id, now, *_):
        await conn.execute(
            "UPDATE research.research_validation_artifacts SET packet=jsonb_set(packet,'{sha256}',to_jsonb(repeat('0',64))) WHERE run_id=$1",
            run_id,
        )
        with pytest.raises(ValidationError_):
            async with conn.transaction():
                await store(conn, run_id, now)


async def test_pending_scope_selection_rotates_without_clipping_saved_metadata():
    from tests.unit.test_manual_research_database import make_run
    from tests.unit.test_research_acceptance_database import outcome
    from backend.macro.research_topics import make_task

    async with fixture() as (conn, run_id, now, tenants, actors, _):
        await conn.execute(
            "DELETE FROM research.research_validation_archive_queue WHERE run_id=$1",
            run_id,
        )
        await conn.execute(
            "DELETE FROM research.research_validation_records WHERE run_id=$1", run_id
        )
        await conn.execute(
            "DELETE FROM research.research_validation_artifacts WHERE run_id=$1", run_id
        )
        window = {
            "start": (now - timedelta(days=90)).date().isoformat(),
            "end": now.date().isoformat(),
            "lookbackDays": 90,
        }

        async def archive(identity, stamp, selected):
            value = registry(identity, 70)
            value["validatedAt"] = stamp.isoformat()
            for index, item in enumerate(value["items"]):
                item["pendingTasks"] = [
                    make_task(
                        f"scope-{index:03d}",
                        "FINANCING_COOLING",
                        "公开核查",
                        window,
                        status="PENDING_REVIEW",
                        reason_code="AUDIT_INCOMPLETE",
                    )
                ]
            packet = {
                "asOf": stamp.isoformat(),
                "researchDiscovery": {"auditManifest": {"selectedScopes": selected}},
            }
            await acceptance.enqueue_research_archive(
                conn,
                identity,
                packet,
                {},
                {},
                stamp,
                outcome(identity, stamp),
                topic_registry=value,
            )
            claim = await acceptance.claim_research_archive(conn, run_id=identity)
            await acceptance.persist_research_archive(conn, claim)

        await archive(run_id, now, [f"scope-{index:03d}" for index in range(10)])
        saved = json.loads(
            await conn.fetchval(
                "SELECT metadata FROM research.research_topic_snapshots WHERE run_id=$1",
                run_id,
            )
        )
        assert len(saved["pendingScopes"]) == 70
        first = await repository().read_pending_topic_scopes(conn, tenants[0], limit=3)
        assert [row["industryId"] for row in first] == [
            "scope-010",
            "scope-011",
            "scope-012",
        ]
        await conn.execute(
            "UPDATE research.daily_research_refresh_requests SET status='FAILED',completed_at=$2 WHERE id=(SELECT debug_request_id FROM research.daily_research_runs WHERE run_id=$1)",
            run_id,
            now + timedelta(seconds=30),
        )
        _, _, later = await make_run(
            conn, tenants[0], actors[0], now=now + timedelta(minutes=1)
        )
        await archive(
            later, now + timedelta(minutes=1), [row["industryId"] for row in first]
        )
        second = await repository().read_pending_topic_scopes(conn, tenants[0], limit=3)
        assert [row["industryId"] for row in second] == [
            "scope-013",
            "scope-014",
            "scope-015",
        ]


async def test_readback_rejects_widened_topic_table_write_permission():
    from scripts.migrate import _readback

    async with fixture() as (conn, *_):
        await conn.execute(
            "GRANT INSERT ON research.research_topic_entries TO wealthsail_api"
        )
        with pytest.raises(RuntimeError, match="topic"):
            await _readback(conn)


@pytest.mark.parametrize("with_selected_scopes", [False, True])
async def test_jsonb_storage_overhead_is_omitted_before_archive_without_stalling_initial(
    with_selected_scopes,
):
    from copy import deepcopy
    from tests.unit.test_research_acceptance_database import outcome

    async with fixture() as (conn, run_id, now, *_):
        await conn.execute(
            "DELETE FROM research.research_validation_archive_queue WHERE run_id=$1",
            run_id,
        )
        await conn.execute(
            "DELETE FROM research.research_validation_records WHERE run_id=$1", run_id
        )
        await conn.execute(
            "DELETE FROM research.research_validation_artifacts WHERE run_id=$1", run_id
        )
        await conn.execute(
            "UPDATE research.daily_research_results SET status='READY',payload='{}',error_class=NULL WHERE run_id=$1",
            run_id,
        )
        base = registry(run_id, 72)
        base["validatedAt"] = now.isoformat()
        for item in base["items"]:
            item.update(
                summary="a" * 4000, impact="b" * 4000, measures=["m" * 1000] * 5
            )
        low, high, value = 0, 1000, None
        while low <= high:
            middle = (low + high) // 2
            candidate = deepcopy(base)
            for item in candidate["items"]:
                item["measures"].append("p" * middle)
            try:
                prepared = repository().prepare_topic_registry(candidate, run_id, now)
            except repository().TopicRegistrySizeError:
                high = middle - 1
            else:
                if with_selected_scopes:
                    postgres_size = await conn.fetchval(
                        "SELECT octet_length($1::jsonb::text)",
                        acceptance._encode(prepared),
                    )
                    if postgres_size > 1048576:
                        high = middle - 1
                        continue
                value = candidate
                low = middle + 1
        assert value is not None
        prepared = repository().prepare_topic_registry(value, run_id, now)
        compact = len(acceptance._encode(prepared).encode("utf-8"))
        selected = (
            [f"scope-{index:03d}-" + "s" * 90 for index in range(10)]
            if with_selected_scopes
            else []
        )
        if with_selected_scopes:
            assert (
                await conn.fetchval(
                    "SELECT octet_length($1::jsonb::text)", acceptance._encode(prepared)
                )
                <= 1048576
            )
        actual = await conn.fetchval(
            "SELECT octet_length((($1::jsonb-'items')||jsonb_build_object('selectedScopes',$2::jsonb))::text)+sum(octet_length(item::text)) FROM jsonb_array_elements($1::jsonb->'items') item",
            acceptance._encode(prepared),
            acceptance._encode(selected),
        )
        assert compact <= 1048576 < actual
        packet = {
            "asOf": now.isoformat(),
            "researchDiscovery": {"auditManifest": {"selectedScopes": selected}},
        }
        await acceptance.enqueue_research_archive(
            conn,
            run_id,
            packet,
            {},
            {},
            now,
            outcome(run_id, now),
            topic_registry=value,
        )
        claim = await acceptance.claim_research_archive(conn, run_id=run_id)
        assert (
            claim["frozen_bundle"]["topicRegistryOmission"]["omissionReason"]
            == "SIZE_LIMIT"
        )
        await acceptance.persist_research_archive(conn, claim)
        assert (
            await conn.fetchval(
                "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
            == "ARCHIVED"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.research_validation_records WHERE run_id=$1 AND kind='INITIAL'",
                run_id,
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_results WHERE run_id=$1",
                run_id,
            )
            == "READY"
        )


async def test_reader_sees_pending_across_archive_commit_and_next_get_sees_registry():
    from contextlib import asynccontextmanager
    import asyncpg
    from backend.common.tenancy import TenantContext
    from backend.investment.research_topic_api import readonly_tenant_transaction
    from tests.unit.test_research_acceptance_database import outcome

    async with fixture(committed=True) as (owner, run_id, now, tenants, _, viewer):
        reader = await asyncpg.connect(os.environ["INVESTMENT_TEST_DATABASE_URL"])
        try:
            for table in (
                "research_validation_archive_queue",
                "research_validation_records",
                "research_validation_artifacts",
            ):
                await owner.execute(
                    f"DELETE FROM research.{table} WHERE run_id=$1", run_id
                )
            value = registry(run_id, 12)
            value["validatedAt"] = now.isoformat()
            await acceptance.enqueue_research_archive(
                owner,
                run_id,
                {"asOf": now.isoformat()},
                {},
                {},
                now,
                outcome(run_id, now),
                topic_registry=value,
            )

            class Pool:
                @asynccontextmanager
                async def acquire(self):
                    yield reader

            class InterleavingConnection:
                committed = False

                async def fetchval(self, *args):
                    return await reader.fetchval(*args)

                async def fetchrow(self, sql, *args):
                    result = await reader.fetchrow(sql, *args)
                    if "SELECT * FROM research.research_topic_snapshots" in sql:
                        assert result is None and not self.committed
                        async with owner.transaction():
                            claim = await acceptance.claim_research_archive(
                                owner, run_id=run_id
                            )
                            await acceptance.persist_research_archive(owner, claim)
                        self.committed = True
                    return result

            ctx = TenantContext(str(viewer), str(tenants[0]), tenant_role="VIEWER")
            connection = InterleavingConnection()
            async with readonly_tenant_transaction(Pool(), ctx):
                first = await repository().read_topic_registry(connection, run_id)
            assert connection.committed is True
            assert (
                first["registryAvailable"] is False
                and first["archiveState"] == "AUDIT_PENDING"
            )
            async with readonly_tenant_transaction(Pool(), ctx) as conn:
                second = await repository().read_topic_registry(conn, run_id)
            assert second["registryAvailable"] is True and len(second["items"]) == 12
        finally:
            await reader.close()
            await owner.execute(
                "DELETE FROM research.research_topic_entries WHERE snapshot_id IN(SELECT id FROM research.research_topic_snapshots WHERE run_id=$1)",
                run_id,
            )
            await owner.execute(
                "DELETE FROM research.research_topic_snapshots WHERE run_id=$1", run_id
            )
