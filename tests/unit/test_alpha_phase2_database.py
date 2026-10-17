"""Real isolated SQL contracts for Phase 2 roles, scope and permanent slots."""

import os
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated DB required"),
]


async def test_phase2_tables_have_restricted_runtime_authority():
    conn = await asyncpg.connect(DSN)
    try:
        for table in (
            "reference.theme_asset_graph",
            "market.alpha_public_batches",
            "research.strategy_memory_rules",
            "research.alpha_opportunity_runs",
            "research.study_excursions",
        ):
            assert await conn.fetchval("SELECT to_regclass($1)", table) is not None
        assert await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','reference.theme_asset_graph','SELECT')"
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','reference.theme_asset_graph','INSERT')"
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','research.alpha_opportunity_runs','INSERT')"
        )
        assert await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','research.alpha_opportunity_runs','INSERT')"
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','research.strategy_memory_rules','UPDATE')"
        )
        assert (
            await conn.fetchval(
                "SELECT count(DISTINCT theme_code) FROM reference.theme_asset_graph"
            )
            == 5
        )
    finally:
        await conn.close()


async def test_phase2_actor_rules_and_permanent_daily_slot():
    from test_opportunity_decision_database import seed
    from backend.common.database import create_pool, tenant_transaction

    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, other = await seed(pool)
        now = datetime(2026, 10, 18, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        async with tenant_transaction(pool, actor, tenant) as conn:
            await conn.execute(
                """INSERT INTO research.strategy_memory_rules(tenant_id,user_id,theme_code,category,rule_text,sample_count,supported_count,mixed_count,refuted_count,inconclusive_count,updated_at)
                VALUES($1,$2,'AI_MEMORY_HBM','TIMING','private',1,1,0,0,0,$3)""",
                tenant,
                actor,
                now,
            )
        async with tenant_transaction(pool, other, tenant) as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM research.strategy_memory_rules WHERE tenant_id=$1 AND user_id=$2",
                    tenant,
                    actor,
                )
                == 0
            )
        async with pool.acquire() as conn:
            await conn.execute("SET ROLE wealthsail_worker")
            slot = uuid4()
            query = """INSERT INTO research.alpha_opportunity_runs(id,tenant_id,user_id,run_date,slot,status,claimed_at)
                VALUES($1,$2,$3,$4,'FUND_1430','RUNNING',$5) ON CONFLICT DO NOTHING RETURNING id"""
            assert (
                await conn.fetchval(query, slot, tenant, actor, now.date(), now) == slot
            )
            await conn.execute(
                "UPDATE research.alpha_opportunity_runs SET status='FAILED' WHERE id=$1",
                slot,
            )
            assert (
                await conn.fetchval(query, uuid4(), tenant, actor, now.date(), now)
                is None
            )
    finally:
        await pool.close()
