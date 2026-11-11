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
