"""The live API projection refreshes prices even when a report is old."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import sys

import pytest

from backend.macro.daily_repository import read_daily_research


@pytest.mark.asyncio
async def test_a_saved_old_daily_report_is_repriced_before_return_without_updating_it(
    monkeypatch,
):
    now = datetime(2026, 10, 28, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    old = now - timedelta(days=5)
    stored = {
        "summary": "历史研究仍可参考",
        "asOf": old.isoformat(),
        "actionablePlans": [
            {"methodVersion": "actionable-zones-v4", "fundCode": "162411"}
        ],
    }
    refreshed = {
        **stored,
        "actionablePlans": [
            {"methodVersion": "actionable-zones-v6", "fundCode": "162411"}
        ],
    }
    refresh = AsyncMock(return_value=refreshed)
    monkeypatch.setitem(
        sys.modules,
        "backend.investment.live_fund_plans",
        SimpleNamespace(refresh_report_fund_plans=refresh),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "run_id": "old-report",
        "slot_date": old.date(),
        "scheduled_for": old,
        "dispatched_at": old,
        "recorded_at": old,
        "status": "READY",
        "payload": stored,
        "public_packet": {"fundHistories": []},
    }
    result = await read_daily_research(conn, now)
    assert (
        result["report"]["actionablePlans"][0]["methodVersion"] == "actionable-zones-v6"
    )
    assert result["asOf"] == old
    assert result["reportRunId"] == "old-report"
    refresh.assert_awaited_once()
    assert refresh.await_args.args[:1] == (conn,)
    assert refresh.await_args.args[2] == now
    assert stored["actionablePlans"][0]["methodVersion"] == "actionable-zones-v4"
    conn.execute.assert_not_awaited()
