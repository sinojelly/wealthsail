"""Read-only NAS capacity reporting never silently declares unknown space healthy."""

import pytest


def snapshot(**changes):
    return {
        "tables": {"archive": 100},
        "queue": {"AUDIT_PENDING": 0, "ARCHIVED": 1, "UNRECOVERABLE": 0},
        "oldestPendingSeconds": 0,
        "redundancyPlan": [],
        **changes,
    }


@pytest.mark.parametrize(
    "size, expected",
    [(99, "OK"), (100, "WARNING"), (199, "WARNING"), (200, "CRITICAL")],
)
def test_database_capacity_thresholds_report_real_boundary(size, expected):
    from scripts.research_archive_storage import capacity_report

    result = capacity_report(
        snapshot(tables={"archive": size}), warn_bytes=100, critical_bytes=200
    )
    assert result["severity"] == expected
    assert result["volume"]["state"] == "NOT_CHECKED"


@pytest.mark.parametrize(
    "free, expected", [(21, "OK"), (20, "WARNING"), (10, "CRITICAL")]
)
def test_nas_volume_and_unrecoverable_backlog_have_explicit_signals(free, expected):
    from scripts.research_archive_storage import capacity_report

    result = capacity_report(
        snapshot(),
        disk={"total": 100, "free": free},
        warn_bytes=1000,
        critical_bytes=2000,
    )
    assert result["severity"] == expected
    assert result["volume"]["state"] == "CHECKED"
    stalled = capacity_report(
        snapshot(queue={"UNRECOVERABLE": 1}, oldestPendingSeconds=24 * 3600),
        warn_bytes=1000,
        critical_bytes=2000,
    )
    assert stalled["severity"] == "CRITICAL"
    assert {"ARCHIVE_UNRECOVERABLE", "ARCHIVE_BACKLOG_AGE"} <= set(stalled["signals"])


def test_capacity_budget_options_reject_unbounded_or_unsafe_maintenance_limits():
    from scripts.research_archive_storage import validate_limits

    for days, limit, warning, critical in (
        (0, 20, 1, 2),
        (29, 20, 1, 2),
        (90, 21, 1, 2),
        (90, 0, 1, 2),
        (90, 20, 2, 1),
    ):
        with pytest.raises(ValueError):
            validate_limits(days, limit, warning, critical)


@pytest.mark.asyncio
async def test_inspection_entry_uses_read_only_transaction_and_does_not_echo_dsn(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from scripts import research_archive_storage as storage

    state = []

    class Connection:
        @asynccontextmanager
        async def transaction(self, **kwargs):
            assert kwargs == {"readonly": True, "isolation": "repeatable_read"}
            state.append("read-only")
            yield

        execute = AsyncMock()
        close = AsyncMock()

    conn = Connection()
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(storage.asyncpg, "connect", connect)
    monkeypatch.setattr(
        storage, "scan_archive_storage", AsyncMock(return_value=snapshot())
    )
    report = await storage.inspect_database(
        "postgresql://secret:PRIVATE@127.0.0.1/owned",
        "127.0.0.1/owned",
        retention_days=90,
        candidate_limit=20,
    )
    assert state == ["read-only"]
    assert "PRIVATE" not in str(report)
    conn.close.assert_awaited_once()
    connect.reset_mock()
    with pytest.raises(ValueError):
        await storage.inspect_database(
            "postgresql://secret:PRIVATE@127.0.0.1/owned",
            "wrong-target",
            retention_days=90,
            candidate_limit=20,
        )
    connect.assert_not_awaited()
