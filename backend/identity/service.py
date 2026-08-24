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
from pathlib import Path

import asyncpg

from backend.common.auth import (
    create_access_token,
    hash_password,
    verify_password,
)
from backend.common.database import Pool, system_connection, tenant_transaction
from backend.common.security import generate_token, hmac_digest
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import now_shanghai, write_audit
from backend.errors import (
    AuthRequiredError,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError_,
)

_BINDING_CHANNEL = "wecom"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
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

    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            "SELECT * FROM identity.authenticate_credentials($1)", email
        )

    if not rows:
        raise AuthRequiredError("邮箱或口令不正确")

    row = rows[0]
    if not verify_password(password, row["password_hash"]):
        # 用户存在但口令错误 → 同样按认证失败处理（不泄露账号是否存在）
        raise AuthRequiredError("邮箱或口令不正确")

    # Active-tenant resolution: prefer the first active membership (a client may
    # later switch via X-Tenant-Id, which RLS re-validates on every query).
    user_id = str(row["user_id"])
    tenant_id = str(row["tenant_id"])
    nickname = row["nickname"]
    tenant_role = row["tenant_role"]

    token, expires_in = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        email=email,
        nickname=nickname,
        tenant_role=tenant_role,
        jwt_secret=settings.jwt_secret,
        ttl_seconds=settings.access_token_ttl_seconds,
        algorithm=settings.jwt_algorithm,
    )

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

    return {
        "accessToken": token,
        "tokenType": "bearer",
        "expiresIn": expires_in,
        "user": {
            "id": user_id,
            "email": email,
            "nickname": nickname,
            "tenantId": tenant_id,
            "tenantName": row["tenant_name"],
            "role": tenant_role,
        },
    }


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
async def register(
    pool: Pool,
    settings: Settings,
    *,
    email: str,
    password: str,
    nickname: str | None = None,
) -> dict:
    """Create a local user with a bcrypt password hash and a default tenant.

    Idempotency: a second registration with the same email is rejected with
    IDEMPOTENCY_CONFLICT (409). The new tenant gets one OWNER membership and a
    default investment account so the workbench renders immediately.
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
    display_name = (nickname or "").strip() or email.split("@")[0]

    # 注册是系统级引导操作（无会话/JWT），经 worker 角色直连完成：
    # API 池角色（wealthsail_user）对 identity 元表无写权限，也不应持有。
    conn = await asyncpg.connect(settings.worker_database_url)
    try:
        async with conn.transaction():
            existing = await conn.fetchval(
                "SELECT 1 FROM identity.profiles WHERE lower(email) = $1", email
            )
            if existing:
                raise IdempotencyConflictError("该邮箱已注册")

            user_id = str(
                await conn.fetchval(
                    """
                    INSERT INTO identity.profiles (email, nickname, password_hash)
                    VALUES ($1, $2, $3)
                    RETURNING id
                    """,
                    email,
                    display_name,
                    hash_password(password),
                )
            )
            tenant_id = str(
                await conn.fetchval(
                    """
                    INSERT INTO identity.tenants (name, status, base_currency)
                    VALUES ($1, 'ACTIVE', 'CNY')
                    RETURNING id
                    """,
                    f"{display_name} 的家庭账户",
                )
            )
            await conn.execute(
                """
                INSERT INTO identity.tenant_members (tenant_id, user_id, role, status)
                VALUES ($1, $2, 'OWNER', 'ACTIVE')
                """,
                tenant_id,
                user_id,
            )
            await conn.execute(
                """
                INSERT INTO identity.accounts (tenant_id, name, account_type, status)
                VALUES ($1, '默认投资账户', 'FUND_PLATFORM', 'ACTIVE')
                """,
                tenant_id,
            )
            await write_audit(
                conn,
                tenant_id=tenant_id,
                actor_type="USER",
                actor_id=user_id,
                action="USER_REGISTER",
                resource_type="identity.profile",
                resource_id=user_id,
                details={"method": "local-bcrypt"},
            )
    finally:
        await conn.close()

    return {
        "userId": user_id,
        "email": email,
        "nickname": display_name,
        "tenantId": tenant_id,
        "tenantName": f"{display_name} 的家庭账户",
    }


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
    token = generate_token(entropy_bytes=16)  # >= 128-bit CSPRNG entropy
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
    external_hash = hmac_digest(settings.binding_hmac_key, external_user_id)

    async with system_connection(pool) as conn:
        try:
            binding = await conn.fetchrow(
                "SELECT * FROM identity.consume_binding_challenge($1, $2, $3)",
                token_hash,
                external_hash,
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
AVATAR_ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}


async def save_avatar(
    pool: Pool,
    ctx: TenantContext,
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> dict:
    """保存头像文件并更新 profiles.avatar_url（同一请求内完成）。"""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in AVATAR_ALLOWED_EXTENSIONS:
        raise ValidationError_(
            f"仅支持 {'/'.join(sorted(AVATAR_ALLOWED_EXTENSIONS))} 格式",
            field_errors=[{"field": "file", "message": "不支持的图片格式"}],
        )
    if content_type and not content_type.startswith("image/"):
        raise ValidationError_(
            "文件类型必须为图片",
            field_errors=[{"field": "file", "message": "非图片文件"}],
        )
    if not data:
        raise ValidationError_(
            "文件内容为空", field_errors=[{"field": "file", "message": "空文件"}]
        )
    if len(data) > AVATAR_MAX_BYTES:
        raise ValidationError_(
            f"头像文件不能超过 {AVATAR_MAX_BYTES // 1024 // 1024} MiB",
            field_errors=[{"field": "file", "message": "文件过大"}],
        )

    avatars_dir = Path(__file__).resolve().parent.parent / "static" / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    object_name = f"{ctx.user_id}_{int(time.time())}_{secrets.token_hex(4)}.{ext}"
    (avatars_dir / object_name).write_bytes(data)

    avatar_url = f"/static/avatars/{object_name}"
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
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
            details={"avatar_url": avatar_url, "bytes": len(data)},
        )
    return {"avatarUrl": avatar_url}
