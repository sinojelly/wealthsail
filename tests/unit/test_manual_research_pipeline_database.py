"""Real PostgreSQL worker flow with fake public providers and a fake model only."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from test_daily_pipeline import NOW, valid_partial_bundle
from test_industry_cycle import checks
from test_manual_research_database import DSN, fixture

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned loopback DB required"),
]


def runtime_boundary(monkeypatch, clock):
    from backend.jobs import (
        alpha_public,
        daily_research as daily,
        research_debug as consumer,
    )
    from backend.integrations.daily_sources import industry_cycle_research_tasks

    state = {
        "calls": 0,
        "active": 0,
        "peak": 0,
        "bad_nav": False,
        "schema_bad": False,
        "timeout": False,
        "hold": None,
        "entered": None,
        "packets": [],
    }
    configured = SimpleNamespace(
        app_env="production",
        daily_research_enabled=False,
        daily_research_debug_enabled=False,
        daily_research_manual_enabled=True,
        daily_research_codex_path="fake-model",
        daily_research_model=None,
        daily_research_timeout_seconds=60,
    )
    monkeypatch.setattr(consumer, "now_shanghai", lambda: clock[0])
    monkeypatch.setattr(daily, "now_shanghai", lambda: clock[0])
    monkeypatch.setattr(
        daily,
        "preflight_codex",
        AsyncMock(return_value={"executable": "fake-model", "version": "fixture"}),
    )
    monkeypatch.setattr(daily, "collect_daily_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(
        daily,
        "prepare_research_inputs",
        AsyncMock(side_effect=lambda _pool, _now, external, **kwargs: external),
    )
    monkeypatch.setattr(daily, "verify_research_sources", AsyncMock(return_value={}))
    network = AsyncMock(
        side_effect=AssertionError("owned runtime test must not call HTTP")
    )
    monkeypatch.setattr("httpx.AsyncClient.send", network)

    async def inference(_settings, *, operation, **kwargs):
        return await operation()

    monkeypatch.setattr(daily, "trace_inference", inference)
    real_load = daily.load_public_packet

    async def load(*args, **kwargs):
        packet = await real_load(*args, **kwargs)
        state["packets"].append(packet)
        return packet

    monkeypatch.setattr(daily, "load_public_packet", load)

    async def model(*args, **kwargs):
        packet = state["packets"][-1]
        state["calls"] += 1
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            if state["hold"] is not None and state["calls"] == 1:
                state["entered"].set()
                await state["hold"].wait()
            if state["timeout"]:
                raise TimeoutError("fake model exit cannot be confirmed")
            if state["schema_bad"]:
                return {"summary": None}
            return {
                "summary": f"owned manual result {state['calls']}",
                "macroCycle": "本轮仅基于已冻结的公开数据。",
                "coverage": [],
                "sources": [],
                "events": [],
                "upcoming": [],
                "energy": [],
                "actionablePlans": [],
                "valueDirections": [],
                "industryCycleReviews": [
                    {
                        "industry": row["industry"],
                        "sourceIds": [],
                        "riskChecks": checks(),
                    }
                    for row in industry_cycle_research_tasks(packet, clock[0])[
                        "candidates"
                    ]
                ],
            }
        finally:
            state["active"] -= 1

    monkeypatch.setattr(daily, "run_codex_research", model)

    async def collect(stamp):
        bundle = valid_partial_bundle(stamp)
        return {
            "asOf": stamp.isoformat(),
            "observations": bundle["observations"],
            "catalysts": [],
            "odds": [],
            "launches": [],
            "sourceResults": bundle["sourceResults"],
        }

    async def nav(_pool, stamp, **kwargs):
        return {
            "fundHistories": []
            if state["bad_nav"]
            else valid_partial_bundle(stamp)["fundHistories"],
            "themeNavSourceResults": {"TEST_NAV": {"status": "SUCCESS", "records": 1}},
        }

    async def refresh(pool, stamp, **kwargs):
        return await alpha_public.sync_alpha_public(
            pool,
            stamp,
            **kwargs,
            collector=collect,
            nav_collector=nav,
            now_provider=lambda: clock[0],
        )

    monkeypatch.setattr(consumer, "sync_alpha_public", refresh)
    return consumer, daily, state, configured, refresh, network


async def test_real_manual_worker_refreshes_sources_replaces_report_and_retains_uncertain_guard(
    monkeypatch,
):
    from backend.common.database import create_pool
    from backend.macro import research_debug_repository as requests
    from backend.macro.daily_repository import consumed, read_daily_research

    clock = [NOW]
    consumer, _daily, state, configured, _refresh, network = runtime_boundary(
        monkeypatch, clock
    )
    async with fixture(committed=True) as (admin, tenant, actor, _other):
        pool = await create_pool(
            DSN, min_size=1, max_size=4, runtime_role="wealthsail_worker"
        )
        try:
            successes = []
            for attempt in range(2):
                clock[0] = NOW + timedelta(minutes=attempt)
                command = uuid4()
                request = await requests.enqueue_refresh(
                    admin, tenant, actor, command, clock[0], request_mode="MANUAL"
                )
                result = await consumer.sync_debug_refresh(pool, configured)
                assert result["status"] == "READY"
                successes.append(result)
                assert (
                    await requests.enqueue_refresh(
                        admin, tenant, actor, command, clock[0], request_mode="MANUAL"
                    )
                )["id"] == request["id"]
                assert (await consumer.sync_debug_refresh(pool, configured))[
                    "status"
                ] == "IDLE"
            assert successes[0]["batchId"] != successes[1]["batchId"]
            assert successes[0]["reportRunId"] != successes[1]["reportRunId"]
            assert state["calls"] == 2 and not await consumed(admin, clock[0])
            current = await read_daily_research(admin, clock[0], enabled=False)
            assert (
                current["reportRunId"] == successes[-1]["reportRunId"]
                and current["reportRunMode"] == "MANUAL"
            )
            assert current["report"]["inputQuality"]["allowed"] is True
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM research.daily_research_model_guards WHERE released_at IS NULL"
                )
                == 0
            )

            clock[0] += timedelta(minutes=1)
            state["bad_nav"] = True
            await requests.enqueue_refresh(
                admin, tenant, actor, uuid4(), clock[0], request_mode="MANUAL"
            )
            assert (await consumer.sync_debug_refresh(pool, configured))[
                "status"
            ] == "FAILED"
            assert state["calls"] == 2
            assert (await read_daily_research(admin, clock[0], enabled=False))[
                "reportRunId"
            ] == successes[-1]["reportRunId"]

            clock[0] += timedelta(minutes=1)
            state.update(bad_nav=False, schema_bad=True)
            request = await requests.enqueue_refresh(
                admin, tenant, actor, uuid4(), clock[0], request_mode="MANUAL"
            )
            assert (await consumer.sync_debug_refresh(pool, configured))[
                "status"
            ] == "FAILED"
            failure = await requests.read_refresh(
                admin, tenant, actor, UUID(request["id"]), clock[0]
            )
            assert (
                failure["failureStage"] == "REPORT_VALIDATION"
                and failure["failureReasonCode"] == "SCHEMA_VALIDATION_FAILED"
            )
            assert (await read_daily_research(admin, clock[0], enabled=False))[
                "reportRunId"
            ] == successes[-1]["reportRunId"]
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM research.daily_research_model_guards WHERE released_at IS NULL"
                )
                == 0
            )
            assert (await consumer.sync_debug_refresh(pool, configured))[
                "status"
            ] == "IDLE"

            clock[0] += timedelta(minutes=1)
            state.update(schema_bad=False, timeout=True)
            await requests.enqueue_refresh(
                admin, tenant, actor, uuid4(), clock[0], request_mode="MANUAL"
            )
            assert (await consumer.sync_debug_refresh(pool, configured))[
                "status"
            ] == "UNCERTAIN"
            assert state["calls"] == 4
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM research.daily_research_model_guards WHERE released_at IS NULL"
                )
                == 1
            )
            clock[0] += timedelta(minutes=36)
            await requests.enqueue_refresh(
                admin, tenant, actor, uuid4(), clock[0], request_mode="MANUAL"
            )
            assert (await consumer.sync_debug_refresh(pool, configured))[
                "status"
            ] == "IDLE"
            assert state["calls"] == 4 and network.await_count == 0
        finally:
            await pool.close()


async def test_real_scheduled_worker_waits_for_manual_model_without_consuming_slot(
    monkeypatch,
):
    from backend.common.database import create_pool
    from backend.macro import research_debug_repository as requests
    from backend.macro.daily_repository import consumed

    clock = [NOW]
    consumer, daily, state, configured, refresh, network = runtime_boundary(
        monkeypatch, clock
    )
    state["hold"], state["entered"] = asyncio.Event(), asyncio.Event()
    waiting, resume = asyncio.Event(), asyncio.Event()

    async def wait_for_guard():
        waiting.set()
        await resume.wait()

    monkeypatch.setattr(daily, "_wait_model_slot", wait_for_guard)
    automatic = SimpleNamespace(**{**vars(configured), "daily_research_enabled": True})
    async with fixture(committed=True) as (admin, tenant, actor, _other):
        pool = await create_pool(
            DSN, min_size=1, max_size=4, runtime_role="wealthsail_worker"
        )
        scheduled_batch = None
        manual_task = scheduled_task = None
        try:
            collected = await refresh(pool, clock[0])
            assert collected["status"] == "PARTIAL"
            scheduled_batch = UUID(collected["batchId"])
            await requests.enqueue_refresh(
                admin, tenant, actor, uuid4(), clock[0], request_mode="MANUAL"
            )
            manual_task = asyncio.create_task(
                consumer.sync_debug_refresh(pool, configured)
            )
            await asyncio.wait_for(state["entered"].wait(), timeout=10)
            scheduled_task = asyncio.create_task(
                daily.sync_daily_research(pool, automatic, clock[0])
            )
            await asyncio.wait_for(waiting.wait(), timeout=10)
            assert state["calls"] == state["active"] == 1
            assert not await consumed(admin, clock[0])
            state["hold"].set()
            assert (await asyncio.wait_for(manual_task, timeout=10))[
                "status"
            ] == "READY"
            resume.set()
            assert (await asyncio.wait_for(scheduled_task, timeout=10))[
                "status"
            ] == "READY"
            assert state["calls"] == 2 and state["peak"] == 1
            assert await consumed(admin, clock[0])
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM research.daily_research_model_guards WHERE released_at IS NULL"
                )
                == 0
            )
            assert network.await_count == 0
        finally:
            state["hold"].set()
            resume.set()
            for task in (manual_task, scheduled_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (manual_task, scheduled_task) if task is not None),
                return_exceptions=True,
            )
            await pool.close()
            if scheduled_batch is not None:
                await admin.execute(
                    "DELETE FROM research.daily_research_pipelines WHERE source_batch_id=$1",
                    scheduled_batch,
                )
                await admin.execute(
                    "DELETE FROM research.daily_research_model_guards WHERE run_id IN(SELECT run_id FROM research.daily_research_runs WHERE source_batch_id=$1)",
                    scheduled_batch,
                )
                await admin.execute(
                    "DELETE FROM research.daily_research_results WHERE run_id IN(SELECT run_id FROM research.daily_research_runs WHERE source_batch_id=$1)",
                    scheduled_batch,
                )
                await admin.execute(
                    "DELETE FROM research.daily_research_runs WHERE source_batch_id=$1",
                    scheduled_batch,
                )
                await admin.execute(
                    "DELETE FROM market.alpha_public_batches WHERE id=$1",
                    scheduled_batch,
                )
