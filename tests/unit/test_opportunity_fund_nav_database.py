"""Owned PostgreSQL: public code-only NAV warming and durable lease isolation."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.common.database import create_pool
from backend.investment import opportunity_fund_cache as cache
from backend.investment import opportunity_fund_nav as nav
from backend.investment.opportunity_fund_prices import enrich_fund_price_positions
from backend.integrations.nav_history_proof import nav_points_digest
from tests.unit.test_live_fund_plans_v6 import history
from tests.unit.test_opportunity_fund_cache_database import fixture

DSN = os.environ.get("OPPORTUNITY_CACHE_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned104 database required"),
]
CODE = "987641"
NAME = "验证自动补齐冷门芯片A"


def fresh_history(code, now):
    row = history(code)
    delta = now.date() - timedelta(days=1) - datetime.fromisoformat(row["asOf"]).date()
    for point in row["points"]:
        point["date"] = (
            datetime.fromisoformat(point["date"]).date() + delta
        ).isoformat()
    row.update(
        asOf=row["points"][-1]["date"],
        retrievedAt=now.isoformat(),
        availableAt=now.isoformat(),
    )
    proof = row["providerHistoryProof"]
    proof.update(
        retrievedAt=now.isoformat(),
        queryStart=row["points"][0]["date"],
        queryEnd=now.date().isoformat(),
        firstDate=row["points"][0]["date"],
        lastDate=row["asOf"],
        pointsDigest=nav_points_digest(row["points"]),
    )
    return row


async def prepare(conn, run, topics):
    await conn.execute(
        """INSERT INTO reference.fund_master(fund_code,fund_name,fund_type,updated_at)
        VALUES($1,$2,'股票型',clock_timestamp()-interval '1 minute')
        ON CONFLICT(fund_code) DO UPDATE SET fund_name=EXCLUDED.fund_name,
          updated_at=EXCLUDED.updated_at""",
        CODE,
        NAME,
    )
    product = {
        "fundCode": CODE,
        "name": NAME,
        "subjectKind": "nav",
        "subjectId": CODE,
        "market": "FUND_CN",
        "associationLevel": "NAME_CANDIDATE",
        "fundGroupKey": NAME[:-1],
    }
    candidates = {
        topic["topicId"]: {
            "topicId": topic["topicId"],
            "status": "MATCHED",
            "funds": [product],
            "indirectFunds": [],
        }
        for topic in topics
    }
    await conn.execute("SET LOCAL ROLE wealthsail_worker")
    claim = await cache.claim_topic_fund_cache(conn, run_id=run)
    assert (await cache.finish_topic_fund_cache(conn, claim, candidates=candidates))[
        "status"
    ] == "READY"
    return product


def connection(monkeypatch, conn):
    @asynccontextmanager
    async def connect(_pool):
        yield conn

    monkeypatch.setattr(nav, "system_connection", connect)


async def test_new_master_code_without_instrument_warms_history_and_get_reuses_it(
    monkeypatch,
):
    async with fixture() as (conn, run, _now, _tenants, _actors, topics):
        product = await prepare(conn, run, topics)
        connection(monkeypatch, conn)
        now = await conn.fetchval("SELECT clock_timestamp()")
        calls = []

        async def collect(pool, observed, *, asset_rows, minimum_points):
            calls.append(deepcopy(asset_rows))
            assert minimum_points == 550
            assert asset_rows == [
                {"asset_code": CODE, "overseas_calendar_required": False}
            ]
            return {"fundHistories": [fresh_history(CODE, observed)]}

        monkeypatch.setattr(nav, "collect_theme_nav_histories", collect)
        first = await nav.schedule_recalled_fund_nav(object(), run, now=now)
        assert first == {
            "selected": 1,
            "reused": 0,
            "claimed": 1,
            "persisted": 1,
            "usable": 1,
            "partial": 0,
            "failed": 0,
        }
        second = await nav.schedule_recalled_fund_nav(object(), run)
        assert second["claimed"] == 0 and second["reused"] == 1 and len(calls) == 1
        after = await conn.fetchval("SELECT clock_timestamp()")
        priced = await enrich_fund_price_positions(
            conn, {"topic": {"funds": [product]}}, {}, after
        )
        assert priced["topic"]["funds"][0]["pricePosition"]["status"] != "DATA_PENDING"
        attempts = await conn.fetch(
            "SELECT phase,release_keys,result FROM market.macro_evidence_attempts WHERE job_key=$1",
            f"OPPORTUNITY_NAV:{CODE}",
        )
        assert {row["phase"] for row in attempts} == {"CLAIMED", "FINISHED"}
        assert all(json.loads(row["release_keys"]) == [] for row in attempts)
        assert all(str(run) not in row["result"] for row in attempts)


async def test_failed_provider_is_cooled_down_without_private_tenant_jobs(monkeypatch):
    async with fixture() as (conn, run, _now, _tenants, _actors, topics):
        await prepare(conn, run, topics)
        connection(monkeypatch, conn)
        fetch = AsyncMock(return_value={"fundHistories": []})
        monkeypatch.setattr(nav, "collect_theme_nav_histories", fetch)
        before = await conn.fetchval("SELECT count(*) FROM workflow.job_runs")
        assert (await nav.schedule_recalled_fund_nav(object(), run))["failed"] == 1
        assert (await nav.schedule_recalled_fund_nav(object(), run))["claimed"] == 0
        assert fetch.await_count == 1
        assert await conn.fetchval("SELECT count(*) FROM workflow.job_runs") == before


async def test_short_unproven_history_is_partial_with_failure_retry_window(monkeypatch):
    async with fixture() as (conn, run, _now, _tenants, _actors, topics):
        await prepare(conn, run, topics)
        connection(monkeypatch, conn)

        async def collect(pool, observed, **kwargs):
            row = fresh_history(CODE, observed)
            row["points"] = row["points"][-10:]
            row.pop("providerHistoryProof")
            return {"fundHistories": [row]}

        monkeypatch.setattr(nav, "collect_theme_nav_histories", collect)
        result = await nav.schedule_recalled_fund_nav(object(), run)
        terminal = json.loads(
            await conn.fetchval(
                "SELECT result FROM market.macro_evidence_attempts WHERE job_key=$1 AND phase='FINISHED'",
                f"OPPORTUNITY_NAV:{CODE}",
            )
        )
        assert terminal["status"] == "PARTIAL", (
            "A short, unproven history has not completed NAV warming"
        )
        assert terminal["usable"] is False
        assert result["usable"] == 0 and result["partial"] == 1


async def test_collection_cancellation_finishes_all_claims_without_history(monkeypatch):
    async with fixture() as (conn, run, _now, _tenants, _actors, topics):
        await prepare(conn, run, topics)
        await conn.execute("RESET ROLE")
        second_code, second_name = "987642", "验证自动补齐冷门材料A"
        await conn.execute(
            "INSERT INTO reference.fund_master(fund_code,fund_name,fund_type,updated_at) VALUES($1,$2,'股票型',clock_timestamp()-interval '1 minute')",
            second_code,
            second_name,
        )
        candidates = json.loads(
            await conn.fetchval(
                "SELECT candidates FROM research.opportunity_fund_cache WHERE run_id=$1",
                run,
            )
        )
        for topic in candidates.values():
            topic["funds"].append(
                {
                    **topic["funds"][0],
                    "fundCode": second_code,
                    "subjectId": second_code,
                    "name": second_name,
                    "fundGroupKey": second_name,
                }
            )
        await conn.execute(
            "UPDATE research.opportunity_fund_cache SET candidates=$2::jsonb WHERE run_id=$1",
            run,
            json.dumps(candidates),
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        connection(monkeypatch, conn)
        started = asyncio.Event()

        async def collect(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(nav, "collect_theme_nav_histories", collect)
        task = asyncio.create_task(nav.schedule_recalled_fund_nav(object(), run))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        phases = await conn.fetch(
            "SELECT phase FROM market.macro_evidence_attempts WHERE job_key=ANY($1::text[])",
            [f"OPPORTUNITY_NAV:{code}" for code in (CODE, second_code)],
        )
        assert [row["phase"] for row in phases].count("CLAIMED") == 2
        assert [row["phase"] for row in phases].count("CANCELLED") == 2
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_research_nav_histories WHERE fund_code=ANY($1::text[])",
                [CODE, second_code],
            )
            == 0
        )


async def test_expired_lease_cannot_persist_or_finish_as_a_new_generation(monkeypatch):
    async with fixture() as (conn, run, _now, _tenants, _actors, topics):
        product = await prepare(conn, run, topics)
        connection(monkeypatch, conn)
        claim = await nav._claim(conn, CODE)
        await conn.execute("RESET ROLE")
        await conn.execute(
            "UPDATE market.macro_evidence_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE run_id=$1",
            claim["id"],
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        now = await conn.fetchval("SELECT clock_timestamp()")
        assert not await nav._finish(object(), claim, product, fresh_history(CODE, now))
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_research_nav_histories WHERE fund_code=$1",
                CODE,
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.macro_evidence_attempts WHERE run_id=$1 AND phase='FINISHED'",
                claim["id"],
            )
            == 0
        )


@pytest.mark.parametrize("boundary", ["foreign", "market", "identity_drift"])
async def test_identity_boundaries_block_provider_or_reject_postfetch_write(
    monkeypatch, boundary
):
    async with fixture() as (conn, run, _now, _tenants, _actors, topics):
        await prepare(conn, run, topics)
        connection(monkeypatch, conn)
        calls = []

        async def collect(pool, observed, **kwargs):
            calls.append(True)
            await conn.execute("RESET ROLE")
            await conn.execute(
                "UPDATE reference.fund_master SET fund_name=fund_name||'变更' WHERE fund_code=$1",
                CODE,
            )
            await conn.execute("SET LOCAL ROLE wealthsail_worker")
            return {"fundHistories": [fresh_history(CODE, observed)]}

        monkeypatch.setattr(nav, "collect_theme_nav_histories", collect)
        if boundary != "identity_drift":
            await conn.execute("RESET ROLE")
            # Rebuild exact READY cache against the new catalog revision below.
            if boundary == "foreign":
                await conn.execute(
                    "UPDATE reference.fund_master SET fund_name='验证美元冷门芯片A' WHERE fund_code=$1",
                    CODE,
                )
            else:
                await conn.execute(
                    "UPDATE research.opportunity_fund_cache SET catalog_day=CURRENT_DATE-1 WHERE run_id=$1",
                    run,
                )
            await conn.execute("SET LOCAL ROLE wealthsail_worker")
        result = await nav.schedule_recalled_fund_nav(object(), run)
        assert result["persisted"] == 0
        assert len(calls) == int(boundary == "identity_drift")
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_research_nav_histories WHERE fund_code=$1",
                CODE,
            )
            == 0
        )


async def test_api_cannot_impersonate_public_collection_service(monkeypatch):
    async with fixture() as (conn, run, _now, _tenants, _actors, topics):
        await prepare(conn, run, topics)
        connection(monkeypatch, conn)
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        with pytest.raises(PermissionError, match="worker service role"):
            await nav.schedule_recalled_fund_nav(object(), run)


async def test_two_worker_connections_serialize_claims_and_roll_back_all_writes():
    # Both independent transactions roll back. The second caller must wait for
    # the first claim's advisory lock, then can claim after that work rolls back.
    async with fixture():
        code = f"{int(uuid4().hex[:8], 16) % 1_000_000:06}"
        pool = await create_pool(
            DSN, min_size=2, max_size=2, runtime_role="wealthsail_worker"
        )
        try:
            async with pool.acquire() as first, pool.acquire() as second:
                first_tx, second_tx = first.transaction(), second.transaction()
                await first_tx.start()
                await second_tx.start()
                pending = None
                try:
                    first_claim = await nav._claim(first, code)
                    second_pid = await second.fetchval("SELECT pg_backend_pid()")
                    pending = asyncio.create_task(nav._claim(second, code))

                    async def wait_for_lock():
                        while not await first.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND NOT granted)",
                            second_pid,
                        ):
                            await asyncio.sleep(0.01)

                    await asyncio.wait_for(wait_for_lock(), 2)
                    assert not pending.done()
                    await first_tx.rollback()
                    first_tx = None
                    second_claim = await asyncio.wait_for(pending, 2)
                    assert first_claim["id"] != second_claim["id"]
                finally:
                    if pending and not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                    if first_tx:
                        await first_tx.rollback()
                    await second_tx.rollback()
        finally:
            await pool.close()


async def test_nav_warmup_selects_latest_research_even_when_older_cycle_archives_later():
    from backend.macro import research_acceptance_repository as acceptance
    from tests.unit.test_manual_research_database import make_run
    from tests.unit.test_opportunity_fund_cache_database import seed
    from tests.unit.test_research_acceptance_database import outcome
    from tests.unit.test_research_topic_repository import registry

    async with fixture(manual=False, archived=True) as (
        conn,
        latest,
        now,
        tenants,
        actors,
        topics,
    ):
        await seed(conn, latest, topics)
        stamp = now - timedelta(days=1)
        _, _, older = await make_run(conn, tenants[0], actors[0], now=stamp)
        value = registry(older, 1)
        value["validatedAt"] = stamp.isoformat()
        await acceptance.enqueue_research_archive(
            conn,
            older,
            {"asOf": stamp.isoformat()},
            {},
            {},
            stamp,
            outcome(older, stamp),
            recorded_at=stamp,
            topic_registry=value,
        )
        claim = await acceptance.claim_research_archive(conn, run_id=older)
        await acceptance.persist_research_archive(conn, claim)
        await seed(conn, older, value["items"])
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        assert await nav._latest_ready_run(Pool()) == latest
