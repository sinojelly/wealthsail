"""POST /api/v1/reports/generate deprecation contract for PRE_MARKET."""

from unittest.mock import ANY, AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
)
from backend.errors import MoneyJSONResponse, register_exception_handlers
from backend.reports import api as reports_api


USER_ID = "00000000-0000-0000-0000-000000000001"
TENANT_ID = "00000000-0000-0000-0000-000000000002"


def _client() -> TestClient:
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(reports_api.router, prefix="/api/v1")
    settings = Settings(_env_file=None, app_env="test")
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        tenant_role="OWNER",
    )
    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_request_id] = lambda: "req-gen-disabled"
    return TestClient(app, raise_server_exceptions=False)


def test_generate_rejects_pre_market_without_calling_service() -> None:
    generate = AsyncMock(return_value={"id": "should-not-run"})
    with patch("backend.reports.api.service.generate_report", new=generate):
        response = _client().post(
            "/api/v1/reports/generate", json={"session": "PRE_MARKET"}
        )

    assert response.status_code == 400
    body = response.json()["error"]["message"]
    assert "旧生成入口已下线" in body
    assert "debug/reports/trigger/PRE_MARKET" in body
    generate.assert_not_awaited()


def test_generate_keeps_working_for_un_split_sessions() -> None:
    generate = AsyncMock(return_value={"id": "report-fund-1430"})
    with patch("backend.reports.api.service.generate_report", new=generate):
        response = _client().post(
            "/api/v1/reports/generate", json={"session": "FUND_1430"}
        )

    assert response.status_code == 200
    assert response.json()["data"] == {"id": "report-fund-1430"}
    generate.assert_awaited_once_with(
        ANY,
        ANY,
        ANY,
        "FUND_1430",
    )
