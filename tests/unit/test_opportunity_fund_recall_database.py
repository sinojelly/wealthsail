"""Real SQL, role permissions and subscription ordering in rollback fixtures."""

from datetime import date
import os
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.investment.opportunity_fund_recall import recall_topic_funds
from tests.unit.test_opportunity_fund_recall import NOW, topic


DSN = os.environ.get("OPPORTUNITY_RECALL_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated opportunity-recall database required"),
]


@pytest.mark.parametrize("role", ["wealthsail_api", "wealthsail_worker"])
@pytest.mark.parametrize("open_a", [True, False])
async def test_full_master_index_recall_prioritizes_open_funds_before_candidate_bound(
    role,
    open_a,
):
    target = urlsplit(DSN)
    assert target.hostname in {"127.0.0.1", "localhost"}
    assert "validation" in target.path or "dynamic_research" in target.path
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    suffix = uuid4().hex[:8]
    term = f"量子纠错{suffix}"
    codes = [f"{970000 + uuid4().int % 20000:06}" for _ in range(14)]
    assert len(set(codes)) == len(codes)
    codes.sort()
    try:
        for offset, code in enumerate(codes):
            instrument = uuid4()
            name = (
                f"隔离{term}{offset}指数A" if offset < 13 else f"隔离中性{suffix}精选C"
            )
            await conn.execute(
                "INSERT INTO reference.fund_master(fund_code,fund_name,fund_type,updated_at) VALUES($1,$2,'股票型-指数',$3)",
                code,
                name,
                NOW,
            )
            await conn.execute(
                "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN',$2)",
                instrument,
                name,
            )
            await conn.execute(
                "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER',$2,'FUND_CN',$3)",
                instrument,
                code,
                date(2020, 1, 1),
            )
            await conn.execute(
                "INSERT INTO reference.fund_profiles(instrument_id,purchase_status,effective_from,retrieved_at) VALUES($1,$2,$3,$4)",
                instrument,
                "OPEN" if offset == 13 or offset == 12 and open_a else "SUSPENDED",
                date(2020, 1, 1),
                NOW,
            )
        index_id = f"recall-{suffix}"
        await conn.execute(
            "INSERT INTO reference.research_indices(id,code,name,market) VALUES($1,$2,$3,'CN')",
            index_id,
            f"IDX{suffix}",
            f"{term}跟踪指数",
        )
        await conn.execute(
            "INSERT INTO reference.product_index_links(fund_code,market,index_id,relationship_type,fund_name,effective_from,source_uri,verified_at) VALUES($1,'FUND_CN',$2,'ETF_LINK',$3,$4,'https://example.com/verified-index-fixture',$5)",
            codes[-1],
            index_id,
            f"隔离中性{suffix}精选C",
            date(2020, 1, 1),
            NOW,
        )
        await conn.execute(f"SET LOCAL ROLE {role}")
        row = topic(
            "quantum",
            title=term,
            summary=term,
            match={
                "industryIds": [],
                "unresolvedClaims": [term],
            },
        )
        result = (await recall_topic_funds(conn, [row], {}, NOW))["topic:quantum"]
        funds = result["funds"]
        expected = set(codes[-2:]) if open_a else {codes[-1]}
        assert {fund["fundCode"] for fund in funds if fund["canSubscribe"]} == expected
        indexed = next(fund for fund in funds if fund["fundCode"] == codes[-1])
        assert indexed["subjectKind"] == "fund" and indexed["subjectId"] == str(
            instrument
        )
        assert funds[0]["fundCode"] == (codes[-2] if open_a else codes[-1])
        assert indexed["matchLevel"] == "DYNAMIC_MATCH"
        assert "跟踪指数" in indexed["matchReason"]
        assert indexed["sourceUri"] == "https://example.com/verified-index-fixture"
        assert all(fund["actionable"] is False for fund in funds)
    finally:
        await tx.rollback()
        await conn.close()
