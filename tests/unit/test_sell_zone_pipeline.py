"""Historical sell coordinates remain usable across public research surfaces."""

from copy import deepcopy
from datetime import timedelta

import pytest

from backend.investment.discovery import _quantitative_scan
from backend.investment.quantitative_scan import public_quantitative_scan
from backend.macro.daily_research import _public_actionable_plan, apply_entry_guards
from test_quantitative_scan import NOW, scan_plan


def sell_plan():
    plan = scan_plan("008298", methodVersion="actionable-zones-v5")
    plan.update(
        currentPrice="1.7675",
        selectedTrack="RIGHT_TREND",
        historicalPriceWindow={
            "state": "READY",
            "sampleCount": 440,
            "requiredSamples": 220,
            "start": "2024-10-01",
            "end": plan["asOf"],
        },
        parameters={"historicalPriceWindow": [220, 500]},
        indicators={
            "ma20": 1.70,
            "navPercentile1y": 98,
            **{key: 1.5 for key in ("p35", "p40", "p50", "p55", "p60", "p65")},
            "p85": 1.72,
            "p95": 1.76,
            "peakHigh": 1.8,
        },
    )
    plan["indicatorStates"] = {
        "p85": {"state": "READY", "sampleCount": 440, "requiredSamples": 220}
    }
    track = plan["tracks"][0]
    track.update(
        entryZoneMin="1.4500",
        entryZoneMax="1.5000",
        stopLossPrice="1.4000",
        takeProfitT1="1.7200",
        takeProfitT2="1.8000",
        sellZoneMin="1.7200",
        sellZoneMax="1.8000",
        inSellZone=True,
        buyZoneAnchor="HISTORICAL_SUPPORT_MA60",
        entryBlocked=True,
        entryBlockReason="IN_SELL_ZONE",
        entryReady=False,
        inBuyZone=False,
        entryStatus="🔥 已入建议卖出区间(98%分位)·持仓宜止盈/空仓勿追高",
        entryStatusTone="rose",
    )
    plan["signals"] = [
        {
            "track": "RIGHT_TREND",
            "triggerState": "WATCH",
            "candidate": False,
            "inEntryZone": False,
            "entryReady": False,
            "distanceToEntryPct": round((1.7675 - 1.5) / 1.7675 * 100, 4),
            "inSellZone": True,
        }
    ]
    return plan


def test_public_plan_preserves_historical_anchors_and_sell_zone_without_private_data():
    original = sell_plan()
    original["indicatorStates"]["private"] = {"secret": "not public"}
    public = _public_actionable_plan(original, "008298", NOW, subject_kind="nav")
    assert public["historicalPriceWindow"] == original["historicalPriceWindow"]
    assert public["parameters"]["historicalPriceWindow"] == [220, 500]
    assert public["indicators"]["p85"] == 1.72
    assert public["indicators"]["peakHigh"] == 1.8
    assert public["indicatorStates"]["p85"]["state"] == "READY"
    assert "private" not in public["indicatorStates"]
    assert public["tracks"][0]["inSellZone"] is True
    assert public["tracks"][0]["sellZoneMin"] == "1.7200"
    assert public["tracks"][0]["buyZoneAnchor"] == "HISTORICAL_SUPPORT_MA60"


def test_discovery_and_validated_scan_keep_sell_signal_and_never_ready_for_buy():
    source = sell_plan()
    scan = _quantitative_scan(
        {"discoveryUniverse": [], "discoveryActionableZones": [source]}, NOW
    )
    assert scan["items"][0]["signals"][0]["inSellZone"] is True
    public = public_quantitative_scan(scan, NOW)
    track, signal = public["items"][0]["tracks"][0], public["items"][0]["signals"][0]
    assert track["sellZoneMin"] == track["takeProfitT1"]
    assert signal["inSellZone"] is True
    assert signal["entryReady"] is False
    assert signal["entryStatusTone"] == "rose"


def test_scan_without_model_selected_track_still_exposes_sell_status_on_fund():
    source = sell_plan()
    source.pop("selectedTrack")
    result = apply_entry_guards({"quantitativeScan": {"items": [source]}}, {}, NOW)
    fund = result["quantitativeScan"]["items"][0]
    assert fund["inSellZone"] is True
    assert fund["entryStatusTone"] == "rose"
    assert fund["entryReady"] is False


