"""Only public, same-subject deterministic anchors enter the daily request."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.macro.daily_research import make_research_packet

NOW = datetime(2026, 10, 17, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def plan():
    return {
        "methodVersion": "actionable-zones-v1",
        "subjectKind": "index",
        "subjectId": "hs300",
        "label": "沪深300",
        "priceBasis": "INDEX_POINTS",
        "unit": "指数点",
        "asOf": "2026-10-16",
        "sourceUri": "https://www.csindex.com.cn/",
        "currentPrice": "4000.125",
        "indicators": {"ma20": 4001, "atr14": 50, "private": "PRIVATE"},
        "tracks": [
            {
                "track": "LEFT_VALUE",
                "state": "CANDIDATE",
                "entryZoneMin": "3950.000",
                "entryZoneMax": "4001.000",
                "stopLossPrice": "3850.000",
                "takeProfitT1": "4101.000",
                "takeProfitT2": "4201.000",
                "priceState": "READY",
                "reasons": ["实际条件成立"],
                "userId": "PRIVATE",
            }
        ],
        "holdings": "PRIVATE",
        "account": "PRIVATE",
        "orderPriceAvailable": False,
    }


def packet(value):
    return make_research_packet(
        {},
        [],
        [{"indexId": "hs300", "asOf": "2026-10-16", "actionableZones": value}],
        [],
        now=NOW,
    )


def test_public_packet_includes_actual_same_index_zones_but_no_actor_fields():
    actual = packet(plan())
    assert actual["actionableZones"][0]["tracks"][0]["entryZoneMin"] == "3950.000"
    assert actual["actionableZones"][0]["currentPrice"] == "4000.125"
    assert "PRIVATE" not in str(actual)


def test_public_packet_does_not_borrow_other_asset_or_future_anchors():
    other = plan()
    other["subjectId"] = "private-fund"
    assert packet(other).get("actionableZones", []) == []
    future = plan()
    future["asOf"] = "2026-10-18"
    assert packet(future).get("actionableZones", []) == []
    private = plan()
    private["subjectKind"] = "fund"
    assert packet(private).get("actionableZones", []) == []


def test_public_packet_keeps_price_basis_and_does_not_mutate_original():
    value = plan()
    original = deepcopy(value)
    actual = packet(value)
    assert actual["actionableZones"][0]["priceBasis"] == "INDEX_POINTS"
    assert actual["actionableZones"][0]["orderPriceAvailable"] is False
    assert value == original
