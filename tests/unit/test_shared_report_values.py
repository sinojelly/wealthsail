"""Exact report transport deduplication never discards a research field."""

from copy import deepcopy
import json

import pytest


def restore(wire):
    items = wire.get("reportValues", {}).get("items", {})

    def walk(node):
        if isinstance(node, dict):
            if set(node) == {"reportValueRef"}:
                return walk(items[node["reportValueRef"]])
            return {key: walk(value) for key, value in node.items()}
        return [walk(value) for value in node] if isinstance(node, list) else node

    result = {key: value for key, value in wire.items() if key != "reportValues"}
    daily = result.get("dailyResearch")
    if isinstance(daily, dict) and "report" in daily:
        result["dailyResearch"] = {**daily, "report": walk(daily["report"])}
    return result


def context():
    plan = {
        "fundCode": "011035",
        "asOf": "2026-11-12",
        "series": {
            "retrievedAt": "2026-11-13T07:00:00+08:00",
            "points": [
                {"date": f"2025-01-{i:02}", "value": str(i)} for i in range(1, 29)
            ],
        },
        "evidence": [{"summary": "保留全部产业信息" * 100}],
        "tracks": [{"entryZoneMin": "1.1", "entryZoneMax": "1.2"}],
    }
    return {
        "privateDecision": {"state": "KEPT"},
        "dailyResearch": {
            "report": {
                "valueDirections": [{"title": "AI硬件", "plans": [plan]}],
                "researchTopics": [{"title": "存储", "plans": [deepcopy(plan)]}],
                "topicCatalog": [{"title": "存储", "plans": [deepcopy(plan)]}],
                "mentionedFunds": [{"actionableZones": deepcopy(plan)}],
                "unknownExtension": {"metadata": [1, True, None]},
            }
        },
    }


def test_v2_roundtrip_preserves_all_fields_and_shares_equal_large_subtrees():
    from backend.investment.report_chart_projection import share_context_values

    original = context()
    before = deepcopy(original)
    wire = share_context_values(original)
    assert restore(wire) == original == before
    assert wire["privateDecision"] is original["privateDecision"]
    assert len(json.dumps(wire)) < len(json.dumps(original)) * 0.65
    assert wire["reportValues"]["version"] == "shared-v2"
    assert all(len(key) == 64 for key in wire["reportValues"]["items"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("asOf", "2026-11-11"),
        ("priceBasis", "ESTIMATE"),
        ("unit", "USD"),
        ("currentPrice", "0.123"),
    ],
)
def test_different_fund_coordinates_are_never_substituted(field, value):
    from backend.investment.report_chart_projection import share_context_values

    original = context()
    original["dailyResearch"]["report"]["researchTopics"][0]["plans"][0][field] = value
    assert restore(share_context_values(original)) == original


def test_reserved_reference_or_non_json_value_preserves_original_encoder_path():
    from backend.investment.report_chart_projection import share_context_values
    from decimal import Decimal

    for extra in [
        {"reportValueRef": "a" * 64},
        {"price": Decimal("1.2")},
        {"legacy": (1, 2)},
    ]:
        original = context()
        original["dailyResearch"]["report"]["unknownExtension"] = extra
        assert share_context_values(original) is original


def test_no_report_or_no_large_duplicate_keeps_legacy_shape():
    from backend.investment.report_chart_projection import share_context_values

    for original in [
        None,
        {"state": "LOADING"},
        {"dailyResearch": {"report": {"summary": "原文"}}},
    ]:
        assert share_context_values(original) is original


def test_existing_top_level_transport_named_extension_is_never_overwritten():
    from backend.investment.report_chart_projection import share_context_values

    original = context()
    extension = {"version": "business-extension", "notes": ["existing caller data"]}
    original["reportValues"] = extension
    assert share_context_values(original) is original
    assert original["reportValues"] is extension


def test_reused_small_wrappers_count_their_large_child_references():
    from backend.investment.report_chart_projection import share_context_values
    from hashlib import sha256

    points = [
        {"date": f"sample-{number}", "value": sha256(str(number).encode()).hexdigest()}
        for number in range(500)
    ]
    # The curve and plan become small projected wrappers once their large
    # points child is replaced. Reusing their object identity still represents
    # twenty wire occurrences of that child, not a single occurrence.
    curve = {"asOf": "2026-11-12", "currency": "CNY", "points": points}
    plan = {"fundCode": "011035", "series": curve}
    original = {"dailyResearch": {"report": {"plans": [plan] * 20}}}
    wire = share_context_values(original)
    assert restore(wire) == original
    assert len(json.dumps(wire)) < len(json.dumps(original)) * 0.15
    assert any(value == points for value in wire["reportValues"]["items"].values())


@pytest.mark.parametrize("extra", [float("nan"), float("inf"), {1: "non-string key"}])
def test_non_json_leaf_anywhere_keeps_complete_original_encoder_path(extra):
    from backend.investment.report_chart_projection import share_context_values

    original = context()
    original["dailyResearch"]["report"]["legacy"] = extra
    assert share_context_values(original) is original


def test_input_cycle_falls_back_without_mutating_or_recursing_forever():
    from backend.investment.report_chart_projection import share_context_values

    original = context()
    report = original["dailyResearch"]["report"]
    report["cycle"] = report
    assert share_context_values(original) is original
    assert report["cycle"] is report


def test_business_reference_named_metadata_outside_report_is_never_interpreted():
    from backend.investment.report_chart_projection import share_context_values

    original = context()
    original["dailyResearch"]["metadata"] = {"reportValueRef": "a" * 64}
    wire = share_context_values(original)
    assert restore(wire) == original
    assert wire["dailyResearch"]["metadata"] is original["dailyResearch"]["metadata"]


def test_http_v2_composes_exact_values_and_curves_without_changing_inline_contract(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.common.tenancy import TenantContext
    from backend.dependencies import get_pool, get_tenant_context
    from backend.investment import radar_api

    original = context()

    @asynccontextmanager
    async def transaction(*_args):
        yield object()

    monkeypatch.setattr(radar_api, "tenant_transaction", transaction)
    monkeypatch.setattr(
        radar_api.opportunity_service, "get_context", AsyncMock(return_value=original)
    )
    app = FastAPI()
    app.include_router(radar_api.router)
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        tenant_role="VIEWER",
    )
    client = TestClient(app)
    assert client.get("/investment/opportunity-context").json()["data"] == original
    wire = client.get("/investment/opportunity-context?chartFormat=shared-v2").json()[
        "data"
    ]
    assert wire["chartSeries"]["version"] == "shared-v1"
    assert wire["reportValues"]["version"] == "shared-v2"
    restored = restore(wire)
    chart_items = restored.pop("chartSeries")["items"]

    def charts(node):
        if isinstance(node, list):
            return [charts(row) for row in node]
        if not isinstance(node, dict):
            return node
        return (
            charts(chart_items[node["seriesRef"]])
            if set(node) == {"seriesRef"}
            else {key: charts(value) for key, value in node.items()}
        )

    assert charts(restored) == original
    assert (
        client.get("/investment/opportunity-context?chartFormat=unknown").status_code
        == 422
    )
