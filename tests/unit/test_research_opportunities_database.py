"""Owned loopback DB: immutable, tenant-scoped observations require no AI."""

import json
import os
from uuid import UUID

import pytest

from backend.common.database import apply_tenant_context
from backend.macro import research_acceptance_repository as acceptance
from backend.macro.daily_repository import finish_daily_run
from backend.macro.research_opportunities import read_fund_observation
from backend.macro.research_topic_repository import save_topic_registry
from tests.unit.test_fund_observation import case
from tests.unit.test_daily_research import NOW
from tests.unit.test_manual_research_database import make_run
from tests.unit.test_research_acceptance_database import outcome
from tests.unit.test_research_history_repair_database import fixture
from tests.unit.test_research_topic_repository import registry

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
        reason="owned102/103 loopback DB required",
    ),
]


class ReadOnlyConnection:
    def __init__(self, conn):
        self.conn = conn

    async def fetchrow(self, sql, *args):
        assert sql.lstrip().startswith("SELECT")
        assert "frozen_bundle" not in sql and "research_validation_artifacts" not in sql
        return await self.conn.fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        assert sql.lstrip().startswith("SELECT")
        return await self.conn.fetch(sql, *args)

    async def fetchval(self, sql, *args):
        assert sql.lstrip().startswith("SELECT")
        return await self.conn.fetchval(sql, *args)

    async def execute(self, *args):
        raise AssertionError("observation reader must not mutate research")


@pytest.mark.parametrize("archived", [False, True])
async def test_failed_manual_scan_is_read_only_and_isolated_with_or_without_archived_topics(
    archived,
):
    async with fixture() as (conn, _, _, tenants, actors, _):
        _, _, run_id = await make_run(conn, tenants[0], actors[0], now=NOW)
        packet = case()
        await conn.execute(
            "UPDATE research.daily_research_runs SET public_packet=$2::jsonb WHERE run_id=$1",
            run_id,
            json.dumps(packet),
        )
        await finish_daily_run(
            conn, run_id, NOW, status="FAILED", error_class="ResearchValidationError"
        )
        receipt = outcome(run_id, NOW)
        await acceptance.enqueue_research_archive(
            conn, run_id, packet, {}, {}, NOW, receipt, recorded_at=NOW
        )
        if archived:
            claim = await acceptance.claim_research_archive(conn, run_id=run_id)
            await acceptance.persist_research_archive(conn, claim)
            topics = registry(run_id, 2)
            topics["validatedAt"] = NOW.isoformat()
            await save_topic_registry(conn, run_id, topics, NOW)
        before = await conn.fetchrow(
            "SELECT * FROM research.daily_research_results WHERE run_id=$1", run_id
        )
        observed = await conn.fetchval("SELECT clock_timestamp()")
        row = {
            "run_id": run_id,
            "dispatched_at": NOW,
            "status": "FAILED",
            "payload": None,
            "public_packet": json.dumps(packet),
        }
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        result = await read_fund_observation(
            ReadOnlyConnection(conn), row, str(run_id), observed
        )
        assert result["coverage"]["scannedFundCount"] == 3
        assert result["coverage"]["executionVerifiedCheckCount"] == 0
        assert result["archiveState"] == ("ARCHIVED" if archived else "AUDIT_PENDING")
        assert result["topicLinkState"] == (
            "ARCHIVED" if archived else "PENDING_ARCHIVE"
        )
        assert result["researchId"] == str(run_id)
        assert not result["investmentEligible"] and not result["actionable"]
        assert all(
            not row["investmentEligible"] and not row["actionable"]
            for row in result["items"]
        )
        await apply_tenant_context(conn, str(actors[2]), str(tenants[1]))
        assert (
            await read_fund_observation(
                ReadOnlyConnection(conn), row, str(run_id), observed
            )
            is None
        )
        await conn.execute("RESET ROLE")
        assert (
            await conn.fetchrow(
                "SELECT * FROM research.daily_research_results WHERE run_id=$1", run_id
            )
            == before
        )
        assert UUID(result["researchId"]) == run_id
