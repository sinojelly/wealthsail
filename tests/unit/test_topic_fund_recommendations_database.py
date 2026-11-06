"""Actual archived themes -> API-role catalog recall without a formal direction."""

import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.common.database import apply_tenant_context
from backend.errors import NotFoundError
from backend.macro.research_opportunities import read_topic_fund_recommendations
from backend.macro.research_topic_repository import save_topic_registry
from backend.investment.opportunity_fund_cache import (
    claim_topic_fund_cache,
    finish_topic_fund_cache,
    prepare_cached_candidates,
)
from backend.investment.opportunity_fund_recall import discover_topic_funds
from tests.unit.test_research_acceptance_database import fixture, outcome
from backend.macro import research_acceptance_repository as acceptance
from tests.unit.test_research_opportunities_database import ReadOnlyConnection
from tests.unit.test_research_topic_repository import registry

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
        reason="isolated research database required",
    ),
]


class RecommendationReadConnection(ReadOnlyConnection):
    async def fetch(self, sql, *args):
        assert sql.lstrip().startswith(("SELECT", "WITH"))
        return await self.conn.fetch(sql, *args)


async def test_failed_manual_topic_reaches_real_master_without_risk_receipt_or_formal_direction():
    async with fixture(
        manual=True, now=datetime.now(timezone.utc) - timedelta(days=2)
    ) as (conn, run_id, now, tenants, actors, _):
        term = "量子探测" + uuid4().hex[:8]
        code = f"{980000 + uuid4().int % 10000:06}"
        await conn.execute(
            "INSERT INTO reference.fund_master(fund_code,fund_name,fund_type,updated_at) VALUES($1,$2,'股票型-指数',$3)",
            code,
            term + "指数A",
            now,
        )
        packet = {
            "asOf": now.isoformat(),
            "discoveryUniverse": [],
            "quantitativeScan": {"items": []},
        }
        await conn.execute(
            "UPDATE research.daily_research_runs SET public_packet=$2::jsonb WHERE run_id=$1",
            run_id,
            json.dumps(packet),
        )
        accepted = outcome(run_id, now)
        accepted["accepted"]["sources"][0].update(
            verification="SOURCE_CHECKED",
            quote=term + "已披露订单增长",
            retrievedAt=now.isoformat(),
        )
        await acceptance.enqueue_research_archive(
            conn, run_id, packet, {}, {}, now, accepted, recorded_at=now
        )
        archive_claim = await acceptance.claim_research_archive(conn, run_id=run_id)
        await acceptance.persist_research_archive(conn, archive_claim)
        topics = registry(run_id, 1)
        topics["validatedAt"] = now.isoformat()
        topics["items"][0].update(title=term, summary=term + "订单增长形成投资机会")
        topics["items"][0]["match"].update(industryIds=[], unresolvedClaims=[term])
        await save_topic_registry(conn, run_id, topics, now)
        evaluated = await conn.fetchval("SELECT clock_timestamp()")
        claim = await claim_topic_fund_cache(conn, run_id=run_id)
        discovered = await discover_topic_funds(
            conn, topics["items"], packet, evaluated
        )
        await finish_topic_fund_cache(
            conn,
            claim,
            candidates=prepare_cached_candidates(
                discovered,
                [topic["topicId"] for topic in topics["items"]],
            ),
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        evaluated = await conn.fetchval("SELECT clock_timestamp()")
        result = await read_topic_fund_recommendations(
            RecommendationReadConnection(conn), run_id, evaluated
        )
        item = result["items"][0]
        assert code in {fund["fundCode"] for fund in item["recommendedFunds"]}
        assert item["riskStatus"] == "UNVERIFIED_RISK"
        assert item["candidateDirection"]["origin"] == "NEWS_TOPIC"
        assert result["coverage"]["matchedTopicCount"] == 1
        assert item["actionable"] is False
        await apply_tenant_context(conn, str(actors[1]), str(tenants[1]))
        with pytest.raises(NotFoundError):
            await read_topic_fund_recommendations(
                RecommendationReadConnection(conn), run_id, evaluated
            )
