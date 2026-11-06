"""Disposable PostgreSQL: durable backfill, immutable boundaries and tenant RLS."""

from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.investment import opportunity_fund_cache as cache
from backend.macro import research_acceptance_repository as acceptance
from backend.macro.research_topic_repository import save_topic_registry
from tests.unit.test_manual_research_database import make_run
from tests.unit.test_research_acceptance_database import outcome
from tests.unit.test_research_topic_repository import registry

DSN = os.environ.get("OPPORTUNITY_CACHE_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not DSN, reason="owned104 database required")]


@asynccontextmanager
async def fixture(*, manual=True, count=1, topic_values=None, source_checked=False, archived=False):
    target = urlsplit(DSN)
    assert target.hostname == "127.0.0.1" and target.port == 29034
    assert target.path == "/wealthsail_opportunity104_validation"
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    tenants, actors = [uuid4(), uuid4()], [uuid4(), uuid4()]
    now = await conn.fetchval("SELECT clock_timestamp()-interval '1 hour'")
    try:
        for tenant, actor in zip(tenants, actors):
            await conn.execute("INSERT INTO identity.tenants(id,name) VALUES($1,'opportunity cache fixture')", tenant)
            await conn.execute("INSERT INTO identity.profiles(id,nickname) VALUES($1,'opportunity cache actor')", actor)
            await conn.execute("INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')", tenant, actor)
        if manual:
            _, _, run = await make_run(conn, tenants[0], actors[0], now=now)
        else:
            run = await conn.fetchval(
                """INSERT INTO research.daily_research_runs(slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet)
                VALUES($1,$2,$2,'cache-fixture','cache-fixture','{}') RETURNING run_id""", now.date(), now,
            )
        packet = {"asOf": now.isoformat()}
        await conn.execute("UPDATE research.daily_research_runs SET public_packet=$2::jsonb WHERE run_id=$1", run, json.dumps(packet))
        accepted = outcome(run, now)
        if source_checked:
            accepted["accepted"]["sources"][0]["verification"] = "SOURCE_CHECKED"
        value = registry(run, count)
        value["validatedAt"] = now.isoformat()
        if topic_values:
            value["items"][0].update(topic_values)
        if archived:
            await acceptance.enqueue_research_archive(
                conn, run, packet, {}, {}, now, accepted, recorded_at=now,
                topic_registry=value,
            )
            claim = await acceptance.claim_research_archive(conn, run_id=run)
            await acceptance.persist_research_archive(conn, claim)
        else:
            await acceptance.save_research_artifacts(conn, run, packet, {}, {}, now)
            await acceptance.save_validation_outcome(conn, run, accepted, recorded_at=now)
            await save_topic_registry(conn, run, value, now)
        yield conn, run, now, tenants, actors, value["items"]
    finally:
        await tx.rollback()
        await conn.close()


def candidates(topics):
    return {topic["topicId"]: {
        "topicId": topic["topicId"], "status": "MATCHED", "funds": [{
            "fundCode": "000001", "name": "隔离半导体A", "subjectKind": "nav",
            "subjectId": "000001", "market": "FUND_CN", "associationLevel": "NAME_CANDIDATE",
            "fundGroupKey": "隔离半导体", "salesChannel": "OFF_EXCHANGE",
        }], "indirectFunds": [],
    } for topic in topics}


async def seed(conn, run, topics):
    await conn.execute("SET LOCAL ROLE wealthsail_worker")
    claim = await cache.claim_topic_fund_cache(conn, run_id=run)
    assert claim is not None
    finished = await cache.finish_topic_fund_cache(conn, claim, candidates=candidates(topics))
    assert finished["status"] == "READY"
    await conn.execute("RESET ROLE")
    return claim


@pytest.mark.parametrize("manual", [True, False])
async def test_cache_uses_exact_snapshot_member_scope_and_api_has_only_safe_selects(manual):
    async with fixture(manual=manual) as (conn, run, now, tenants, actors, topics):
        await seed(conn, run, topics)
        read_at = await conn.fetchval("SELECT clock_timestamp()")
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        found = await cache.read_cached_topic_funds(conn, run, now, topics, read_at)
        assert found == candidates(topics)
        for query in (
            "SELECT lease_token FROM research.opportunity_fund_cache",
            "UPDATE research.opportunity_fund_cache SET status='PENDING'",
            "DELETE FROM research.opportunity_fund_cache",
            "UPDATE reference.opportunity_fund_catalog_revision SET revision=revision+1",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(query)
        await apply_tenant_context(conn, str(actors[1]), str(tenants[1]))
        other = await cache.read_cached_topic_funds(conn, run, now, topics, read_at)
        assert (other is None) is manual
        await conn.execute("SELECT set_config('request.jwt.claims','{}',true)")
        assert await cache.read_cached_topic_funds(conn, run, now, topics, read_at) is None


async def test_cache_rejects_frozen_boundary_mismatch_and_mutable_fund_fields_in_database():
    async with fixture() as (conn, run, _now, _tenants, _actors, topics):
        claim = await seed(conn, run, topics)
        for update in (
            "content_digest=repeat('b',64)",
            "validated_at=validated_at-interval '1 second'",
            "candidates=jsonb_set(candidates,ARRAY[(SELECT topic_id FROM research.research_topic_entries WHERE snapshot_id=opportunity_fund_cache.snapshot_id LIMIT 1),'funds','0','canBuyNow'],'true')",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(f"UPDATE research.opportunity_fund_cache SET {update} WHERE run_id=$1", run)
        assert claim["attempts"] == 1


async def test_expired_claim_cannot_finish_and_eighth_crash_is_explicitly_failed():
    async with fixture() as (conn, run, now, tenants, actors, topics):
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        first = await cache.claim_topic_fund_cache(conn, run_id=run)
        await conn.execute("UPDATE research.opportunity_fund_cache SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1", run)
        second = await cache.claim_topic_fund_cache(conn, run_id=run)
        assert second["attempts"] == 2 and second["lease_token"] != first["lease_token"]
        assert await cache.finish_topic_fund_cache(conn, first, candidates=candidates(topics)) is None
        assert (await cache.finish_topic_fund_cache(conn, second, candidates=candidates(topics)))["status"] == "READY"
        await conn.execute("UPDATE research.opportunity_fund_cache SET status='COMPUTING',candidates='{}',attempts=8,lease_token=gen_random_uuid(),lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1", run)
        assert await cache.claim_topic_fund_cache(conn, run_id=run) is None
        assert await conn.fetchval("SELECT status FROM research.opportunity_fund_cache WHERE run_id=$1", run) == "FAILED"
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        assert await cache.read_topic_fund_cache_status(conn, run, now, await conn.fetchval("SELECT clock_timestamp()")) == "FAILED"


async def test_catalog_or_algorithm_change_misses_then_background_claim_rebuilds():
    async with fixture() as (conn, run, now, tenants, actors, topics):
        await seed(conn, run, topics)
        for assignment in ("match_version='obsolete-v1'", "catalog_revision=catalog_revision+1", "catalog_day=catalog_day-1"):
            await conn.execute(f"UPDATE research.opportunity_fund_cache SET {assignment} WHERE run_id=$1", run)
            await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
            assert await cache.read_cached_topic_funds(conn, run, now, topics, await conn.fetchval("SELECT clock_timestamp()")) is None
            await conn.execute("RESET ROLE")
            await seed(conn, run, topics)


async def test_worker_failure_is_retryable_without_touching_economic_archive_or_model():
    async with fixture() as (conn, run, _now, _tenants, _actors, _topics):
        original = await conn.fetchval("SELECT outcome FROM research.research_validation_records WHERE run_id=$1", run)

        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        async def failing_discovery(*_):
            raise ConnectionError("PRIVATE model key and connection string")

        result = await cache.precompute_topic_funds(Pool(), run, _discover=failing_discovery)
        assert result == {"claimed": True, "status": "PENDING", "runId": str(run)}
        state = await conn.fetchrow("SELECT attempts,last_error_class,next_attempt_at>clock_timestamp() AS backed_off FROM research.opportunity_fund_cache WHERE run_id=$1", run)
        assert state["attempts"] == 1 and state["last_error_class"] == "ConnectionError" and state["backed_off"]
        assert await conn.fetchval("SELECT outcome FROM research.research_validation_records WHERE run_id=$1", run) == original


async def test_old72_topic_snapshot_is_backfilled_without_request_or_model_execution():
    async with fixture(count=72) as (conn, run, now, tenants, actors, topics):
        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        # No run is supplied: the worker discovers pre-existing archive snapshots.
        result = await cache.precompute_topic_funds(Pool())
        assert result == {"claimed": True, "status": "READY", "runId": str(run)}
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))
        found = await cache.read_cached_topic_funds(conn, run, now, topics, await conn.fetchval("SELECT clock_timestamp()"))
        assert len(found) == 72
        assert all(not {"subscription", "canBuyNow", "quantitativeScore", "confirmedNav"} & fund.keys()
                   for row in found.values() for field in ("funds", "indirectFunds") for fund in row[field])
        await conn.execute("RESET ROLE")
        assert await cache.precompute_topic_funds(Pool(), run) == {"claimed": False, "status": "PENDING"}


@pytest.mark.parametrize("queued_old,old_count", [(True, 1), (False, 9)])
async def test_latest_archived_cycle_is_backfilled_before_older_backlog(queued_old, old_count):
    async with fixture(manual=False, archived=True) as (conn, latest, now, tenants, actors, _topics):
        # Older research is archived later, so created_at cannot stand in for
        # the research clock. Nine snapshots also exceed a single seed batch.
        old_runs = []
        for index in range(old_count):
            stamp = now - timedelta(days=index + 1)
            older = await conn.fetchval(
                """INSERT INTO research.daily_research_runs(slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet)
                VALUES($1,$2,$2,'cache-backlog','cache-fixture','{}') RETURNING run_id""",
                stamp.date(), stamp,
            )
            value = registry(older, 1)
            value["validatedAt"] = stamp.isoformat()
            await acceptance.enqueue_research_archive(
                conn, older, {"asOf": stamp.isoformat()}, {}, {}, stamp,
                outcome(older, stamp), recorded_at=stamp, topic_registry=value,
            )
            claim = await acceptance.claim_research_archive(conn, run_id=older)
            await acceptance.persist_research_archive(conn, claim)
            old_runs.append(older)
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        if queued_old:
            claim = await cache.claim_topic_fund_cache(conn, run_id=old_runs[0])
            await cache.finish_topic_fund_cache(conn, claim, error_class="TimeoutError")
            await conn.execute(
                """UPDATE research.opportunity_fund_cache SET next_attempt_at=clock_timestamp()-interval '1 day'
                WHERE run_id=$1""", old_runs[0],
            )

        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        async def local_discovery(_conn, topics, _packet, _now):
            return candidates(topics)

        assert await conn.fetchval(
            "SELECT status FROM research.research_validation_archive_queue WHERE run_id=$1", latest,
        ) == "ARCHIVED"
        assert await conn.fetchval(
            "SELECT count(*) FROM research.opportunity_fund_cache WHERE run_id=$1", latest,
        ) == 0
        assert await cache.precompute_topic_funds(Pool(), _discover=local_discovery) == {
            "claimed": True, "status": "READY", "runId": str(latest),
        }
        assert await conn.fetchval(
            "SELECT status FROM research.opportunity_fund_cache WHERE run_id=$1", latest,
        ) == "READY"
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM research.opportunity_fund_cache WHERE run_id=ANY($1::uuid[]) AND status='READY')",
            old_runs,
        )


