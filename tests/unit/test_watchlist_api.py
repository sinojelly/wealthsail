from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_request_id, get_tenant_context
from backend.main import create_app


USER_ID = "00000000-0000-0000-0000-000000000001"
TENANT_ID = "00000000-0000-0000-0000-000000000002"


def _client(role: str | None = "VIEWER") -> TestClient:
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_request_id] = lambda: "req-watchlist"
    if role is not None:
        app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
            USER_ID, TENANT_ID, tenant_role=role
        )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def transaction(monkeypatch):
    from backend.watchlist import api

    @asynccontextmanager
    async def fake_transaction(*_args):
        yield object()

    monkeypatch.setattr(api, "tenant_transaction", fake_transaction)


def test_watchlist_requires_authentication() -> None:
    response = _client(role=None).get("/api/v1/watchlist")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "EDITOR", "VIEWER"])
def test_every_tenant_role_can_manage_its_own_watchlist(role: str, transaction) -> None:
    added = AsyncMock(
        return_value={
            "symbol": "600036",
            "assetName": "招商银行",
            "assetType": "STOCK",
            "addedAt": "2026-09-01T00:00:00+00:00",
        }
    )
    with patch("backend.watchlist.api.service.add_watchlist_item", new=added):
        response = _client(role).post("/api/v1/watchlist", json={"symbol": "600036"})

    assert response.status_code == 200
    assert response.json()["data"]["assetName"] == "招商银行"
    assert added.await_args.args[1].user_id == USER_ID


def test_post_accepts_only_symbol_and_rejects_extra_fields(transaction) -> None:
    added = AsyncMock(return_value={"symbol": "600036"})
    with patch("backend.watchlist.api.service.add_watchlist_item", new=added):
        response = _client().post(
            "/api/v1/watchlist",
            json={"symbol": "600036", "assetName": "客户端伪造名称"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    added.assert_not_awaited()


def test_delete_decodes_symbol_path_and_uses_context_owned_user(transaction) -> None:
    removed = AsyncMock(
        return_value={
            "symbol": "600036",
            "assetName": "招商银行",
            "assetType": "STOCK",
            "addedAt": "2026-09-01T00:00:00+00:00",
        }
    )
    with patch("backend.watchlist.api.service.remove_watchlist_item", new=removed):
        response = _client().delete("/api/v1/watchlist/%20600036%20")

    assert response.status_code == 200
    assert removed.await_args.args[1].user_id == USER_ID
    assert removed.await_args.args[2] == " 600036 "


def test_routes_are_mounted_under_the_standard_api_prefix() -> None:
    paths = create_app().openapi()["paths"]
    assert set(paths["/api/v1/watchlist"]) >= {"get", "post"}
    assert "delete" in paths["/api/v1/watchlist/{symbol}"]
