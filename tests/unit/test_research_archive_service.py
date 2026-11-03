"""Recovery only drains frozen archive jobs, including after research is disabled."""

import asyncio
from contextlib import asynccontextmanager
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_daily_research_job import NOW


def boundary(monkeypatch):
    mod = import_module("backend.jobs.research_archive")
    state = {"transaction": False}

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            state["transaction"] = True
            try:
                yield
            finally:
                state["transaction"] = False

    @asynccontextmanager
    async def connection(_pool):
        yield Conn()

    claim = {"run_id": "one", "lease_token": "token", "attempts": 1}
    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(
        mod.repository,
        "claim_research_archive",
        AsyncMock(return_value=claim),
        raising=False,
    )
    monkeypatch.setattr(
        mod.repository, "persist_research_archive", AsyncMock(), raising=False
    )
    monkeypatch.setattr(
        mod.repository,
        "finish_research_archive",
        AsyncMock(
            side_effect=lambda *args, **kwargs: {
                "status": "UNRECOVERABLE" if claim["attempts"] >= 8 else "AUDIT_PENDING"
            }
        ),
        raising=False,
    )
    return mod, state, claim


@pytest.mark.asyncio
async def test_recovery_archives_frozen_job_without_any_provider(monkeypatch):
    from backend.integrations import codex_research, daily_sources

    mod, state, claim = boundary(monkeypatch)
    model, verifier = AsyncMock(), AsyncMock()
    monkeypatch.setattr(codex_research, "run_codex_research", model)
    monkeypatch.setattr(daily_sources, "verify_research_sources", verifier)

    async def persist(*args):
        assert state["transaction"] is True

    mod.repository.persist_research_archive.side_effect = persist
    result = await mod.archive_research_run(object(), "one", now=NOW)
    assert result["auditStatus"] == "ARCHIVED"
    mod.repository.persist_research_archive.assert_awaited_once()
    assert mod.repository.persist_research_archive.await_args.args[1] == claim
    mod.repository.finish_research_archive.assert_not_awaited()
    model.assert_not_awaited()
    verifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_job_does_not_reuse_application_time_for_lease_authority(
    monkeypatch,
):
    mod, _state, claim = boundary(monkeypatch)
    await mod.archive_research_run(object(), "one", now=NOW)
    assert len(mod.repository.claim_research_archive.await_args.args) == 1
    assert mod.repository.persist_research_archive.await_args.args[1:] == (claim,)
    mod.repository.persist_research_archive.side_effect = ConnectionError(
        "private test failure"
    )
    await mod.archive_research_run(object(), "one", now=NOW)
    assert mod.repository.finish_research_archive.await_args.args[1:] == (claim,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attempt,expected", [(1, "AUDIT_PENDING"), (8, "UNRECOVERABLE")]
)
async def test_recovery_failure_is_visible_and_bounded(
    monkeypatch, caplog, attempt, expected
):
    mod, _state, claim = boundary(monkeypatch)
    claim["attempts"] = attempt
    mod.repository.persist_research_archive.side_effect = ConnectionError(
        "PRIVATE raw database URI"
    )
    with caplog.at_level("WARNING"):
        result = await mod.archive_research_run(object(), "one", now=NOW)
    assert result["auditStatus"] == expected
    assert mod.repository.finish_research_archive.await_args.kwargs == {
        "status": "AUDIT_PENDING",
        "error_class": "ConnectionError",
    }
    assert "PRIVATE" not in caplog.text
    assert expected in caplog.text


@pytest.mark.asyncio
async def test_pending_archive_service_starts_with_all_research_modes_disabled(
    monkeypatch,
):
    mod = import_module("backend.jobs.research_archive")
    from backend.jobs.alpha_runtime import run_alpha_runtime

    entered = asyncio.Event()

    async def service(_pool, *, stop_event):
        entered.set()
        await stop_event.wait()

    monkeypatch.setattr(mod, "run_research_archive_service", service)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_alpha_runtime(
            object(),
            SimpleNamespace(
                daily_research_enabled=False,
                daily_research_manual_enabled=False,
                daily_research_debug_enabled=False,
            ),
            stop_event=stop,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        stop.set()
        await task


@pytest.mark.asyncio
async def test_recovery_batch_is_bounded_and_does_not_wait_for_a_new_job(monkeypatch):
    mod = import_module("backend.jobs.research_archive")
    archive = AsyncMock(return_value={"auditStatus": "ARCHIVED", "claimed": True})
    monkeypatch.setattr(mod, "archive_research_run", archive)
    assert len(await mod.sync_research_archives(object(), now=NOW)) == 4
    assert archive.await_count == 4
    archive.reset_mock()
    archive.return_value = {"auditStatus": "AUDIT_PENDING", "claimed": False}
    assert await mod.sync_research_archives(object(), now=NOW) == []
    archive.assert_awaited_once()


@pytest.mark.asyncio
async def test_archive_cancellation_leaves_the_durable_lease_for_recovery(monkeypatch):
    mod, _state, _claim = boundary(monkeypatch)
    mod.repository.persist_research_archive.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await mod.archive_research_run(object(), "one", now=NOW)
    mod.repository.finish_research_archive.assert_not_awaited()
