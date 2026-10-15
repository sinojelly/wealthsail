"""V2 verified notices preserve real worker/actor permissions and budget."""

import os
from copy import deepcopy
from datetime import timedelta

import pytest

from backend.common.database import create_pool, tenant_transaction
from backend.investment import decision_repository as repo
from test_opportunity_decision_database import seed
from test_verified_research_notices import NOW, result, crossing


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
    reason="isolated validation database required",
)
async def test_real_coverage_only_saves_baseline_and_verified_reversal_notifies_once():
    dsn = os.environ["INVESTMENT_TEST_DATABASE_URL"]
    pool = await create_pool(dsn, min_size=1, max_size=2)
    worker = await create_pool(
        dsn, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    try:
        tenant, actor, other = await seed(pool)
        scope = {"direction_id": "gold:physical", "index_id": "au9999"}
        async with tenant_transaction(pool, actor, tenant) as conn:
            saved = await repo.set_monitor(
                conn, tenant, actor, scope, True, NOW - timedelta(days=1)
            )
        async with worker.acquire() as conn:
            monitor = dict(
                await conn.fetchrow(
                    "SELECT * FROM risk.opportunity_condition_monitors WHERE id::text=$1",
                    saved["id"],
                )
            )
            current = result()
            async with conn.transaction():
                assert (await repo.record_monitor_result(conn, monitor, current, NOW))[
                    "baseline"
                ]
            recovered = deepcopy(current)
            recovered["directionModel"]["missingRequiredEvidence"] = []
            async with conn.transaction():
                update = await repo.record_monitor_result(
                    conn, monitor, recovered, NOW + timedelta(minutes=15)
                )
                assert (
                    not update["notice"]
                    and update["reason"] == "NO_VERIFIED_BUSINESS_CHANGE"
                )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM risk.opportunity_decision_snapshots WHERE monitor_id=$1",
                    monitor["id"],
                )
                == 2
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM risk.opportunity_condition_notices WHERE monitor_id=$1",
                    monitor["id"],
                )
                == 0
            )
            recovered["researchBoard"]["items"][0].update(
                category="CHANGE", key="trend-failed:hs300"
            )
            async with conn.transaction():
                assert (
                    await repo.record_monitor_result(
                        conn, monitor, recovered, NOW + timedelta(minutes=30)
                    )
                )["notice"]
            async with conn.transaction():
                assert not (
                    await repo.record_monitor_result(
                        conn, monitor, recovered, NOW + timedelta(minutes=45)
                    )
                )["notice"]
        async with tenant_transaction(pool, other, tenant) as conn:
            assert not await repo.list_notices(
                conn, tenant, other, **scope, exact_scope=True
            )
        async with tenant_transaction(pool, actor, tenant) as conn:
            notices = await repo.list_notices(
                conn, tenant, actor, **scope, exact_scope=True
            )
            assert len(notices) == 1 and notices[0]["severity"] == "WATCH"
    finally:
        await worker.close()
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
    reason="isolated validation database required",
)
async def test_real_three_snapshot_coverage_recovery_is_silent_but_roundtrip_reversals_notify():
    pool = await create_pool(
        os.environ["INVESTMENT_TEST_DATABASE_URL"], min_size=1, max_size=2
    )
    worker = await create_pool(
        os.environ["INVESTMENT_TEST_DATABASE_URL"],
        min_size=1,
        max_size=2,
        runtime_role="wealthsail_worker",
    )
    try:
        tenant, actor, other = await seed(pool)
        scope = {"direction_id": "a-share:broad", "index_id": "hs300"}
        async with tenant_transaction(pool, actor, tenant) as conn:
            saved = await repo.set_monitor(
                conn, tenant, actor, scope, True, NOW - timedelta(days=1)
            )
        async with worker.acquire() as conn:
            monitor = dict(
                await conn.fetchrow(
                    "SELECT * FROM risk.opportunity_condition_monitors WHERE id::text=$1",
                    saved["id"],
                )
            )

            async def persist(value, minute):
                async with conn.transaction():
                    return await repo.record_monitor_result(
                        conn, monitor, value, NOW + timedelta(minutes=minute)
                    )

            assert (await persist(crossing("UP"), 0))["baseline"]
            gap = crossing("UP")
            gap["researchBoard"]["items"] = []
            gap["directionModel"]["missingRequiredEvidence"] = ["PMI"]
            assert not (await persist(gap, 15))["notice"]
            assert not (await persist(crossing("UP"), 30))["notice"]
            assert not (await persist(gap, 45))["notice"]
            assert (await persist(crossing("DOWN"), 60))["notice"]
            assert not (await persist(gap, 75))["notice"]
            assert (await persist(crossing("UP"), 90))["notice"]
            snapshots = await conn.fetch(
                "SELECT result FROM risk.opportunity_decision_snapshots WHERE monitor_id=$1 ORDER BY as_of",
                monitor["id"],
            )
            assert len(snapshots) == 7
            for row in snapshots:
                stored = repo.decode(row["result"])
                assert len(stored["verifiedBusinessBaseline"]["items"]) == 1
            after = repo.decode(snapshots[-1]["result"])
            assert after["verifiedBusinessBaseline"]["items"][0]["key"].endswith("_UP")
        async with tenant_transaction(pool, other, tenant) as conn:
            assert not await repo.list_notices(
                conn, tenant, other, **scope, exact_scope=True
            )
        async with tenant_transaction(pool, actor, tenant) as conn:
            notices = await repo.list_notices(
                conn, tenant, actor, **scope, exact_scope=True
            )
            assert len(notices) == 2
    finally:
        await worker.close()
        await pool.close()
