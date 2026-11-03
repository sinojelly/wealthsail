"""History repair APIs distinguish true read-only preview from durable generation."""

from contextlib import asynccontextmanager
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.errors import (
    DataUnavailableError,
    MoneyJSONResponse,
    register_exception_handlers,
)

RUN, ACTOR, TENANT, DERIVED = [uuid4() for _ in range(4)]


def client(monkeypatch, role="OWNER", active=True):
    module = import_module("backend.investment.research_history_repair_api")
    conn = AsyncMock()
    conn.fetchval.return_value = active
    state = {"readonly": False, "committed": False}

    @asynccontextmanager
    async def transaction(*args):
        yield conn
        state["committed"] = True

    @asynccontextmanager
    async def readonly(*args):
        state["readonly"] = True
        yield conn

    monkeypatch.setattr(module, "tenant_transaction", transaction)
    monkeypatch.setattr(module, "readonly_tenant_transaction", readonly)
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        str(ACTOR), str(TENANT), tenant_role=role
    )
    return TestClient(app), module, state


@pytest.mark.parametrize("role,active", [("VIEWER", True), ("OWNER", False)])
def test_preview_and_generation_require_current_active_admin(monkeypatch, role, active):
    api, module, _ = client(monkeypatch, role, active)
    preview = AsyncMock()
    generate = AsyncMock()
    monkeypatch.setattr(module.repository, "preview_history_repair", preview)
    monkeypatch.setattr(module.repository, "generate_history_repair", generate)
    assert (
        api.get(
            f"/api/v1/investment/daily-research/history-repairs/from/{RUN}/preview"
        ).status_code
        == 403
    )
    assert (
        api.post(
            f"/api/v1/investment/daily-research/history-repairs/from/{RUN}",
            json={"commandKey": str(uuid4())},
        ).status_code
        == 403
    )
    preview.assert_not_awaited()
    generate.assert_not_awaited()


def test_preview_uses_read_only_transaction_and_accepts_no_model_body(monkeypatch):
    api, module, state = client(monkeypatch)
    monkeypatch.setattr(
        module.repository,
        "preview_history_repair",
        AsyncMock(return_value={"sourceRunId": str(RUN), "state": "PREVIEW"}),
    )
    response = api.get(
        f"/api/v1/investment/daily-research/history-repairs/from/{RUN}/preview"
    )
    assert response.status_code == 200 and state["readonly"] is True
    assert state["committed"] is False
    assert (
        api.post(
            f"/api/v1/investment/daily-research/history-repairs/from/{RUN}",
            json={"commandKey": str(uuid4()), "raw": {"inject": True}},
        ).status_code
        == 400
    )


def test_failed_generate_commits_budget_attempt_before_raising_safe_error(monkeypatch):
    api, module, state = client(monkeypatch)
    monkeypatch.setattr(
        module.repository,
        "generate_history_repair",
        AsyncMock(
            return_value=module.repository.ReplayFailure(
                DataUnavailableError("历史修复超时，已停止校验")
            )
        ),
    )
    response = api.post(
        f"/api/v1/investment/daily-research/history-repairs/from/{RUN}",
        json={"commandKey": str(uuid4())},
    )
    assert response.status_code == 503 and state["committed"] is True


def test_history_list_has_server_generation_capability_and_bounded_repository_projection(
    monkeypatch,
):
    api, module, _ = client(monkeypatch, role="VIEWER")
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(module.repository, "list_history_repairs", read)
    response = api.get(
        f"/api/v1/investment/daily-research/history-repairs?sourceRunId={RUN}"
    )
    assert response.json()["data"] == {"items": [], "canGenerate": False}
    assert read.await_args.kwargs["admin"] is False


@pytest.mark.parametrize(
    "role,active,status",
    [("VIEWER", True, 403), ("OWNER", False, 403), ("OWNER", True, 200)],
)
def test_private_repair_details_require_live_admin_and_are_never_implicit(
    monkeypatch, role, active, status
):
    api, module, _state = client(monkeypatch, role, active)
    details = AsyncMock(
        return_value={
            "id": str(DERIVED),
            "privateResult": {"formalReport": {"price": "ADMIN_ONLY"}},
        }
    )
    monkeypatch.setattr(module.repository, "read_history_repair_details", details)
    response = api.get(
        f"/api/v1/investment/daily-research/history-repairs/{DERIVED}/details"
    )
    assert response.status_code == status
    if status == 403:
        details.assert_not_awaited()
    else:
        assert "ADMIN_ONLY" in response.text
