"""Identity service: login, one-time binding challenges, profile & memberships.

Login validates credentials and mints a Supabase-compatible JWT (HS256). The
binding challenge follows docs/10 §3.3: a 128-bit CSPRNG token is shown to the
user once, only its salted HMAC digest is persisted, and consumption is atomic
via the SECURITY DEFINER helper ``identity.consume_binding_challenge``.
"""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import timedelta
from io import BytesIO
import os
from pathlib import Path

import asyncpg
from PIL import Image, ImageOps, UnidentifiedImageError

from backend.common.auth import (
    create_access_token,
    hash_password,
    verify_password,
)
from backend.common.database import (
    Pool,
    api_system_transaction,
    tenant_transaction,
)
from backend.common.security import (
    envelope_encrypt,
    generate_binding_code,
    generate_token,
    hmac_digest,
)
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import now_shanghai, write_audit
from backend.errors import (
    AuthRequiredError,
    ForbiddenError,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError_,
)

_BINDING_CHANNEL = "wecom"


def credentials_are_valid(
    password: str, password_hash: str | None, *, auth_dev_mode: bool
) -> bool:
    """Validate a password, with a narrow opt-in for passwordless dev seeds.

    ``AUTH_DEV_MODE`` is rejected during production startup.  The fallback is
    deliberately limited to rows that do not have a password hash: once a
    password has been configured, even a development instance must verify it.
    """
    if verify_password(password, password_hash):
        return True
    return bool(auth_dev_mode and password and not password_hash)


def refresh_token_hash(settings: Settings, token: str) -> str:
    """Return the non-reversible database representation of a refresh token."""
    return hmac_digest(settings.refresh_token_hmac_key, token)