def test_partial_short_nav_candidate_cannot_become_buy_ready_in_scan():
    from backend.indicators.actionable_zones import compute_actionable_price_plan

    plan = compute_actionable_price_plan(
        [
            {"date": (NOW.date() - timedelta(days=19 - i)).isoformat(), "close": "1.0"}
            for i in range(20)
        ],
        subject_kind="nav",
        subject_id="008298",
        price_basis="UNIT_NAV",
        unit="CNY",
        source_uri="https://fund.eastmoney.com/008298.html",
        market="FUND_CN",
    )
    plan.update(fundCode="008298", historyQuality="READY")
    left = plan["tracks"][0]
    assert left["state"] == "CANDIDATE"
    assert left["priceState"] == "PARTIAL" and left["entryReady"] is False
    scan = _quantitative_scan(
        {"discoveryUniverse": [], "discoveryActionableZones": [plan]}, NOW
    )
    assert all(signal["entryReady"] is False for signal in scan["items"][0]["signals"])
    assert public_quantitative_scan(scan, NOW)["items"] == []


def test_scan_respects_explicit_entry_not_ready_even_with_complete_coordinates():
    plan = scan_plan()
    plan["tracks"][0].update(state="CANDIDATE", entryReady=False)
    scan = _quantitative_scan(
        {"discoveryUniverse": [], "discoveryActionableZones": [plan]}, NOW
    )
    assert scan["items"][0]["signals"][0]["entryReady"] is False
    public = public_quantitative_scan(scan, NOW)
    assert public["items"][0]["signals"][0]["entryReady"] is False


@pytest.mark.parametrize("restriction", [None, "DELAYED_NAV", "SUBSCRIPTION"])
def test_sell_status_survives_high_and_other_entry_guards(restriction):
    original = sell_plan()
    packet = {"asOf": NOW.isoformat()}
    if restriction == "DELAYED_NAV":
        original.update(label="纳斯达克QDII", overseasCalendarRequired=True)
        packet["series"] = [
            {
                "code": "US_NASDAQ_CHANGE_PCT",
                "value": -3,
                "asOf": NOW.date().isoformat(),
            }
        ]
    if restriction == "SUBSCRIPTION":
        packet["fundSubscriptionStatus"] = {"008298": {"purchaseStatus": "SUSPENDED"}}
    value = {"valueDirections": [{"signal": "RIGHT_TREND", "plans": [original]}]}
    before = deepcopy(value)
    actual = apply_entry_guards(value, packet, NOW)["valueDirections"][0]
    assert len(actual["plans"]) == 1
    plan = actual["plans"][0]
    for row in (plan, plan["tracks"][0], plan["signals"][0]):
        assert row["inSellZone"] is True
        assert row["entryStatusTone"] == "rose"
        assert row["entryBlocked"] is True
        assert row["entryReady"] is False
        assert row["inBuyZone"] is False
    if restriction == "DELAYED_NAV":
        assert plan["entryBlockReason"] == "DELAYED_NAV_MARKET_DROP"
    if restriction == "SUBSCRIPTION":
        assert plan["subscriptionRestricted"] is True
    assert value == before


def test_old_high_report_does_not_invent_a_new_algorithm_sell_zone():
    old = sell_plan()
    old["methodVersion"] = "actionable-zones-v4"
    for row in (old, old["tracks"][0], old["signals"][0]):
        row.pop("inSellZone", None)
    result = apply_entry_guards({"actionablePlans": [old]}, {}, NOW)
    assert "inSellZone" not in result["actionablePlans"][0]
    assert result["actionablePlans"][0]["entryStatusTone"] == "amber"


def test_model_prompt_separates_sell_advice_from_buy_readiness():
    from backend.macro.daily_research import build_prompt

    prompt = build_prompt({})
    assert "inSellZone=true" in prompt
    assert "sellZoneMin/sellZoneMax" in prompt
    assert "中期均线MA60" in prompt
    assert "不得作为买入信号" in prompt


def test_unusable_nav_history_clears_all_active_sell_coordinates(monkeypatch):
    from backend.investment import theme_graph
    from test_theme_graph import NOW as HISTORY_NOW, nav_history

    calculated = sell_plan()
    monkeypatch.setattr(
        theme_graph,
        "compute_actionable_price_plan",
        lambda *a, **k: deepcopy(calculated),
    )
    monkeypatch.setattr(
        theme_graph, "_history_quality", lambda *a: ("CONFLICT", "历史冲突")
    )
    result = theme_graph.nav_plan(nav_history(), now=HISTORY_NOW)
    track = result["tracks"][0]
    assert track["sellZoneMin"] is track["sellZoneMax"] is None
    assert track["inSellZone"] is False
    assert track.get("entryStatusTone") != "rose"
