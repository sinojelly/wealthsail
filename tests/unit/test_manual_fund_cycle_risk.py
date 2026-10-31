"""Manual fund research retains the same public industry veto as the daily card."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.indicators.actionable_zones import compute_actionable_price_plan
from backend.investment import service, theme_graph
from test_fund_nav_research import NOW, Store, _history


EARLY = {"stage": "EARLY_COMMERCIALIZATION", "riskFlags": ["FINANCING_COOLING"]}


def report(code="110022", *, as_of=None):
    return {
        "asOf": (as_of or NOW).isoformat(),
        "valueDirections": [
            {
                "industryCycle": deepcopy(EARLY),
                "plans": [
                    {
                        "subjectKind": "nav",
                        "subjectId": code,
                        "fundCode": code,
                        "priceBasis": "UNIT_NAV",
                        "market": "FUND_CN",
                    }
                ],
            }
        ],
        "privatePortfolio": {"secret": "never-return"},
    }


def frozen_packet():
    return {
        "asOf": NOW.isoformat(),
        "relativeStrengthBenchmark": {
            "state": "READY",
            "indexId": "hs300",
            "asOf": "2026-10-16",
            "retrievedAt": NOW.isoformat(),
            "sourceUri": "https://www.csindex.com.cn/",
            "points": [{**point, "close": "100"} for point in _history()["points"]],
        },
        "privatePositions": ["not-public"],
    }


def risk_row(payload=None, packet=None):
    return {
        "payload": payload or report(),
        "public_packet": packet or frozen_packet(),
        "recorded_at": NOW,
        "dispatched_at": NOW,
    }


def test_same_fund_overlay_keeps_current_prices_and_ignores_other_products():
    context = service.fund_risk_context(report(), NOW, packet=frozen_packet())
    rows = _history(price="2")["points"]
    baseline = compute_actionable_price_plan(
        rows,
        subject_kind="nav",
        subject_id="110022",
        price_basis="UNIT_NAV",
    )
    protected = service.protect_fund_plan(baseline, context)
    assert protected["industryCycle"]["stage"] == "EARLY_COMMERCIALIZATION"
    assert (
        protected["tracks"][0]["entryZoneMax"] < baseline["tracks"][0]["entryZoneMax"]
    )
    assert protected["tracks"][1] == baseline["tracks"][1]
    other = deepcopy(baseline)
    other["subjectId"] = "999999"
    assert service.protect_fund_plan(other, context) == other
    assert "secret" not in str(context)
    assert "not-public" not in str(context)


@pytest.mark.asyncio
async def test_context_loader_skips_future_stale_and_future_packet_rows():
    future = report(as_of=NOW + timedelta(hours=1))
    stale = report(as_of=NOW - timedelta(days=11))
    future_packet = frozen_packet()
    future_packet["asOf"] = (NOW + timedelta(hours=1)).isoformat()
    conn = AsyncMock()
    conn.fetch.return_value = [
        risk_row(future),
        risk_row(stale),
        risk_row(packet=future_packet),
    ]
    assert await service.read_fund_risk_context(conn, NOW) == {}
    conn.fetch.return_value = [risk_row()]
    actual = await service.read_fund_risk_context(conn, NOW)
    assert actual["cyclesByFund"]["110022"]["riskFlags"] == ["FINANCING_COOLING"]


@pytest.mark.asyncio
async def test_manual_cached_nav_has_same_industry_discount_and_benchmark(monkeypatch):
    from backend.macro import alpha_repository

    monkeypatch.setattr(
        alpha_repository, "read_alpha_public", AsyncMock(return_value={})
    )

    class RiskStore(Store):
        async def fetch(self, query, *args):
            if "research.daily_research_results" in query:
                return [risk_row()]
            return await super().fetch(query, *args)

    result = await theme_graph.cached_nav_analysis(
        RiskStore(_history(price="2")), "110022", NOW
    )
    plan = result["actionableZones"]
    assert plan["industryCycle"]["stage"] == "EARLY_COMMERCIALIZATION"
    assert plan["industryRiskAdjustment"]["state"] == "APPLIED"
    assert plan["relativeStrength"]["excessReturn60d"] == pytest.approx(0)
    assert plan["tracks"][0]["entryStatus"] == plan["industryRiskWarning"]


@pytest.mark.asyncio
async def test_fund_analysis_fallback_also_obeys_daily_risk(monkeypatch):
    identity = str(uuid4())
    history = _history(price="2")
    analysis = {
        "asOf": history["asOf"],
        "source": history["sourceUri"],
        "retrievedAt": NOW.isoformat(),
        "dataQuality": "READY",
        "state": "READY",
        "seriesKind": "UNIT_NAV",
        "unit": "CNY",
        "market": "FUND_CN",
        "points": history["points"],
    }
    monkeypatch.setattr(service, "_research_analysis", AsyncMock(return_value=analysis))
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(
        theme_graph, "cached_nav_analysis", AsyncMock(return_value=None)
    )
    conn = AsyncMock()
    conn.fetchval.return_value = "110022"
    conn.fetch.return_value = [risk_row()]
    result = await service.analysis(conn, "fund", identity)
    plan = result["actionableZones"]
    assert plan["industryCycle"]["riskFlags"] == ["FINANCING_COOLING"]
    assert plan["relativeStrength"]["excessReturn60d"] == pytest.approx(0)
    assert plan["industryRiskAdjustment"]["state"] == "APPLIED"


@pytest.mark.asyncio
async def test_unavailable_public_context_does_not_break_manual_nav_read(monkeypatch):
    conn = AsyncMock()
    conn.fetch.side_effect = RuntimeError("read-only research history unavailable")
    assert await service.read_fund_risk_context(conn, NOW) == {}


@pytest.mark.asyncio
async def test_selected_personal_decision_keeps_daily_risk_and_frozen_relative_strength(
    monkeypatch,
):
    from test_opportunity_context import dependencies
    from backend.macro import alpha_repository

    actual_nav_analysis = theme_graph.cached_nav_analysis
    module = dependencies(monkeypatch)
    monkeypatch.setattr(
        module,
        "read_daily_research",
        AsyncMock(return_value={"state": "READY", "report": report()}),
    )
    rows = _history(price="2")["points"]
    baseline = compute_actionable_price_plan(
        rows, subject_kind="nav", subject_id="110022", price_basis="UNIT_NAV"
    )
    monkeypatch.setattr(theme_graph, "cached_nav_analysis", actual_nav_analysis)
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"fundHistories": [_history()]}),
    )
    conn = AsyncMock()

    async def fetch(query, *args):
        if "research.daily_research_results" in query:
            return [risk_row()]
        if (
            "market.fund_research_nav_histories" in query
            or "reference.trading_calendars" in query
        ):
            return await Store(_history()).fetch(query, *args)
        return []

    conn.fetch.side_effect = fetch
    conn.fetchval.return_value = None
    conn.fetchrow.return_value = None
    result = await module.get_context(
        conn, "tenant-a", "user-a", fund_code="110022", fund_market="FUND_CN", now=NOW
    )
    selected = result["decision"]["actionableZones"]
    assert selected["industryCycle"]["stage"] == "EARLY_COMMERCIALIZATION"
    assert selected["relativeStrength"]["excessReturn60d"] == pytest.approx(0)
    assert selected["tracks"][0]["entryZoneMax"] < baseline["tracks"][0]["entryZoneMax"]
    assert (
        result["dailyResearch"]["report"]["valueDirections"][0]["industryCycle"]
        == EARLY
    )


@pytest.mark.asyncio
async def test_context_reader_does_not_swallow_cancellation():
    import asyncio

    conn = AsyncMock()
    conn.fetch.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await service.read_fund_risk_context(conn, NOW)


@pytest.mark.asyncio
async def test_follow_keeps_direction_risk_when_old_plan_lacks_cycle_metadata():
    from test_study_live_fund_plans import (
        OriginConnection,
        history,
        origin_row,
        read_origin,
        NOW as FOLLOW_NOW,
    )

    row, code, track = origin_row("direction")
    row["payload"]["asOf"] = (FOLLOW_NOW - timedelta(days=9)).isoformat()
    row["payload"]["valueDirections"][0]["industryCycle"] = deepcopy(EARLY)
    conn = OriginConnection(row, [history(code)])
    origin = await read_origin(conn, "direction", code, track)
    assert (
        origin["actionablePlan"]["industryCycle"]["stage"] == "EARLY_COMMERCIALIZATION"
    )
    assert origin["actionablePlan"]["industryRiskAdjustment"]["state"] == "APPLIED"
    assert origin["valueDirection"]["industryCycle"] == EARLY


def test_exact_fund_risk_applies_to_its_adjusted_etf_prices_without_copying_nav():
    context = service.fund_risk_context(report("510999"), NOW)
    baseline = compute_actionable_price_plan(
        [
            {**point, "close": 100, "high": 101, "low": 99}
            for point in _history()["points"]
        ],
        subject_kind="fund",
        subject_id=str(uuid4()),
        price_basis="ETF_HFQ",
        market="SSE",
    )
    baseline["fundCode"] = "510999"
    protected = service.protect_fund_plan(baseline, context)
    assert protected["priceBasis"] == "ETF_HFQ"
    assert protected["currentPrice"] == "100.0000"
    assert protected["tracks"][0]["entryZoneMax"] == "98.0000"
    assert protected["tracks"][1] == baseline["tracks"][1]
