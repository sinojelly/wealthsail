from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import pytest

from backend.operations.monitoring import (
    RuntimeSignal,
    alert_content,
    collect_database_signals,
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
            RuntimeSignal("OUTBOX_UNKNOWN", "secret-unknown-id", "tenant-a"),
            RuntimeSignal("JOB_FAILED", "secret-job-id", "tenant-a"),
            RuntimeSignal(
                "TENANT_NO_ENABLED_JOBS",
                "enabled-report-jobs",
                "secret-tenant-id",
            ),
        ]
    )

    assert "Worker 服务已停止" in content
    assert "推送死信：1 条" in content
    assert "推送结果未知：1 条" in content
    assert "后台任务失败：1 条" in content
    assert "无启用报告任务：1 个租户" in content
    assert "secret-row-id" not in content
    assert "tenant-a" not in content
    assert "secret-tenant-id" not in content


@pytest.mark.asyncio
async def test_database_monitor_collects_unknown_outbox_results() -> None:
    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def transaction(self):
            return Transaction()

        async def execute(self, _sql):
            return "SET"

        async def fetch(self, sql, *_args):
            self.sql.append(sql)
            if "FROM identity.tenants" in sql or "FROM workflow.jobs" in sql:
                return []
            return [
                {
                    "kind": "OUTBOX_UNKNOWN",
                    "object_id": "outbox-1",
                    "tenant_id": "tenant-a",
                }
            ]

    class Pool:
        def __init__(self) -> None:
            self.conn = Conn()

        @asynccontextmanager
        async def acquire(self):
            yield self.conn

    pool = Pool()
    signals = await collect_database_signals(
        pool,
        failed_job_window_minutes=15,
        dead_outbox_window_hours=24,
    )

    assert any("status = 'UNKNOWN'" in sql for sql in pool.conn.sql)
    assert signals == [RuntimeSignal("OUTBOX_UNKNOWN", "outbox-1", "tenant-a")]


@pytest.mark.asyncio
async def test_database_monitor_reports_active_tenants_without_enabled_jobs() -> None:
    """An enabled job for one tenant must not hide another tenant's outage."""

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Conn:
        def __init__(self, rows) -> None:
            self.rows = rows
            self.sql = ""

        def transaction(self):
            return Transaction()

        async def execute(self, _sql):
            return "SET"

        async def fetch(self, sql, *_args):
            self.sql = sql
            return self.rows

    class Pool:
        def __init__(self) -> None:
            self.connections = [
                Conn([]),
                Conn(
                    [
                        {"tenant_id": "tenant-with-no-jobs"},
                        {"tenant_id": "tenant-with-disabled-jobs"},
                        {"tenant_id": "tenant-with-enabled-job"},
                    ]
                ),
                Conn([{"tenant_id": "tenant-with-enabled-job"}]),
            ]
            self.acquired: list[Conn] = []

        @asynccontextmanager
        async def acquire(self):
            conn = self.connections[len(self.acquired)]
            self.acquired.append(conn)
            yield conn

    pool = Pool()

    signals = await collect_database_signals(
        pool,
        failed_job_window_minutes=15,
        dead_outbox_window_hours=24,
    )

    assert signals == [
        RuntimeSignal(
            "TENANT_NO_ENABLED_JOBS",
            "enabled-report-jobs",
            "tenant-with-disabled-jobs",
        ),
        RuntimeSignal(
            "TENANT_NO_ENABLED_JOBS",
            "enabled-report-jobs",
            "tenant-with-no-jobs",
        ),
    ]
    assert "status = 'ACTIVE'" in pool.acquired[1].sql
    assert "is_enabled = TRUE" in pool.acquired[2].sql
