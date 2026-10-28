"""New buy/sell coordinates survive model and personal read projections."""

from copy import deepcopy

import pytest

from backend.macro.model_projection import model_packet
from backend.investment import study_price_watch as watch
from test_entry_safety48 import NOW, SafetyFacts, plan, quote


def sell_plan():
    value = plan()
    value.update(
        methodVersion="actionable-zones-v5",
        inSellZone=True,
        entryBlocked=True,
        entryStatus="🔥 已入建议卖出区间(90%分位)·持仓宜止盈/空仓勿追高",
        entryStatusTone="rose",
        historicalPriceWindow={
            "sampleCount": 500,
            "requiredSamples": 220,
            "state": "READY",
        },
        indicators={"p55": 1.0, "p85": 1.4, "peakHigh": 1.7},
    )
    value["tracks"][0].update(
        sellZoneMin="1.2",
        sellZoneMax="1.4",
        inSellZone=True,
        entryBlocked=True,
        entryStatus=value["entryStatus"],
        entryStatusTone="rose",
    )
    return value


def test_model_receives_the_same_historical_anchors_and_sell_state_as_the_ui():
    value = sell_plan()
    projected = model_packet({"quantitativeScan": {"items": [value]}})[
        "quantitativeScan"
    ]["items"][0]
    assert projected["methodVersion"] == "actionable-zones-v5"
    assert projected["historicalPriceWindow"]["sampleCount"] == 500
    assert projected["indicators"]["p85"] == 1.4
    assert projected["tracks"][0]["sellZoneMin"] == "1.2"
    assert projected["tracks"][0]["sellZoneMax"] == "1.4"
    assert projected["tracks"][0]["inSellZone"] is True


def test_sell_state_itself_prevents_a_buy_hit_and_survives_the_watch_summary():
    frozen = plan()
    safety = deepcopy(frozen)
    safety["inSellZone"] = True
    assert watch.entry_permitted(frozen, frozen["tracks"][0], safety) is False
    result = watch.price_watch(frozen, estimate=quote(), now=NOW, entry_safety=safety)
    assert result["state"] != "ENTRY_ZONE"
    assert result["entrySafety"]["inSellZone"] is True


def test_current_public_sell_condition_is_visible_even_below_an_older_frozen_target():
    frozen = plan()
    safety = sell_plan()
    result = watch.price_watch(
        frozen, estimate=quote("1.1"), now=NOW, entry_safety=safety
    )
    assert result["state"] == "TAKE_PROFIT"
    assert "当前公开研究" in result["reason"]


@pytest.mark.asyncio
async def test_current_sell_state_overlays_frozen_prices_without_moving_their_coordinates():
    frozen = plan()
    before = deepcopy(frozen)
    guarded = (
        await watch.current_entry_safety(
            SafetyFacts(
                packet={
                    "asOf": NOW.isoformat(),
                    "quantitativeScan": {"items": [sell_plan()]},
                }
            ),
            [frozen],
            NOW,
        )
    )[0]
    assert guarded["inSellZone"] is True
    assert guarded["entryStatusTone"] == "rose"
    assert not watch.entry_permitted(frozen, frozen["tracks"][0], guarded)
    for name in ("entryZoneMin", "entryZoneMax", "takeProfitT1", "takeProfitT2"):
        assert guarded["tracks"][0][name] == frozen["tracks"][0][name]
    assert frozen == before


@pytest.mark.asyncio
async def test_a_frozen_sell_signal_does_not_become_a_permanent_current_sell_signal():
    frozen = sell_plan()
    current = plan()
    current["indicators"] = {"navPercentile1y": 40, "ma20": 1.5}
    guarded = (
        await watch.current_entry_safety(
            SafetyFacts(
                packet={
                    "asOf": NOW.isoformat(),
                    "quantitativeScan": {"items": [current]},
                }
            ),
            [frozen],
            NOW,
        )
    )[0]
    assert guarded.get("inSellZone") is not True
    assert all(row.get("inSellZone") is not True for row in guarded["tracks"])
