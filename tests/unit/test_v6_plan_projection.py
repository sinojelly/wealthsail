"""Profit and calculation provenance must reach every public plan reader."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.indicators.actionable_zones import compute_actionable_price_plan
from backend.macro.daily_research import _public_actionable_plan
from backend.macro.model_projection import model_packet
from backend.investment import study_price_watch as watch
from test_entry_safety48 import NOW, SafetyFacts, plan


def calculated():
    value = compute_actionable_price_plan(
        [
            {
                "date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                "close": str(1 + i * 0.002),
            }
            for i in range(220)
        ],
        subject_kind="nav",
        subject_id="008298",
        price_basis="UNIT_NAV",
        unit="CNY/份",
        source_uri="https://fund.eastmoney.com/008298.html",
        market="FUND_CN",
    )
    value["fundCode"] = "008298"
    return value


@pytest.mark.parametrize("projection", ["public", "model"])
def test_v6_profit_and_span_facts_are_not_dropped_by_public_or_model_projection(
    projection,
):
    value = calculated()
    value["researchAsOf"] = "2026-01-01"
    value["priceRefresh"] = {
        "state": "READY",
        "methodVersion": "actionable-zones-v6",
        "navAsOf": value["asOf"],
    }
    if projection == "public":
        result = _public_actionable_plan(
            value,
            "008298",
            datetime(2026, 10, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
            subject_kind="nav",
        )
    else:
        result = model_packet({"quantitativeScan": {"items": [value]}})[
            "quantitativeScan"
        ]["items"][0]
    for key in ("p30", "p45", "p75", "p92", "troughLow", "priceSpan"):
        assert result["indicators"][key] == value["indicators"][key]
    for original, projected in zip(value["tracks"], result["tracks"], strict=True):
        assert projected["estimatedProfitMinPct"] == original["estimatedProfitMinPct"]
        assert projected["estimatedProfitMaxPct"] == original["estimatedProfitMaxPct"]
    assert result["researchAsOf"] == "2026-01-01"
    assert result["priceRefresh"]["methodVersion"] == "actionable-zones-v6"


@pytest.mark.asyncio
async def test_v6_current_sell_state_is_recognized_without_moving_frozen_coordinates():
    frozen = plan()
    current = deepcopy(frozen)
    current.update(
        methodVersion="actionable-zones-v6",
        inSellZone=True,
        entryBlocked=True,
        entryStatus="🔥 已入建议卖出区间(90%分位)·持仓宜止盈/空仓勿追高",
        entryStatusTone="rose",
    )
    current["tracks"][0].update(
        inSellZone=True,
        entryBlocked=True,
        entryStatus=current["entryStatus"],
        entryStatusTone="rose",
    )
    result = (
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
    assert result["inSellZone"] is True
    assert result["tracks"][0]["takeProfitT1"] == frozen["tracks"][0]["takeProfitT1"]
