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
        assert read.await_args.kwargs == {"include_research_mentions": included}
    assert seen == ["tenant", "tenant"]
    assert (
        client.post(
            "/api/v1/investment/personal-funds-reference", json={"enabled": True}
        ).status_code
        == 405
    )
