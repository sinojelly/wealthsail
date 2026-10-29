"""Test safe write retry and diagnostic sqlstate logging in AI usage tracker."""

from types import SimpleNamespace
import pytest

from backend.ai_usage import tracker


class DummyDbError(Exception):
    def __init__(self, message, sqlstate="22P02"):
        super().__init__(message)
        self.sqlstate = sqlstate


@pytest.mark.asyncio
async def test_finish_call_retries_on_failure_and_succeeds(monkeypatch, caplog):
    """finish_call allows one safe retry and logs retry warning with sqlstate."""
    attempts = 0

    async def persist_mock(pool, tenant_id, user_id, event, data):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DummyDbError("invalid input syntax for type json", sqlstate="22P02")
        return None

    monkeypatch.setattr(tracker.repository, "persist", persist_mock)
    task = tracker.Task(object(), SimpleNamespace(), None, None, "DAILY_RESEARCH", "公开研究")

    with caplog.at_level("WARNING"):
        await task.write("finish_call", {"id": "call-1"})

    assert attempts == 2
    assert "ai_usage_write_retry event=finish_call attempt=1 error_class=DummyDbError sqlstate=22P02" in caplog.text


@pytest.mark.asyncio
async def test_finish_call_logs_failure_after_exhausting_retries(monkeypatch, caplog):
    """When retries are exhausted, ai_usage_write_failed is logged with sqlstate."""
    attempts = 0

    async def persist_mock(pool, tenant_id, user_id, event, data):
        nonlocal attempts
        attempts += 1
        raise DummyDbError("persistent DB error", sqlstate="42501")

    monkeypatch.setattr(tracker.repository, "persist", persist_mock)
    task = tracker.Task(object(), SimpleNamespace(), None, None, "DAILY_RESEARCH", "公开研究")

    with caplog.at_level("WARNING"):
        await task.write("finish_call", {"id": "call-1"})

    assert attempts == 2
    assert "ai_usage_write_failed event=finish_call error_class=DummyDbError sqlstate=42501" in caplog.text
