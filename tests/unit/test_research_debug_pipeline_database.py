"""Real Worker SQL + fake providers prove same-day source/report isolation."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from test_research_debug_database import DSN, NOW, fixture

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned debug DB required"),
]


async def test_two_debug_updates_use_new_batches_and_source_failure_keeps_previous_report(
    monkeypatch,
):
    from backend.common.database import create_pool
    from backend.jobs import (
        research_debug as debug,
        alpha_public,
        daily_research as daily,
    )
    from backend.macro import research_debug_repository as repo
    from backend.macro.daily_repository import read_daily_research, consumed

    clock = [NOW]
    settings = SimpleNamespace(
        app_env="test",
        daily_research_debug_enabled=True,
        daily_research_enabled=True,
        daily_research_codex_path="test",
        daily_research_model=None,
        daily_research_timeout_seconds=900,
    )
    model = AsyncMock(
        return_value={
            "summary": "isolated model fixture",
            "coverage": [],
            "sources": [],
            "events": [],
            "upcoming": [],
            "energy": [],
            "macroCycle": "isolated fixture",
            "actionablePlans": [],
            "valueDirections": [],
        }
    )
    monkeypatch.setattr(debug, "now_shanghai", lambda: clock[0])
    monkeypatch.setattr(daily, "now_shanghai", lambda: clock[0])
    monkeypatch.setattr(
        daily,
        "preflight_codex",
        AsyncMock(return_value={"executable": "test", "version": "test"}),
    )
    monkeypatch.setattr(daily, "collect_daily_sources", AsyncMock(return_value={}))
    prepared = AsyncMock(side_effect=lambda pool, stamp, external, **kwargs: external)
    monkeypatch.setattr(daily, "prepare_research_inputs", prepared)
    network = AsyncMock(side_effect=AssertionError("SQL fixture must not call HTTP"))
    monkeypatch.setattr("httpx.AsyncClient.send", network)
    monkeypatch.setattr(daily, "run_codex_research", model)
    monkeypatch.setattr(daily, "verify_research_sources", AsyncMock(return_value={}))
    # Keep the actual validator: the fake model must answer the current frozen
    # industry's required reviews rather than relying on a legacy empty schema.
    from copy import deepcopy
    from backend.integrations.daily_sources import industry_cycle_research_tasks
    from test_industry_cycle import checks

    packets = []
    real_load = daily.load_public_packet

    async def load_packet(*args, **kwargs):
        result = await real_load(*args, **kwargs)
        packets.append(result)
        return result

    async def model_report(*args, **kwargs):
        result = deepcopy(model.return_value)
        result["industryCycleReviews"] = [
            {"industry": row["industry"], "sourceIds": [], "riskChecks": checks()}
            for row in industry_cycle_research_tasks(packets[-1], clock[0])[
                "candidates"
            ]
        ]
        return result

    monkeypatch.setattr(daily, "load_public_packet", load_packet)
    model.side_effect = model_report
    fail = [False]

    async def collect(_stamp):
        if fail[0]:
            raise TimeoutError()
        return {
            "asOf": clock[0].isoformat(),
            "observations": [
                {
                    "code": "CN_T10",
                    "value": "1.8",
                    "referenceDate": clock[0].date().isoformat(),
                    "sourceKey": "AKSHARE_CN_T10",
                    "sourceUri": "https://data.eastmoney.com/cjsj/zmgzsyl.html",
                    "retrievedAt": clock[0].isoformat(),
                }
            ],
            "catalysts": [],
            "odds": [],
            "launches": [],
            "sourceResults": {"TEST": {"status": "SUCCESS"}},
        }

    async def nav(*_):
        return {"fundHistories": [], "themeNavSourceResults": {}}

    async def refresh(pool, stamp, **kwargs):
        return await alpha_public.sync_alpha_public(
            pool,
            stamp,
            **kwargs,
            collector=collect,
            nav_collector=nav,
            now_provider=lambda: clock[0],
        )

    monkeypatch.setattr(debug, "sync_alpha_public", refresh)
    async with fixture(committed=True) as (admin, tenant, owner, _other):
        pool = await create_pool(
            DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
        )
        try:
            results = []
            for attempt in range(2):
                clock[0] = NOW + timedelta(minutes=attempt)
                await repo.enqueue_refresh(admin, tenant, owner, uuid4(), clock[0])
                results.append(await debug.sync_debug_refresh(pool, settings))
                assert results[-1]["status"] == "READY"
            assert results[0]["batchId"] != results[1]["batchId"]
            assert results[0]["reportRunId"] != results[1]["reportRunId"]
            assert not await consumed(admin, clock[0])
            for result in results:
                row = await admin.fetchrow(
                    "SELECT source_batch_id,debug_request_id,public_packet FROM research.daily_research_runs WHERE run_id=$1",
                    UUID(result["reportRunId"]),
                )
                assert str(row["source_batch_id"]) == result["batchId"]
                assert str(row["debug_request_id"]) == result["requestId"]
            fail[0] = True
            clock[0] += timedelta(minutes=1)
            await repo.enqueue_refresh(admin, tenant, owner, uuid4(), clock[0])
            assert (await debug.sync_debug_refresh(pool, settings))[
                "status"
            ] == "FAILED"
            assert (await read_daily_research(admin, clock[0]))[
                "reportRunId"
            ] == results[-1]["reportRunId"]
            assert model.await_count == 2
            assert prepared.await_count == 2
            assert network.await_count == 0
        finally:
            await pool.close()
