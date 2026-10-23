"""Optional delivery integrations must leave core identity boundaries intact."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, Response
from starlette.requests import Request

from backend.common.tenancy import TenantContext
from backend.config import Settings, validate_runtime_settings
from backend.errors import DataUnavailableError
from backend.identity import api as identity_api
from backend.integrations import wecom, wecom_bot
from backend.jobs import outbox


def _api_settings(**overrides) -> Settings:
    values = dict(
        _env_file=None,
        app_env="production",
        database_url="postgresql://wealthsail_api_login@postgres/wealthsail",
        worker_database_url="",
        jwt_secret="A" * 40,
        refresh_token_hmac_key="B" * 40,
        password_reset_hmac_key="C" * 40,
        binding_hmac_key="D" * 40,
        routing_encryption_key="E" * 40,
        wecom_enabled=False,
        password_reset_email_enabled=False,
        wecom_corp_id="",
        wecom_corp_secret="",
        wecom_callback_token="",
        wecom_callback_aes_key="",
        wecom_bot_secret="",
        wecom_opportunity_webhook_url="",
        model_api_key=None,
        smtp_host=None,
        smtp_from=None,
        web_base_url="https://wealthsail.example.test",
        cors_origins=["https://wealthsail.example.test"],
    )
    return Settings(**{**values, **overrides})


def _worker_settings(**overrides) -> Settings:
    values = dict(
        _env_file=None,
        app_env="production",
        database_url="",
        worker_database_url="postgresql://wealthsail_worker_login@postgres/wealthsail",
        worker_database_pool_mode="direct",
        jwt_secret="",
        refresh_token_hmac_key="",
        password_reset_hmac_key="",
        binding_hmac_key="",
        routing_encryption_key="E" * 40,
        wecom_enabled=False,
        wecom_corp_id="",
        wecom_corp_secret="",
    )
    return Settings(**{**values, **overrides})


def test_integration_flags_default_to_enabled_for_existing_deployments() -> None:
    settings = Settings(_env_file=None)
    assert settings.wecom_enabled is True
    assert settings.password_reset_email_enabled is True


def test_production_api_can_disable_unconfigured_wecom_and_email() -> None:
    settings = _api_settings()
    assert validate_runtime_settings(settings, process="api") is settings


@pytest.mark.parametrize("url", [
    "file:///auth", "http://user:password@runner:8090", "http://runner?token=x",
    "http://runner/path", "http://runner#fragment", "http://runner:bad",
])
def test_remote_codex_requires_a_safe_origin(url):
    with pytest.raises(ValueError, match="CODEX_RUNNER_URL"):
        validate_runtime_settings(
            _api_settings(codex_runner_url=url, codex_runner_token="F" * 64), process="api"
        )


def test_remote_codex_requires_separate_authentication_without_api_model_key():
    with pytest.raises(ValueError, match="CODEX_RUNNER_TOKEN"):
        validate_runtime_settings(
            _api_settings(codex_runner_url="http://codex-runner:8090"), process="api"
        )
    settings = _api_settings(
        codex_runner_url="http://codex-runner:8090", codex_runner_token="F" * 64
    )
    assert validate_runtime_settings(settings, process="api") is settings
    assert "F" * 64 not in repr(settings)


@pytest.mark.parametrize("value", ["", "  "])
def test_blank_codex_models_from_env_templates_use_account_default(monkeypatch, value):
    monkeypatch.setenv("CODEX_MODEL", value)
    monkeypatch.setenv("DAILY_RESEARCH_MODEL", value)
    settings = _api_settings()
    assert settings.codex_model is None
    assert settings.daily_research_model is None


@pytest.mark.parametrize(
    "field,name",
    [
        ("jwt_secret", "JWT_SECRET"),
        ("refresh_token_hmac_key", "REFRESH_TOKEN_HMAC_KEY"),
        ("password_reset_hmac_key", "PASSWORD_RESET_HMAC_KEY"),
        ("binding_hmac_key", "BINDING_HMAC_KEY"),
        ("routing_encryption_key", "ROUTING_ENCRYPTION_KEY"),
    ],
)
def test_disabled_integrations_still_require_core_secrets(field, name) -> None:
    with pytest.raises(ValueError, match=name):
        validate_runtime_settings(_api_settings(**{field: ""}), process="api")


@pytest.mark.parametrize(
    "field",
    [
        "refresh_token_hmac_key",
        "password_reset_hmac_key",
        "binding_hmac_key",
        "routing_encryption_key",
    ],
)
def test_disabled_integrations_still_require_independent_secrets(field) -> None:
    with pytest.raises(ValueError, match="must be independent"):
        validate_runtime_settings(_api_settings(**{field: "A" * 40}), process="api")


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"auth_dev_mode": True}, "AUTH_DEV_MODE"),
        ({"web_base_url": "http://wealthsail.example.test"}, "HTTPS"),
        ({"cors_origins": ["*"]}, "CORS_ORIGINS"),
        (
            {"worker_database_url": "postgresql://worker@postgres/wealthsail"},
            "WORKER_DATABASE_URL",
        ),
        ({"wecom_corp_secret": "sending-secret"}, "Worker/model"),
    ],
)
def test_disabled_integrations_keep_other_production_api_guards(
    overrides, message
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_runtime_settings(_api_settings(**overrides), process="api")


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"wecom_enabled": True}, "WECOM_CALLBACK_TOKEN"),
        (
            {"wecom_enabled": True, "wecom_callback_token": "F" * 24},
            "WECOM_CALLBACK_AES_KEY",
        ),
        (
            {
                "wecom_enabled": True,
                "wecom_callback_token": "F" * 24,
                "wecom_callback_aes_key": "G" * 43,
            },
            "WECOM_CORP_ID",
        ),
        ({"password_reset_email_enabled": True}, "SMTP_HOST"),
        (
            {
                "password_reset_email_enabled": True,
                "smtp_host": "smtp.example.test",
                "smtp_from": "security@example.test",
                "smtp_starttls": False,
            },
            "SMTP_STARTTLS",
        ),
        (
            {
                "password_reset_email_enabled": True,
                "smtp_host": "smtp.example.test",
                "smtp_from": "security@example.test",
                "smtp_username": "user",
                "smtp_password": None,
            },
            "SMTP_PASSWORD",
        ),
    ],
)
def test_enabled_integrations_still_require_their_configuration(
    overrides, message
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_runtime_settings(_api_settings(**overrides), process="api")


def test_disabled_wecom_allows_worker_without_sending_credentials() -> None:
    settings = _worker_settings()
    assert validate_runtime_settings(settings, process="worker") is settings


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"wecom_enabled": True}, "WECOM_CORP_ID"),
        ({"routing_encryption_key": ""}, "ROUTING_ENCRYPTION_KEY"),
        ({"jwt_secret": "A" * 40}, "JWT_SECRET"),
        ({"database_url": "postgresql://api@postgres/wealthsail"}, "DATABASE_URL"),
        ({"worker_database_pool_mode": "transaction"}, "transaction pooling"),
    ],
)
def test_disabled_wecom_keeps_worker_authority_and_pooling_guards(
    overrides, message
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_runtime_settings(_worker_settings(**overrides), process="worker")


@pytest.mark.asyncio
async def test_disabled_wecom_does_not_claim_or_modify_outbox(monkeypatch) -> None:
    stale = AsyncMock(return_value=0)
    claim = AsyncMock(return_value=[])
    monkeypatch.setattr(outbox, "mark_stale_sending_unknown", stale)
    monkeypatch.setattr(outbox, "claim_batch", claim)

    assert await outbox.deliver_batch(object(), _worker_settings()) == 0
    stale.assert_not_awaited()
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_wecom_rejects_direct_send_before_network(monkeypatch) -> None:
    send = AsyncMock(return_value="must-not-send")
    monkeypatch.setattr(wecom, "_send_app_message", send)
    with pytest.raises(wecom.DeliveryRejectedError, match="disabled"):
        await wecom.send_message(_worker_settings(), "member-1", "hello")
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_wecom_rejects_access_to_previously_cached_token(
    monkeypatch,
) -> None:
    settings = _worker_settings()
    monkeypatch.setattr(
        wecom,
        "_token_cache",
        {wecom._credential_key(settings): ("cached-token", float("inf"))},
    )
    with pytest.raises(wecom.DeliveryRejectedError, match="disabled"):
        await wecom.get_access_token(settings)


@pytest.mark.asyncio
async def test_disabled_wecom_callback_get_skips_verification(monkeypatch) -> None:
    check = Mock(side_effect=AssertionError("disabled callback must not authenticate"))
    monkeypatch.setattr(wecom, "_check_timestamp", check)
    response = await wecom.wecom_url_verify(settings=_api_settings())
    assert response.status_code == 503
    check.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_wecom_callback_post_skips_body_decrypt_and_database(
    monkeypatch,
) -> None:
    request = Mock(spec=Request)
    request.body = AsyncMock(
        side_effect=AssertionError("disabled callback must not read body")
    )
    claim = AsyncMock(side_effect=AssertionError("disabled callback must not touch DB"))
    monkeypatch.setattr(wecom, "_claim_callback_once", claim)
    response = await wecom.wecom_callback(
        request, pool=object(), settings=_api_settings()
    )
    assert response.status_code == 503
    request.body.assert_not_awaited()
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_wecom_rejects_binding_before_creating_challenge(
    monkeypatch,
) -> None:
    factory = Mock(side_effect=AssertionError("disabled binding must not enter domain"))
    monkeypatch.setattr(identity_api, "identity_service", factory)
    with pytest.raises(DataUnavailableError, match="企业微信"):
        await identity_api.wecom_binding(
            identity_api.WeComBindingRequest(),
            pool=object(),
            ctx=TenantContext("user-1", "tenant-1", tenant_role="OWNER"),
            settings=_api_settings(),
            request_id="disabled-binding",
        )
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_reset_email_rejects_request_before_generating_token(
    monkeypatch,
) -> None:
    factory = Mock(side_effect=AssertionError("disabled reset must not create token"))
    monkeypatch.setattr(identity_api, "identity_service", factory)
    background = BackgroundTasks()
    with pytest.raises(DataUnavailableError, match="密码重置"):
        await identity_api.request_password_reset(
            identity_api.PasswordResetRequest(email="user@example.test"),
            background,
            pool=object(),
            settings=_api_settings(),
            request_id="disabled-reset",
        )
    assert not background.tasks
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_integrations_preserve_login_and_reset_confirmation(
    monkeypatch,
) -> None:
    identity = SimpleNamespace(
        login=AsyncMock(
            return_value={"accessToken": "access", "_refreshToken": "refresh"}
        ),
        reset_password=AsyncMock(return_value={"status": "reset"}),
    )
    monkeypatch.setattr(identity_api, "identity_service", lambda *_args: identity)
    response = Response()
    result = await identity_api.login(
        identity_api.LoginRequest(email="user@example.test", password="valid-password"),
        response,
        pool=object(),
        settings=_api_settings(),
        request_id="normal-login",
    )
    assert result["data"] == {"accessToken": "access"}
    assert "Secure" in response.headers["set-cookie"]
    reset = await identity_api.confirm_password_reset(
        identity_api.PasswordResetConfirmRequest(
            token="existing-reset-token", newPassword="new-password"
        ),
        pool=object(),
        settings=_api_settings(),
        request_id="existing-reset",
    )
    assert reset["data"] == {"status": "reset"}
    identity.login.assert_awaited_once()
    identity.reset_password.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_wecom_blocks_configured_group_webhook() -> None:
    settings = _worker_settings(
        wecom_opportunity_webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only"
    )
    response = SimpleNamespace(status_code=200, json=lambda: {"errcode": 0})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    with pytest.raises(wecom.DeliveryRejectedError, match="disabled"):
        await wecom_bot.send_opportunity_webhook(settings, "hello", client=client)
    client.post.assert_not_awaited()
