"""Transient database failures preserve daily scheduling and replay boundaries."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import errno
import logging
import socket
from unittest.mock import AsyncMock

import asyncpg
import pytest

from test_daily_pipeline_edges import isolate_edges, stored_pipeline
from test_daily_research_job import NOW, settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", [asyncpg.InsufficientPrivilegeError, asyncpg.PostgresSyntaxError]
)
async def test_permanent_archive_failure_stops_service_without_minute_retries(
    monkeypatch, caplog, error_type
):
    mod, state, repo = isolate_edges(monkeypatch)
    repo.archive_prior_pipelines.side_effect = [False, error_type("private details")]
    stop = asyncio.Event()

    async def stop_waiting(event, seconds):
        event.set()

    wait = AsyncMock(side_effect=stop_waiting)
    with caplog.at_level(logging.ERROR, logger="wealthsail.daily_research"):
        with pytest.raises(error_type):
            await mod.run_daily_research_service(
                object(),
                settings(),
                stop_event=stop,
                now_provider=lambda: NOW,
                wait=wait,
            )
    wait.assert_not_awaited()
    repo.claim_pipeline.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()
    assert not state["consumed"]
    assert f"error_class={error_type.__name__}" in caplog.text
    assert "retryable=false action=stop_service" in caplog.text
    assert "private details" not in caplog.text


def unavailable_acquisition(monkeypatch, mod, error, *, acquisition):
    from backend.common.database import system_connection

    connection = mod.system_connection

    class Pool:
        attempts = 0

        @asynccontextmanager
        async def acquire(self):
            self.attempts += 1
            if self.attempts == acquisition:
                raise error
            async with connection(self) as conn:
                conn.add_query_logger = lambda callback: None
                conn.remove_query_logger = lambda callback: None
                yield conn

    # Keep the production connection context: the failure comes from asyncpg's
    # acquisition boundary, before any repository call can normalize it.
    monkeypatch.setattr(mod, "system_connection", system_connection)
    return Pool()


def ready_same_day(monkeypatch, mod, repo, clock):
    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])
    repo.scheduled_source_batch.side_effect = None
    repo.scheduled_source_batch.return_value = {
        "id": "source-batch",
        "run_date": clock[0].date(),
        "status": "READY",
        "completed_at": clock[0],
    }
    mod.load_public_packet.side_effect = lambda *_, **__: {"asOf": clock[0].isoformat()}


@pytest.mark.asyncio
async def test_initial_database_failure_cannot_skip_a_plan_due_during_retry(
    monkeypatch,
):
    mod, state, repo = isolate_edges(monkeypatch)
    clock = [NOW.replace(hour=6, minute=59, second=30)]
    ready_same_day(monkeypatch, mod, repo, clock)
    repo.read_pipeline.side_effect = [ConnectionError("database reconnect"), None, None]
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        if len(delays) == 1:
            assert seconds == 60
            assert not state["consumed"]
            clock[0] += timedelta(seconds=seconds)
        else:
            event.set()

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_catch_up=False),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert delays[0] == 60
    assert repo.claim_pipeline.await_args.args[1] == NOW.replace(
        hour=7, minute=0, second=30
    )
    assert state["terminal"][-1]["status"] == "READY"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "acquisition,error",
    [
        (1, socket.gaierror(socket.EAI_AGAIN, "temporary name resolution failure")),
        (3, socket.gaierror(socket.EAI_AGAIN, "temporary name resolution failure")),
        (3, OSError(errno.ENETUNREACH, "network temporarily unreachable")),
    ],
    ids=["initialize-dns", "preclaim-dns", "preclaim-network"],
)
async def test_transient_pool_acquisition_failure_preserves_the_daily_plan(
    monkeypatch, acquisition, error
):
    mod, state, repo = isolate_edges(monkeypatch)
    clock = [NOW.replace(hour=7)]
    ready_same_day(monkeypatch, mod, repo, clock)
    # Startup reads, loop maintenance, then sync's preclaim state read.
    pool = unavailable_acquisition(monkeypatch, mod, error, acquisition=acquisition)
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        if len(delays) == 1:
            assert seconds == 60, "transient acquisition must preserve today's plan"
            assert not state["consumed"]
            clock[0] += timedelta(seconds=seconds)
        else:
            event.set()

    await mod.run_daily_research_service(
        pool,
        settings(daily_research_catch_up=False),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert repo.claim_pipeline.await_args.args[1] == NOW.replace(hour=7, minute=1)
    assert state["terminal"][-1]["status"] == "READY"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        socket.gaierror(socket.EAI_NONAME, "database name does not exist"),
        PermissionError(errno.EACCES, "connection permission denied"),
    ],
    ids=["permanent-dns", "permission-denied"],
)
async def test_permanent_pool_acquisition_failure_does_not_retry_daily_research(
    monkeypatch, error
):
    mod, state, repo = isolate_edges(monkeypatch)
    pool = unavailable_acquisition(monkeypatch, mod, error, acquisition=3)
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        event.set()

    await mod.run_daily_research_service(
        pool,
        settings(),
        stop_event=stop,
        now_provider=lambda: NOW,
        wait=wait,
    )
    assert delays == [23 * 3600]
    assert not state["consumed"]
    repo.claim_pipeline.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage,error_type",
    [
        ("read", ConnectionError),
        ("claim", ConnectionError),
        ("read", TimeoutError),
        ("read", asyncpg.CannotConnectNowError),
        ("claim", asyncpg.ConnectionDoesNotExistError),
        ("read", asyncpg.TooManyConnectionsError),
        ("claim", asyncpg.SerializationError),
        ("claim", asyncpg.DeadlockDetectedError),
        ("claim", asyncpg.LockNotAvailableError),
    ],
)
async def test_preclaim_database_failure_retries_today_instead_of_skipping_tomorrow(
    monkeypatch, failure_stage, error_type
):
    mod, state, repo = isolate_edges(monkeypatch)
    clock = [NOW.replace(hour=7)]
    ready_same_day(monkeypatch, mod, repo, clock)
    if failure_stage == "read":
        repo.read_pipeline.side_effect = [
            None,
            error_type("read temporarily unavailable"),
            None,
        ]
    else:
        claim = repo.claim_pipeline.side_effect
        failed = False

        async def flaky_claim(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise error_type("claim temporarily unavailable")
            return claim(*args, **kwargs)

        repo.claim_pipeline.side_effect = flaky_claim
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        if len(delays) == 1:
            assert seconds == 60, "database reconnect must preserve today's plan"
            assert not state["consumed"]
            clock[0] += timedelta(seconds=seconds)
        else:
            event.set()

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_catch_up=False),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert repo.claim_pipeline.await_args.args[1] == NOW.replace(hour=7, minute=1)
    assert state["terminal"][-1]["status"] == "READY"
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_retry_keeps_existing_owner_observation_without_reclaim(
    monkeypatch,
):
    mod, state, repo = isolate_edges(monkeypatch)
    clock = [NOW]
    _, stored = stored_pipeline()
    repo.read_pipeline.side_effect = [
        dict(stored),
        ConnectionError("owner observation disconnected"),
        dict(stored),
    ]
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        assert seconds == 60
        if len(delays) == 1:
            clock[0] += timedelta(seconds=seconds)
        else:
            event.set()

    await mod.run_daily_research_service(
        object(),
        settings(daily_research_catch_up=False),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert delays == [60, 60]
    assert not state["consumed"]
    repo.claim_pipeline.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        ValueError,
        asyncpg.InsufficientPrivilegeError,
        asyncpg.PostgresSyntaxError,
        asyncpg.QueryCanceledError,
        PermissionError,
    ],
)
async def test_non_transient_preclaim_failure_keeps_existing_daily_failure_policy(
    monkeypatch, error_type
):
    mod, state, repo = isolate_edges(monkeypatch)
    repo.read_pipeline.side_effect = [None, error_type("invalid pipeline metadata")]
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        event.set()

    await mod.run_daily_research_service(
        object(),
        settings(),
        stop_event=stop,
        now_provider=lambda: NOW,
        wait=wait,
    )
    assert delays == [23 * 3600]
    assert not state["consumed"]
    repo.claim_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_database_outage_cannot_retry_model_execution(monkeypatch):
    mod, state, repo = isolate_edges(monkeypatch)
    clock = [NOW]
    ready_same_day(monkeypatch, mod, repo, clock)
    monkeypatch.setattr(
        mod,
        "finish_daily_run",
        AsyncMock(side_effect=ConnectionError("terminal database unavailable")),
    )
    stage = mod._pipeline_stage

    async def mark_stage(pool, claim, **fields):
        if fields.get("state", "RUNNING") != "RUNNING":
            # The same database outage also prevents the fallback failure mark,
            # so the connection error reaches the scheduler after model use.
            raise ConnectionError("terminal database remains unavailable")
        await stage(pool, claim, **fields)

    monkeypatch.setattr(mod, "_pipeline_stage", mark_stage)
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        event.set()

    await mod.run_daily_research_service(
        object(),
        settings(),
        stop_event=stop,
        now_provider=lambda: clock[0],
        wait=wait,
    )
    assert delays == [23 * 3600]
    assert state["consumed"]
    assert state["terminal"] == []
    mod.finish_daily_run.assert_awaited_once()
    mod.run_codex_research.assert_awaited_once()
    repo.claim_pipeline.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_stop_or_cancellation_interrupts_initial_database_retries(
    monkeypatch, cancelled
):
    mod, state, repo = isolate_edges(monkeypatch)
    repo.read_pipeline.side_effect = ConnectionError("database unavailable")
    stop, delays = asyncio.Event(), []

    async def wait(event, seconds):
        delays.append(seconds)
        assert seconds == 60
        if cancelled:
            raise asyncio.CancelledError()
        event.set()

    service = mod.run_daily_research_service(
        object(),
        settings(),
        stop_event=stop,
        now_provider=lambda: NOW,
        wait=wait,
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await service
    else:
        await service
    assert delays == [60]
    assert not state["consumed"]
    repo.claim_pipeline.assert_not_awaited()
    mod.run_codex_research.assert_not_awaited()
