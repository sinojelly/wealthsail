"""Automatic public candidates are independent of any user's selected theme."""

from copy import deepcopy
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_phase2_research import NOW, anchor


def module():
    assert util.find_spec("backend.investment.discovery"), "automatic universe missing"
    return import_module("backend.investment.discovery")


@pytest.mark.asyncio
async def test_shared_catalog_outside_seed_themes_enters_universe_without_actor_or_prices():
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": "005112",
            "fund_name": "测试医药基金",
            "market": "FUND_CN",
            "instrument_id": None,
            "overseas_calendar_required": False,
            "private": "exclude",
        }
    ]
    actual = await module().read_discovery_assets(conn, NOW, [])
    assert actual[0]["subjectId"] == "005112"
    assert actual[0]["subjectKind"] == "nav"
    assert "private" not in actual[0]
    assert "user_id" not in conn.fetch.await_args.args[0]


@pytest.mark.asyncio
async def test_ambiguous_catalog_identity_is_not_rescued_by_seed_theme():
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": "005112",
            "fund_name": "测试医药基金",
            "market": "FUND_CN",
            "instrument_id": "first",
            "mapping_count": 2,
        }
    ]
    themes = [
        {
            "assetCode": "005112",
            "market": "FUND_CN",
            "assetKind": "ACTIVE_FUND",
            "assetName": "测试基金",
        }
    ]
    assert await module().read_discovery_assets(conn, NOW, themes) == []


def test_real_index_and_non_seed_fund_anchors_are_kept_but_unknown_or_wrong_market_is_not():
    nav = anchor()
    nav.pop("themeCode")
    nav.update(subjectId="005112")
    index = deepcopy(nav)
    index.update(
        subjectId="hs300", subjectKind="index", unit="points", priceBasis="INDEX_POINTS"
    )
    unknown = deepcopy(nav)
    unknown["subjectId"] = "999999"
    universe = [
        {
            "subjectKind": "nav",
            "subjectId": "005112",
            "fundCode": "005112",
            "market": "FUND_CN",
            "label": "医药测试",
        }
    ]
    public = module().discovery_packet(universe, [nav, index, unknown], {}, NOW)
    assert {x["subjectId"] for x in public["discoveryActionableZones"]} == {
        "005112",
        "hs300",
    }
    assert len(public["discoveryUniverse"]) == 1
    universe[0]["market"] = "SSE"
    assert [
        x["subjectId"]
        for x in module().discovery_packet(universe, [nav, index], {}, NOW)[
            "discoveryActionableZones"
        ]
    ] == ["hs300"]


def test_previous_direction_keys_and_prices_are_preserved_without_private_text_or_future_data():
    nav = anchor()
    nav.pop("themeCode")
    nav.update(subjectId="005112", selectedTrack="LEFT_VALUE")
    universe = [
        {
            "subjectKind": "nav",
            "subjectId": "005112",
            "fundCode": "005112",
            "market": "FUND_CN",
            "label": "医药测试",
        }
    ]
    previous = {
        "report": {
            "valueDirections": [
                {
                    "key": "healthcare",
                    "title": "医药",
                    "thesis": "公开已存研究",
                    "signal": "LEFT_VALUE",
                    "plans": [nav],
                    "private": "omit",
                }
            ]
        }
    }
    public = module().discovery_packet(universe, [nav], previous, NOW)
    old = public["previousValueDirections"][0]
    assert old["key"] == "healthcare"
    assert old["plans"][0]["asOf"] == "2026-10-16"
    assert "private" not in old
    previous["report"]["valueDirections"][0]["plans"][0]["asOf"] = "2099-01-01"
    assert (
        module().discovery_packet(universe, [nav], previous, NOW)[
            "previousValueDirections"
        ][0]["plans"]
        == []
    )


