"""Archive recovery is independent of publication and never consumes a model slot."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_daily_research_job import NOW, isolate, settings


@pytest.mark.asyncio
async def test_daily_terminal_releases_model_without_waiting_for_archive(monkeypatch):
    mod, state = isolate(monkeypatch)
    archive_entered, archive_resume = asyncio.Event(), asyncio.Event()
    order = []

    async def release(*args, **kwargs):
        assert state["terminal"][0]["status"] == "READY"
        assert not state.get("in_transaction", False)
        order.append("release")
        return True

    async def slow_archive(*args, **kwargs):
        order.append("archive")
        archive_entered.set()
        await archive_resume.wait()
        return {"auditStatus": "ARCHIVED"}

    mod.release_model_guard.side_effect = release
    mod.archive_research_run.side_effect = slow_archive
    task = asyncio.create_task(mod.sync_daily_research(object(), settings(), NOW))
    try:
        for _ in range(30):
            await asyncio.sleep(0)
            if task.done() or archive_entered.is_set():
                break
        assert order == ["release"], (
            "publication must release the model before any archive wait"
        )
        assert task.done(), (
            "daily execution must return without awaiting frozen-data archival"
        )
        assert (await task)["auditStatus"] == "AUDIT_PENDING"
        mod.archive_research_run.assert_not_awaited()
        mod.run_codex_research.assert_awaited_once()
        mod.release_model_guard.assert_awaited_once()
    finally:
        archive_resume.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_archive_stage", ["artifact", "outcome"])
async def test_archive_failure_keeps_strict_ready_and_visible_pending(
    monkeypatch, caplog, failed_archive_stage
):
    mod, state = isolate(monkeypatch)

    async def enqueue(conn, *args, **kwargs):
        assert state["in_transaction"] is True
        state["archive_enqueued"] = args, kwargs

    async def archive(*args, **kwargs):
        assert state["terminal"][0]["status"] == "READY"
        assert not state.get("in_transaction", False)
        raise ConnectionError(f"PRIVATE {failed_archive_stage} database URI")

    monkeypatch.setattr(
        mod.archive_repository,
        "enqueue_research_archive",
        AsyncMock(side_effect=enqueue),
        raising=False,
    )
    monkeypatch.setattr(
        mod, "archive_research_run", AsyncMock(side_effect=archive), raising=False
    )
    with caplog.at_level("WARNING"):
        result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "READY"
    assert result["auditStatus"] == "AUDIT_PENDING"
    assert state["archive_enqueued"][0][0] == "one"
    assert state["terminal"][0]["payload"] is not None
    assert "PRIVATE" not in caplog.text
    mod.archive_research_run.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_failure_is_still_a_core_transaction_failure(monkeypatch):
    mod, _state = isolate(monkeypatch)
    enqueue = AsyncMock(side_effect=ConnectionError("PRIVATE outbox failure"))
    archive = AsyncMock()
    monkeypatch.setattr(
        mod.archive_repository, "enqueue_research_archive", enqueue, raising=False
    )
    monkeypatch.setattr(mod, "archive_research_run", archive, raising=False)
    with pytest.raises(ConnectionError):
        await mod.sync_daily_research(object(), settings(), NOW)
    archive.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_deferred_archive_cancellation_cannot_affect_daily_terminal(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    mod.archive_research_run.side_effect = asyncio.CancelledError()
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "READY" and result["auditStatus"] == "AUDIT_PENDING"
    assert state["terminal"][0]["status"] == "READY"
    assert len(state["terminal"]) == 1
    assert not any(row.get("state") == "UNCERTAIN" for row in state["pipeline_events"])
    mod.archive_repository.enqueue_research_archive.assert_awaited_once()
    mod.archive_research_run.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_formal_result_failure_is_not_hidden_by_archive_fallback(monkeypatch):
    mod, _state = isolate(monkeypatch)
    mod.finish_daily_run = AsyncMock(
        side_effect=ConnectionError("PRIVATE result failure")
    )
    enqueue, archive = AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        mod.archive_repository, "enqueue_research_archive", enqueue, raising=False
    )
    monkeypatch.setattr(mod, "archive_research_run", archive, raising=False)
    with pytest.raises(ConnectionError):
        await mod.sync_daily_research(object(), settings(), NOW)
    archive.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()