async def _create_refresh_session(
    pool: Pool,
    settings: Settings,
    *,
    user_id: str,
    tenant_id: str,
) -> str:
    token = generate_token(32)
    expires_at = now_shanghai() + timedelta(days=settings.refresh_token_ttl_days)
    async with api_system_transaction(pool) as conn:
        await conn.execute(
            """
            INSERT INTO identity.refresh_sessions
                (user_id, tenant_id, token_hash, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            user_id,
            tenant_id,
            refresh_token_hash(settings, token),
            expires_at,
        )
    return token


def _login_response(row, settings: Settings, tenant_id: str | None = None) -> dict:
    active_tenant = tenant_id or str(row["tenant_id"])
    token, expires_in = create_access_token(
        user_id=str(row["user_id"]),
        tenant_id=active_tenant,
        email=row["email"],
        nickname=row["nickname"],
        tenant_role=row["tenant_role"],
        jwt_secret=settings.jwt_secret,
        ttl_seconds=settings.access_token_ttl_seconds,
        algorithm=settings.jwt_algorithm,
    )
    return {
        "accessToken": token,
        "tokenType": "bearer",
        "expiresIn": expires_in,
        "user": {
            "id": str(row["user_id"]),
            "email": row["email"],
            "nickname": row["nickname"],
            "tenantId": active_tenant,
            "tenantName": row["tenant_name"],
            "role": row["tenant_role"],
        },
    }


def protect_routing_value(settings: Settings, external_user_id: str) -> tuple[str, str]:
    """Return the lookup HMAC and Worker-decryptable routing ciphertext."""
    return (
        hmac_digest(settings.binding_hmac_key, external_user_id),
        envelope_encrypt(settings.routing_encryption_key, external_user_id),
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
async def load_login_identity(conn: asyncpg.Connection, email: str):
    """Load login material through the private API database role.

    The query is intentionally not exposed as a SECURITY DEFINER RPC.  Its
    column grants belong only to ``wealthsail_api``; browser/``authenticated``
    roles cannot request password hashes for arbitrary email addresses.
    """
    return await conn.fetch(
        """
        SELECT p.id AS user_id, p.email, p.nickname, p.password_hash,
               tm.tenant_id, t.name AS tenant_name, tm.role AS tenant_role
        FROM identity.profiles p
        JOIN identity.tenant_members tm
          ON tm.user_id = p.id AND tm.status = 'ACTIVE'
        JOIN identity.tenants t
          ON t.id = tm.tenant_id AND t.status = 'ACTIVE'
        WHERE lower(p.email) = lower($1)
        ORDER BY tm.created_at ASC
        """,
        email,
    )


async def login(
    pool: Pool,
    settings: Settings,
    *,
    email: str,
    password: str | None,
) -> dict:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValidationError_(
            "邮箱格式不正确", field_errors=[{"field": "email", "message": email}]
        )
    if not password:
        raise ValidationError_(
            "缺少登录口令", field_errors=[{"field": "password", "message": "必填"}]
        )

    async with api_system_transaction(pool) as conn:
        rows = await load_login_identity(conn, email)

    if not rows:
        raise AuthRequiredError("邮箱或口令不正确")

    row = rows[0]
    if not credentials_are_valid(
        password, row["password_hash"], auth_dev_mode=settings.auth_dev_mode
    ):
        # 用户存在但口令错误 → 同样按认证失败处理（不泄露账号是否存在）
        raise AuthRequiredError("邮箱或口令不正确")

    # Active-tenant resolution: prefer the first active membership (a client may
    # later switch via X-Tenant-Id, which RLS re-validates on every query).
    user_id = str(row["user_id"])
    tenant_id = str(row["tenant_id"])

    async with tenant_transaction(pool, user_id, tenant_id) as conn:
        await write_audit(
            conn,
            tenant_id=tenant_id,
            actor_type="USER",
            actor_id=user_id,
            action="SESSION_LOGIN",
            resource_type="identity.session",
            resource_id=tenant_id,
            details={"method": "password"},
        )

    result = _login_response(row, settings)
    result["_refreshToken"] = await _create_refresh_session(
        pool, settings, user_id=user_id, tenant_id=tenant_id
    )
    return result


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
async def register(
    pool: Pool,
    settings: Settings,
    *,
    email: str,
    password: str,
    invitation_token: str,
    nickname: str | None = None,
) -> dict:
    """Accept a one-time, tenant-bound invitation and create the pilot user.

    Public tenant creation is intentionally absent from the MVP.  The database
    helper validates token digest, email, expiry and one-time status atomically.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise ValidationError_(
            "邮箱格式不正确", field_errors=[{"field": "email", "message": email}]
        )
    if not password or len(password) < 8:
        raise ValidationError_(
            "口令至少 8 个字符", field_errors=[{"field": "password", "message": "至少 8 个字符"}]
        )
    if not invitation_token or len(invitation_token.strip()) < 16:
        raise ValidationError_(
            "邀请令牌无效",
            field_errors=[{"field": "invitationToken", "message": "必填"}],
        )
    display_name = (nickname or "").strip() or email.split("@")[0]
    invitation_hash = hmac_digest(
        settings.binding_hmac_key, invitation_token.strip()
    )
    async with api_system_transaction(pool) as conn:
        try:
            row = await conn.fetchrow(
                "SELECT * FROM identity.accept_invitation($1, $2, $3, $4)",
                invitation_hash,
                email,
                display_name,
                hash_password(password),
            )
        except asyncpg.PostgresError as exc:
            message = str(exc)
            if "INVITATION_INVALID" in message:
                raise NotFoundError("邀请令牌无效、已使用或已过期") from exc
            if "EMAIL_ALREADY_REGISTERED" in message:
                raise IdempotencyConflictError("该邮箱已注册，请直接登录") from exc
            raise

    if row is None:
        raise NotFoundError("邀请令牌无效、已使用或已过期")
    user_id = str(row["out_user_id"])
    tenant_id = str(row["out_tenant_id"])

    return {
        "userId": user_id,
        "email": email,
        "nickname": display_name,
        "tenantId": tenant_id,
        "tenantName": row["out_tenant_name"],
    }


async def create_invitation(
    pool: Pool,
    settings: Settings,
    ctx: TenantContext,
    *,
    email: str,
    role: str,
) -> dict:
    """Create a one-time invite for an existing pilot tenant (OWNER/ADMIN only)."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValidationError_("邮箱格式不正确")
    if role not in {"ADMIN", "EDITOR", "VIEWER"}:
        raise ValidationError_("邀请角色无效")
    if ctx.tenant_role not in {"OWNER", "ADMIN"}:
        raise ForbiddenError("只有所有者或管理员可以邀请成员")

    token = generate_token(32)
    token_hash = hmac_digest(settings.binding_hmac_key, token)
    expires_at = now_shanghai() + timedelta(days=7)
    async with api_system_transaction(pool) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM identity.create_invitation($1, $2, $3, $4, $5, $6)",
            ctx.user_id,
            ctx.tenant_id,
            email,
            role,
            token_hash,
            expires_at,
        )
    if row is None:
        raise ForbiddenError("邀请创建失败")
    return {
        "invitationId": str(row["out_invitation_id"]),
        "token": token,
        "email": email,
        "role": role,
        "expiresAt": expires_at,
    }


async def refresh_session(pool: Pool, settings: Settings, refresh_token: str) -> dict:
    """Rotate a refresh token and mint access claims from current membership."""
    if not refresh_token:
        raise AuthRequiredError("刷新会话不存在")
    old_hash = refresh_token_hash(settings, refresh_token)
    new_token = generate_token(32)
    new_hash = refresh_token_hash(settings, new_token)
    expires_at = now_shanghai() + timedelta(days=settings.refresh_token_ttl_days)

    async with api_system_transaction(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT rs.id AS refresh_session_id, rs.user_id, rs.tenant_id,
                       p.email, p.nickname, t.name AS tenant_name,
                       tm.role AS tenant_role
                FROM identity.refresh_sessions rs
                JOIN identity.profiles p ON p.id = rs.user_id
                JOIN identity.tenants t ON t.id = rs.tenant_id AND t.status = 'ACTIVE'
                JOIN identity.tenant_members tm
                  ON tm.user_id = rs.user_id AND tm.tenant_id = rs.tenant_id
                 AND tm.status = 'ACTIVE'
                WHERE rs.token_hash = $1 AND rs.revoked_at IS NULL
                  AND rs.expires_at > now()
                FOR UPDATE OF rs
                """,
                old_hash,
            )
            if row is None:
                raise AuthRequiredError("刷新会话无效或已过期")
            await conn.execute(
                "UPDATE identity.refresh_sessions SET revoked_at=now(), rotated_at=now() WHERE id=$1",
                row["refresh_session_id"],
            )
            await conn.execute(
                """
                INSERT INTO identity.refresh_sessions
                    (user_id, tenant_id, token_hash, expires_at, parent_session_id)
                VALUES ($1, $2, $3, $4, $5)
                """,
                row["user_id"],
                row["tenant_id"],
                new_hash,
                expires_at,
                row["refresh_session_id"],
            )
    result = _login_response(row, settings)
    result["_refreshToken"] = new_token
    return result


