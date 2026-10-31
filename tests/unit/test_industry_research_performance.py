"""Bound research work and cache immutable math, never time-dependent guards."""

from copy import deepcopy
from datetime import timedelta

import pytest

from test_daily_sources import NOW


def test_industry_queries_prioritize_news_then_ready_then_nearest_with_a_hard_cap():
    from backend.integrations.daily_sources import industry_cycle_research_tasks

    packet = {
        "discoveryUniverse": [{"discoverySector": f"SECTOR_{i}"} for i in range(25)],
        "quantitativeScan": {
            "items": [
                {
                    "discoverySector": "NEAREST",
                    "signals": [{"distanceToEntryPct": 0.1}],
                },
                {
                    "discoverySector": "READY",
                    "signals": [{"entryReady": True, "distanceToEntryPct": 0}],
                },
            ]
        },
        "alphaPublic": {
            "sectorCatalysts": {"NEWS": ["catalyst-today"], "OTHER": ["misc"]}
        },
    }
    result = industry_cycle_research_tasks(packet, NOW)
    assert [row["industry"] for row in result["candidates"][:3]] == [
        "NEWS",
        "READY",
        "NEAREST",
    ]
    assert len(result["candidates"]) == 10
    assert sum(len(row["queries"]) for row in result["candidates"]) == 50
    assert result["omittedCandidateCount"] == 18
    assert all("OTHER" != row["industry"] for row in result["candidates"])


@pytest.mark.asyncio
async def test_live_plan_math_is_reused_without_reusing_calculation_clock_or_mutable_outputs(
    monkeypatch,
):
    from backend.investment import live_fund_plans as mod
    from test_live_fund_plans_v6 import NOW as CLOCK, history, old_plan, LocalHistory

    calls = []
    compute = mod.compute_actionable_price_plan

    def counted(*args, **kwargs):
        calls.append(kwargs["subject_id"])
        return compute(*args, **kwargs)

    monkeypatch.setattr(mod, "compute_actionable_price_plan", counted)
    if hasattr(mod, "_PLAN_CACHE"):
        mod._PLAN_CACHE.clear()
    data, report = history(), {"actionablePlans": [old_plan()]}
    first = await mod.refresh_report_fund_plans(LocalHistory([data]), report, CLOCK)
    original_price = first["actionablePlans"][0]["tracks"][0]["entryZoneMin"]
    first["actionablePlans"][0]["tracks"][0]["entryZoneMin"] = "9999"
    later = CLOCK + timedelta(minutes=1)
    second = await mod.refresh_report_fund_plans(LocalHistory([data]), report, later)
    assert calls == ["008298"]
    assert second["actionablePlans"][0]["tracks"][0]["entryZoneMin"] == original_price
    assert (
        second["actionablePlans"][0]["priceRefresh"]["calculatedAt"]
        == later.isoformat()
    )


@pytest.mark.asyncio
async def test_cache_cannot_reuse_old_ready_state_after_nav_freshness_expires(
    monkeypatch,
):
    from backend.investment import live_fund_plans as mod
    from test_live_fund_plans_v6 import NOW as CLOCK, history, old_plan, LocalHistory

    if hasattr(mod, "_PLAN_CACHE"):
        mod._PLAN_CACHE.clear()
    data, report = history(), {"actionablePlans": [old_plan()]}
    first = await mod.refresh_report_fund_plans(LocalHistory([data]), report, CLOCK)
    assert first["actionablePlans"][0]["priceRefresh"]["state"] == "READY"
    expired = await mod.refresh_report_fund_plans(
        LocalHistory([data]), report, CLOCK + timedelta(days=11)
    )
    assert expired["actionablePlans"][0]["priceRefresh"]["state"] != "READY"
    assert all(
        row["entryReady"] is False for row in expired["actionablePlans"][0]["tracks"]
    )


def test_cache_key_binds_revised_price_content_benchmark_and_has_a_hard_size_limit():
    from backend.investment import live_fund_plans as mod
    from test_live_fund_plans_v6 import history

    cached = getattr(mod, "_cached_nav_plan", None)
    assert callable(cached), "bounded price-plan cache missing"
    mod._PLAN_CACHE.clear()
    data = history()
    points = data["points"][-500:]
    view = {
        "asOf": data["asOf"],
        "retrievedAt": data["retrievedAt"],
        "sourceUri": data["sourceUri"],
    }
    first = cached("008298", view, points, None)
    revised = deepcopy(points)
    revised[-1]["close"] = "2.0000"
    assert (
        cached("008298", view, revised, None)["currentPrice"] != first["currentPrice"]
    )
    benchmark = {
        "asOf": data["asOf"],
        "points": [{**point, "close": "4000"} for point in points],
    }
    assert (
        cached("008298", view, points, benchmark)["relativeStrength"]["excessReturn20d"]
        is not None
    )
    changed = deepcopy(benchmark)
    changed["points"][-1]["close"] = "5000"
    assert (
        cached("008298", view, points, changed)["relativeStrength"]["excessReturn20d"]
        != cached("008298", view, points, benchmark)["relativeStrength"][
            "excessReturn20d"
        ]
    )
    for index in range(260):
        cached(f"{index:06d}", view, points[:2], None)
    assert len(mod._PLAN_CACHE) <= 256
