"""Product relationships use public code plus market, not manufactured UUIDs."""

import os
from datetime import timedelta

import asyncpg
import pytest

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated DB required"),
]


async def test_public_product_relations_have_read_only_api_authority():
    conn = await asyncpg.connect(DSN)
    try:
        assert await conn.fetchval(
            "SELECT to_regclass('reference.product_index_links')"
        )
        assert await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','reference.product_index_links','SELECT')"
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','reference.product_index_links','INSERT')"
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege('authenticated','reference.product_index_links','SELECT')"
        )
        assert await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','reference.product_index_links','UPDATE')"
        )
        assert await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','reference.research_product_index_links','SELECT')"
        )
    finally:
        await conn.close()


async def test_seeded_otc_index_links_survive_missing_history_and_instrument_uuid():
    from backend.investment import repository, service
    from backend.investment.schemas import ScreenQuery

    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        # A controlled fixture removes unrelated products, never user data.
        await conn.execute("DELETE FROM reference.fund_index_links")
        await conn.execute(
            "DELETE FROM reference.product_index_links WHERE fund_code NOT IN ('000051','110020','202015')"
        )
        await conn.execute(
            "DELETE FROM reference.instrument_aliases WHERE alias_type='TICKER' AND alias_value IN ('000051','110020','202015')"
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        result = await service.screen(
            conn, ScreenQuery(directionId="a-share:broad", indexId="hs300")
        )
        assert {row["fundCode"] for row in result["items"]} == {
            "000051",
            "110020",
            "202015",
        }
        assert result["total"] == 3
        assert all(row["instrumentId"] is None for row in result["items"])
        indices = await repository.indices(conn, "a-share:broad")
        assert next(row for row in indices if row["id"] == "hs300")["fund_count"] == 3
        search = await service.search(conn, "000051")
        candidate = next(row for row in search if row["kind"] == "fund")
        assert candidate["indexId"] == "hs300" and candidate["instrumentId"] is None
        assert (
            await repository.location(conn, None, fund_code="000051", market="SSE")
            is None
        )
    finally:
        await tx.rollback()
        await conn.close()


async def test_expired_future_and_benchmark_relationships_do_not_become_index_tracking():
    from backend.investment.repository import location, screen
    from backend.investment.schemas import ScreenQuery

    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        today = await conn.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
        await conn.execute(
            "INSERT INTO reference.fund_master(fund_code,fund_name,fund_type) VALUES('989991','测试真实身份一','股票型'),('989992','测试真实身份二','指数型'),('989993','测试真实身份三','指数型'),('989994','测试真实身份四','指数型') ON CONFLICT DO NOTHING"
        )
        for code, relationship, start, end, verified in (
            ("989991", "BENCHMARK", today - timedelta(days=2), None, "now()"),
            (
                "989992",
                "TRACKS",
                today - timedelta(days=2),
                today - timedelta(days=1),
                "now()",
            ),
            ("989993", "ETF_LINK", today + timedelta(days=1), None, "now()"),
            (
                "989994",
                "TRACKS",
                today - timedelta(days=1),
                None,
                "now()+interval '1 day'",
            ),
        ):
            await conn.execute(
                f"INSERT INTO reference.product_index_links(fund_code,market,index_id,relationship_type,fund_name,effective_from,effective_to,source_uri,verified_at) VALUES($1,'FUND_CN','hs300',$2,$3,$4,$5,'https://official.example.test/product', {verified})",
                code,
                relationship,
                "测试产品",
                start,
                end,
            )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        rows, count = await screen(conn, ScreenQuery(indexId="hs300", keyword="98999"))
        assert count == 0 and not rows
        for code in ("989991", "989992", "989993", "989994"):
            assert await location(conn, None, fund_code=code, market="FUND_CN") is None
    finally:
        await tx.rollback()
        await conn.close()
