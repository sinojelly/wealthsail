"""New read-model services have independent status and joined cancellation."""

import asyncio
import json
from unittest.mock import AsyncMock
import pytest
from backend.jobs import macro_worker


@pytest.mark.asyncio
async def test_manual_macro_refresh_reports_market_failure_independently(
    monkeypatch, capsys
):
    from test_macro_worker import _settings

    pool = type("Pool", (), {"close": AsyncMock()})()
    monkeypatch.setattr(macro_worker, "get_settings", _settings)
    monkeypatch.setattr(macro_worker, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(macro_worker, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(
        macro_worker,
        "refresh_macro_history",
        AsyncMock(return_value={"status": "COMPLETED"}),
    )
    monkeypatch.setattr(
        macro_worker,
        "sync_macro_evidence",
        AsyncMock(return_value={"status": "PARTIAL"}),
    )
    market = AsyncMock(side_effect=TimeoutError("secret-provider-url"))
    monkeypatch.setattr(macro_worker, "sync_opportunity_market", market, raising=False)
    assert await macro_worker.main(["--once"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["marketEvidence"] == {
        "status": "UNAVAILABLE",
        "errorClass": "TimeoutError",
    }
    assert market.await_args.kwargs == {"force": True}


@pytest.mark.asyncio
async def test_macro_runtime_starts_and_joins_market_and_monitor_services(monkeypatch):
    entered = set()
    cleaned = []

    def idle(name):
        async def run(pool, *, stop_event):
            entered.add(name)
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.append(name)

        return run

    for name, attr in (
        ("history", "run_macro_service"),
        ("official", "run_macro_evidence_service"),
        ("market", "run_opportunity_market_service"),
        ("monitor", "run_opportunity_monitor_service"),
    ):
        monkeypatch.setattr(macro_worker, attr, idle(name), raising=False)
    task = asyncio.create_task(macro_worker._serve(object()))
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert entered == {"history", "official", "market", "monitor"}
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert set(cleaned) == entered


@pytest.mark.asyncio
async def test_full_worker_joins_new_read_services_before_pool_close(monkeypatch):
    from backend import worker
    from test_worker_startup import _patch_worker_loops, _patch_macro_provider

    events = []

    class Stop:
        def set(self):
            pass

        async def wait(self):
            await asyncio.sleep(0)

    _patch_worker_loops(monkeypatch, events, Stop())
    _patch_macro_provider(monkeypatch, AsyncMock(return_value={"status": "SKIPPED"}))

    def idle(name):
        async def run(pool):
            events.append(name + "-start")
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                events.append(name + "-cleaned")

        return run

    monkeypatch.setattr(
        worker, "_opportunity_market_loop", idle("market"), raising=False
    )
    monkeypatch.setattr(
        worker, "_opportunity_monitor_loop", idle("monitor"), raising=False
    )
    await worker.main()
    assert events.index("market-cleaned") < events.index("pool-close")
    assert events.index("monitor-cleaned") < events.index("pool-close")
