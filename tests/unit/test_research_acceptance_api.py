"""Acceptance inspection is distinct from fresh model execution and feature gates."""

from contextlib import asynccontextmanager
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.errors import MoneyJSONResponse, register_exception_handlers

RUN, ACTOR, TENANT = uuid4(), uuid4(), uuid4()


def client(monkeypatch, *, role="OWNER", active=True):
    assert util.find_spec("backend.investment.research_acceptance_api"), (
        "acceptance API missing"
    )
    module = import_module("backend.investment.research_acceptance_api")
    conn = AsyncMock()
    conn.fetchval.return_value = active

    @asynccontextmanager
    async def transaction(*args):
        yield conn

    monkeypatch.setattr(module, "tenant_transaction", transaction)
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        str(ACTOR), str(TENANT), tenant_role=role
    )
    return TestClient(app), module


@pytest.mark.parametrize("role", ["VIEWER", "OWNER"])
def test_initial_read_is_safe_for_members_and_admin_details_are_explicit(
    monkeypatch, role
):
    api, module = client(monkeypatch, role=role)
    read = AsyncMock(return_value={"researchId": str(RUN), "decision": "PARTIAL"})
    monkeypatch.setattr(module.repository, "read_validation_summary", read)
    response = api.get(f"/api/v1/investment/daily-research/validation/{RUN}")
    assert response.status_code == 200
    assert read.await_args.kwargs["admin"] is (role == "OWNER")


@pytest.mark.parametrize(
    "role,active", [("VIEWER", True), ("EDITOR", True), ("OWNER", False)]
)
def test_raw_and_replay_require_current_active_administrator(monkeypatch, role, active):
    api, module = client(monkeypatch, role=role, active=active)
    read = AsyncMock()
    replay = AsyncMock()
    monkeypatch.setattr(module.repository, "read_artifacts_admin", read)
    monkeypatch.setattr(module.repository, "replay_validation", replay)
    assert (
        api.get(
            f"/api/v1/investment/daily-research/validation/{RUN}/artifacts"
        ).status_code
        == 403
    )
    assert (
        api.post(
            f"/api/v1/investment/daily-research/validation/{RUN}/replays",
            json={"commandKey": str(uuid4())},
        ).status_code
        == 403
    )
    read.assert_not_awaited()
    replay.assert_not_awaited()


def test_replay_is_synchronous_idempotent_command_with_authenticated_actor(monkeypatch):
    api, module = client(monkeypatch)
    replay = AsyncMock(
        return_value={"id": str(uuid4()), "researchId": str(RUN), "kind": "REPLAY"}
    )
    monkeypatch.setattr(module.repository, "replay_validation", replay)
    key = uuid4()
    response = api.post(
        f"/api/v1/investment/daily-research/validation/{RUN}/replays",
        json={"commandKey": str(key)},
    )
    assert response.status_code == 200
    assert replay.await_args.args[1:5] == (RUN, ACTOR, TENANT, key)
    assert (
        api.post(
            f"/api/v1/investment/daily-research/validation/{RUN}/replays",
            json={"commandKey": str(key), "model": "forbidden"},
        ).status_code
        == 400
    )
