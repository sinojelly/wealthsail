"""Public HTTP contract for the fund-screener cache endpoints."""

from contextlib import asynccontextmanager
from unittest.mock import ANY, AsyncMock, patch

from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context
from backend.main import create_app


USER_ID = "00000000-0000-0000-0000-000000000001"
TENANT_ID = "00000000-0000-0000-0000-000000000002"


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_request_id] = lambda: "req-fund-screener"
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        USER_ID, TENANT_ID, tenant_role="VIEWER"
    )
    return TestClient(app, raise_server_exceptions=False)


def _transaction(monkeypatch) -> None:
    from backend.funds import api

    @asynccontextmanager
    async def fake_transaction(*_args):
        yield object()

    monkeypatch.setattr(api, "tenant_transaction", fake_transaction)


def test_fund_screener_routes_are_mounted_under_standard_api_prefix() -> None:
    """Removing the router would make the fund-screener unreachable to the web app."""

    paths = create_app().openapi()["paths"]

    assert "get" in paths["/api/v1/funds/search"]
    assert "get" in paths["/api/v1/funds/{fund_code}/range"]
    assert "post" in paths["/api/v1/funds/{fund_code}/fetch"]


def test_search_returns_cached_matches_without_calling_a_provider(monkeypatch) -> None:
    """A cache miss must stay a fast read instead of turning search into provider I/O."""

    _transaction(monkeypatch)
    found = AsyncMock(
        return_value=[
            {
                "fundCode": "110022",
                "fundName": "易方达消费行业",
                "fundType": "OFF_EXCHANGE_FUND",
                "dataSource": "AKSHARE",
            }
        ]
    )
    with patch("backend.funds.api.service.search", new=found):
        response = _client().get("/api/v1/funds/search", params={"keyword": "1100"})

    assert response.status_code == 200
    assert response.json()["data"][0]["fundCode"] == "110022"
    found.assert_awaited_once_with(ANY, "1100", tenant_id=TENANT_ID)


def test_range_returns_not_collected_as_a_successful_read(monkeypatch) -> None:
    """An absent cache row is an expected state, not a server error or fake data."""

    _transaction(monkeypatch)
    lookup = AsyncMock(
        return_value={
            "fundCode": "110022",
            "status": "NOT_COLLECTED",
            "message": "未收录",
        }
    )
    with patch("backend.funds.api.service.get_range", new=lookup):
        response = _client().get("/api/v1/funds/110022/range")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "NOT_COLLECTED"


def test_fetch_accepts_one_durable_request_and_returns_accepted(monkeypatch) -> None:
    """The HTTP request must enqueue work, never wait for an external data source."""

    _transaction(monkeypatch)
    enqueue = AsyncMock(
        return_value={
            "fundCode": "110022",
            "status": "QUEUED",
            "message": "已提交抓取请求",
            "runId": "run-1",
            "created": True,
        }
    )
    with patch("backend.funds.api.service.enqueue_fetch", new=enqueue):
        response = _client().post("/api/v1/funds/110022/fetch")

    assert response.status_code == 202
    assert response.json()["data"]["message"] == "已提交抓取请求"
    enqueue.assert_awaited_once_with(ANY, tenant_id=TENANT_ID, fund_code="110022")


def test_search_miss_on_exact_code_enqueues_durable_fetch(monkeypatch) -> None:
    """A six-digit code absent from the cache must auto-queue one Worker fetch."""

    _transaction(monkeypatch)
    from backend.funds import repository as funds_repository

    empty_search = AsyncMock(return_value=[])
    enqueue = AsyncMock(return_value={"id": "run-1", "created": True})
    with (
        patch(
            "backend.funds.repository.search_nav_ranges", new=empty_search
        ),
        patch(
            "backend.funds.repository.enqueue_nav_range_fetch", new=enqueue
        ),
    ):
        response = _client().get("/api/v1/funds/search", params={"keyword": "163407"})

    assert response.status_code == 200
    assert response.json()["data"] == []
    enqueue.assert_awaited_once_with(
        ANY, tenant_id=TENANT_ID, fund_code="163407"
    )


def test_search_fuzzy_keyword_miss_does_not_enqueue(monkeypatch) -> None:
    """Only an exact six-digit code may trigger an enqueue; names never do."""

    _transaction(monkeypatch)
    from backend.funds import repository as funds_repository

    empty_search = AsyncMock(return_value=[])
    enqueue = AsyncMock()
    with (
        patch(
            "backend.funds.repository.search_nav_ranges", new=empty_search
        ),
        patch(
            "backend.funds.repository.enqueue_nav_range_fetch", new=enqueue
        ),
    ):
        response = _client().get(
            "/api/v1/funds/search", params={"keyword": "沪深300"}
        )

    assert response.status_code == 200
    enqueue.assert_not_awaited()


def test_search_hit_on_exact_code_does_not_enqueue(monkeypatch) -> None:
    """An already-cached code must not schedule redundant fetch work."""

    _transaction(monkeypatch)
    from backend.funds import repository as funds_repository

    cached = AsyncMock(
        return_value=[
            {
                "fund_code": "110022",
                "fund_name": "易方达消费行业",
                "fund_type": "OFF_EXCHANGE_FUND",
                "latest_nav": 1.0,
                "latest_nav_date": None,
                "highest_nav": 1.0,
                "highest_nav_date": None,
                "lowest_nav": 1.0,
                "lowest_nav_date": None,
                "drawdown_from_high": 0,
                "rise_from_low": 0,
                "current_percentile": None,
                "inception_date": None,
                "data_source": "AKSHARE",
                "updated_at": None,
            }
        ]
    )
    enqueue = AsyncMock()
    with (
        patch("backend.funds.repository.search_nav_ranges", new=cached),
        patch(
            "backend.funds.repository.enqueue_nav_range_fetch", new=enqueue
        ),
    ):
        response = _client().get("/api/v1/funds/search", params={"keyword": "110022"})

    assert response.status_code == 200
    enqueue.assert_not_awaited()
