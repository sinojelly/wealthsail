"""Dynamic calibration namespaces keep real profit proofs and actor boundaries."""

from copy import deepcopy

import pytest

from backend.investment import strategy_memory as memory, study_repository as repo
from test_strategy_memory_database import (
    DSN,
    NOW,
    actor_role,
    seed,
    connection,
    baseline,
    add_track,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="explicit disposable memory DB required"),
]


async def test_discovery_memory_sql_uses_frozen_key_and_invalidates_reused_profit():
    conn = await connection()
    tx = conn.transaction()
    await tx.start()
    try:
        tenant, actor, other, _, instrument, track, version, fills = await seed(
            conn, discovery_key="healthcare-recovery"
        )
        second, second_version = await add_track(
            conn, tenant, actor, instrument, discovery_key="consumer-recovery"
        )
        await actor_role(conn, actor)
        rules = await memory.refresh_strategy_memory(
            conn, tenant, actor, "DISCOVERY:healthcare-recovery", NOW
        )
        assert baseline(rules)["sample_count"] == 1
        assert baseline(rules)["win_rate"] == 1
        public = memory.public_memory(
            await memory.read_strategy_memory(conn, tenant, actor, NOW), NOW
        )
        assert public[0]["themeCode"] == "DISCOVERY:healthcare-recovery"
        assert public[0]["profitSampleCount"] == 1
        assert "PRIVATE" not in str(public)
        for fill in fills:
            await repo.add_trade_link(
                conn, second, second_version, deepcopy(fill), "PRIVATE link", NOW
            )
        await memory.refresh_memory_for_trade(
            conn, tenant, actor, fills[0]["fillId"], "DISCOVERY:consumer-recovery", NOW
        )
        rows = await memory.read_strategy_memory(conn, tenant, actor, NOW)
        assert {row["theme_code"] for row in rows} == {
            "DISCOVERY:healthcare-recovery",
            "DISCOVERY:consumer-recovery",
        }
        assert all(row["profit_sample_count"] == 0 for row in rows)
        await actor_role(conn, other)
        assert await memory.read_strategy_memory(conn, tenant, actor, NOW) == []
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.parametrize("key", ["Private Personal Title", ["healthcare-recovery"]])
async def test_malformed_frozen_key_does_not_enter_public_memory_namespace(key):
    conn = await connection()
    tx = conn.transaction()
    await tx.start()
    try:
        tenant, actor, _, _, _, _, _, _ = await seed(conn, discovery_key=key)
        await actor_role(conn, actor)
        rules = await memory.refresh_strategy_memory(
            conn, tenant, actor, "AI_MEMORY_HBM", NOW
        )
        assert baseline(rules)["sample_count"] == 1
        public = memory.public_memory(
            await memory.read_strategy_memory(conn, tenant, actor, NOW), NOW
        )
        assert public[0]["themeCode"] == "AI_MEMORY_HBM"
        assert "Personal" not in str(public)
    finally:
        await tx.rollback()
        await conn.close()
