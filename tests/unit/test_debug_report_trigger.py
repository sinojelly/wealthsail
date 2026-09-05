"""Controlled one-shot report trigger contracts."""

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.debug import api as debug_api
from backend.debug import service as debug_service
from backend.reports import service as reports_service
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
)
from backend.errors import (
    MoneyJSONResponse,
    NotFoundError,
    ValidationError_,
    register_exception_handlers,
)


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
        response = client(role).post("/api/v1/debug/reports/trigger/POST_MARKET")

    assert response.status_code == status
    if status == 200:
        assert response.json()["data"] == {"reportId": "report-1", "outboxId": None}
        assert "target_user_id" not in trigger.await_args.kwargs
    else:
        trigger.assert_not_awaited()


def test_debug_trigger_rejects_unknown_session_before_service_call() -> None:
    trigger = AsyncMock()
    with patch("backend.debug.api.trigger_report_once", new=trigger):
        response = client("OWNER").post("/api/v1/debug/reports/trigger/UNKNOWN")

    # WealthSail normalizes FastAPI request validation to its canonical 400 envelope.
    assert response.status_code == 400
    trigger.assert_not_awaited()


def test_debug_trigger_ignores_external_target_and_uses_authenticated_user() -> None:
    trigger = AsyncMock(return_value={"reportId": "report-1", "outboxId": "outbox-1"})
    with patch("backend.debug.api.trigger_report_once", new=trigger):
        response = client("ADMIN").post(
            f"/api/v1/debug/reports/trigger/PRE_MARKET?user_id={TARGET_ID}"
        )

    assert response.status_code == 200
    trigger.assert_awaited_once()
    assert "target_user_id" not in trigger.await_args.kwargs


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

    generate = AsyncMock(
        return_value={"id": "report-1", "_deliveryOutboxId": "outbox-1"}
    )
    lookup = AsyncMock(side_effect=AssertionError("post-commit lookup is unsafe"))
    monkeypatch.setattr(debug_service, "tenant_transaction", transaction)
    monkeypatch.setattr(debug_service.report_service, "generate_report", generate)
    monkeypatch.setattr(debug_service.repository, "get_report_outbox_id", lookup)
    ctx = TenantContext(USER_ID, TENANT_ID, tenant_role="OWNER")
    settings = Settings(_env_file=None, app_env="test")
    pool = object()

    result = await debug_service.trigger_report_once(
        pool, ctx, settings, "POST_MARKET"
    )

    assert result == {"reportId": "report-1", "outboxId": "outbox-1"}
    generate.assert_awaited_once_with(
        pool,
        ctx,
        settings,
        "POST_MARKET",
        actor_type="USER",
        force=True,
        # A manual debug run is deliberately unicast to the authenticated
        # operator, even though the STANDARD report row remains tenant-wide.
        delivery_user_id=USER_ID,
        trigger_audit_action="DEBUG_REPORT_TRIGGERED",
        trigger_audit_details={"targetUserId": USER_ID},
    )
    lookup.assert_not_awaited()
    assert all("workflow.jobs" not in sql for sql, _args in conn.calls)


@pytest.mark.asyncio
async def test_debug_service_requires_authenticated_user_to_remain_active(monkeypatch) -> None:
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
        )

    generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "phase"),
    [
        ("PRE_MARKET", "ALERT"),
        ("INTRADAY", "PERSONALIZED"),
        ("FUND_1430", "MACRO"),
        ("POST_MARKET", "ALERT"),
    ],
)
async def test_debug_service_rejects_incompatible_session_phase(
    monkeypatch, session: str, phase: str
) -> None:
    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield MembershipConnection(active=True)

    generate = AsyncMock()
    monkeypatch.setattr(debug_service, "tenant_transaction", transaction)
    monkeypatch.setattr(debug_service.report_service, "generate_report", generate)

    with pytest.raises(ValidationError_, match="不支持"):
        await debug_service.trigger_report_once(
            object(),
            TenantContext(USER_ID, TENANT_ID, tenant_role="ADMIN"),
            Settings(_env_file=None, app_env="test"),
            session,
            phase=phase,
        )

    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_debug_personalized_phase_requires_active_member(monkeypatch) -> None:
    conn = MembershipConnection(active=False)

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield conn

    personalized = AsyncMock()
    monkeypatch.setattr(debug_service, "tenant_transaction", transaction)
    monkeypatch.setattr(
        debug_service.pre_market_personalized,
        "generate_pre_market_personalized",
        personalized,
    )

    with pytest.raises(NotFoundError, match="活动成员"):
        await debug_service.trigger_report_once(
            object(),
            TenantContext(USER_ID, TENANT_ID, tenant_role="ADMIN"),
            Settings(_env_file=None, app_env="test"),
            "PRE_MARKET",
            phase="PERSONALIZED",
        )

    personalized.assert_not_awaited()