async def revoke_refresh_session(
    pool: Pool, settings: Settings, refresh_token: str | None
) -> None:
    if not refresh_token:
        return
    async with api_system_transaction(pool) as conn:
        await conn.execute(
            """
            UPDATE identity.refresh_sessions
               SET revoked_at = COALESCE(revoked_at, now())
             WHERE token_hash = $1
            """,
            refresh_token_hash(settings, refresh_token),
        )


async def switch_tenant(
    pool: Pool,
    settings: Settings,
    ctx: TenantContext,
    *,
    tenant_id: str,
    refresh_token: str | None,
) -> dict:
    """Issue a new tenant-bound token after re-reading active membership."""
    async with api_system_transaction(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT p.id AS user_id, p.email, p.nickname,
                   tm.tenant_id, tm.role AS tenant_role, t.name AS tenant_name
            FROM identity.profiles p
            JOIN identity.tenant_members tm ON tm.user_id=p.id AND tm.status='ACTIVE'
            JOIN identity.tenants t ON t.id=tm.tenant_id AND t.status='ACTIVE'
            WHERE p.id=$1 AND tm.tenant_id=$2
            """,
            ctx.user_id,
            tenant_id,
        )
        if row is None:
            raise ForbiddenError("目标租户成员关系不存在或已停用")
        if refresh_token:
            updated = await conn.execute(
                """
                UPDATE identity.refresh_sessions
                   SET tenant_id=$1
                 WHERE token_hash=$2 AND user_id=$3
                   AND revoked_at IS NULL AND expires_at > now()
                """,
                tenant_id,
                refresh_token_hash(settings, refresh_token),
                ctx.user_id,
            )
            if updated.endswith("0"):
                raise AuthRequiredError("刷新会话无效，请重新登录")
    return _login_response(row, settings)


# ---------------------------------------------------------------------------
# WeCom binding challenges
# ---------------------------------------------------------------------------
async def create_binding_challenge(
    pool: Pool,
    ctx: TenantContext,
    settings: Settings,
) -> dict:
    """Create a one-time binding challenge for the authenticated user.

    Returns the raw token exactly once; only its HMAC digest is persisted.
    """
    token = generate_binding_code()
    token_hash = hmac_digest(settings.binding_hmac_key, token)
    expires_at = now_shanghai() + timedelta(seconds=settings.binding_token_ttl_seconds)

    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        # Expire dangling PENDING challenges for this user+channel first.
        await conn.execute(
            """
            UPDATE identity.binding_challenges
               SET status = 'EXPIRED', resolved_at = now(),
                   version = version + 1
             WHERE tenant_id = $1 AND user_id = $2
               AND channel_type = $3 AND status = 'PENDING'
            """,
            ctx.tenant_id,
            ctx.user_id,
            _BINDING_CHANNEL,
        )
        challenge_id = await conn.fetchval(
            """
            INSERT INTO identity.binding_challenges
                (tenant_id, user_id, channel_type, token_hash,
                 expires_at, max_attempts, idempotency_key)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            ctx.tenant_id,
            ctx.user_id,
            _BINDING_CHANNEL,
            token_hash,
            expires_at,
            settings.binding_token_max_attempts,
            f"challenge:{ctx.user_id}:{uuid.uuid4().hex}",
        )
        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action="BINDING_CHALLENGE_CREATED",
            resource_type="identity.binding_challenges",
            resource_id=str(challenge_id),
            details={"channel_type": _BINDING_CHANNEL},
        )

    return {
        "token": token,
        "expiresInSeconds": settings.binding_token_ttl_seconds,
        "channelType": _BINDING_CHANNEL,
    }


