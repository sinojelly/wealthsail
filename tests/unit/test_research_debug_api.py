"""The debug API queues an operator command without holding Worker authority."""

from contextlib import asynccontextmanager
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
)
from backend.errors import MoneyJSONResponse, register_exception_handlers

TENANT, USER = str(uuid4()), str(uuid4())


def engine():
    assert util.find_spec("backend.investment.research_debug_api"), (
        "research debug API missing"
    )
    return import_module("backend.investment.research_debug_api")


def client(
    monkeypatch, *, role="OWNER", env="test", enabled=True, active=True, daily=True
):
    module = engine()
    connection = AsyncMock()
    connection.fetchval.return_value = active

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield connection

    monkeypatch.setattr(module, "tenant_transaction", transaction)
    monkeypatch.setattr(
        module.repository, "latest_refresh", AsyncMock(return_value=None)
    )
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/api/v1")
    settings = Settings(
        _env_file=None,
        app_env=env,
        daily_research_debug_enabled=enabled,
        daily_research_enabled=daily,
    )
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=USER, tenant_id=TENANT, tenant_role=role
    )
    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_request_id] = lambda: "debug-test"
    return TestClient(app, raise_server_exceptions=False), module


@pytest.mark.parametrize(
    "options",
    [
        {"role": "VIEWER"},
        {"role": "EDITOR"},
        {"env": "production"},
        {"env": "staging"},
        {"enabled": False},
        {"active": False},
        {"daily": False},
    ],
)
def test_unavailable_capability_is_quiet_and_post_cannot_enqueue(monkeypatch, options):
    api, module = client(monkeypatch, **options)
    queue = AsyncMock()
    monkeypatch.setattr(module.repository, "enqueue_refresh", queue)
    response = api.get("/api/v1/investment/daily-research/refresh")
    assert response.status_code == 200
    assert response.json()["data"] == {"enabled": False, "request": None}
    response = api.post(
        "/api/v1/investment/daily-research/refresh", json={"commandKey": str(uuid4())}
    )
    assert response.status_code == 403
    assert not queue.called


def test_accepted_command_is_bound_to_authenticated_actor_and_returns_202(monkeypatch):
    api, module = client(monkeypatch)
    queue = AsyncMock(return_value={"id": "request-one", "status": "QUEUED"})
    monkeypatch.setattr(module.repository, "enqueue_refresh", queue)
    key = str(uuid4())
    response = api.post(
        "/api/v1/investment/daily-research/refresh", json={"commandKey": key}
    )
    assert response.status_code == 202
    assert response.json()["data"] == {"id": "request-one", "status": "QUEUED"}
    arguments = queue.call_args.args
    assert arguments[1:4] == (UUID(TENANT), UUID(USER), UUID(key))


@pytest.mark.parametrize(
    "payload",
    [
        {"commandKey": "bad"},
        {"commandKey": str(uuid4()), "tenantId": TENANT},
        {"commandKey": str(uuid4()), "prices": [1, 2]},
    ],
)
def test_client_cannot_supply_actor_prices_or_invalid_command_identity(
    monkeypatch, payload
):
    api, module = client(monkeypatch)
    queue = AsyncMock()
    monkeypatch.setattr(module.repository, "enqueue_refresh", queue)
    response = api.post("/api/v1/investment/daily-research/refresh", json=payload)
    assert response.status_code == 400
    assert not queue.called


def test_successful_capability_returns_only_own_latest_request(monkeypatch):
    api, module = client(monkeypatch)
    latest = AsyncMock(return_value={"id": "own", "status": "RUNNING"})
    monkeypatch.setattr(module.repository, "latest_refresh", latest)
    response = api.get("/api/v1/investment/daily-research/refresh")
    assert response.json()["data"] == {
        "enabled": True,
        "request": {"id": "own", "status": "RUNNING"},
    }
    assert latest.call_args.args[1:3] == (UUID(TENANT), UUID(USER))
