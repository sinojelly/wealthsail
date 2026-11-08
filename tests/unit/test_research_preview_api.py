"""Only current active admins can request a read-only historical simulation."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.errors import (
    ForbiddenError,
    MoneyJSONResponse,
    register_exception_handlers,
)

RUN, ACTOR, TENANT = [uuid4() for _ in range(3)]
NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)


def client(monkeypatch, *, role="OWNER", active=True):
    module = import_module("backend.investment.research_preview_api")
    state = {"readonly": False, "transactions": 0}
    conn = AsyncMock()
    conn.fetchval.return_value = active

    @asynccontextmanager
    async def readonly(_pool, ctx):
        assert ctx.tenant_id == str(TENANT)
        state["readonly"] = True
        state["transactions"] += 1
        yield conn

    monkeypatch.setattr(module, "readonly_tenant_transaction", readonly)
    monkeypatch.setattr(module, "now_shanghai", lambda: NOW)
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/api/v1")
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        str(ACTOR), str(TENANT), tenant_role=role
    )
    return TestClient(app), module, conn, state


@pytest.mark.parametrize(
    "role,active,status",
    [
        ("OWNER", True, 200),
        ("ADMIN", True, 200),
        ("VIEWER", True, 403),
        ("OWNER", False, 403),
    ],
)
def test_preview_uses_single_current_admin_without_a_second_owner(
    monkeypatch, role, active, status
):
    api, module, conn, state = client(monkeypatch, role=role, active=active)
    read = AsyncMock(
        return_value={"runId": str(RUN), "readOnly": True, "investmentEligible": False}
    )
    monkeypatch.setattr(module.service, "preview_research", read)
    response = api.get(f"/api/v1/operations/research-preview/{RUN}")
    assert response.status_code == status
    if status == 200:
        assert response.json()["data"]["readOnly"] is True
        assert response.json()["data"]["investmentEligible"] is False
        assert state["readonly"] is True
        assert read.await_args.args == (conn, RUN)
        assert read.await_args.kwargs == {"now": NOW}
    else:
        read.assert_not_awaited()
    conn.execute.assert_not_awaited()


def test_artifact_tenant_policy_rejection_does_not_fall_back_to_public_or_live_report(
    monkeypatch,
):
    api, module, conn, _state = client(monkeypatch)
    monkeypatch.setattr(
        module.service,
        "preview_research",
        AsyncMock(side_effect=ForbiddenError("材料不可访问")),
    )
    response = api.get(f"/api/v1/operations/research-preview/{RUN}")
    assert response.status_code == 403
    assert "data" not in response.json()
    conn.execute.assert_not_awaited()


def test_preview_accepts_no_write_command_or_arbitrary_run_identity(monkeypatch):
    api, module, _conn, _state = client(monkeypatch)
    read = AsyncMock()
    monkeypatch.setattr(module.service, "preview_research", read)
    assert (
        api.post(
            f"/api/v1/operations/research-preview/{RUN}", json={"raw": "injected"}
        ).status_code
        == 405
    )
    assert api.get("/api/v1/operations/research-preview/not-a-uuid").status_code == 400
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_transaction_is_readonly_and_applies_existing_tenant_scope(
    monkeypatch,
):
    module = import_module("backend.investment.research_preview_api")
    state = {"readonly": None, "scope": None}

    class Conn:
        @asynccontextmanager
        async def transaction(self, *, readonly):
            state["readonly"] = readonly
            yield

    conn = Conn()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    async def scope(connection, user, tenant):
        assert connection is conn
        state["scope"] = (user, tenant)

    monkeypatch.setattr(module, "apply_tenant_context", scope)
    ctx = TenantContext(str(ACTOR), str(TENANT), tenant_role="ADMIN")
    async with module.readonly_tenant_transaction(Pool(), ctx) as actual:
        assert actual is conn
    assert state == {"readonly": True, "scope": (str(ACTOR), str(TENANT))}
