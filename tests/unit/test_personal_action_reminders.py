"""Held funds use current public coordinates without requiring a frozen study."""

from copy import deepcopy
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.investment import sell_risk
from test_sell_risk47 import (
    ACTOR,
    NOW,
    TENANT,
    ActorFacts,
    confirmed,
    holding_row,
    target,
)


def current_plan():
    plan = deepcopy(target()["plan"])
    plan.pop("selectedTrack")
    plan["historyQuality"] = "READY"
    plan["tracks"][0].update(sellZoneMin="1.2", sellZoneMax="1.4")
    return plan


def held():
    return target(relationship=["HELD"], plan=None, planOrigin="NONE")


@pytest.mark.parametrize(
    "value,change,state",
    [
        ("0.7999", 0, "STOP_LOSS"),
        ("0.8", 0, "BUY_ZONE"),
        ("0.85", 0, "BUY_ZONE"),
        ("1.0", 0, "BUY_ZONE"),
        ("1.2", 0, "SELL_ZONE"),
        ("1.5", 0, "SELL_ZONE"),
        ("1.15", 2.5, "LARGE_DAILY_MOVE"),
        ("1.15", -2.5, "LARGE_DAILY_MOVE"),
        ("1.15", 2.49, "NORMAL"),
    ],
)
def test_current_holding_coordinates_and_daily_move_have_inclusive_boundaries(
    value, change, state
):
    owned = held()
    quote = confirmed(value, actionableZones=current_plan(), dailyChangePct=change)
    before = deepcopy((owned, quote))
    item = sell_risk.evaluate_action_reminder(owned, confirmed=quote, now=NOW)
    assert item["state"] == state
    assert item["planOrigin"] == "CURRENT_ACTIONABLE_ZONES"
    assert item["dailyChangePct"] == change
    assert item["selectedTrack"] == "LEFT_VALUE"
    assert (owned, quote) == before


def test_normal_holding_reports_distance_to_both_zones():
    item = sell_risk.evaluate_action_reminder(
        held(),
        confirmed=confirmed("1.15", actionableZones=current_plan(), dailyChangePct=0),
        now=NOW,
    )
    assert item["state"] == "NORMAL" and item["triggerStates"] == []
    assert item["distanceToBuyPct"] == pytest.approx(4.3478)
    assert item["distanceToSellPct"] == pytest.approx(4.3478)
    assert item["distanceToStopPct"] == pytest.approx(30.4348)


def test_a_frozen_study_keeps_its_own_coordinates_and_rsi_alone_is_not_an_action():
    owned = target(relationship=["HELD"])
    fresh = current_plan()
    fresh["tracks"][0].update(entryZoneMin="1.1", entryZoneMax="1.16")
    item = sell_risk.evaluate_action_reminder(
        owned,
        confirmed=confirmed("1.15", 99, actionableZones=fresh),
        now=NOW,
    )
    assert item["state"] == "NORMAL"
    assert item["planOrigin"] == "FROZEN_STUDY"
    assert item["entryZoneMax"] == "1.1"


def test_quote_without_comparable_zones_can_only_report_a_real_daily_move():
    assert (
        sell_risk.evaluate_action_reminder(
            held(), confirmed=confirmed("1.15", dailyChangePct=0), now=NOW
        )
        is None
    )
    item = sell_risk.evaluate_action_reminder(
        held(), confirmed=confirmed("1.15", dailyChangePct=-3), now=NOW
    )
    assert item["state"] == "LARGE_DAILY_MOVE"
    assert item["distanceToBuyPct"] is None


@pytest.mark.parametrize(
    "change", [{"quality": "STALE"}, {"fundCode": "000216"}, {"usable": False}]
)
def test_unusable_prices_cannot_become_normal_or_triggered_holdings(change):
    assert (
        sell_risk.evaluate_action_reminder(
            held(),
            confirmed=confirmed("1.0", actionableZones=current_plan(), **change),
            now=NOW,
        )
        is None
    )


def test_fresh_estimate_uses_latest_confirmed_nav_as_its_daily_return_base():
    item = sell_risk.evaluate_action_reminder(
        held(),
        confirmed=confirmed("1.15", actionableZones=current_plan(), dailyChangePct=-4),
        estimate=confirmed("1.1845", quality="ESTIMATED", asOf=NOW.isoformat()),
        now=NOW,
    )
    assert item["state"] == "LARGE_DAILY_MOVE"
    assert item["dailyChangePct"] == 3
    assert item["dailyChangeAsOf"] == NOW.isoformat()
    assert item["basis"] == "ESTIMATE_1430"


def test_wrong_fund_current_coordinates_cannot_be_compared():
    wrong = current_plan()
    wrong.update(fundCode="000216", subjectId="000216")
    assert (
        sell_risk.evaluate_action_reminder(
            held(), confirmed=confirmed("1.0", actionableZones=wrong), now=NOW
        )
        is None
    )


