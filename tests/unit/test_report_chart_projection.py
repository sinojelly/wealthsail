"""Opt-in response copies retain each chart consumer without duplicate series."""

from contextlib import asynccontextmanager
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context


def plan(as_of="2026-11-10"):
    series = {
        "fundCode": "011035", "asOf": as_of, "state": "PARTIAL",
        "retrievedAt": as_of + "T10:00:00+08:00",
        "sourceUri": "https://fund.eastmoney.com/011035.html",
        "unit": "CNY/份", "priceBasis": "UNIT_NAV",
        "points": [{"date": as_of, "value": "1.2345"}],
    }
    value = {
        "fundCode": "011035", "subjectKind": "nav", "subjectId": "011035",
        "asOf": as_of, "currentPrice": "1.2345", "series": series,
        "tracks": [{
            "track": "LEFT_VALUE", "entryZoneMin": "1.1000",
            "entryZoneMax": "1.2000", "takeProfitT1": "1.3000",
            "takeProfitT2": "1.4000", "stopLossPrice": "1.0000",
        }],
    }
    value["actionableZones"] = {
        **deepcopy(value), "buyZone": {"min": "1.1000", "max": "1.2000"},
        "targets": ["1.3000", "1.4000"],
    }
    return value


def context():
    return {"privateDecision": {"state": "UNCHANGED"}, "dailyResearch": {
        "runId": "original-run", "report": {
            "summary": "全部洞察原文保留", "customField": {"original": True},
            "actionablePlans": [plan()], "actionableZones": [plan()],
            "themeActionableZones": [plan()], "discoveryActionableZones": [plan()],
            "valueDirections": [{
                "key": "hardware", "title": "AI硬件", "auditStatus": "UNAUDITED_SCOPE",
                "plans": [plan(), plan("2026-11-09")],
                "fundPlans": [plan()], "quantitativeEvidence": [plan()],
            }],
            "researchTopics": [{"key": "memory", "plans": [plan()]}],
            "topicCatalog": [{"key": "memory", "fundPlans": [plan()]}],
            "mentionedFunds": [plan()], "quantitativeScan": {"items": [plan()]},
        },
    }}


def consumers(value):
    report = value["dailyResearch"]["report"]
    for field in ("actionablePlans", "actionableZones", "themeActionableZones", "discoveryActionableZones"):
        yield from ((False, row) for row in report[field])
    for direction in report["valueDirections"]:
        for field in ("plans", "fundPlans", "quantitativeEvidence"):
            yield from ((False, row) for row in direction[field])
    for field in ("researchTopics", "topicCatalog"):
        for topic in report[field]:
            yield from ((False, row) for row in topic.get("plans", topic.get("fundPlans", [])))
    yield from ((True, row) for row in report["mentionedFunds"])
    yield from ((False, row) for row in report["quantitativeScan"]["items"])


def test_copy_on_write_keeps_every_consumer_chart_price_and_original_object():
    from backend.investment.report_chart_projection import compact_context_charts

    original = context()
    before = deepcopy(original)
    result = compact_context_charts(original)
    assert original == before
    assert result["privateDecision"] is original["privateDecision"]
    old_report, report = original["dailyResearch"]["report"], result["dailyResearch"]["report"]
    assert report["customField"] is old_report["customField"]
    assert report.keys() == old_report.keys()
    assert len(list(consumers(result))) == len(list(consumers(original)))
    for (mention, old), (_, current) in zip(consumers(original), consumers(result)):
        expected = deepcopy(old)
        if mention:
            expected.pop("series")
            assert current["actionableZones"] is old["actionableZones"]
            assert current["actionableZones"]["series"] == old["series"]
        else:
            expected["actionableZones"].pop("series")
            assert current["series"] is old["series"]
        assert current == expected
        assert current["tracks"] is old["tracks"]
    assert len(report["valueDirections"][0]["plans"]) == 2
    dates = [row["series"]["asOf"] for row in report["valueDirections"][0]["plans"]]
    assert dates == ["2026-11-10", "2026-11-09"]
    assert len(json.dumps(result).encode()) < len(json.dumps(original).encode())