@pytest.mark.asyncio
async def test_debug_personalized_phase_returns_null_for_empty_context(
    monkeypatch,
) -> None:
    conn = MembershipConnection(active=True)

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield conn

    # Empty/empty member: no personal facts to report.
    personalized = AsyncMock(return_value=None)
    monkeypatch.setattr(debug_service, "tenant_transaction", transaction)
    monkeypatch.setattr(
        debug_service.pre_market_personalized,
        "generate_pre_market_personalized",
        personalized,
    )

    pool = object()
    ctx = TenantContext(USER_ID, TENANT_ID, tenant_role="ADMIN")
    settings = Settings(_env_file=None, app_env="test")
    result = await debug_service.trigger_report_once(
        pool,
        ctx,
        settings,
        "PRE_MARKET",
        phase="PERSONALIZED",
    )

    assert result == {"reportId": None, "outboxId": None}
    personalized.assert_awaited_once_with(
        pool,
        ctx,
        settings,
        force=True,
        trigger_audit_action="DEBUG_REPORT_TRIGGERED",
        trigger_audit_details={"targetUserId": USER_ID},
    )


@pytest.mark.asyncio
async def test_debug_personalized_phase_reaches_real_generator(monkeypatch) -> None:
    """Personalized debug generation must force a new self-scoped version."""

    @asynccontextmanager
    async def debug_txn(*_a, **_k):
        yield MembershipConnection(active=True)

    @asynccontextmanager
    async def gen_txn(*_a, **_k):
        yield SimpleNamespace()

    captured: dict[str, object] = {}

    async def fake_context(
        conn, tenant_id, session, *, audience_user_id=None, asset_scope=None
    ):
        captured["audience"] = audience_user_id
        captured["scope"] = asset_scope
        return {"positions": [{"instrumentId": "inst-1"}], "watchlist": []}

    async def fake_generate(*_a, **kwargs):
        captured["generate_kwargs"] = kwargs
        return {"id": "rep-1", "_deliveryOutboxId": "ob-1"}

    monkeypatch.setattr(debug_service, "tenant_transaction", debug_txn)
    monkeypatch.setattr(
        debug_service.pre_market_personalized, "tenant_transaction", gen_txn
    )
    monkeypatch.setattr(reports_service, "build_report_context", fake_context)
    monkeypatch.setattr(reports_service, "generate_report", fake_generate)

    result = await debug_service.trigger_report_once(
        object(),
        TenantContext(USER_ID, TENANT_ID, tenant_role="ADMIN"),
        Settings(_env_file=None, app_env="test"),
        "PRE_MARKET",
        phase="PERSONALIZED",
    )

    assert result == {"reportId": "rep-1", "outboxId": "ob-1"}
    assert captured["audience"] == USER_ID
    assert captured["generate_kwargs"]["report_variant"] == "PRE_MARKET_PERSONALIZED"
    assert captured["generate_kwargs"]["audience_user_id"] == USER_ID
    assert captured["generate_kwargs"]["delivery_user_id"] == USER_ID
    assert captured["generate_kwargs"]["force"] is True
    assert (
        captured["generate_kwargs"]["trigger_audit_action"]
        == "DEBUG_REPORT_TRIGGERED"
    )
    # The real generator must own the asset scope, not the caller's role.
    assert captured["scope"].kind == "MINE"
    assert str(captured["scope"].owner_user_id) == USER_ID


