"""Opt-in monitoring commands use the authenticated actor, never client actors."""

from contextlib import asynccontextmanager
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context


def setup(monkeypatch):
    assert util.find_spec("backend.investment.decision_api"), "monitor routes missing"
    api = import_module("backend.investment.decision_api")
    calls = []

    @asynccontextmanager
    async def transaction(pool, user, tenant):
        calls.append((user, tenant))
        yield "connection"

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = lambda: "pool"
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        "actor", "tenant"
    )
    return TestClient(app), api, calls


def test_enable_and_list_monitor_are_actor_scoped_and_camelcase(monkeypatch):
    client, api, calls = setup(monkeypatch)
    save = AsyncMock(return_value={"id": "monitor", "enabled": True})
    monkeypatch.setattr(api.repository, "set_monitor", save)
    listing = AsyncMock(
        return_value=[{"id": "monitor", "enabled": True, "fundMarket": "SSE"}]
    )
    monkeypatch.setattr(api.repository, "list_monitors", listing)
    response = client.post(
        "/api/v1/investment/condition-monitors",
        json={
            "directionId": "a-share:broad",
            "fundCode": "510300",
            "fundMarket": "SSE",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"id": "monitor", "enabled": True}
    assert save.await_args.args[1:3] == ("tenant", "actor")
    assert save.await_args.args[3]["fund_market"] == "SSE"
    assert (
        client.get("/api/v1/investment/condition-monitors").json()["data"][0][
            "fundMarket"
        ]
        == "SSE"
    )
    assert calls == [("actor", "tenant"), ("actor", "tenant")]


@pytest.mark.parametrize(
    "change",
    [
        {"tenantId": "other"},
        {"userId": "other"},
        {"enabled": "true"},
        {"instrumentId": "bad"},
    ],
)
def test_monitor_rejects_actor_injection_and_ambiguous_types(monkeypatch, change):
    client, api, calls = setup(monkeypatch)
    response = client.post(
        "/api/v1/investment/condition-monitors",
        json={"directionId": "a-share:broad", "enabled": True, **change},
    )
    assert response.status_code == 422
    assert calls == []


def test_acknowledge_and_notice_list_use_actor(monkeypatch):
    client, api, calls = setup(monkeypatch)
    identifier = uuid4()
    ack = AsyncMock(
        return_value={"id": str(identifier), "readAt": "2026-10-14T18:00:00+08:00"}
    )
    monkeypatch.setattr(api.repository, "ack_notice", ack)
    monkeypatch.setattr(api.repository, "list_notices", AsyncMock(return_value=[]))
    assert client.post(f"/api/v1/investment/condition-notices/{identifier}/ack").json()[
        "data"
    ]["id"] == str(identifier)
    assert ack.await_args.args[1:4] == ("tenant", "actor", identifier)
    assert client.get("/api/v1/investment/condition-notices").json()["data"] == []
    assert calls == [("actor", "tenant"), ("actor", "tenant")]
