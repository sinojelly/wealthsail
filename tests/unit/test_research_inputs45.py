"""The same fresh news drives collection and analysis, outside DB transactions."""

from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from test_daily_research import NOW


@pytest.mark.asyncio
async def test_preparation_passes_the_same_news_to_dynamic_nav_and_preserves_real_etf_signals(
    monkeypatch,
):
    from backend.jobs import research_inputs as mod
    from backend.investment import discovery
    from backend.integrations import etf_flows

    active = 0

    class Conn:
        async def fetch(self, sql, start, end):
            return [
                {
                    "calendar_date": start + timedelta(days=i),
                    "is_trading_day": (start + timedelta(days=i)).weekday() < 5,
                }
                for i in range((end - start).days + 1)
            ]

    @asynccontextmanager
    async def connection(pool):
        nonlocal active
        active += 1
        try:
            yield Conn()
        finally:
            active -= 1

    catalysts = [
        {
            "id": "cls",
            "title": "商业卫星订单",
            "publishedAt": NOW.isoformat(),
            "retrievedAt": NOW.isoformat(),
        }
    ]
    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(
        mod, "read_alpha_public", AsyncMock(return_value={"catalysts": catalysts})
    )

    async def nav(pool, now, *, catalysts):
        assert active == 0
        assert [row["id"] for row in catalysts] == ["cls", "doc"]
        return {
            "fundHistories": [],
            "themeNavSourceResults": {
                "028777": {"status": "SHORT_HISTORY", "records": 5}
            },
            "fundMasterSourceResult": {"status": "SUCCESS", "totalEntries": 28107},
        }

    async def flows(now, *, trading_dates):
        assert active == 0
        assert [str(day) for day in trading_dates] == ["2026-10-14", "2026-10-15"]
        return {
            "signals": [{"id": "etf-1", "metric": "ETF_SHARE_CHANGE"}],
            "sourceResults": {"SSE_ETF_SHARES": {"status": "SUCCESS"}},
        }

    monkeypatch.setattr(discovery, "collect_discovery_nav_histories", nav)
    monkeypatch.setattr(etf_flows, "fetch_etf_flow_signals", flows)
    external = {"documents": [{"id": "doc", "title": "卫星政策"}]}
    result = await mod.prepare_research_inputs(object(), NOW, external)
    assert result["etfFlowSignals"]["signals"][0]["id"] == "etf-1"
    assert result["sourceResults"]["SSE_ETF_SHARES"]["status"] == "SUCCESS"
    assert result["dynamicNavSourceResults"]["028777"]["records"] == 5
    assert result["sourceResults"]["DISCOVERY_FUND_MASTER"]["totalEntries"] == 28107


@pytest.mark.asyncio
async def test_missing_calendar_day_does_not_guess_etf_comparison_dates():
    from backend.jobs.research_inputs import completed_etf_dates

    class Conn:
        async def fetch(self, *args):
            return [
                {
                    "calendar_date": NOW.date() - timedelta(days=1),
                    "is_trading_day": True,
                }
            ]

    assert await completed_etf_dates(Conn(), NOW) == []


@pytest.mark.asyncio
async def test_frozen_scheduled_batch_reuses_its_nav_without_an_extra_collection(
    monkeypatch,
):
    from backend.jobs import research_inputs as mod
    from backend.investment import discovery
    from backend.integrations import etf_flows

    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def connection(pool):
        yield conn

    read = AsyncMock(
        return_value={
            "fundHistories": [{"fundCode": "008298"}],
            "themeNavSourceResults": {"008298": {"status": "SUCCESS", "records": 500}},
        }
    )
    nav = AsyncMock(side_effect=AssertionError("must use frozen batch"))
    monkeypatch.setattr(mod, "system_connection", connection)
    monkeypatch.setattr(mod, "read_alpha_public", read)
    monkeypatch.setattr(discovery, "collect_discovery_nav_histories", nav)
    monkeypatch.setattr(
        etf_flows,
        "fetch_etf_flow_signals",
        AsyncMock(return_value={"signals": [], "sourceResults": {}}),
    )
    result = await mod.prepare_research_inputs(
        object(), NOW, {}, source_batch_id="frozen-batch"
    )
    assert read.await_args.kwargs["source_batch_id"] == "frozen-batch"
    assert result["dynamicNavSourceResults"]["008298"]["records"] == 500
    nav.assert_not_awaited()