@pytest.mark.asyncio
async def test_recomputed_nav_anchor_overrides_insufficient_legacy_theme_anchor(
    monkeypatch,
):
    from backend.investment import theme_graph
    from test_theme_graph import nav_history

    history = nav_history()
    history["points"][-1]["close"] = "1.25"
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": "008281",
            "fund_name": "实际芯片基金",
            "market": "FUND_CN",
            "instrument_id": None,
        }
    ]
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=history["calendarProof"])
    )
    old = anchor()
    old["currentPrice"] = "9.9999"
    old["tracks"][0].update(state="INSUFFICIENT", entryZoneMin=None, entryZoneMax=None)
    actual = await module().build_discovery_packet(
        conn, {"themeActionableZones": [old]}, {"fundHistories": [history]}, [], {}, NOW
    )
    plan = actual["discoveryActionableZones"][0]
    assert plan["currentPrice"] == "1.2500"
    assert plan["historyQuality"] == "READY"
    assert any(row["entryZoneMin"] is not None for row in plan["tracks"])


@pytest.mark.parametrize("field", ["market", "fundMarket"])
def test_explicit_coordinate_market_conflict_is_rejected_instead_of_relabelled(field):
    nav = anchor()
    nav.pop("themeCode")
    nav.update(subjectId="005112", **{field: "SSE"})
    universe = [
        {
            "subjectKind": "nav",
            "subjectId": "005112",
            "fundCode": "005112",
            "market": "FUND_CN",
            "label": "真实产品",
        }
    ]
    actual = module().discovery_packet(universe, [nav], {}, NOW)
    assert actual["discoveryActionableZones"] == []


def test_discovery_universe_only_emits_allowed_fields_and_unambiguous_otc_identities():
    first, second = str(uuid4()), str(uuid4())
    universe = [
        {
            "subjectKind": "nav",
            "subjectId": "005112",
            "fundCode": "005112",
            "market": "FUND_CN",
            "label": "真实产品",
            "privateNote": "OMIT_PRIVATE",
            "user_id": "PRIVATE_ACTOR",
            "metadata": {"holdings": "PRIVATE"},
        },
        {
            "subjectKind": "nav",
            "subjectId": "000001",
            "fundCode": "000001",
            "market": "SSE",
            "label": "同码股票",
        },
        {
            "subjectKind": "fund",
            "subjectId": first,
            "fundCode": "110020",
            "market": "FUND_CN",
            "label": "歧义产品一",
        },
        {
            "subjectKind": "fund",
            "subjectId": second,
            "fundCode": "110020",
            "market": "FUND_CN",
            "label": "歧义产品二",
        },
        {
            "subjectKind": "nav",
            "subjectId": "000051",
            "fundCode": "000051",
            "market": "FUND_CN",
            "label": "显式歧义",
            "mapping_count": 2,
        },
    ]
    public = module().discovery_packet(universe, [], {}, NOW)
    assert public["discoveryUniverse"] == [
        {
            "subjectKind": "nav",
            "subjectId": "005112",
            "fundCode": "005112",
            "market": "FUND_CN",
            "label": "真实产品",
            "overseasCalendarRequired": False,
        }
    ]


@pytest.mark.asyncio
async def test_registered_fund_uses_stored_real_nav_analysis_when_public_history_is_absent(
    monkeypatch,
):
    from backend.investment import service

    instrument = str(uuid4())
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": "005112",
            "fund_name": "实际医药基金",
            "market": "FUND_CN",
            "instrument_id": instrument,
        }
    ]
    stored = anchor()
    stored.pop("themeCode")
    stored.update(
        subjectKind="fund",
        subjectId=instrument,
        fundCode="005112",
        historyQuality="READY",
    )
    monkeypatch.setattr(
        service,
        "analysis",
        AsyncMock(
            return_value={
                "dataQuality": "READY",
                "seriesKind": "UNIT_NAV",
                "actionableZones": stored,
            }
        ),
    )
    actual = await module().build_discovery_packet(conn, {}, {}, [], {}, NOW)
    assert len(actual["discoveryActionableZones"]) == 1
    plan = actual["discoveryActionableZones"][0]
    assert plan["subjectId"] == instrument and plan["priceBasis"] == "UNIT_NAV"
    assert (
        plan["currentPrice"] == "1.2500"
        and plan["sourceUri"] == "https://example.org/nav"
    )


