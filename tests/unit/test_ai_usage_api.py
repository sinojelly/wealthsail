from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.common.tenancy import TenantContext
from backend.errors import register_exception_handlers
from backend.operations import api
from backend.dependencies import get_pool, get_tenant_context, get_settings_dep


@pytest.fixture
def endpoint(monkeypatch):
    tenant, user = str(uuid4()), str(uuid4())
    state = {"platform": False, "role": "OWNER", "seen": []}
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    register_exception_handlers(app)
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=user, tenant_id=tenant, tenant_role=state["role"]
    )
    app.dependency_overrides[get_settings_dep] = lambda: SimpleNamespace()

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield self

        async def fetchval(self, _sql, identity):
            assert identity == user
            return state["platform"]

    @asynccontextmanager
    async def transaction(_pool, actor, scope):
        assert (actor, scope) == (user, tenant)
        yield Conn()

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    from backend.ai_usage import repository
    from backend.integrations import codex_client

    async def tasks(_conn, scope, **kwargs):
        assert scope == tenant
        state["seen"].append(kwargs)
        return {
            "tasks": [],
            "total": 0,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        }

    async def account(_settings):
        state["accountCalled"] = True
        return {
            "status": "READY",
            "provider": "codex",
            "email": "operator@example.com",
            "planType": "plus",
            "windows": [],
            "credits": None,
            "accessToken": "NEVER_RETURN",
        }

    monkeypatch.setattr(repository, "list_tasks", tasks)
    monkeypatch.setattr(codex_client, "account", account, raising=False)
    with TestClient(app) as client:
        yield client, state


def test_tenant_operator_sees_only_tenant_history_without_shared_account(endpoint):
    client, state = endpoint
    response = client.get("/api/v1/operations/ai-usage?limit=10&offset=20")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["account"]["status"] == "RESTRICTED"
    assert not state.get("accountCalled")
    assert state["seen"] == [{"include_global": False, "limit": 10, "offset": 20}]


def test_platform_operator_can_read_live_account_and_global_public_history(endpoint):
    client, state = endpoint
    state["platform"] = True
    response = client.get("/api/v1/operations/ai-usage")
    assert response.status_code == 200
    assert response.json()["data"]["account"]["email"] == "operator@example.com"
    assert "NEVER_RETURN" not in response.text and "accessToken" not in response.text
    assert state["seen"][0]["include_global"] is True


def test_ordinary_member_cannot_read_usage(endpoint):
    client, state = endpoint
    state["role"] = "VIEWER"
    response = client.get("/api/v1/operations/ai-usage")
    assert response.status_code == 403
    assert not state["seen"] and not state.get("accountCalled")
