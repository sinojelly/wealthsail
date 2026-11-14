"""Current references preserve tenant RLS while permitting a narrow public NAV append."""

from contextlib import asynccontextmanager
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.errors import MoneyJSONResponse, register_exception_handlers


def test_reference_uses_actor_scope_and_cannot_accept_commands(monkeypatch):
    module = import_module("backend.investment.personal_funds_api")
    ctx = TenantContext(str(uuid4()), str(uuid4()), tenant_role="VIEWER")
    conn = object()
    seen = []

    @asynccontextmanager
    async def transaction(pool, user_id, tenant_id):
        assert (user_id, tenant_id) == (ctx.user_id, ctx.tenant_id)
        seen.append("tenant")
        yield conn

    read = AsyncMock(
        return_value={"source": "LOCAL_DATABASE_ONLY", "readOnly": True, "funds": []}
    )
    monkeypatch.setattr(module, "tenant_transaction", transaction, raising=False)
    monkeypatch.setattr(module, "personal_funds_reference", read)
    codes = AsyncMock(return_value=[])
    prepared = {"asOf": module.now_shanghai(), "refresh": {}}
    repair = AsyncMock(return_value=prepared)
    monkeypatch.setattr(module, "personal_nav_refresh_codes", codes)
    monkeypatch.setattr(module, "ensure_nav_histories", repair)
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = object
    app.dependency_overrides[get_tenant_context] = lambda: ctx
    client = TestClient(app)
    for included in (True, False):
        response = client.get(
            f"/api/v1/investment/personal-funds-reference?includeResearchMentions={str(included).lower()}"
        )
        assert (
            response.status_code == 200 and response.json()["data"]["readOnly"] is True
        )
        assert read.await_args.args[:3] == (conn, ctx.tenant_id, ctx.user_id)
        assert read.await_args.kwargs == {
            "include_research_mentions": False,
            "allow_external_fetch": False,
        }
    assert seen == ["tenant", "tenant"]
    assert (
        client.post(
            "/api/v1/investment/personal-funds-reference", json={"enabled": True}
        ).status_code
        == 405
    )
    refreshed = client.post("/api/v1/investment/personal-funds-reference/refresh")
    assert refreshed.status_code == 200
    assert read.await_args.args[:3] == (conn, ctx.tenant_id, ctx.user_id)
    assert read.await_args.kwargs == {
        "include_research_mentions": False,
        "allow_external_fetch": False,
    }
    assert repair.await_args.args[0] is None
    assert repair.await_args.kwargs["allow_external_fetch"] is True
    assert callable(repair.await_args.kwargs["transaction_factory"])
    assert refreshed.json()["data"]["navSync"]["policy"] == "EXPLICIT_REFRESH"


def test_explicit_refresh_never_holds_an_outer_connection_during_provider_work(
    monkeypatch,
):
    module = import_module("backend.investment.personal_funds_api")
    ctx = TenantContext(str(uuid4()), str(uuid4()), tenant_role="VIEWER")
    current = module.now_shanghai()
    conn = object()
    borrowed, committed = 0, 0

    @asynccontextmanager
    async def transaction(pool, user_id, tenant_id):
        nonlocal borrowed, committed
        assert borrowed == 0, "no nested pool borrow is allowed"
        assert (user_id, tenant_id) == (ctx.user_id, ctx.tenant_id)
        borrowed += 1
        try:
            yield conn
        finally:
            borrowed -= 1
            committed += 1

    async def inventory(*_args):
        assert borrowed == 1
        return ["011035"]

    async def repair(connection, codes, now, **kwargs):
        assert connection is None and codes == ["011035"] and borrowed == 0
        async with kwargs["transaction_factory"]() as claim_conn:
            assert claim_conn is conn and borrowed == 1
        assert borrowed == 0 and committed == 2
        return {
            "asOf": current,
            "refresh": {
                "011035": {"state": "FAILED", "reason": "NAV_SOURCE_SYNC_FAILED"}
            },
        }

    async def local(*_args, **kwargs):
        assert borrowed == 1 and kwargs["allow_external_fetch"] is False
        return {"funds": [{"fundCode": "011035", "history": {"points": []}}]}

    monkeypatch.setattr(module, "tenant_transaction", transaction)
    monkeypatch.setattr(module, "personal_nav_refresh_codes", inventory)
    monkeypatch.setattr(module, "ensure_nav_histories", repair)
    monkeypatch.setattr(module, "personal_funds_reference", local)
    app = FastAPI(default_response_class=MoneyJSONResponse)
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = object
    app.dependency_overrides[get_tenant_context] = lambda: ctx
    response = TestClient(app).post(
        "/api/v1/investment/personal-funds-reference/refresh"
    )
    assert response.status_code == 200
    assert (
        response.json()["data"]["funds"][0]["history"]["refresh"]["reason"]
        == "NAV_SOURCE_SYNC_FAILED"
    )
    assert borrowed == 0 and committed == 3
