"""Fund matches get local technical context without provider calls or buy grants."""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module, util

import pytest

from tests.unit.test_live_fund_plans_v6 import NOW, LocalHistory, history


def module():
    assert util.find_spec("backend.investment.opportunity_fund_prices"), (
        "fund price-position enrichment missing"
    )
    return import_module("backend.investment.opportunity_fund_prices")


def product(code="987651"):
    return {
        "fundCode": code,
        "subjectKind": "nav",
        "subjectId": code,
        "market": "FUND_CN",
        "name": "测试新产业ETF联接A",
        "matchLevel": "DYNAMIC_MATCH",
        "missingEvidence": [],
        "quantitativeScore": None,
        "actionable": False,
    }


@pytest.mark.asyncio
async def test_out_of_curated_pool_fund_uses_existing_local_nav_history():
    raw = {"topic:new": {"funds": [product()], "indirectFunds": []}}
    before = deepcopy(raw)
    conn = LocalHistory([history("987651")])
    result = await module().enrich_fund_price_positions(conn, raw, {}, NOW)
    fund = result["topic:new"]["funds"][0]
    assert fund["pricePosition"]["status"] != "DATA_PENDING"
    assert fund["pricePosition"]["asOf"] == "2026-10-27"
    assert fund["confirmedNav"]["sourceUri"] == "https://fund.eastmoney.com/987651.html"
    assert fund["referenceTracks"]
    assert fund["canBuyNow"] is False and fund["pricePosition"]["actionable"] is False
    assert raw == before
    assert len(conn.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["absent", "short", "stale", "future", "foreign"])
async def test_invalid_local_history_does_not_invent_price_position_or_remove_fund(
    boundary,
):
    row = history("987651")
    if boundary == "short":
        row["points"] = row["points"][-60:]
        row.pop("providerHistoryProof")
    elif boundary == "stale":
        row["retrievedAt"] = (NOW - timedelta(days=5)).isoformat()
    elif boundary == "future":
        row["retrievedAt"] = (NOW + timedelta(days=1)).isoformat()
    elif boundary == "foreign":
        row["currency"] = "USD"
    result = await module().enrich_fund_price_positions(
        LocalHistory([] if boundary == "absent" else [row]),
        {"topic:new": {"funds": [product()]}},
        {},
        NOW,
    )
    fund = result["topic:new"]["funds"][0]
    assert fund["fundCode"] == "987651"
    assert fund["pricePosition"]["status"] == "DATA_PENDING"
    assert fund["canBuyNow"] is False


def test_overheating_wins_over_an_entry_zone_without_valuation_claims():
    plan = {
        "historyQuality": "READY",
        "priceRefresh": {"state": "READY"},
        "asOf": "2026-10-27",
        "currentPrice": "2",
        "indicators": {"rsi14": 75},
        "tracks": [
            {
                "priceState": "READY",
                "inBuyZone": True,
                "entryZoneMin": "1.9",
                "entryZoneMax": "2.1",
                "takeProfitT1": "2.4",
            }
        ],
    }
    result = module().price_position(plan, NOW)
    assert result["status"] == "OVERHEATED"
    assert "追高" in result["label"]
    assert result["actionable"] is False


def test_technical_entry_zone_is_not_described_as_fundamental_undervaluation():
    plan = {
        "historyQuality": "READY",
        "priceRefresh": {"state": "READY"},
        "asOf": "2026-10-27",
        "currentPrice": "1",
        "indicators": {"rsi14": 35},
        "tracks": [
            {
                "priceState": "READY",
                "inBuyZone": True,
                "entryZoneMin": "0.95",
                "entryZoneMax": "1.05",
                "takeProfitT1": "1.2",
            }
        ],
    }
    result = module().price_position(plan, NOW)
    assert result["status"] == "ENTRY_ZONE"
    assert "低估" not in result["label"] and "可分批" not in result["label"]


@pytest.mark.asyncio
async def test_identical_fund_in_multiple_topics_has_one_price_read():
    result = await module().enrich_fund_price_positions(
        conn := LocalHistory([history("987651")]),
        {"one": {"funds": [product()]}, "two": {"indirectFunds": [product()]}},
        {},
        NOW,
    )
    assert len(conn.calls) == 1
    assert (
        result["one"]["funds"][0]["pricePosition"]
        == result["two"]["indirectFunds"][0]["pricePosition"]
    )


@pytest.mark.asyncio
async def test_mismatched_identity_cannot_borrow_another_topics_price_context():
    from uuid import uuid4

    wrong = {**product(), "subjectKind": "fund", "subjectId": str(uuid4())}
    result = await module().enrich_fund_price_positions(
        LocalHistory([history("987651")]),
        {"valid": {"funds": [product()]}, "wrong": {"funds": [wrong]}},
        {},
        NOW,
    )
    assert result["valid"]["funds"][0]["pricePosition"]["status"] != "DATA_PENDING"
    assert result["wrong"]["funds"][0]["pricePosition"]["status"] == "DATA_PENDING"
