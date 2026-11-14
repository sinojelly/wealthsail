from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.funds import nav_history
from backend.integrations import fund_nav_fallback
from backend.investment import opportunity_fund_nav
from backend.jobs import fund_nav_sync
from tests.unit.test_live_fund_plans_v6 import NOW, history


@pytest.mark.asyncio
async def test_representative_pool_keeps_all_three_coal_lofs():
    expected = {"161032", "161724", "168204"}
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": code,
            "fund_name": f"煤炭{code}A",
            "instrument_id": None,
            "sector": "COAL",
            "overseas_calendar_required": False,
        }
        for code in sorted(expected)
    ]

    plans = await nav_history.read_representative_nav_funds(conn, NOW)

    _, codes, cutoff = conn.fetch.await_args.args
    assert len(codes) == len(set(codes)) == 46 and expected.issubset(codes)
    assert cutoff == NOW
    assert {plan["fundCode"] for plan in plans} == expected
    assert all(plan["subjectId"] == plan["fundCode"] for plan in plans)
    assert all(plan["discoverySector"] == "COAL" for plan in plans)


@pytest.mark.asyncio
async def test_background_backfill_persists_coal_and_mentions_and_skips_cooldown(
    monkeypatch,
):
    conn, released = AsyncMock(), False
    codes = ("161032", "161724", "168204", "000950")
    products = [{"fundCode": code, "overseasCalendarRequired": False} for code in codes]

    @asynccontextmanager
    async def connect(_pool):
        nonlocal released
        released = False
        try:
            yield conn
        finally:
            released = True

    async def claim(sql, code):
        assert "market.claim_fund_nav_sync_attempt" in sql
        return {"claimed": code != "161724"}

    async def collect(_pool, _now, *, asset_rows, fetcher):
        assert released
        assert callable(fetcher)
        assert [row["asset_code"] for row in asset_rows] == [
            "161032",
            "168204",
            "000950",
        ]
        return {"fundHistories": [history("161032"), history("000950")]}

    conn.fetchrow.side_effect = claim
    monkeypatch.setattr(fund_nav_sync, "system_connection", connect)
    monkeypatch.setattr(
        fund_nav_sync, "_load_gap_fill_targets", AsyncMock(return_value=products)
    )
    monkeypatch.setattr(fund_nav_sync, "collect_theme_nav_histories", collect)

    assert await fund_nav_sync.backfill_research_nav_gaps(object(), NOW) == 2
    assert {call.args[1] for call in conn.execute.await_args_list} == {
        "161032",
        "000950",
    }
    assert all(
        "market.fund_research_nav_histories" in call.args[0]
        for call in conn.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_representative_warming_runs_without_a_recalled_report(monkeypatch):
    stop = asyncio.Event()
    pool = object()

    async def backfill(actual_pool, _now):
        assert actual_pool is pool
        stop.set()
        return 0

    monkeypatch.setattr(fund_nav_sync, "backfill_research_nav_gaps", backfill)
    monkeypatch.setattr(
        opportunity_fund_nav, "_research_model_running", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        opportunity_fund_nav, "_latest_ready_runs", AsyncMock(return_value=[])
    )

    await asyncio.wait_for(
        opportunity_fund_nav.run_opportunity_fund_nav_service(pool, stop_event=stop),
        1,
    )


def test_report_gap_targets_include_structured_and_narrative_funds():
    report = {
        "summary": "留意 001092，来源 https://example.com/000777",
        "mentionedFunds": [{"fundCode": "000950"}],
        "topicCatalog": [{"title": "煤炭", "plans": [{"fundCode": "168204"}]}],
        "actionableZones": [{"fundCode": "161724"}, {"fundCode": "161724"}],
    }

    assert fund_nav_sync._report_nav_target_codes(report) == [
        "161724",
        "000950",
        "168204",
        "001092",
    ]


@pytest.mark.asyncio
async def test_direct_unit_nav_excludes_cumulative_series_and_checks_code(monkeypatch):
    disclosed = datetime(2026, 11, 13, tzinfo=timezone.utc)
    quote = {"x": int(disclosed.timestamp() * 1000), "y": 1.2345, "equityReturn": 0.1}
    asynchronous_fallback = AsyncMock(side_effect=RuntimeError("source unavailable"))
    monkeypatch.setattr(
        fund_nav_fallback, "fetch_fund_nav_history_with_fallback", asynchronous_fallback
    )

    def response(request, code):
        assert str(request.url) == "https://fund.eastmoney.com/pingzhongdata/161032.js"
        return httpx.Response(
            200,
            text=(
                f'var fS_code = "{code}"; var Data_ACWorthTrend = [[0, 2.3456]];'
                f"var Data_netWorthTrend = {json.dumps([quote])};"
            ),
        )

    start, end = date(2026, 11, 1), date(2026, 11, 15)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response(req, "161032"))
    ) as client:
        rows = await fund_nav_fallback.fetch_research_nav_gap_history(
            "161032", start, end, client
        )
    assert len(rows) == 1 and rows[0].value == Decimal("1.2345")
    proof = rows[0].metadata["providerHistoryProof"]
    assert proof["methodVersion"] == "eastmoney-pingzhongdata-unit-nav-v1"
    assert proof["fundCode"] == "161032" and proof["pointCount"] == 1
    assert rows[0].metadata["returnPct"] == "0.1"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response(req, "161724"))
    ) as client:
        with pytest.raises(RuntimeError, match="source unavailable"):
            await fund_nav_fallback.fetch_research_nav_gap_history(
                "161032", start, end, client
            )
