from __future__ import annotations

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from backend.common.auth import create_access_token
from backend.config import Settings
from backend.dependencies import get_auth_session
from backend.errors import AuthRequiredError


class _Context:
    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None

    def __init__(self, value):
        self.value = value


class _Conn:
    def __init__(self, auth_version: int) -> None:
        self.auth_version = auth_version

    def transaction(self):
        return _Context(self)

    async def execute(self, *_args):
        return "OK"

    async def fetchval(self, *_args):
        return self.auth_version


class _Pool:
    def __init__(self, auth_version: int) -> None:
        self.conn = _Conn(auth_version)

    def acquire(self):
        return _Context(self.conn)


def _request(auth_version: int) -> Request:
    settings = Settings(_env_file=None, jwt_secret="test-secret")
    app = type("App", (), {})()
    app.state = type("State", (), {})()
    app.state.settings = settings
    app.state.db_pool = _Pool(auth_version)
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "app": app})


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_access_token_version_must_match_current_profile() -> None:
    token, _ = create_access_token(
        user_id="user-1",
        tenant_id="tenant-1",
        email="user@example.test",
        nickname="User",
        tenant_role="OWNER",
        auth_version=3,
        jwt_secret="test-secret",
        ttl_seconds=3600,
    )

    session = await get_auth_session(_request(3), _credentials(token))
    assert session.user_id == "user-1"

    with pytest.raises(AuthRequiredError, match="撤销"):
        await get_auth_session(_request(4), _credentials(token))
