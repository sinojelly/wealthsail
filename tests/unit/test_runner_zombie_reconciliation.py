"""Test runner periodic and startup zombie AI usage reconciliation."""

from unittest.mock import AsyncMock, patch
import pytest

from backend.jobs import runner
from backend.config import Settings


class MockPool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


class MockConnContext:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


@pytest.mark.asyncio
async def test_claim_stale_runs_reconciles_zombie_calls(caplog):
    """claim_stale_runs must invoke zombie call reconciliation on startup."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool = MockPool(conn)
    settings = Settings(_env_file=None)

    with patch("backend.jobs.runner.system_connection", return_value=MockConnContext(conn)), \
         patch("backend.jobs.runner.reconcile_zombie_ai_usage_calls", new=AsyncMock(return_value=3)) as mock_reconcile:
        recovered = await runner.claim_stale_runs(pool, settings)

    assert recovered == 1
    mock_reconcile.assert_awaited_once_with(pool)


@pytest.mark.asyncio
async def test_run_recoverable_jobs_is_decoupled_from_ai_usage(caplog):
    """run_recoverable_jobs queries workflow.job_runs without being coupled to AI usage calls."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = MockPool(conn)
    settings = Settings(_env_file=None)

    with patch("backend.jobs.runner.system_connection", return_value=MockConnContext(conn)), \
         patch("backend.ai_usage.repository.reconcile_stale_calls") as mock_reconcile:
        outcomes = await runner.run_recoverable_jobs(pool, settings)

    assert outcomes == {}
    mock_reconcile.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_zombie_ai_usage_calls_executes_independently(caplog):
    """reconcile_zombie_ai_usage_calls executes in its own connection and logs count."""
    conn = AsyncMock()
    pool = MockPool(conn)

    with patch("backend.jobs.runner.system_connection", return_value=MockConnContext(conn)), \
         patch("backend.ai_usage.repository.reconcile_stale_calls", new=AsyncMock(return_value="UPDATE 4")) as mock_reconcile:
        with caplog.at_level("WARNING"):
            zombies = await runner.reconcile_zombie_ai_usage_calls(pool, limit=50)

    assert zombies == 4
    mock_reconcile.assert_awaited_once_with(conn, limit=50)
    assert "reconciled 4 zombie ai usage calls" in caplog.text


@pytest.mark.asyncio
async def test_reconciliation_failure_does_not_abort_job_recovery(caplog):
    """Failure in reconcile_stale_calls must be logged without raising or blocking runs."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 2")
    pool = MockPool(conn)
    settings = Settings(_env_file=None)

    with patch("backend.jobs.runner.system_connection", return_value=MockConnContext(conn)), \
         patch("backend.ai_usage.repository.reconcile_stale_calls", side_effect=RuntimeError("db timeout")):
        with caplog.at_level("ERROR"):
            recovered = await runner.claim_stale_runs(pool, settings)

    assert recovered == 2
    assert "zombie ai usage call reconciliation failed" in caplog.text
