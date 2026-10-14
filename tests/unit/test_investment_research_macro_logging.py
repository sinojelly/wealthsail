"""Daily research exposes empty and failed macro acquisition in its logs."""

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.jobs import investment_macro, investment_research


@pytest.mark.asyncio
@pytest.mark.parametrize("observations", [0, 240])
async def test_research_logs_macro_observations_and_completion(
    monkeypatch, caplog, observations
):
    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def connection(_pool):
        yield conn

    monkeypatch.setattr(investment_research, "system_connection", connection)
    monkeypatch.setattr(
        investment_research,
        "ensure_trading_calendar",
        AsyncMock(return_value={"status": "READY"}),
    )
    monkeypatch.setattr(
        investment_macro,
        "sync_macro",
        AsyncMock(return_value={"observations": observations}),
    )
    now = datetime(2026, 10, 14, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    with caplog.at_level("INFO", logger="wealthsail.investment_research"):
        result = await investment_research.sync_research(object(), object(), now)

    assert result == {"succeeded": 0, "failed": 0}
    assert f"research_macro_sync observations={observations}" in caplog.text
    assert (
        f"research_sync_completed succeeded=0 failed=0 macro_observations={observations}"
        in caplog.text
    )
    assert ("research_macro_history_empty" in caplog.text) is (observations == 0)


@pytest.mark.asyncio
async def test_research_keeps_failure_distinct_from_empty_history(monkeypatch, caplog):
    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def connection(_pool):
        yield conn

    monkeypatch.setattr(investment_research, "system_connection", connection)
    monkeypatch.setattr(
        investment_research,
        "ensure_trading_calendar",
        AsyncMock(return_value={"status": "READY"}),
    )
    monkeypatch.setattr(
        investment_macro,
        "sync_macro",
        AsyncMock(side_effect=RuntimeError("provider unavailable")),
    )
    now = datetime(2026, 10, 14, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    with caplog.at_level("INFO", logger="wealthsail.investment_research"):
        result = await investment_research.sync_research(object(), object(), now)

    assert result == {"succeeded": 0, "failed": 0}
    assert "research_macro_sync_failed" in caplog.text
    assert "research_macro_history_empty" not in caplog.text
    assert (
        "research_sync_completed succeeded=0 failed=0 macro_observations=None"
        in caplog.text
    )
