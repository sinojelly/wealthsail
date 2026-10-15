"""Persistent per-index research, independent of report/outbox tasks."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from importlib import import_module
from unittest.mock import AsyncMock

import pytest

from test_index_official import NOW


def module():
    return import_module("backend.jobs.index_research")


def test_refresh_clock_24h_success_and_one_hour_failure_retry():
    job = module()
    assert job.refresh_due(NOW, None)
    previous = {
        "recorded_at": NOW,
        "result": {"status": "COMPLETED", "configurationHash": "a"},
    }
    assert not job.refresh_due(NOW + timedelta(hours=23), previous, "a")
    assert job.refresh_due(NOW + timedelta(hours=24), previous, "a")
    previous["result"]["status"] = "FAILED"
    assert not job.refresh_due(NOW + timedelta(minutes=59), previous, "a")
    assert job.refresh_due(NOW + timedelta(hours=1), previous, "a")
    assert job.refresh_due(NOW, previous, "changed-config")


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status", [None, 403])
async def test_one_index_failure_isolated_and_network_has_no_connection(
    monkeypatch, http_status
):
    job = module()
    active = 0

    @asynccontextmanager
    async def connection(pool):
        nonlocal active
        active += 1
        try:
            yield AsyncMock()
        finally:
            active -= 1

    async def refresh(pool, settings, kind, key, as_of, **kwargs):
        assert active == 0
        if key == "a500":
            if http_status:
                import httpx

                response = httpx.Response(
                    http_status,
                    request=httpx.Request(
                        "GET", "https://www.csindex.com.cn/private?token=secret"
                    ),
                )
                raise httpx.HTTPStatusError(
                    "secret response", request=response.request, response=response
                )
            raise TimeoutError()
        return {"asOf": "2026-10-14"}

    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(
        job,
        "subjects",
        AsyncMock(return_value=[{"id": key} for key in ("hs300", "a500", "zz500")]),
    )
    monkeypatch.setattr(
        job,
        "claim_index",
        AsyncMock(return_value={"id": "claim", "configurationHash": "a"}),
    )
    finish = AsyncMock()
    monkeypatch.setattr(job, "finish_index", finish)
    monkeypatch.setattr(job, "refresh_subject", refresh)
    monkeypatch.setattr(job, "ensure_trading_calendar", AsyncMock())
    result = await job.sync_index_research(object(), object(), NOW)
    assert result["status"] == "PARTIAL"
    assert result["indices"]["a500"]["status"] == "FAILED"
    assert result["indices"]["a500"].get("httpStatus") == http_status
    assert "secret" not in str(result)
    assert result["indices"]["hs300"]["status"] == "COMPLETED"
    assert finish.await_count == 3


@pytest.mark.asyncio
async def test_cancellation_persisted_and_propagates(monkeypatch):
    job = module()

    @asynccontextmanager
    async def connection(pool):
        yield AsyncMock()

    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(job, "subjects", AsyncMock(return_value=[{"id": "hs300"}]))
    monkeypatch.setattr(
        job,
        "claim_index",
        AsyncMock(return_value={"id": "claim", "configurationHash": "a"}),
    )
    monkeypatch.setattr(
        job, "refresh_subject", AsyncMock(side_effect=asyncio.CancelledError())
    )
    monkeypatch.setattr(job, "ensure_trading_calendar", AsyncMock())
    finish = AsyncMock()
    monkeypatch.setattr(job, "finish_index", finish)
    with pytest.raises(asyncio.CancelledError):
        await job.sync_index_research(object(), object(), NOW)
    assert finish.await_args.kwargs["cancelled"] is True


@pytest.mark.asyncio
async def test_service_boot_then_15_minute_wake_and_stops(monkeypatch):
    job = module()
    stop = asyncio.Event()
    sync = AsyncMock()
    consume = AsyncMock(return_value=False)
    delays = []

    async def wait(event, seconds):
        delays.append(seconds)
        if 5 in delays and 900 in delays:
            stop.set()
        await event.wait()

    monkeypatch.setattr(job, "sync_index_research", sync)
    monkeypatch.setattr(job, "consume_index_refresh", consume)
    await asyncio.wait_for(
        job.run_index_research_service(object(), object(), stop_event=stop, wait=wait),
        1,
    )
    sync.assert_awaited_once()
    consume.assert_awaited_once()
    assert set(delays) == {5, 900}


def test_migration_registers_only_empty_defaults_without_fabricated_publish_hour():
    from pathlib import Path

    sql = Path("migrations/080_index_research_sources.sql").read_text("utf-8")
    for code in ("000300", "000510", "000905"):
        assert code in sql
    assert "PREVIOUS_COMPLETE_SESSION" in sql and "RETRIEVAL_ONLY" in sql
    assert "publish_hour IS NULL" in sql and "verified_at IS NULL" in sql
    assert "(id,code) IN" in sql


@pytest.mark.asyncio
async def test_macro_worker_owns_and_awaits_cancelled_index_service(monkeypatch):
    from backend.jobs import macro_worker as worker

    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def index(pool, *, stop_event):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    async def macro(pool, *, stop_event):
        await entered.wait()

    monkeypatch.setattr(worker, "run_index_research_service", index, raising=False)
    monkeypatch.setattr(worker, "run_macro_service", macro)
    monkeypatch.setattr(worker, "run_macro_evidence_service", AsyncMock())
    monkeypatch.setattr(worker, "run_opportunity_market_service", AsyncMock())
    monkeypatch.setattr(worker, "run_opportunity_monitor_service", AsyncMock())
    await asyncio.wait_for(worker._serve(object()), 1)
    assert cleaned.is_set()
