from __future__ import annotations

import pytest

from backend.config import Settings, validate_runtime_settings
from backend.main import create_app
from modules.identity.service import IdentityService, IdentitySettings


def test_trade_decision_password_reset_and_tenant_routes_are_mounted() -> None:
    paths = create_app().openapi()["paths"]
    assert "post" in paths["/api/v1/trades/fills"]
    assert "post" in paths["/api/v1/trades/fills/{fill_id}/confirm"]
    assert "get" in paths["/api/v1/trades/fills"]
    assert "post" in paths["/api/v1/research/decisions"]
    assert "get" in paths["/api/v1/research/decisions"]
    assert "post" in paths["/api/v1/auth/password-reset/request"]
    assert "post" in paths["/api/v1/auth/password-reset/confirm"]
    assert "post" in paths["/api/v1/auth/register-with-tenant"]
    assert "post" in paths["/api/v1/auth/tenants"]


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


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Conn:
    def __init__(self) -> None:
        self.call = None

    def transaction(self):
        return _Tx()

    async def execute(self, *_args):
        return "OK"

    async def fetchrow(self, sql, *args):
        self.call = (sql, args)
        return {
            "out_user_id": "user-1",
            "out_tenant_id": "tenant-1",
            "out_tenant_name": "个人投资组合",
            "out_role": "OWNER",
        }


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_self_registration_is_one_atomic_domain_command() -> None:
    conn = _Conn()
    service = IdentityService(
        _Pool(conn),
        IdentitySettings(
            jwt_secret="jwt",
            refresh_token_hmac_key="refresh",
            binding_hmac_key="binding",
        ),
    )
    result = await service.self_register(
        email="New@example.test",
        password="correct horse battery staple",
        nickname="New User",
        tenant_name="个人投资组合",
    )
    assert "identity.register_with_tenant" in conn.call[0]
    assert result["tenantId"] == "tenant-1"
    assert result["role"] == "OWNER"
