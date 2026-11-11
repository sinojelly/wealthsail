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

    async def account(_settings, **kwargs):
        state["accountCalled"] = True
        state["accountOptions"] = kwargs
        if state.get("accountFailure"):
            raise RuntimeError(
                "private@example.test TOKEN_NEVER_LOG raw supplier reply"
            )
        return {
            "status": state.get("accountStatus", "READY"),
            "provider": "codex",
            "email": "operator@example.com",
            "planType": "plus",
            "windows": [],
            "credits": None,
            "accessToken": "NEVER_RETURN",
            "accountFingerprint": "INTERNAL_ONLY",
            "authGeneration": "INTERNAL_ONLY",
            "observedAt": "2026-10-28T01:00:00+00:00",
            "limitations": ["ACCOUNT_STALE"]
            if state.get("accountStatus") == "STALE"
            else [],
            "diagnostic": state.get("accountDiagnostic"),
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


def test_task_poll_does_not_read_account_even_for_platform_operator(endpoint):
    client, state = endpoint
    state["platform"] = True
    response = client.get("/api/v1/operations/ai-usage?view=tasks&limit=10&offset=20")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["tasks"] == [] and data["offset"] == 20
    assert "account" not in data
    assert not state.get("accountCalled")
    assert state["seen"] == [{"include_global": True, "limit": 10, "offset": 20}]


@pytest.mark.parametrize("refresh", [False, True])
def test_account_only_poll_skips_task_scan_and_supports_explicit_refresh(
    endpoint, refresh
):
    client, state = endpoint
    state["platform"] = True
    response = client.get(
        f"/api/v1/operations/ai-usage?view=account&refresh_account={str(refresh).lower()}"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"account"}
    assert data["account"]["status"] == "READY"
    assert state["accountOptions"].get("refresh", False) is refresh
    assert not state["seen"]
    assert "INTERNAL_ONLY" not in response.text and "NEVER_RETURN" not in response.text


def test_stale_account_keeps_last_success_time_and_safe_fields(endpoint):
    client, state = endpoint
    state.update(platform=True, accountStatus="STALE")
    response = client.get("/api/v1/operations/ai-usage?view=account")
    account = response.json()["data"]["account"]
    assert account["status"] == "STALE"
    assert account["email"] == "operator@example.com"
    assert account["observedAt"] == "2026-10-28T01:00:00+00:00"
    assert account["limitations"] == ["ACCOUNT_STALE"]
    assert "INTERNAL_ONLY" not in response.text


@pytest.mark.parametrize("role", ["OWNER", "VIEWER"])
def test_account_only_refresh_cannot_bypass_platform_role(endpoint, role):
    client, state = endpoint
    state["role"] = role
    response = client.get(
        "/api/v1/operations/ai-usage?view=account&refresh_account=true"
    )
    assert response.status_code == (200 if role == "OWNER" else 403)
    if role == "OWNER":
        assert response.json()["data"] == {
            "account": {
                "status": "RESTRICTED",
                "provider": "codex",
                "email": None,
                "planType": None,
                "authType": None,
                "observedAt": None,
                "windows": [],
                "credits": None,
                "limitations": ["PLATFORM_ADMIN_REQUIRED"],
            }
        }
    assert not state["seen"] and not state.get("accountCalled")


def test_unknown_usage_view_is_rejected_instead_of_silently_reading_account(endpoint):
    client, state = endpoint
    response = client.get("/api/v1/operations/ai-usage?view=other")
    assert response.status_code == 400
    assert not state["seen"] and not state.get("accountCalled")


def test_unexpected_account_failure_is_safe_and_diagnosed_without_losing_tasks(
    endpoint, caplog
):
    client, state = endpoint
    state.update(platform=True, accountFailure=True)
    response = client.get("/api/v1/operations/ai-usage")
    assert response.status_code == 200
    assert response.json()["data"]["account"]["status"] == "UNAVAILABLE"
    assert response.json()["data"]["tasks"] == []
    assert "stage=operations_api" in caplog.text
    assert "error_class=RuntimeError" in caplog.text and "duration_ms=" in caplog.text
    assert (
        "private@example.test" not in caplog.text
        and "TOKEN_NEVER_LOG" not in caplog.text
    )


@pytest.mark.parametrize("platform", [True, False])
def test_account_runtime_diagnostic_is_whitelisted_and_requires_platform_access(endpoint, platform):
    client, state = endpoint
    diagnostic = {"version": "codex-diagnostic-v1", "origin": "ACCOUNT_RPC", "stage": "ACCOUNT_READ",
                  "reasonCode": "RPC_RESPONSE_TIMEOUT", "rpcCode": -32603, "elapsedMs": 15005}
    state.update(platform=platform, accountStatus="UNAVAILABLE",
                 accountDiagnostic={**diagnostic, "message": "TOKEN_NEVER_RETURN"})
    response = client.get("/api/v1/operations/ai-usage?view=account")
    account = response.json()["data"]["account"]
    if platform:
        assert account.get("diagnostic") == diagnostic
    else:
        assert account["status"] == "RESTRICTED"
        assert "diagnostic" not in account
        assert not state.get("accountCalled")
    assert "TOKEN_NEVER_RETURN" not in response.text