async def consume_binding_challenge(
    pool: Pool,
    settings: Settings,
    *,
    token: str,
    external_user_id: str,
    external_username: str | None,
) -> dict:
    """Consume a binding challenge for a verified external WeCom identity.

    Called from the WeCom callback ingress. The external user id is stored as
    a salted HMAC hash; raw identifiers never touch the database.
    """
    if not token or not external_user_id:
        raise ValidationError_("绑定令牌或外部用户标识缺失")

    token_hash = hmac_digest(settings.binding_hmac_key, token.strip())
    external_hash, external_ciphertext = protect_routing_value(
        settings, external_user_id
    )

    async with api_system_transaction(pool) as conn:
        try:
            binding = await conn.fetchrow(
                "SELECT * FROM identity.consume_binding_challenge($1, $2, $3, $4)",
                token_hash,
                external_hash,
                external_ciphertext,
                external_username,
            )
        except asyncpg.PostgresError as exc:
            message = str(exc)
            if "BINDING_TOKEN_INVALID" in message:
                raise NotFoundError("绑定口令无效或已过期") from exc
            if "BINDING_TOKEN_EXHAUSTED" in message:
                raise ValidationError_("绑定口令尝试次数超限，请重新生成") from exc
            raise

    if binding is None:
        raise NotFoundError("绑定口令无效或已过期")

    async with tenant_transaction(
        pool, str(binding["out_user_id"]), str(binding["out_tenant_id"])
    ) as conn:
        await write_audit(
            conn,
            tenant_id=str(binding["out_tenant_id"]),
            actor_type="USER",
            actor_id=str(binding["out_user_id"]),
            action="WECOM_BOUND",
            resource_type="identity.channel_bindings",
            resource_id=str(binding["out_binding_id"]),
            details={"channel_type": binding["out_channel_type"]},
        )

    return {
        "bindingId": str(binding["out_binding_id"]),
        "status": binding["out_status"],
        "channelType": binding["out_channel_type"],
        "tenantId": str(binding["out_tenant_id"]),
    }


