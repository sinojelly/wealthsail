from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.operations.monitoring import (
    RuntimeSignal,
    alert_content,
    due_signals,
    mark_sent,
)


def test_runtime_signals_are_deduplicated_during_cooldown() -> None:
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    old = RuntimeSignal("OUTBOX_DEAD", "outbox-old", "tenant-a")
    new = RuntimeSignal("JOB_FAILED", "job-new", "tenant-a")
    state = {old.fingerprint: (now - timedelta(minutes=5)).isoformat()}

    assert due_signals(
        [old, new], state, now=now, cooldown=timedelta(minutes=60)
    ) == [new]


def test_mark_sent_records_only_successful_signals() -> None:
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    signal = RuntimeSignal("WORKER_STOPPED", "wealthsail-worker.service")
    state: dict[str, str] = {}

    mark_sent(state, [signal], now=now)

    assert state == {signal.fingerprint: now.isoformat()}


def test_alert_content_has_counts_without_internal_identifiers() -> None:
    content = alert_content(
        [
            RuntimeSignal("WORKER_STOPPED", "wealthsail-worker.service"),
            RuntimeSignal("OUTBOX_DEAD", "secret-row-id", "tenant-a"),
            RuntimeSignal("JOB_FAILED", "secret-job-id", "tenant-a"),
        ]
    )

    assert "Worker 服务已停止" in content
    assert "推送死信：1 条" in content
    assert "后台任务失败：1 条" in content
    assert "secret-row-id" not in content
    assert "tenant-a" not in content