@pytest.mark.parametrize("field", ["asOf", "retrievedAt", "points", "sourceUri"])
@pytest.mark.parametrize("mention", [False, True])
def test_distinct_nested_chart_is_preserved_even_for_the_same_fund(field, mention):
    from backend.investment.report_chart_projection import compact_context_charts

    product = plan()
    product["actionableZones"]["series"][field] = (
        [{"date": "2026-11-09", "value": "1.2000"}] if field == "points" else "distinct"
    )
    report = {"mentionedFunds" if mention else "actionablePlans": [product]}
    original = {"dailyResearch": {"report": report}}
    before = deepcopy(original)
    assert compact_context_charts(original) is original
    assert original == before


@pytest.mark.parametrize("query,compact", [("", False), ("?compactCharts=false", False), ("?compactCharts=true", True)])
def test_compact_query_is_opt_in_and_never_changes_service_or_stored_data(monkeypatch, query, compact):
    from backend.investment import radar_api as api

    original = context()
    before = deepcopy(original)
    read = AsyncMock(return_value=original)
    monkeypatch.setattr(api.opportunity_service, "get_context", read)

    @asynccontextmanager
    async def transaction(pool, user, tenant):
        assert (user, tenant) == ("actor", "tenant")
        yield "connection"

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = lambda: "pool"
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext("actor", "tenant")
    response = TestClient(app).get("/api/v1/investment/opportunity-context" + query)

    assert response.status_code == 200
    delivered = response.json()["data"]
    if compact:
        assert "series" not in delivered["dailyResearch"]["report"]["mentionedFunds"][0]
        assert "series" not in delivered["dailyResearch"]["report"]["actionablePlans"][0]["actionableZones"]
    else:
        assert delivered == before
    assert original == before
    assert "compact_charts" not in read.await_args.kwargs


def restore_shared_charts(value):
    """Test the public wire contract independently of backend traversal."""
    items = value["chartSeries"]["items"]

    def restore(node):
        if isinstance(node, dict):
            if set(node) == {"seriesRef"}:
                return deepcopy(items[node["seriesRef"]])
            return {key: restore(child) for key, child in node.items()}
        if isinstance(node, list):
            return [restore(child) for child in node]
        return node

    return {key: restore(child) for key, child in value.items() if key != "chartSeries"}


def test_shared_curves_round_trip_every_field_without_mutation_or_copying_unrelated_values():
    from backend.investment.report_chart_projection import share_context_charts

    original = context()
    before = deepcopy(original)
    original["series"] = {"unrelatedMarketSeries": [1, 2]}
    before["series"] = {"unrelatedMarketSeries": [1, 2]}
    result = share_context_charts(original)
    assert original == before
    assert restore_shared_charts(result) == before
    assert result["chartSeries"]["version"] == "shared-v1"
    assert len(result["chartSeries"]["items"]) == 2
    assert result["privateDecision"] is original["privateDecision"]
    assert result["series"] is original["series"]
    assert result["dailyResearch"]["report"]["customField"] is original["dailyResearch"]["report"]["customField"]
    for (_, old), (_, current) in zip(consumers(original), consumers(result)):
        assert set(current["series"]) == {"seriesRef"}
        assert current["tracks"] is old["tracks"]
        assert result["chartSeries"]["items"][current["series"]["seriesRef"]] == old["series"]
    assert any(series is original["dailyResearch"]["report"]["actionablePlans"][0]["series"]
               for series in result["chartSeries"]["items"].values())


@pytest.mark.parametrize("field,value", [
    ("fundCode", "007466"), ("asOf", "2026-11-09"),
    ("retrievedAt", "2026-11-10T11:00:00+08:00"),
    ("sourceUri", "https://different.example/nav"),
    ("points", [{"date": "2026-11-10", "value": "1.2346"}]),
    ("extension", {"complete": False}),
])
def test_shared_curves_keep_distinct_full_content_including_same_fund_clocks(field, value):
    from backend.investment.report_chart_projection import share_context_charts

    one, two = plan(), plan()
    two["series"][field] = value
    original = {"dailyResearch": {"report": {"valueDirections": [{"plans": [one, two]}]}}}
    result = share_context_charts(original)
    assert len(result["chartSeries"]["items"]) == 2
    assert restore_shared_charts(result) == original
    assert (result["dailyResearch"]["report"]["valueDirections"][0]["plans"][0]["series"]
            != result["dailyResearch"]["report"]["valueDirections"][0]["plans"][1]["series"])