async def test_catalog_revision_changes_once_per_committed_catalog_write_transaction():
    conn = await asyncpg.connect(DSN)
    code = "990041"
    try:
        before = await conn.fetchval("SELECT revision FROM reference.opportunity_fund_catalog_revision")
        async with conn.transaction():
            await conn.execute("INSERT INTO reference.fund_master(fund_code,fund_name,fund_type) VALUES($1,'隔离版本测试A','股票型')", code)
            first = await conn.fetchval("SELECT revision FROM reference.opportunity_fund_catalog_revision")
            await conn.execute("UPDATE reference.fund_master SET fund_name='隔离版本测试C' WHERE fund_code=$1", code)
            assert await conn.fetchval("SELECT revision FROM reference.opportunity_fund_catalog_revision") == first == before + 1
        async with conn.transaction():
            await conn.execute("UPDATE reference.fund_master SET fund_name='隔离版本测试E' WHERE fund_code=$1", code)
            assert await conn.fetchval("SELECT revision FROM reference.opportunity_fund_catalog_revision") == first + 1
    finally:
        await conn.execute("DELETE FROM reference.fund_master WHERE fund_code=$1", code)
        await conn.close()


async def test_migration_readback_rejects_api_write_or_missing_force_rls():
    from scripts import migrate

    assert hasattr(migrate, "_readback_opportunity_fund_cache"), "Deployment must verify new cache privileges"
    async with fixture() as (conn, *_):
        await migrate._readback_opportunity_fund_cache(conn)
        for damage in (
            "GRANT UPDATE ON research.opportunity_fund_cache TO wealthsail_api",
            "ALTER TABLE research.opportunity_fund_cache NO FORCE ROW LEVEL SECURITY",
            "ALTER TABLE reference.fund_master DISABLE TRIGGER opportunity_catalog_revision",
        ):
            class Restore(Exception):
                pass

            with pytest.raises(Restore):
                async with conn.transaction():
                    await conn.execute(damage)
                    with pytest.raises(RuntimeError, match="opportunity fund cache"):
                        await migrate._readback_opportunity_fund_cache(conn)
                    raise Restore()


