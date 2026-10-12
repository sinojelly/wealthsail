from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.main import create_app


def client(monkeypatch):
    from backend.investment import api

    @asynccontextmanager
    async def transaction(*args):
        yield object()

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002", tenant_role="VIEWER",
    )
    return TestClient(app)


def test_research_routes_exist_without_changing_old_fund_routes():
    paths = create_app().openapi()["paths"]
    assert "/api/v1/investment/directions" in paths
    assert "/api/v1/investment/market-context" in paths
    assert "/api/v1/investment/search" in paths
    assert "/api/v1/funds/screener" in paths
    assert "/api/v1/funds/{fund_code}/fetch" in paths


def test_global_search_is_a_read_and_returns_location(monkeypatch):
    from backend.investment import service
    lookup = AsyncMock(return_value=[{"kind": "fund", "code": "510330", "indexId": "hs300", "directionId": "a-share:broad"}])
    monkeypatch.setattr(service, "search", lookup)
    response = client(monkeypatch).get("/api/v1/investment/search?keyword=510330")
    assert response.status_code == 200
    assert response.json()["data"][0]["indexId"] == "hs300"
    assert lookup.await_count == 1


@pytest.mark.parametrize("query", ["page=0", "pageSize=101", "sortBy=DROP%20TABLE", "navPositionMin=80&navPositionMax=20"])
def test_invalid_screener_inputs_return_project_validation_error(monkeypatch, query):
    assert client(monkeypatch).get(f"/api/v1/funds/screener?{query}").status_code == 400


def test_screener_does_not_lose_combined_filters(monkeypatch):
    from backend.investment import service
    screen = AsyncMock(return_value={"items": [], "total": 0, "page": 2, "pageSize": 20})
    monkeypatch.setattr(service, "screen", screen)
    response = client(monkeypatch).get("/api/v1/funds/screener?keyword=华夏&directionId=a-share:broad&indexId=hs300&page=2")
    assert response.status_code == 200
    query = screen.await_args.args[1]
    assert query.keyword == "华夏"
    assert query.direction_id == "a-share:broad"
    assert query.index_id == "hs300"
    assert query.page == 2


@pytest.mark.asyncio
async def test_repository_parameterizes_filters_and_sorts_nulls_last():
    from backend.investment.repository import screen
    from backend.investment.schemas import ScreenQuery
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchval.return_value = 21
    result = await screen(conn, ScreenQuery(keyword="x' OR true", page=2, navPositionMin=30))
    assert result[1] == 21
    sql, *params = conn.fetch.await_args.args
    assert "x' OR true" not in sql
    assert "%x' OR true%" in params
    assert "NULLS LAST" in sql
    assert "OFFSET" in sql
    assert params[-1] == 20


def test_macro_unknown_release_does_not_publish_recommendations():
    from backend.investment.service import macro_dto
    result = macro_dto({"field_code": "PMI", "value": 51, "as_of": "2026-09-01", "published_at": None,
                        "quality_status": "FRESH", "source_name": "fixture", "revision": 1})
    assert result["value"] == 51
    assert result["state"] == "UNVERIFIED"
    assert result["assessment"] is None
