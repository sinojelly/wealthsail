"""Archive recovery is independent of publication and never consumes a model slot."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_daily_research_job import NOW, isolate, settings


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
    assert "AUDIT_PENDING" in caplog.text
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
async def test_archive_cancellation_cannot_replace_committed_ready_with_uncertain(
    monkeypatch,
):
    mod, state = isolate(monkeypatch)
    mod.archive_research_run.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await mod.sync_daily_research(object(), settings(), NOW)
    assert state["terminal"][0]["status"] == "READY"
    assert len(state["terminal"]) == 1
    assert not any(row.get("state") == "UNCERTAIN" for row in state["pipeline_events"])
    mod.archive_repository.enqueue_research_archive.assert_awaited_once()
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