@pytest.mark.parametrize("title,field,polarity", [
    ("隔离微波量子订单增长", "recommendedFunds", "POSITIVE_OPPORTUNITY"),
    ("隔离微波量子订单下滑", "relatedRiskFunds", "NEGATIVE_RISK"),
    ("美国PMI与汇率变化", "observationFunds", "MACRO_OBSERVATION"),
])
async def test_api_reader_keeps_polarity_and_local_nav_live_after_cached_discovery(monkeypatch, title, field, polarity):
    from backend.macro.research_opportunities import read_topic_fund_recommendations
    from tests.unit import test_live_fund_plans_v6 as nav_fixture

    topic_values = {"title": title, "summary": title, "match": {
        "status": "STANDARD", "industryIds": [], "unresolvedClaims": ["隔离微波量子"],
        "relatedIndustryIds": [], "evidence": [], "registryVersion": "industry-registry-v1",
    }}
    async with fixture(topic_values=topic_values, source_checked=True) as (conn, run, _now, tenants, actors, _topics):
        code = "990042"
        await conn.execute("INSERT INTO reference.fund_master(fund_code,fund_name,fund_type,updated_at) VALUES($1,'隔离微波量子ETF联接A','股票型-指数',clock_timestamp()-interval '1 hour')", code)
        viewed = await conn.fetchval("SELECT clock_timestamp()-interval '1 second'")
        monkeypatch.setattr(nav_fixture, "NOW", viewed)
        history = nav_fixture.history(code)
        await conn.execute("INSERT INTO market.fund_nav_chart_histories(fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$2,$3::jsonb)", code, viewed, json.dumps(history))

        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        assert (await cache.precompute_topic_funds(Pool(), run))["status"] == "READY"
        await apply_tenant_context(conn, str(actors[0]), str(tenants[0]))

        class ReadOnly:
            async def fetch(self, sql, *args):
                assert sql.lstrip().startswith(("SELECT", "WITH")) and "unnest" not in sql
                return await conn.fetch(sql, *args)

            async def fetchrow(self, sql, *args):
                assert sql.lstrip().startswith("SELECT")
                return await conn.fetchrow(sql, *args)

            async def fetchval(self, sql, *args):
                assert sql.lstrip().startswith("SELECT")
                return await conn.fetchval(sql, *args)

        result = await read_topic_fund_recommendations(ReadOnly(), run, await conn.fetchval("SELECT clock_timestamp()"))
        item = result["items"][0]
        assert item["topicPolarity"] == polarity
        fund = next(fund for fund in item[field] if fund["fundCode"] == code)
        assert fund["pricePosition"]["status"] != "DATA_PENDING"
        assert fund["confirmedNav"]["sourceUri"] == f"https://fund.eastmoney.com/{code}.html"
        assert fund["actionable"] is False and fund["canBuyNow"] is False
        if field != "recommendedFunds":
            assert item["recommendedFunds"] == []
