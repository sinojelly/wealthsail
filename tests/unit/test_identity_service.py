"""backend/identity 领域服务单元测试（假连接断言 SQL 与业务规则，无需真实库）。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.common import auth as jwt_module
from backend.errors import (
    AuthRequiredError,
    IdempotencyConflictError as ConflictError,
    NotFoundError,
    ValidationError_ as ValidationError,
)
from backend.identity.domain_service import (
    IdentityService,
    IdentitySettings,
    TenantContext,
    credentials_are_valid,
    generate_token,
    hmac_digest,
    hash_password,
    verify_password,
)

_KEYS = dict(
    jwt_secret="test-jwt-secret",
    refresh_token_hmac_key="test-refresh-hmac",
    binding_hmac_key="test-binding-hmac",
    password_reset_hmac_key="test-password-reset-hmac",
)


def _settings(**overrides) -> IdentitySettings:
    return IdentitySettings(**_KEYS, **overrides)


# ---------------------------------------------------------------------------
# 假连接 / 假池（记录 SQL 与参数）
# ---------------------------------------------------------------------------
class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._fetchrow: dict | None = None
        self._fetch: list[dict] | None = None
        self._fetchval: object = None
        self.execute_result = "UPDATE 1"
        self.transaction_entries = 0
        self.transaction_exits = 0

    async def fetchrow(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        return self._fetchrow

    async def fetch(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        return self._fetch or []

    async def fetchval(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        return self._fetchval

    async def execute(self, sql, *args) -> Any:
        self.calls.append((sql, args))
        return self.execute_result

    def transaction(self):
        class _Tx:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                self._conn.transaction_entries += 1
                return self._conn

            async def __aexit__(self, *_args):
                self._conn.transaction_exits += 1
                return None

        return _Tx(self)


class FakePool:
    def __init__(self, conn: FakeConn | None = None) -> None:
        self.conn = conn or FakeConn()

    def acquire(self):
        class _Acquire:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *_args):
                return None

        return _Acquire(self.conn)


def _service(conn: FakeConn | None = None, **settings_overrides) -> IdentityService:
    return IdentityService(
        FakePool(conn) if conn is not None else FakePool(),
        _settings(**settings_overrides),
    )


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_register_calls_accept_invitation_with_hashed_password() -> None:
    conn = FakeConn()
    conn._fetchrow = {
        "out_user_id": "user-1",
        "out_tenant_id": "tenant-1",
        "out_tenant_name": "试点家庭",
    }
    service = _service(conn)

    result = await service.register(
        email="  PILOT@Example.test ",
        password="correct horse battery staple",
        nickname="Pilot",
        invitation_token="one-time-invitation-token",
    )

    rpc_call = next(c for c in conn.calls if "accept_invitation" in c[0])
    assert rpc_call[1][1] == "pilot@example.test"  # 邮箱小写归一化
    assert rpc_call[1][0] != "one-time-invitation-token"  # 明文只存摘要
    assert rpc_call[1][0] == hmac_digest(_KEYS["binding_hmac_key"], "one-time-invitation-token")
    assert rpc_call[1][3] != "correct horse battery staple"  # 密码绝不落明文
    assert verify_password("correct horse battery staple", rpc_call[1][3])
    assert result["tenantId"] == "tenant-1"
    assert result["tenantName"] == "试点家庭"


@pytest.mark.asyncio
async def test_register_rejects_weak_password_and_short_token() -> None:
    service = _service()
    with pytest.raises(ValidationError):
        await service.register(
            email="a@b.com", password="short", invitation_token="x" * 16
        )
    with pytest.raises(ValidationError):
        await service.register(
            email="a@b.com", password="long-enough-password", invitation_token="short"
        )
    with pytest.raises(ValidationError):
        await service.register(
            email="not-an-email", password="long-enough-password", invitation_token="x" * 16
        )


@pytest.mark.asyncio
async def test_register_maps_db_errors() -> None:
    import asyncpg

    class _Exc(asyncpg.PostgresError):
        def __init__(self, message: str):
            super().__init__(message)

    conn = FakeConn()

    async def _raise_invalid(sql, *args):
        raise _Exc("INVITATION_INVALID")

    conn.fetchrow = _raise_invalid
    service = _service(conn)
    with pytest.raises(NotFoundError):
        await service.register(
            email="a@b.com", password="long-enough-password", invitation_token="x" * 16
        )

    async def _raise_dup(sql, *args):
        raise _Exc("EMAIL_ALREADY_REGISTERED")

    conn.fetchrow = _raise_dup
    with pytest.raises(ConflictError):
        await service.register(
            email="a@b.com", password="long-enough-password", invitation_token="x" * 16
        )


# ---------------------------------------------------------------------------
# 登录 / 凭据校验
# ---------------------------------------------------------------------------
def test_password_hash_roundtrip_and_dev_mode_is_disabled() -> None:
    hashed = hash_password("demo1234")
    assert verify_password("demo1234", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("demo1234", None)

    # dev 模式只放行“确实没有哈希”的种子用户，且必须显式开启
    assert not credentials_are_valid("anything", None, auth_dev_mode=True)
    assert not credentials_are_valid("anything", None, auth_dev_mode=False)
    assert not credentials_are_valid("", None, auth_dev_mode=True)
    assert not credentials_are_valid("wrong", hashed, auth_dev_mode=True)


@pytest.mark.asyncio
async def test_login_mints_jwt_and_persists_refresh_hash() -> None:
    conn = FakeConn()
    conn._fetch = [
        {
            "user_id": "user-1",
            "email": "zhangming@example.test",
            "nickname": "张明",
            "password_hash": hash_password("demo1234"),
            "tenant_id": "tenant-1",
            "tenant_name": "家庭组合",
            "tenant_role": "OWNER",
        }
    ]
    service = _service(conn)

    result = await service.login(email="ZHANGMING@example.test", password="demo1234")

    token = result["accessToken"]
    session = jwt_module.decode_access_token(token, _KEYS["jwt_secret"])
    assert session.user_id == "user-1"
    assert session.tenant_id == "tenant-1"
    assert session.tenant_role == "OWNER"

    insert_call = next(
        c for c in conn.calls if "INSERT INTO identity.refresh_sessions" in c[0]
    )
    raw_token = result["_refreshToken"]
    assert insert_call[1][2] != raw_token
    assert insert_call[1][2] == service._refresh_token_hash(raw_token)

    audit = next(c for c in conn.calls if "audit.audit_log" in c[0])
    assert "SESSION_LOGIN" in audit[1]
    assert isinstance(audit[1][7], str)
    assert json.loads(audit[1][7]) == {"method": "password"}


@pytest.mark.asyncio
async def test_login_rejects_bad_credentials_uniformly() -> None:
    conn = FakeConn()
    conn._fetch = [
        {
            "user_id": "user-1",
            "email": "a@example.test",
            "nickname": "A",
            "password_hash": hash_password("demo1234"),
            "tenant_id": "tenant-1",
            "tenant_name": "T",
            "tenant_role": "OWNER",
        }
    ]
    service = _service(conn)
    with pytest.raises(AuthRequiredError):
        await service.login(email="a@example.test", password="wrong-password")

    # 账号不存在与口令错误消息一致（防枚举）
    conn._fetch = []
    with pytest.raises(AuthRequiredError) as excinfo:
        await service.login(email="ghost@example.test", password="whatever-1234")
    assert excinfo.value.message == "邮箱或口令不正确"


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refresh_session_rotates_token() -> None:
    conn = FakeConn()
    conn._fetchrow = {
        "refresh_session_id": "rs-1",
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "email": "a@example.test",
        "nickname": "A",
        "tenant_name": "T",
        "tenant_role": "EDITOR",
    }
    service = _service(conn)

    result = await service.refresh_session("old-refresh-token")

    sqls = [c[0] for c in conn.calls]
    assert any("FOR UPDATE OF rs" in s for s in sqls)
    assert any("rotated_at=now()" in s for s in sqls)
    assert any("parent_session_id" in s for s in sqls)
    assert result["_refreshToken"] != "old-refresh-token"
    assert result["user"]["role"] == "EDITOR"


@pytest.mark.asyncio
async def test_refresh_session_rejects_unknown_token() -> None:
    conn = FakeConn()
    conn._fetchrow = None
    service = _service(conn)
    with pytest.raises(AuthRequiredError):
        await service.refresh_session("stale-token")


@pytest.mark.asyncio
async def test_revoke_refresh_session_is_idempotent() -> None:
    conn = FakeConn()
    service = _service(conn)
    await service.revoke_refresh_session("token-to-revoke")
    await service.revoke_refresh_session(None)  # 空令牌静默成功
    sqls = [c[0] for c in conn.calls]
    assert any("revoked_at" in s for s in sqls)


@pytest.mark.asyncio
async def test_switch_tenant_rebinds_refresh_session() -> None:
    conn = FakeConn()
    conn._fetchrow = {
        "user_id": "user-1",
        "email": "a@example.test",
        "nickname": "A",
        "tenant_id": "tenant-2",
        "tenant_role": "VIEWER",
        "tenant_name": "二号租户",
    }
    conn.execute_result = "UPDATE 1"
    service = _service(conn)
    actor = TenantContext(tenant_id="tenant-1", user_id="user-1", tenant_role="OWNER")

    result = await service.switch_tenant(
        actor, tenant_id="tenant-2", refresh_token="refresh-token"
    )

    update = next(c for c in conn.calls if "SET tenant_id=$1" in c[0])
    assert update[1][0] == "tenant-2"
    assert update[1][1] != "refresh-token"  # 库中只有摘要
    assert result["user"]["tenantId"] == "tenant-2"


# ---------------------------------------------------------------------------
# 企微绑定
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_binding_challenge_returns_token_once_and_expires_dangling() -> None:
    conn = FakeConn()
    conn._fetchval = "challenge-1"
    service = _service(conn)
    actor = TenantContext(tenant_id="tenant-1", user_id="user-1", tenant_role="OWNER")

    result = await service.create_binding_challenge(actor)

    assert len(result["token"]) >= 16
    sqls = [c[0] for c in conn.calls]
    assert any("status = 'EXPIRED'" in s for s in sqls)  # 旧码作废
    insert = next(c for c in conn.calls if "INSERT INTO identity.binding_challenges" in c[0])
    assert insert[1][3] != result["token"]  # 库中只有摘要
    assert insert[1][3] == hmac_digest(_KEYS["binding_hmac_key"], result["token"])


@pytest.mark.asyncio
async def test_consume_binding_challenge_hashes_external_id() -> None:
    conn = FakeConn()
    conn._fetchrow = {
        "out_binding_id": "binding-1",
        "out_tenant_id": "tenant-1",
        "out_user_id": "user-1",
        "out_channel_type": "wecom",
        "out_status": "ACTIVE",
    }
    service = _service(conn)

    result = await service.consume_binding_challenge(
        token="binding-code-token",
        external_user_id="wecom-user-abc",
        external_username="陈国栋",
    )

    call = next(c for c in conn.calls if "consume_binding_challenge" in c[0])
    assert call[1][0] == hmac_digest(_KEYS["binding_hmac_key"], "binding-code-token")
    assert call[1][1] == hmac_digest(_KEYS["binding_hmac_key"], "wecom-user-abc")
    assert call[1][2].startswith("v1:")  # 信封加密密文
    assert call[1][3] == "陈国栋"
    assert result["status"] == "ACTIVE"
    assert result["channelType"] == "wecom"
    assert conn.transaction_entries == 1
    assert conn.transaction_exits == 1
    sqls = [sql for sql, _args in conn.calls]
    assert next(i for i, sql in enumerate(sqls) if "consume_binding_challenge" in sql) < next(
        i for i, sql in enumerate(sqls) if "INSERT INTO audit.audit_log" in sql
    )


@pytest.mark.asyncio
async def test_consume_binding_maps_exhausted_error() -> None:
    import asyncpg

    class _Exc(asyncpg.PostgresError):
        def __init__(self, message: str):
            super().__init__(message)

    conn = FakeConn()

    async def _raise(sql, *args):  # noqa: ANN001
        raise _Exc("BINDING_TOKEN_EXHAUSTED")

    conn.fetchrow = _raise  # type: ignore[method-assign]
    service = _service(conn)
    with pytest.raises(ValidationError):
        await service.consume_binding_challenge(
            token="t", external_user_id="wecom-user-x", external_username=None
        )


@pytest.mark.asyncio
async def test_revoke_binding_requires_password() -> None:
    conn = FakeConn()
    conn._fetchval = hash_password("demo1234")
    conn._fetchrow = {"id": "binding-1", "status": "REVOKED", "channel_type": "wecom"}
    service = _service(conn)
    actor = TenantContext(tenant_id="tenant-1", user_id="user-1", tenant_role="OWNER")

    with pytest.raises(AuthRequiredError):
        await service.revoke_binding(conn, actor, binding_id="b1", password="wrong")  # type: ignore[arg-type]

    result = await service.revoke_binding(conn, actor, binding_id="b1", password="demo1234")  # type: ignore[arg-type]
    assert result["status"] == "REVOKED"
    assert any("REVOKED" in c[0] for c in conn.calls)


# ---------------------------------------------------------------------------
# 资料 / 密码重置
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_profile_validates_and_updates() -> None:
    conn = FakeConn()
    conn._fetchrow = {
        "id": "user-1",
        "email": "a@example.test",
        "nickname": "新昵称",
        "avatar_url": "/static/avatars/x.webp",
    }
    service = _service(conn)
    actor = TenantContext(tenant_id="tenant-1", user_id="user-1", tenant_role="OWNER")

    with pytest.raises(ValidationError):
        await service.update_profile(conn, actor)  # type: ignore[arg-type]

    result = await service.update_profile(conn, actor, nickname=" 新昵称 ")  # type: ignore[arg-type]
    assert result["nickname"] == "新昵称"
    update = next(c for c in conn.calls if "UPDATE identity.profiles" in c[0])
    assert update[1][0] == "新昵称"


@pytest.mark.asyncio
async def test_request_password_reset_is_anti_enumeration() -> None:
    conn = FakeConn()
    values = iter(["user-1", False])

    async def fetchval(sql, *args):
        conn.calls.append((sql, args))
        return next(values)

    conn.fetchval = fetchval
    service = _service(conn)

    result = await service.request_password_reset(email="a@example.test")

    assert result["status"] == "sent"
    assert result["token"] is not None
    insert = next(
        c
        for c in conn.calls
        if "INSERT INTO identity.password_reset_tokens" in c[0]
    )
    assert insert[1][1] != result["token"]  # 只有摘要
    assert insert[1][1] == hmac_digest(
        _KEYS["password_reset_hmac_key"], result["token"]
    )
    # 不存在的邮箱同样返回 sent，但不会创建重置令牌。
    unknown_conn = FakeConn()
    unknown_service = _service(unknown_conn)
    result2 = await unknown_service.request_password_reset(email="ghost@example.test")
    assert result2["status"] == "sent"
    assert result2["token"] is None
    token_calls = [
        c for c in unknown_conn.calls if "password_reset_tokens" in c[0]
    ]
    assert token_calls == []


@pytest.mark.asyncio
async def test_reset_password_invokes_privileged_helper() -> None:
    conn = FakeConn()
    conn._fetchrow = {"out_user_id": "user-1", "out_email": "a@example.test"}
    service = _service(conn)

    result = await service.reset_password(token="reset-token", new_password="new-pass-1234")

    call = next(c for c in conn.calls if "identity.reset_password" in c[0])
    assert call[1][0] == hmac_digest(
        _KEYS["password_reset_hmac_key"], "reset-token"
    )
    assert call[1][1] != "new-pass-1234"
    assert verify_password("new-pass-1234", call[1][1])
    assert result["email"] == "a@example.test"


# ---------------------------------------------------------------------------
# JWT 模块
# ---------------------------------------------------------------------------
def test_jwt_roundtrip_and_compat_payload() -> None:
    token, expires_in = jwt_module.create_access_token(
        user_id="user-1",
        tenant_id="tenant-1",
        email="a@example.test",
        nickname="A",
        tenant_role="OWNER",
        jwt_secret=_KEYS["jwt_secret"],
        ttl_seconds=3600,
    )
    assert expires_in == 3600
    session = jwt_module.decode_access_token(token, _KEYS["jwt_secret"])
    assert session.user_id == "user-1"
    assert session.tenant_id == "tenant-1"
    assert session.tenant_role == "OWNER"
    assert session.email == "a@example.test"


def test_jwt_tampered_or_expired_token_rejected() -> None:
    token, _ = jwt_module.create_access_token(
        user_id="user-1",
        tenant_id="tenant-1",
        email=None,
        nickname=None,
        tenant_role="VIEWER",
        jwt_secret=_KEYS["jwt_secret"],
        ttl_seconds=3600,
    )
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(AuthRequiredError):
        jwt_module.decode_access_token(tampered, _KEYS["jwt_secret"])
    with pytest.raises(AuthRequiredError):
        jwt_module.decode_access_token(token, "wrong-secret")

    expired, _ = jwt_module.create_access_token(
        user_id="user-1",
        tenant_id="tenant-1",
        email=None,
        nickname=None,
        tenant_role="VIEWER",
        jwt_secret=_KEYS["jwt_secret"],
        ttl_seconds=-10,
    )
    with pytest.raises(AuthRequiredError):
        jwt_module.decode_access_token(expired, _KEYS["jwt_secret"])


def test_generate_token_is_grouped_and_high_entropy() -> None:
    tokens = {generate_token(16) for _ in range(50)}
    assert len(tokens) == 50
    first = tokens.pop()
    assert first.startswith("WS-")
    assert "-" in first
    assert "=" not in first  # base32 无填充
