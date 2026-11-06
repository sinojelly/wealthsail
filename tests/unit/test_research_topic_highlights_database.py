"""Owned PostgreSQL safe-summary priority and RLS; history stays byte-identical."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.common.database import apply_tenant_context
from backend.errors import NotFoundError
from backend.macro import research_acceptance_repository as acceptance
from backend.macro import research_topic_repository as topics
from tests.unit.test_research_acceptance_database import fixture, outcome
from tests.unit.test_research_opportunities_database import ReadOnlyConnection
from tests.unit.test_research_topic_repository import registry

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
        reason="owned loopback 103 DB required",
    ),
]


@pytest.mark.parametrize("manual", [False, True])
async def test_safe_initial_events_prioritize_whole_catalog_without_rewriting_failed_research(
    manual,
):
    async with fixture(
        manual=manual, now=datetime.now(timezone.utc) - timedelta(days=2)
    ) as (conn, run_id, now, tenants, actors, viewer):
        accepted = outcome(run_id, now)
        accepted["accepted"]["events"] = [
            {
                "id": "event:risk",
                "title": "重大融资负面待跟踪",
                "summary": "保留风险线索",
                "importance": "HIGH",
                "phase": "CONFIRMED",
                "sourceIds": ["s1"],
                "relatedTopicKeys": ["topic-63"],
            }
        ]
        await acceptance.enqueue_research_archive(
            conn,
            run_id,
            {"asOf": now.isoformat()},
            {},
            {},
            now,
            accepted,
            recorded_at=now,
        )
        claim = await acceptance.claim_research_archive(conn, run_id=run_id)
        await acceptance.persist_research_archive(conn, claim)
        catalog = registry(run_id, 64)
        catalog["validatedAt"] = now.isoformat()
        catalog["items"][12]["evidenceCoverageScore"] = 100
        await topics.save_topic_registry(conn, run_id, catalog, now)
        await conn.execute(
            "INSERT INTO research.daily_research_results(run_id,status,recorded_at,error_class) VALUES($1,'FAILED',$2,'ResearchValidationError')",
            run_id,
            now,
        )
        original = await conn.fetchrow(
            "SELECT outcome,summary FROM research.research_validation_records WHERE run_id=$1 AND kind='INITIAL'",
            run_id,
        )
        original_rows = await conn.fetch(
            "SELECT ordinal,body FROM research.research_topic_entries WHERE snapshot_id=(SELECT id FROM research.research_topic_snapshots WHERE run_id=$1) ORDER BY ordinal",
            run_id,
        )
        observed = await conn.fetchval("SELECT clock_timestamp()")
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        await apply_tenant_context(conn, str(viewer), str(tenants[0]))
        normal = await topics.read_topic_registry(
            ReadOnlyConnection(conn), run_id, observed
        )
        result = await topics.read_topic_registry(
            ReadOnlyConnection(conn), run_id, observed, include_highlights=True
        )
        assert result["items"] == normal["items"]
        assert result["nextCursor"] == normal["nextCursor"]
        selected = result["highlights"]["items"]
        assert selected[0]["topic"]["key"] == "topic-63"
        assert selected[0]["priority"]["importance"] == "HIGH"
        assert selected[0]["priority"]["checkedSourceCoveragePercent"] is None
        assert selected[1]["topic"]["key"] == "topic-12"
        assert all(not row["topic"]["investmentEligible"] for row in selected)
        assert len(selected) == 10 and "PRIVATE_PRICE" not in json.dumps(result)
        await apply_tenant_context(conn, str(actors[1]), str(tenants[1]))
        if manual:
            with pytest.raises(NotFoundError):
                await topics.read_topic_registry(
                    ReadOnlyConnection(conn), run_id, observed, include_highlights=True
                )
        else:
            shared = await topics.read_topic_registry(
                ReadOnlyConnection(conn), run_id, observed, include_highlights=True
            )
            assert shared["highlights"] == result["highlights"]
        await conn.execute("RESET ROLE")
        assert (
            await conn.fetchrow(
                "SELECT outcome,summary FROM research.research_validation_records WHERE run_id=$1 AND kind='INITIAL'",
                run_id,
            )
            == original
        )
        assert (
            await conn.fetch(
                "SELECT ordinal,body FROM research.research_topic_entries WHERE snapshot_id=(SELECT id FROM research.research_topic_snapshots WHERE run_id=$1) ORDER BY ordinal",
                run_id,
            )
            == original_rows
        )
        assert (
            await conn.fetchval(
                "SELECT status FROM research.daily_research_results WHERE run_id=$1",
                run_id,
            )
            == "FAILED"
        )