def test_shared_curves_deduplicate_equal_objects_with_different_key_order_and_keep_empty_curves():
    from backend.investment.report_chart_projection import share_context_charts

    first = plan()["series"]
    second = dict(reversed(list(first.items())))
    original = {"dailyResearch": {"report": {"unknownNewSection": [
        {"series": first}, {"series": second},
        {"series": {"points": [], "state": "UNAVAILABLE"}},
        {"series": {"ordinaryMetadata": True}},
    ]}}}
    result = share_context_charts(original)
    assert len(result["chartSeries"]["items"]) == 2
    assert restore_shared_charts(result) == original
    rows = result["dailyResearch"]["report"]["unknownNewSection"]
    assert rows[0]["series"] == rows[1]["series"]
    assert rows[3] is original["dailyResearch"]["report"]["unknownNewSection"][3]


def test_shared_curve_bytes_drop_without_truncating_any_curves_or_funds():
    from backend.investment.report_chart_projection import share_context_charts

    report = context()
    for _, item in consumers(report):
        for owner in (item, item["actionableZones"]):
            owner["series"]["points"] = [
                {"date": f"point-{index}", "value": f"{1 + index / 10000:.4f}"}
                for index in range(550)
            ]
    projected = share_context_charts(report)
    assert len(json.dumps(projected).encode()) < len(json.dumps(report).encode()) * 0.2
    assert restore_shared_charts(projected) == report


def test_sharing_never_conflates_decimal_metadata_with_json_string_metadata():
    from decimal import Decimal
    from fastapi.encoders import jsonable_encoder
    from backend.investment.report_chart_projection import share_context_charts

    decimal_curve = {"points": [], "extension": Decimal("1.25")}
    text_curve = {"points": [], "extension": "1.25"}
    original = {"dailyResearch": {"report": {"unknownSection": [
        {"series": decimal_curve}, {"series": text_curve},
    ]}}}
    shared = share_context_charts(original)
    assert restore_shared_charts(jsonable_encoder(shared)) == jsonable_encoder(original)
    assert shared["dailyResearch"]["report"]["unknownSection"][0]["series"] is decimal_curve


def test_shared_curve_table_has_no_implicit_fund_or_curve_limit():
    from backend.investment.report_chart_projection import share_context_charts

    original = {"dailyResearch": {"report": {"mentionedFunds": [
        {"fundCode": f"{index:06d}", "series": {
            "fundCode": f"{index:06d}", "points": [{"date": "2026-11-10", "value": "1.2345"}],
        }} for index in range(601)
    ]}}}
    result = share_context_charts(original)
    assert len(result["chartSeries"]["items"]) == 601
    assert restore_shared_charts(result) == original


@pytest.mark.parametrize("original", [None, {}, {"dailyResearch": None}, {"dailyResearch": {"report": None}},
                                      {"dailyResearch": {"report": {"summary": "完整原文"}}}])
def test_shared_format_without_curves_keeps_the_existing_response_object(original):
    from backend.investment.report_chart_projection import share_context_charts

    assert share_context_charts(original) is original


@pytest.mark.parametrize("query,shared,compact", [
    ("", False, False), ("?chartFormat=inline", False, False),
    ("?chartFormat=shared-v1", True, False),
    ("?chartFormat=shared-v1&compactCharts=true", True, True),
])
def test_chart_wire_format_is_opt_in_and_composes_with_existing_compaction(monkeypatch, query, shared, compact):
    from backend.investment import radar_api as api
    from backend.investment.report_chart_projection import compact_context_charts

    original = context()
    read = AsyncMock(return_value=original)
    monkeypatch.setattr(api.opportunity_service, "get_context", read)

    @asynccontextmanager
    async def transaction(pool, user, tenant):
        yield "connection"

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_pool] = lambda: "pool"
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext("actor", "tenant")
    response = TestClient(app).get("/investment/opportunity-context" + query)
    assert response.status_code == 200
    delivered = response.json()["data"]
    expected = compact_context_charts(original) if compact else original
    if shared:
        assert restore_shared_charts(delivered) == expected
    else:
        assert delivered == expected and "chartSeries" not in delivered
    assert "chart_format" not in read.await_args.kwargs
