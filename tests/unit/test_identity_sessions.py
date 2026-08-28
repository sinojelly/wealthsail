"""Closed-pilot invitation and refresh-cookie regression tests."""

from __future__ import annotations

import pytest
from fastapi import Response

from backend.config import Settings
from backend.identity.api import _set_refresh_cookie
from backend.identity.service import credentials_are_valid, refresh_token_hash, register


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
async def test_registration_accepts_only_database_validated_invitation() -> None:
    class Conn:
        def __init__(self):
            self.sql = ""
            self.args = ()

        async def fetchrow(self, sql, *args):
            self.sql, self.args = sql, args
            return {
                "out_user_id": "user-1",
                "out_tenant_id": "tenant-1",
                "out_tenant_name": "试点家庭",
            }

        async def execute(self, sql, *args):
            self.sql, self.args = sql, args
            return "OK"

        def transaction(self):
            class _Tx:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return None

            return _Tx()

    conn = Conn()
    settings = Settings(
        _env_file=None,
        worker_database_url="must-not-be-used-by-api",
        binding_hmac_key="invite-hmac-key",
    )
    result = await register(
        _Pool(conn),
        settings,
        email="PILOT@example.test",
        password="correct horse battery staple",
        nickname="Pilot",
        invitation_token="one-time-invitation-token",
    )

    assert "identity.accept_invitation" in conn.sql
    assert conn.args[1] == "pilot@example.test"
    assert conn.args[0] != "one-time-invitation-token"
    assert result["tenantId"] == "tenant-1"


def test_refresh_token_is_hmac_only_and_cookie_is_http_only_strict() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        refresh_token_hmac_key="production-refresh-hmac-key",
        refresh_cookie_name="wealthsail_refresh",
    )
    digest = refresh_token_hash(settings, "raw-refresh-token")
    assert digest != "raw-refresh-token"
    assert "raw-refresh-token" not in digest

    response = Response()
    _set_refresh_cookie(response, settings, "raw-refresh-token")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api/v1/auth" in cookie


def test_passwordless_seed_accounts_are_always_rejected() -> None:
    """No runtime mode may turn a passwordless seed into an auth bypass."""
    assert not credentials_are_valid("any-non-empty-dev-password", None, auth_dev_mode=True)
    assert not credentials_are_valid("any-non-empty-dev-password", None, auth_dev_mode=False)
    assert not credentials_are_valid("", None, auth_dev_mode=True)