def test_stored_analysis_plan_retains_database_availability_for_packet_visibility():
    from datetime import timedelta
    from backend.investment.service import actionable_plan_for_analysis

    later = NOW + timedelta(minutes=1)
    result = {
        "state": "READY",
        "dataQuality": "READY",
        "seriesKind": "UNIT_NAV",
        "asOf": "2026-10-16",
        "source": "https://example.org/nav",
        "retrievedAt": NOW.isoformat(),
        "availableAt": later.isoformat(),
        "points": [{"date": "2026-10-16", "close": "1.25"}],
    }
    plan = actionable_plan_for_analysis(result, "fund", str(uuid4()), now=NOW)
    assert plan["availableAt"] == later.isoformat()
    universe = [
        {
            "subjectKind": "fund",
            "subjectId": plan["subjectId"],
            "fundCode": "005112",
            "market": "FUND_CN",
            "label": "实际基金",
        }
    ]
    assert (
        module().discovery_packet(universe, [plan], {}, NOW)["discoveryActionableZones"]
        == []
    )


@pytest.mark.parametrize(
    "basis,quality", [("PROVIDER_RETURN_INDEX", "READY"), ("UNIT_NAV", "STALE")]
)
@pytest.mark.asyncio
async def test_stored_fallback_does_not_relabel_return_index_or_restore_stale_nav(
    monkeypatch, basis, quality
):
    from backend.investment import service

    instrument = str(uuid4())
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": "005112",
            "fund_name": "实际医药基金",
            "market": "FUND_CN",
            "instrument_id": instrument,
        }
    ]
    stored = anchor()
    stored.update(
        subjectKind="fund",
        subjectId=instrument,
        fundCode="005112",
        priceBasis=basis,
        historyQuality=quality,
    )
    monkeypatch.setattr(
        service,
        "analysis",
        AsyncMock(
            return_value={
                "dataQuality": quality,
                "seriesKind": basis,
                "actionableZones": stored,
            }
        ),
    )
    assert (await module().build_discovery_packet(conn, {}, {}, [], {}, NOW))[
        "discoveryActionableZones"
    ] == []


def test_previous_prices_are_preserved_when_current_anchor_is_recomputed():
    old = anchor()
    old.update(
        subjectId="005112",
        selectedTrack="LEFT_VALUE",
        asOf="2026-08-03",
        currentPrice="0.8500",
    )
    current = deepcopy(old)
    current.update(asOf="2026-10-16", currentPrice="1.2500")
    universe = [
        {
            "subjectKind": "nav",
            "subjectId": "005112",
            "fundCode": "005112",
            "market": "FUND_CN",
            "label": "实际产品",
        }
    ]
    previous = {
        "report": {
            "valueDirections": [{"key": "healthcare", "title": "医药", "plans": [old]}]
        }
    }
    public = module().discovery_packet(universe, [old, current], previous, NOW)
    assert public["discoveryActionableZones"][0]["currentPrice"] == "1.2500"
    assert public["previousValueDirections"][0]["plans"][0]["currentPrice"] == "0.8500"
    assert public["previousValueDirections"][0]["plans"][0]["asOf"] == "2026-08-03"


def test_authoritative_unavailable_recomputation_removes_an_old_buy_zone():
    old = anchor()
    old.update(subjectId="005112")
    current = deepcopy(old)
    current["tracks"][0].update(
        state="INSUFFICIENT",
        entryZoneMin=None,
        entryZoneMax=None,
        stopLossPrice=None,
        takeProfitT1=None,
        takeProfitT2=None,
    )
    universe = [
        {
            "subjectKind": "nav",
            "subjectId": "005112",
            "fundCode": "005112",
            "market": "FUND_CN",
            "label": "实际产品",
        }
    ]
    plan = module().discovery_packet(universe, [old, current], {}, NOW)[
        "discoveryActionableZones"
    ][0]
    assert plan["tracks"][0]["state"] == "INSUFFICIENT"
    assert plan["tracks"][0]["entryZoneMin"] is None
