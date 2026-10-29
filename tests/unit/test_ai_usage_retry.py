"""Test safe write retry and diagnostic sqlstate logging in AI usage tracker."""

from types import SimpleNamespace
import pytest

from backend.ai_usage import tracker


class DummyDbError(Exception):
    def __init__(self, message, sqlstate="40001"):
        super().__init__(message)
        self.sqlstate = sqlstate


@pytest.mark.asyncio
async def test_finish_call_retries_transient_failure_and_succeeds(monkeypatch, caplog):
    """finish_call retries transient failures (e.g. deadlock 40001) and succeeds."""
    attempts = 0

    async def persist_mock(pool, tenant_id, user_id, event, data):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DummyDbError("serialization failure", sqlstate="40001")
        return None

    monkeypatch.setattr(tracker.repository, "persist", persist_mock)
    task = tracker.Task(object(), SimpleNamespace(), None, None, "DAILY_RESEARCH", "公开研究")

    with caplog.at_level("WARNING"):
        await task.write("finish_call", {"id": "call-1"})

    assert attempts == 2
    assert "ai_usage_write_retry event=finish_call attempt=1 error_class=DummyDbError sqlstate=40001" in caplog.text


@pytest.mark.asyncio
async def test_finish_call_does_not_retry_non_transient_error(monkeypatch, caplog):
    """Non-transient errors (such as 22P02 or 42501) fail immediately without retry."""
    attempts = 0

    async def persist_mock(pool, tenant_id, user_id, event, data):
        nonlocal attempts
        attempts += 1
        raise DummyDbError("invalid input syntax for type json", sqlstate="22P02")

    monkeypatch.setattr(tracker.repository, "persist", persist_mock)
    task = tracker.Task(object(), SimpleNamespace(), None, None, "DAILY_RESEARCH", "公开研究")

    with caplog.at_level("WARNING"):
        await task.write("finish_call", {"id": "call-1"})

    assert attempts == 1
    assert "ai_usage_write_retry" not in caplog.text
    assert "ai_usage_write_failed event=finish_call error_class=DummyDbError sqlstate=22P02" in caplog.text


@pytest.mark.asyncio
async def test_finish_call_logs_failure_after_exhausting_retries(monkeypatch, caplog):
    """When transient retries are exhausted, ai_usage_write_failed is logged with sqlstate."""
    attempts = 0

    async def persist_mock(pool, tenant_id, user_id, event, data):
        nonlocal attempts
        attempts += 1
        raise DummyDbError("connection terminated unexpectedly", sqlstate="08006")

    monkeypatch.setattr(tracker.repository, "persist", persist_mock)
    task = tracker.Task(object(), SimpleNamespace(), None, None, "DAILY_RESEARCH", "公开研究")

    with caplog.at_level("WARNING"):
        await task.write("finish_call", {"id": "call-1"})

    assert attempts == 2
    assert "ai_usage_write_failed event=finish_call error_class=DummyDbError sqlstate=08006" in caplog.text