async def revoke_binding(
    conn: asyncpg.Connection,
    ctx: TenantContext,
    *,
    binding_id: str,
    password: str,
) -> dict:
    """Soft-revoke a same-user/same-tenant binding after password reauth."""
    password_hash = await conn.fetchval(
        "SELECT password_hash FROM identity.profiles WHERE id=$1", ctx.user_id
    )
    if not verify_password(password, password_hash):
        raise AuthRequiredError("重新验证口令失败")
    row = await conn.fetchrow(
        """
        UPDATE identity.channel_bindings
           SET status='REVOKED', revoked_at=now(), version=version+1
         WHERE id=$1 AND tenant_id=$2 AND user_id=$3 AND status='ACTIVE'
        RETURNING id, status, channel_type
        """,
        binding_id,
        ctx.tenant_id,
        ctx.user_id,
    )
    if row is None:
        raise NotFoundError("活动绑定不存在或已撤销")
    await write_audit(
        conn,
        tenant_id=ctx.tenant_id,
        actor_type="USER",
        actor_id=ctx.user_id,
        action="WECOM_BINDING_REVOKED",
        resource_type="identity.channel_bindings",
        resource_id=str(row["id"]),
        details={"channel_type": row["channel_type"]},
    )
    return {"bindingId": str(row["id"]), "status": row["status"]}


