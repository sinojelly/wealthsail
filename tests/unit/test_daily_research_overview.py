"""Current public observations remain useful without model or full entry gates."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from test_daily_research import NOW, engine
from test_research_board import build, trend


def overview(*, evidence=None, board=None, daily=None, context=None):
    evidence = [trend()] if evidence is None else evidence
    return engine().build_research_overview(
        context or {},
        [],
        evidence,
        {},
        board or build(evidence=evidence),
        daily or {"state": "NOT_RUN", "report": None},
        now=NOW,
    )


def test_positive_or_unmet_trend_points_to_same_actual_condition_not_empty_opportunity_list():
    for positive in (True, False):
        row = trend(positive=positive)
        result = overview(evidence=[row])
        fact = next(h for h in result["highlights"] if h["kind"] == "INDEX_TREND")
        assert fact["targetTab"] == "direction" and fact["indexId"] == "hs300"
        assert fact["id"].endswith(":trend:hs300")
        assert fact["asOf"] == "2026-10-14"
        assert fact["tone"] == ("GREEN" if positive else "BLUE")
        assert ("尚未满足" in fact["summary"]) is (not positive)


def test_independent_short_pe_is_visible_without_full_valuation_or_fake_percentile():
    row = trend()
    row["valuation"] = {
        "state": "PARTIAL",
        "pe": {
            "state": "INSUFFICIENT",
            "latest": 12.8,
            "percentile": None,
            "sampleCount": 500,
        },
    }
    result = overview(evidence=[row])
    pe = next(h for h in result["highlights"] if h["kind"] == "INDEX_PE")
    assert "12.8" in pe["summary"] and "分位" in pe["summary"]
    assert "不足" in pe["summary"] and pe["tone"] != "GREEN"
    assert "买入" not in str(result) and "便宜" not in str(result)


def test_real_future_schedule_is_a_planned_observation_not_a_completed_fact():
    context = {
        "items": [
            {
                "code": "PMI",
                "label": "制造业PMI",
                "nextRelease": {
                    "status": "SCHEDULED",
                    "scheduledAt": (NOW + timedelta(days=10)).isoformat(),
                    "sourceUri": "https://www.stats.gov.cn/calendar",
                },
            }
        ]
    }
    result = overview(evidence=[], context=context)
    future = result["highlights"][0]
    assert future["kind"] == "UPCOMING" and future["targetTab"] == "macro"
    assert "计划" in future["summary"] and "实际公布" in future["impact"]


def test_unknown_risk_and_source_failures_do_not_fill_highlights_or_generate_red():
    result = overview(
        evidence=[],
        daily={"state": "FAILED", "errorClass": "TimeoutError", "report": None},
    )
    assert result["highlights"] == []


def test_verified_private_breach_first_but_daily_model_event_is_never_red():
    evidence = [trend()]
    board = build(
        evidence=evidence,
        decision={"state": "POSITION_REVIEW", "gates": []},
        portfolio={
            "state": "READY",
            "heldSelected": True,
            "budgetViolation": True,
            "budgetState": "BLOCKED",
        },
    )
    daily = {
        "state": "READY",
        "asOf": NOW.isoformat(),
        "report": {
            "asOf": NOW.isoformat(),
            "sources": [
                {
                    "id": "source:a",
                    "verification": "SOURCE_CHECKED",
                    "publishedDate": "2026-10-15",
                    "retrievedAt": NOW.isoformat(),
                }
            ],
            "events": [
                {
                    "id": "event:a",
                    "title": "公开政策线索",
                    "summary": "需要核对实际传导。",
                    "importance": "HIGH",
                    "phase": "ANNOUNCED",
                    "sourceIds": ["source:a"],
                    "affectedDirectionIds": ["a-share:broad"],
                    "channels": ["融资可能变化"],
                    "marketReaction": "UNVERIFIED",
                }
            ],
        },
    }
    result = overview(
        evidence=evidence,
        board=board,
        daily=daily,
        context={"directionId": "a-share:broad"},
    )
    assert result["highlights"][0]["kind"] == "PERSONAL_RISK"
    assert result["highlights"][0]["id"].endswith(":personal-budget")
    assert result["highlights"][0]["tone"] == "RED"
    assert len(result["highlights"]) <= 3
    event = next(h for h in result["highlights"] if h["kind"] == "EVENT")
    assert event["tone"] != "RED" and "模型" in event["impact"]


@pytest.mark.parametrize("bad", ["future", "stale", "foreign"])
def test_future_old_or_other_direction_daily_report_cannot_mask_current_index_fact(bad):
    stamp = (
        NOW + timedelta(seconds=1)
        if bad == "future"
        else NOW - timedelta(days=30)
        if bad == "stale"
        else NOW
    )
    daily = {
        "state": "READY",
        "asOf": stamp.isoformat(),
        "report": {
            "asOf": stamp.isoformat(),
            "sources": [{"id": "s", "retrievedAt": stamp.isoformat()}],
            "events": [
                {
                    "id": "e",
                    "title": "错范围事件",
                    "summary": "other",
                    "importance": "HIGH",
                    "sourceIds": ["s"],
                    "affectedDirectionIds": ["us:broad"]
                    if bad == "foreign"
                    else ["a-share:broad"],
                }
            ],
        },
    }
    result = overview(daily=daily, context={"directionId": "a-share:broad"})
    assert all(h["kind"] != "EVENT" for h in result["highlights"])
    assert any(h["kind"] == "INDEX_TREND" for h in result["highlights"])


def test_context_returns_daily_and_overview_without_changing_existing_decision(
    monkeypatch,
):
    # Assembly stays read-only; all I/O collaborators use their existing contracts.
    from backend.investment import opportunity_service as service

    monkeypatch.setattr(
        service, "market_context", AsyncMock(return_value={"items": [], "events": []})
    )
    monkeypatch.setattr(
        service.market_repository, "read_permissions", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        service.market_repository, "read_collection", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        service.market_repository, "read_series", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(service, "resolve_instrument", AsyncMock(return_value=None))
    personal = {
        "decision": {"state": "WAIT", "gates": []},
        "portfolio": {
            "state": "UNKNOWN",
            "heldSelected": None,
            "budgetState": "UNKNOWN",
        },
    }
    monkeypatch.setattr(
        service, "decision_context", AsyncMock(return_value=deepcopy(personal))
    )
    daily = {"methodVersion": "daily-research-v1", "state": "NOT_RUN", "report": None}
    monkeypatch.setattr(
        service, "read_daily_research", AsyncMock(return_value=daily), raising=False
    )

    async def run():
        result = await service.get_context(AsyncMock(), "tenant", "actor", now=NOW)
        assert result["dailyResearch"] == daily
        assert result["researchOverview"]["highlights"] == []
        assert result["decision"] == personal["decision"]

    import asyncio

    asyncio.run(run())


def test_daily_future_plan_with_checked_source_can_be_a_limited_highlight():
    daily = {
        "state": "READY",
        "asOf": NOW.isoformat(),
        "report": {
            "asOf": NOW.isoformat(),
            "sources": [
                {
                    "id": "s",
                    "verification": "SOURCE_CHECKED",
                    "retrievedAt": NOW.isoformat(),
                }
            ],
            "events": [],
            "upcoming": [
                {
                    "id": "planned:x",
                    "title": "未来公开发布计划",
                    "phase": "PLANNED",
                    "scheduledDate": "2026-11-01",
                    "scheduledAt": None,
                    "sourceIds": ["s"],
                    "affectedDirectionIds": [],
                }
            ],
        },
    }
    result = overview(evidence=[], daily=daily)
    fact = result["highlights"][0]
    assert fact["kind"] == "UPCOMING" and fact["targetTab"] == "events"
    assert "计划" in fact["summary"] and "模型" in fact["impact"]
