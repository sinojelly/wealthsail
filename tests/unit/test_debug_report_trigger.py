"""Controlled one-shot report trigger contracts."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.debug import api as debug_api
from backend.debug import service as debug_service
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
)
from backend.errors import MoneyJSONResponse, NotFoundError, register_exception_handlers


USER_ID = "00000000-0000-0000-0000-000000000001"
TENANT_ID = "00000000-0000-0000-0000-000000000002"
TARGET_ID = "00000000-0000-0000-0000-000000000003"


def client(role: str) -> TestClient:
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(debug_api.router, prefix="/api/v1")
    settings = Settings(_env_file=None, app_env="test")
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        tenant_role=role,
    )
    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_request_id] = lambda: "req-debug-report"
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("role", "status"),
    [("OWNER", 200), ("ADMIN", 200), ("EDITOR", 403), ("VIEWER", 403)],
)
def test_debug_trigger_role_boundary(role: str, status: int) -> None:
    trigger = AsyncMock(return_value={"reportId": "report-1", "outboxId": None})
    with patch("backend.debug.api.trigger_report_once", new=trigger):
        response = client(role).post(
            "/api/v1/debug/reports/trigger/POST_MARKET"
        )

    assert response.status_code == status
    if status == 200:
        assert response.json()["data"] == {"reportId": "report-1", "outboxId": None}
        assert trigger.await_args.kwargs["target_user_id"] == USER_ID
    else:
        trigger.assert_not_awaited()


def test_debug_trigger_rejects_unknown_session_before_service_call() -> None:
    trigger = AsyncMock()
    with patch("backend.debug.api.trigger_report_once", new=trigger):
        response = client("OWNER").post(
            "/api/v1/debug/reports/trigger/UNKNOWN"
        )

    # WealthSail normalizes FastAPI request validation to its canonical 400 envelope.
    assert response.status_code == 400
    trigger.assert_not_awaited()


def test_debug_trigger_forwards_explicit_target_user() -> None:
    trigger = AsyncMock(
        return_value={"reportId": "report-1", "outboxId": "outbox-1"}
    )
    with patch("backend.debug.api.trigger_report_once", new=trigger):
        response = client("ADMIN").post(
            f"/api/v1/debug/reports/trigger/PRE_MARKET?user_id={TARGET_ID}"
        )

    assert response.status_code == 200
    assert trigger.await_args.kwargs["target_user_id"] == TARGET_ID


class MembershipConnection:
    def __init__(self, active: bool) -> None:
        self.active = active
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args):
        self.calls.append((sql, args))
        return self.active


@pytest.mark.asyncio
async def test_debug_service_generates_once_without_scheduler_dependency(
    monkeypatch,
) -> None:
    conn = MembershipConnection(active=True)

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield conn

    generate = AsyncMock(return_value={"id": "report-1"})
    lookup = AsyncMock(return_value="outbox-1")
    audit = AsyncMock()
    monkeypatch.setattr(debug_service, "tenant_transaction", transaction)
    monkeypatch.setattr(debug_service.report_service, "generate_report", generate)
    monkeypatch.setattr(debug_service.repository, "get_report_outbox_id", lookup)
    monkeypatch.setattr(debug_service, "write_audit", audit)
    ctx = TenantContext(USER_ID, TENANT_ID, tenant_role="OWNER")
    settings = Settings(_env_file=None, app_env="test")
    pool = object()

    result = await debug_service.trigger_report_once(
        pool, ctx, settings, "POST_MARKET", target_user_id=TARGET_ID
    )

    assert result == {"reportId": "report-1", "outboxId": "outbox-1"}
    generate.assert_awaited_once_with(
        pool,
        ctx,
        settings,
        "POST_MARKET",
        actor_type="DEBUG_USER",
        force=True,
        delivery_user_id=TARGET_ID,
    )
    lookup.assert_awaited_once_with(conn, TENANT_ID, "report-1", TARGET_ID)
    assert audit.await_args.kwargs["action"] == "DEBUG_REPORT_TRIGGERED"
    assert audit.await_args.kwargs["details"] == {
        "session": "POST_MARKET",
        "targetUserId": TARGET_ID,
        "outboxId": "outbox-1",
    }
    assert all("workflow.jobs" not in sql for sql, _args in conn.calls)


@pytest.mark.asyncio
async def test_debug_service_hides_inactive_or_cross_tenant_target(monkeypatch) -> None:
    conn = MembershipConnection(active=False)

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield conn

    generate = AsyncMock()
    monkeypatch.setattr(debug_service, "tenant_transaction", transaction)
    monkeypatch.setattr(debug_service.report_service, "generate_report", generate)

    with pytest.raises(NotFoundError, match="活动成员"):
        await debug_service.trigger_report_once(
            object(),
            TenantContext(USER_ID, TENANT_ID, tenant_role="ADMIN"),
            Settings(_env_file=None, app_env="test"),
            "POST_MARKET",
            target_user_id=TARGET_ID,
        )

    generate.assert_not_awaited()
