"""History and official services own separate tasks and statuses."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.jobs import macro_worker


@pytest.mark.asyncio
async def test_once_reports_official_failure_without_changing_history_exit(
    monkeypatch, capsys
):
    from backend.config import Settings
    import json

    settings = Settings(
        _env_file=None,
        worker_database_url="postgresql://worker@example.test/db",
        worker_database_pool_mode="direct",
    )
    pool = type("Pool", (), {"close": AsyncMock()})()
    monkeypatch.setattr(macro_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(macro_worker, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(macro_worker, "verify_runtime_login_boundary", AsyncMock())
    monkeypatch.setattr(
        macro_worker,
        "refresh_macro_history",
        AsyncMock(return_value={"status": "COMPLETED", "observations": 240}),
    )
    official = AsyncMock(
        return_value={"status": "UNAVAILABLE", "errorClass": "TimeoutError"}
    )
    monkeypatch.setattr(macro_worker, "sync_macro_evidence", official, raising=False)
    assert await macro_worker.main(["--once"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (
        data["status"] == "COMPLETED"
        and data["officialEvidence"]["status"] == "UNAVAILABLE"
    )
    assert official.await_args.kwargs == {"force": True}
    pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_macro_runtime_starts_official_independently_and_joins_both_on_cancel(
    monkeypatch,
):
    history_entered, official_entered = asyncio.Event(), asyncio.Event()
    cleaned = []

    async def history(pool, *, stop_event):
        history_entered.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            cleaned.append("history")

    async def official(pool, *, stop_event):
        official_entered.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            cleaned.append("official")

    monkeypatch.setattr(macro_worker, "run_macro_service", history)
    monkeypatch.setattr(
        macro_worker, "run_macro_evidence_service", official, raising=False
    )
    running = asyncio.create_task(macro_worker._serve(object()))
    try:
        await asyncio.wait_for(history_entered.wait(), 1)
        await asyncio.sleep(0)
        assert official_entered.is_set()
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    assert sorted(cleaned) == ["history", "official"]


@pytest.mark.asyncio
async def test_production_worker_evidence_task_does_not_block_loops_and_joins_before_pool_close(
    monkeypatch,
):
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
    monkeypatch.setattr(
        worker,
        "now_shanghai",
        lambda: datetime(2026, 10, 14, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    async def official(pool):
        events.append("official-start")
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            events.append("official-cleaned")

    monkeypatch.setattr(worker, "_macro_evidence_loop", official, raising=False)
    await worker.main()
    assert events[:3] == ["scheduler-start", "outbox-start", "data-task-start"]
    assert events.index("official-cleaned") < events.index("pool-close")