async def get_binding_status(
    conn: asyncpg.Connection, ctx: TenantContext, *, channel_type: str = "wecom"
) -> dict:
    """Return the current ACTIVE binding for this user+tenant, if any."""
    binding = await conn.fetchrow(
        """
        SELECT id, channel_type, external_username, status, bound_at, last_used_at
        FROM identity.channel_bindings
        WHERE tenant_id = $1 AND user_id = $2
          AND channel_type = $3 AND status = 'ACTIVE'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        ctx.tenant_id,
        ctx.user_id,
        channel_type,
    )
    if binding is None:
        return {"bound": False, "binding": None}
    return {
        "bound": True,
        "binding": {
            "id": str(binding["id"]),
            "channelType": binding["channel_type"],
            "externalUsername": binding["external_username"],
            "status": binding["status"],
            "boundAt": binding["bound_at"],
            "lastUsedAt": binding["last_used_at"],
        },
    }


async def revoke_current_binding(
    conn: asyncpg.Connection,
    ctx: TenantContext,
    *,
    password: str,
    channel_type: str = "wecom",
) -> dict:
    """Password-reauthenticated soft revoke of the user's ACTIVE binding.

    Unlike ``revoke_binding`` the caller does not need to know the binding id;
    the current ACTIVE binding for this user+tenant+channel is revoked.
    """
    password_hash = await conn.fetchval(
        "SELECT password_hash FROM identity.profiles WHERE id=$1", ctx.user_id
    )
    if not verify_password(password, password_hash):
        raise AuthRequiredError("重新验证口令失败")
    row = await conn.fetchrow(
        """
        UPDATE identity.channel_bindings
           SET status='REVOKED', revoked_at=now(), version=version+1
         WHERE tenant_id=$1 AND user_id=$2 AND channel_type=$3 AND status='ACTIVE'
        RETURNING id, status, channel_type
        """,
        ctx.tenant_id,
        ctx.user_id,
        channel_type,
    )
    if row is None:
        raise NotFoundError("活动绑定不存在或已撤销")
    await write_audit(
        conn,
        tenant_id=ctx.tenant_id,
        actor_type="USER",
        actor_id=ctx.user_id,
        action="WECOM_BINDING_REVOKED",
        resource_type="identity.channel_bindings",
        resource_id=str(row["id"]),
        details={"channel_type": row["channel_type"]},
    )
    return {"bindingId": str(row["id"]), "status": row["status"]}


# ---------------------------------------------------------------------------
# Profile / memberships
# ---------------------------------------------------------------------------
async def get_me(
    conn: asyncpg.Connection, ctx: TenantContext
) -> dict:
    profile = await conn.fetchrow(
        """
        SELECT id, email, nickname, avatar_url, created_at
        FROM identity.profiles
        WHERE id = $1
        """,
        ctx.user_id,
    )
    tenants = await conn.fetch(
        """
        SELECT tm.tenant_id, t.name, tm.role, tm.status
        FROM identity.tenant_members tm
        JOIN identity.tenants t ON t.id = tm.tenant_id
        WHERE tm.user_id = $1 AND tm.status = 'ACTIVE'
        ORDER BY tm.created_at ASC
        """,
        ctx.user_id,
    )
    bindings = await conn.fetch(
        """
        SELECT id, channel_type, external_username, status, bound_at, last_used_at
        FROM identity.channel_bindings
        WHERE tenant_id = $1 AND user_id = $2
        ORDER BY created_at DESC
        """,
        ctx.tenant_id,
        ctx.user_id,
    )
    if profile is None:
        raise NotFoundError("用户资料不存在")

    return {
        "profile": {
            "id": str(profile["id"]),
            "email": profile["email"],
            "nickname": profile["nickname"],
            "avatarUrl": profile["avatar_url"],
            "createdAt": profile["created_at"],
        },
        "activeTenantId": ctx.tenant_id,
        "activeRole": ctx.tenant_role,
        "tenants": [
            {
                "tenantId": str(t["tenant_id"]),
                "name": t["name"],
                "role": t["role"],
                "status": t["status"],
            }
            for t in tenants
        ],
        "bindings": [
            {
                "id": str(b["id"]),
                "channelType": b["channel_type"],
                "externalUsername": b["external_username"],
                "status": b["status"],
                "boundAt": b["bound_at"],
                "lastUsedAt": b["last_used_at"],
            }
            for b in bindings
        ],
    }


# ---------------------------------------------------------------------------
# Profile update / avatar upload
# ---------------------------------------------------------------------------
async def update_profile(
    conn: asyncpg.Connection,
    ctx: TenantContext,
    *,
    nickname: str | None = None,
    avatar_url: str | None = None,
) -> dict:
    """更新当前用户的昵称/头像（RLS 限制为本人行，见 profile_self_update）。"""
    if nickname is None and avatar_url is None:
        raise ValidationError_(
            "没有需要更新的字段",
            field_errors=[{"field": "body", "message": "至少提供 nickname 或 avatar_url 之一"}],
        )

    sets: list[str] = []
    values: list[object] = []
    details: dict[str, str] = {}
    if nickname is not None:
        nickname = nickname.strip()
        if not nickname:
            raise ValidationError_(
                "昵称不能为空", field_errors=[{"field": "nickname", "message": "必填"}]
            )
        sets.append(f"nickname = ${len(values) + 1}")
        values.append(nickname)
        details["nickname"] = nickname
    if avatar_url is not None:
        avatar_url = avatar_url.strip()
        sets.append(f"avatar_url = ${len(values) + 1}")
        values.append(avatar_url)
        details["avatar_url"] = avatar_url

    values.append(ctx.user_id)
    row = await conn.fetchrow(
        f"""
        UPDATE identity.profiles
           SET {", ".join(sets)}, updated_at = now()
         WHERE id = ${len(values)}
         RETURNING id, email, nickname, avatar_url
        """,
        *values,
    )
    if row is None:
        raise NotFoundError("用户资料不存在")

    await write_audit(
        conn,
        tenant_id=ctx.tenant_id,
        actor_type="USER",
        actor_id=ctx.user_id,
        action="PROFILE_UPDATED",
        resource_type="identity.profiles",
        resource_id=str(row["id"]),
        details=details,
    )
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "nickname": row["nickname"],
        "avatarUrl": row["avatar_url"],
    }


# 头像上传约束
AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
AVATAR_MAX_PIXELS = 16_000_000
AVATAR_MAX_DIMENSION = 1024
AVATAR_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


def normalize_avatar_image(data: bytes, content_type: str | None) -> tuple[bytes, str]:
    """Decode, bound and re-encode untrusted image bytes.

    File names, extensions and client MIME values are never trusted. Re-encoding
    strips active/unknown payloads, metadata and animation before persistence.
    """
    if not data:
        raise ValidationError_("文件内容为空")
    if len(data) > AVATAR_MAX_BYTES:
        raise ValidationError_("头像文件不能超过 2 MiB")
    if content_type and not content_type.startswith("image/"):
        raise ValidationError_("文件类型必须为图片")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in AVATAR_ALLOWED_FORMATS:
                raise ValidationError_("不支持的图片编码")
            if source.width * source.height > AVATAR_MAX_PIXELS:
                raise ValidationError_("图片像素数过大")
            source.seek(0)
            frame = ImageOps.exif_transpose(source.convert("RGBA" if source.mode in {"RGBA", "LA"} else "RGB"))
            frame.thumbnail((AVATAR_MAX_DIMENSION, AVATAR_MAX_DIMENSION))
            output = BytesIO()
            frame.save(output, format="WEBP", quality=88, method=6)
    except ValidationError_:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError_("图片无法解码或内容已损坏") from exc
    encoded = output.getvalue()
    if len(encoded) > AVATAR_MAX_BYTES:
        raise ValidationError_("重编码后的头像仍超过 2 MiB")
    return encoded, "webp"


async def save_avatar(
    pool: Pool,
    ctx: TenantContext,
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> dict:
    """保存头像文件并更新 profiles.avatar_url（同一请求内完成）。"""
    del filename  # untrusted client name is intentionally ignored
    encoded, ext = normalize_avatar_image(data, content_type)

    avatars_dir = Path(__file__).resolve().parent.parent / "static" / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    object_name = f"{ctx.user_id}_{int(time.time())}_{secrets.token_hex(16)}.{ext}"
    final_path = avatars_dir / object_name
    temporary_path = avatars_dir / f".{object_name}.tmp"
    with temporary_path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, final_path)

    avatar_url = f"/static/avatars/{object_name}"
    old_url: str | None = None
    try:
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            old_url = await conn.fetchval(
                "SELECT avatar_url FROM identity.profiles WHERE id=$1", ctx.user_id
            )
            await conn.execute(
                "UPDATE identity.profiles SET avatar_url = $1, updated_at = now() WHERE id = $2",
                avatar_url,
                ctx.user_id,
            )
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="AVATAR_UPDATED",
                resource_type="identity.profiles",
                resource_id=ctx.user_id,
                details={"avatar_url": avatar_url, "bytes": len(encoded)},
            )
    except Exception:
        final_path.unlink(missing_ok=True)
        raise

    prefix = "/static/avatars/"
    if old_url and old_url.startswith(prefix):
        old_name = old_url.removeprefix(prefix)
        old_path = (avatars_dir / old_name).resolve()
        if old_path.parent == avatars_dir.resolve() and old_path != final_path.resolve():
            old_path.unlink(missing_ok=True)
    return {"avatarUrl": avatar_url}