@pytest.mark.asyncio
async def test_debug_macro_phase_is_the_default_for_pre_market(monkeypatch) -> None:
    conn = MembershipConnection(active=True)

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield conn

    generate = AsyncMock(return_value={"id": "macro-1"})
    monkeypatch.setattr(debug_service, "tenant_transaction", transaction)
    monkeypatch.setattr(debug_service.report_service, "generate_report", generate)
    pool = object()

    result = await debug_service.trigger_report_once(
        pool,
        TenantContext(USER_ID, TENANT_ID, tenant_role="ADMIN"),
        Settings(_env_file=None, app_env="test"),
        "PRE_MARKET",
    )

    assert result == {"reportId": "macro-1", "outboxId": None}
    generate.assert_awaited_once_with(
        pool,
        TenantContext(USER_ID, TENANT_ID, tenant_role="ADMIN"),
        Settings(_env_file=None, app_env="test"),
        "PRE_MARKET",
        actor_type="USER",
        force=True,
        report_variant="PRE_MARKET_MACRO",
        delivery_user_id=USER_ID,
        rebuild_shared_draft=True,
        trigger_audit_action="DEBUG_REPORT_TRIGGERED",
        trigger_audit_details={
            "targetUserId": USER_ID,
            "sharedDraftRebuilt": True,
        },
    )


@pytest.mark.asyncio
async def test_debug_intraday_alert_phase_routes_to_real_generator(monkeypatch) -> None:
    """?session=INTRADAY&phase=ALERT must reach the real condition-triggered
    generator and return its report id (here a single holding breaches +3%)."""

    @asynccontextmanager
    async def debug_txn(*_a, **_k):
        yield MembershipConnection(active=True)

    @asynccontextmanager
    async def gen_txn(*_a, **_k):
        yield SimpleNamespace()

    captured: dict[str, object] = {}

    async def fake_context(
        conn, tenant_id, session, *, audience_user_id=None, asset_scope=None
    ):
        captured["audience"] = audience_user_id
        captured["scope"] = asset_scope
        return {
            "positions": [{"instrumentId": "inst-1", "changePct": "3.5"}],
            "summary": {"dailyPnl": "0", "totalAsset": "100"},
        }

    async def fake_generate(*_a, **kwargs):
        captured["generate_kwargs"] = kwargs
        return {"id": "rep-intraday", "_deliveryOutboxId": None}

    async def fake_day_change(*_a, **_k):
        return {"inst-1": Decimal("3.5")}

    async def fake_claim(*_a, **_k):
        return False

    monkeypatch.setattr(debug_service, "tenant_transaction", debug_txn)
    monkeypatch.setattr(debug_service.intraday, "tenant_transaction", gen_txn)
    monkeypatch.setattr(debug_service.intraday, "build_report_context", fake_context)
    monkeypatch.setattr(debug_service.intraday, "generate_report", fake_generate)
    monkeypatch.setattr(
        debug_service.intraday.repository, "latest_day_change_pct", fake_day_change
    )
    monkeypatch.setattr(
        debug_service.intraday.repository, "claim_intraday_alert", fake_claim
    )

    result = await debug_service.trigger_report_once(
        object(),
        TenantContext(USER_ID, TENANT_ID, tenant_role="ADMIN"),
        Settings(_env_file=None, app_env="test"),
        "INTRADAY",
        phase="ALERT",
    )

    assert result == {"reportId": "rep-intraday", "outboxId": None}
    assert captured["generate_kwargs"]["report_variant"] == "INTRADAY_ALERT"
    assert captured["generate_kwargs"]["force"] is True
    assert captured["generate_kwargs"]["audience_user_id"] == USER_ID
    assert captured["generate_kwargs"]["delivery_user_id"] == USER_ID
    assert captured["generate_kwargs"]["actor_type"] == "USER"
    assert (
        captured["generate_kwargs"]["trigger_audit_action"]
        == "DEBUG_REPORT_TRIGGERED"
    )
    assert captured["generate_kwargs"]["push_content_override"] is not None
    # The alert generator must own the asset scope, not the caller's role.
    assert captured["scope"].kind == "MINE"
    assert str(captured["scope"].owner_user_id) == USER_ID
