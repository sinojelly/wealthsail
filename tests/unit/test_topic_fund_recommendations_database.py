"""Actual archived themes -> API-role catalog recall without a formal direction."""

import json
import os
from uuid import uuid4

import pytest

from backend.common.database import apply_tenant_context
from backend.errors import NotFoundError
from backend.macro.research_opportunities import read_topic_fund_recommendations
from backend.macro.research_topic_repository import save_topic_registry
from tests.unit.test_research_history_repair_database import fixture
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
    async with fixture(manual=True) as (conn, run_id, now, tenants, actors, _):
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
        topics = registry(run_id, 1)
        topics["validatedAt"] = now.isoformat()
        topics["items"][0].update(title=term, summary=term + "新政策投资机会")
        topics["items"][0]["match"].update(industryIds=[], unresolvedClaims=[term])
        await save_topic_registry(conn, run_id, topics, now)
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
        await apply_tenant_context(conn, str(actors[2]), str(tenants[1]))
        with pytest.raises(NotFoundError):
            await read_topic_fund_recommendations(
                RecommendationReadConnection(conn), run_id, evaluated
            )
