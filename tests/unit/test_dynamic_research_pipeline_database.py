"""Real batch -> atomic outbox -> immutable paged catalog; no model execution."""

import os
from urllib.parse import urlsplit

import pytest

from backend.common.database import apply_tenant_context
from backend.macro import research_acceptance_repository as archive
from backend.macro import research_topic_repository as catalog
from tests.unit.test_research_repair import NOW, fixture as inputs
from tests.unit import test_research_acceptance_database as source_fixture

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned102 DB required"),
]


async def test_real_batch_registry_and_original_materials_archive_as_one_snapshot(
    monkeypatch,
):
    from backend.macro.research_batch import evaluate_research_batch
    from backend.macro.research_topics import freeze_discovery_packet

    target = urlsplit(DSN)
    assert target.hostname in {"localhost", "127.0.0.1", "::1"}
    assert target.port == 29034 and target.path == "/wealthsail_dynamic_research102_v1"
    monkeypatch.setattr(source_fixture, "stamp", lambda: NOW)
    async with source_fixture.fixture() as (conn, run_id, now, tenants, actors, _):
        packet, raw, checks = inputs()
        raw["valueDirections"].pop()
        raw["topicCatalog"] = [
            {
                "key": f"topic-{index}",
                "title": f"新主题{index}",
                "thesis": "原始行业线索等待核验。",
                "industryClaims": [f"动态新产业{index}"],
                "relatedIndustryIds": [],
                "sourceIds": ["s2"],
                "unknowns": [],
                "pendingTasks": [],
            }
            for index in range(30)
        ]
        packet = freeze_discovery_packet(packet, now)
        batch = evaluate_research_batch(
            raw, packet, checks, research_id=str(run_id), validated_at=now
        )
        assert batch["autoPublishAllowed"]
        await conn.execute(
            "INSERT INTO research.daily_research_results(run_id,status,recorded_at,payload) VALUES($1,'READY',$2,$3::jsonb)",
            run_id,
            now,
            archive._encode(batch["formalReport"]),
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await archive.enqueue_research_archive(
            conn,
            run_id,
            packet,
            raw,
            checks,
            now,
            batch["originalOutcome"].to_dict(),
            topic_registry=batch["registry"],
        )
        claim = await archive.claim_research_archive(conn, run_id=run_id)
        await archive.persist_research_archive(conn, claim)
        pending = await catalog.read_pending_topic_scopes(conn)
        assert (
            len(
                {
                    row["industryId"]
                    for row in pending
                    if row["sourceRunId"] == str(run_id)
                }
            )
            >= 30
        )
        assert any(row.get("searchTerm") == "动态新产业29" for row in pending)
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        first = await catalog.read_topic_registry(conn, run_id, now, limit=12)
        assert (
            first["registryAvailable"] and first["coverage"]["pendingScopeCount"] >= 30
        )
        assert first["totalCount"] == 31 and first["actionable"] is False
        count, page = len(first["items"]), first
        while page["nextCursor"]:
            page = await catalog.read_topic_registry(
                conn, run_id, now, limit=12, cursor=page["nextCursor"]
            )
            assert page["snapshotId"] == first["snapshotId"]
            count += len(page["items"])
        assert count == 31
        await conn.execute("RESET ROLE")
        assert (
            await conn.fetchval(
                "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1",
                run_id,
            )
            == "ARCHIVED"
        )
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
            == 1
        )
