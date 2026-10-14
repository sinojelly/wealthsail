"""Monitor reads downgrade the worker and never run delivery/outbox work."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from importlib import import_module, util
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 14, 18, tzinfo=ZoneInfo("Asia/Shanghai"))


def job():
    assert util.find_spec("backend.jobs.opportunity_monitors"), (
        "monitor service missing"
    )
    return import_module("backend.jobs.opportunity_monitors")


@pytest.mark.asyncio
async def test_tick_reads_under_api_actor_then_persists_with_explicit_owner(
    monkeypatch,
):
    service = job()
    conn = AsyncMock()
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW,
        "direction_id": "a-share:broad",
        "fund_market": "SSE",
    }
    conn.fetch.return_value = [monitor]
    phases = []

    @asynccontextmanager
    async def system(pool):
        phases.append("system")
        yield conn
        phases.append("released")

    @asynccontextmanager
    async def actor(pool, user, tenant):
        assert (user, tenant) == ("actor", "tenant")
        phases.append("actor")
        yield "actor-connection"
        phases.append("actor-released")

    @asynccontextmanager
    async def tx():
        yield

    conn.transaction = tx
    monkeypatch.setattr(service, "system_connection", system)
    monkeypatch.setattr(service, "tenant_transaction", actor)
    context = AsyncMock(return_value={"decision": {}, "portfolio": {}, "notices": []})
    persist = AsyncMock(return_value={"baseline": True, "notice": False})
    monkeypatch.setattr(service, "get_context", context)
    monkeypatch.setattr(service.repository, "record_monitor_result", persist)
    output = await service.sync_opportunity_monitors("pool", NOW)
    assert output == {"checked": 1, "baselines": 1, "notices": 0, "failed": 0}
    assert context.await_args.args[:3] == ("actor-connection", "tenant", "actor")
    assert context.await_args.kwargs["fund_market"] == "SSE"
    assert phases == [
        "system",
        "released",
        "actor",
        "actor-released",
        "system",
        "released",
    ]
    assert persist.await_args.args[1] == monitor


@pytest.mark.asyncio
async def test_independent_loop_retries_and_cancels_owned_work(monkeypatch):
    service = job()
    tick = AsyncMock(side_effect=[RuntimeError("isolated failure"), None])
    monkeypatch.setattr(service, "sync_opportunity_monitors", tick)
    stop = asyncio.Event()
    waits = []

    async def wait(event, seconds):
        waits.append(seconds)
        if len(waits) == 2:
            event.set()

    await service.run_opportunity_monitor_service(
        "pool", now_provider=lambda: NOW, wait=wait, stop_event=stop
    )
    assert waits == [900, 900] and tick.await_count == 2
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def running(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(service, "sync_opportunity_monitors", running)
    task = asyncio.create_task(service.run_opportunity_monitor_service("pool"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
