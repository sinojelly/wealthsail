from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from backend.common.tenancy import TenantContext
from backend.config import Settings, validate_runtime_settings
from backend.identity import api as identity_api
from backend.main import create_app


def test_l1_trade_decision_and_password_reset_routes_are_mounted() -> None:
    paths = create_app().openapi()["paths"]
    assert "post" in paths["/api/v1/trades/fills"]
    assert "post" in paths["/api/v1/trades/fills/{fill_id}/confirm"]
    assert "get" in paths["/api/v1/trades/fills"]
    assert "post" in paths["/api/v1/research/decisions"]
    assert "get" in paths["/api/v1/research/decisions"]
    assert "post" in paths["/api/v1/auth/password-reset/request"]
    assert "post" in paths["/api/v1/auth/password-reset/confirm"]
    assert "/api/v1/auth/register-with-tenant" not in paths
    assert "/api/v1/auth/tenants" not in paths


def test_production_api_fails_before_startup_when_secrets_are_missing() -> None:
    settings = Settings(_env_file=None, app_env="production", auth_dev_mode=False)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        validate_runtime_settings(settings, process="api")


def test_auth_dev_mode_is_rejected_in_every_environment() -> None:
    settings = Settings(_env_file=None, app_env="development", auth_dev_mode=True)
    with pytest.raises(ValueError, match="no longer supported"):
        validate_runtime_settings(settings, process="api")


def test_production_api_accepts_only_independent_hardened_secrets() -> None:
    base = dict(
        _env_file=None,
        app_env="production",
        database_url="postgresql://api-login@db.example/wealthsail",
        jwt_secret="A" * 40,
        refresh_token_hmac_key="B" * 40,
        password_reset_hmac_key="C" * 40,
        binding_hmac_key="D" * 40,
        routing_encryption_key="E" * 40,
        wecom_callback_token="F" * 24,
        wecom_callback_aes_key="G" * 43,
        wecom_corp_id="ww-production",
        smtp_host="smtp.example.com",
        smtp_from="no-reply@example.com",
        web_base_url="https://wealthsail.example.com",
        cors_origins=["https://wealthsail.example.com"],
    )
    settings = Settings(**base)
    assert validate_runtime_settings(settings, process="api") is settings

    reused = Settings(**{**base, "password_reset_hmac_key": "B" * 40})
    with pytest.raises(ValueError, match="must be independent"):
        validate_runtime_settings(reused, process="api")


@pytest.mark.asyncio
async def test_switch_tenant_keeps_refresh_token_out_of_json(monkeypatch) -> None:
    class Identity:
        async def switch_tenant(self, *_args, **_kwargs):
            return {
                "accessToken": "access-token",
                "_refreshToken": "rotated-refresh-token",
                "user": {"tenantId": "tenant-2"},
            }

    monkeypatch.setattr(identity_api, "identity_service", lambda *_args: Identity())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/switch-tenant",
            "headers": [(b"cookie", b"wealthsail_refresh=old-refresh")],
        }
    )
    response = Response()

    result = await identity_api.switch_tenant(
        identity_api.SwitchTenantRequest(tenantId="tenant-2"),
        response,
        request,
        pool=object(),
        ctx=TenantContext("user-1", "tenant-1", tenant_role="OWNER"),
        settings=Settings(_env_file=None),
        request_id="req-switch",
    )

    assert "_refreshToken" not in result["data"]
    assert "rotated-refresh-token" not in str(result)
    assert "HttpOnly" in response.headers["set-cookie"]