def test_current_intraday_estimate_is_used_before_the_1430_window():
    clock = NOW.replace(hour=10, minute=15)
    quote = confirmed(
        "1.25",
        quality="ESTIMATED",
        asOf=clock.isoformat(),
        retrievedAt=clock.isoformat(),
        checkedAt=clock.isoformat(),
        tradingDayConfirmed=True,
        changePct="3.1",
    )
    item = sell_risk.evaluate_action_reminder(
        held(),
        confirmed=confirmed(
            "1.15", retrievedAt=clock.isoformat(), actionableZones=current_plan()
        ),
        estimate=quote,
        now=clock,
    )
    assert item["state"] == "SELL_ZONE"
    assert item["triggerStates"] == ["SELL_ZONE", "LARGE_DAILY_MOVE"]
    assert item["basis"] == "INTRADAY_ESTIMATE"
    assert item["dailyChangePct"] == 3.1


@pytest.mark.asyncio
async def test_unfrozen_holding_uses_the_saved_public_estimate_without_provider_io(
    monkeypatch,
):
    from backend.investment import personal_funds

    conn = ActorFacts(holdings=[holding_row()])
    monkeypatch.setattr(
        sell_risk,
        "confirmed_quotes",
        AsyncMock(
            return_value={
                "000051": confirmed("1.15", actionableZones=current_plan()),
            }
        ),
    )
    monkeypatch.setattr(
        personal_funds,
        "_persisted_estimates",
        AsyncMock(
            return_value={
                "000051": confirmed(
                    "1.25",
                    quality="ESTIMATED",
                    asOf=NOW.isoformat(),
                    checkedAt=NOW.isoformat(),
                    tradingDayConfirmed=True,
                ),
            }
        ),
    )
    result = await sell_risk.sell_risk_alerts(conn, TENANT, ACTOR, NOW)
    assert result["actionReminders"][0]["basis"] == "INTRADAY_ESTIMATE"
    assert result["actionReminders"][0]["state"] == "SELL_ZONE"


@pytest.mark.asyncio
async def test_read_groups_only_actual_triggers_and_summarizes_middle_holdings(
    monkeypatch,
):
    conn = ActorFacts(
        holdings=[holding_row(), holding_row("000216"), holding_row("000003")]
    )
    quiet = confirmed("1.15", actionableZones=current_plan(), dailyChangePct=0)
    triggered = deepcopy(quiet)
    triggered.update(fundCode="000216", subjectId="000216", value="1.25")
    triggered["actionableZones"].update(fundCode="000216", subjectId="000216")
    monkeypatch.setattr(
        sell_risk,
        "confirmed_quotes",
        AsyncMock(return_value={"000051": quiet, "000216": triggered}),
    )
    data = await sell_risk.sell_risk_alerts(conn, TENANT, ACTOR, NOW)
    assert [row["fundCode"] for row in data["actionReminders"]] == ["000216"]
    assert [row["fundCode"] for row in data["normalHoldings"]] == ["000051"]
    assert data["summary"] == {
        "heldCount": 3,
        "evaluatedCount": 2,
        "alertCount": 1,
        "normalCount": 1,
        "unavailableCount": 1,
    }
    assert "quantity" not in str(data)


@pytest.mark.asyncio
async def test_daily_actionable_zones_are_available_for_unfrozen_holdings():
    plan = current_plan()
    plan["selectedTrack"] = "LEFT_VALUE"
    rows = await sell_risk.load_risk_targets(
        ActorFacts(holdings=[holding_row()]),
        TENANT,
        ACTOR,
        NOW,
        daily={
            "reportRunId": "today",
            "report": {"asOf": NOW.isoformat(), "actionableZones": [plan]},
        },
    )
    assert rows[0]["planOrigin"] == "DAILY_REPORT"
    assert rows[0]["plan"]["selectedTrack"] == "LEFT_VALUE"


@pytest.mark.asyncio
async def test_confirmed_quote_and_current_zones_use_the_latest_chart_history(
    monkeypatch,
):
    from backend.investment import discovery
    from test_live_fund_plans_v6 import NOW as clock, history

    latest = history("000051")
    older = deepcopy(latest)
    older["points"] = older["points"][:-1]
    older["asOf"] = older["points"][-1]["date"]
    older.pop("providerHistoryProof")
    monkeypatch.setattr(
        discovery, "_cached_histories", AsyncMock(return_value={"000051": older})
    )

    class ChartFacts(ActorFacts):
        async def fetch(self, sql, *args):
            if "market.fund_nav_chart_histories" in sql:
                return [{"fund_code": "000051", "payload": latest}]
            return await super().fetch(sql, *args)

    quotes = await sell_risk.confirmed_quotes(ChartFacts(), [held()], clock)
    assert quotes["000051"]["asOf"] == latest["asOf"]
    assert quotes["000051"]["actionableZones"]["asOf"] == latest["asOf"]
    assert Decimal(quotes["000051"]["value"]) == Decimal(latest["points"][-1]["close"])
