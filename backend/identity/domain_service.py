"""身份与会话领域服务。

该模块位于正式 ``backend/identity`` 纵向领域边界内，复用全站唯一的数据库
事务、错误码、密码和 JWT 实现，避免身份协议出现两套不兼容实现。

职责：
  * 注册（一次性邀请）与登录（bcrypt 校验 + JWT + refresh session）
  * 试点成员邀请与活动租户切换
  * 企业微信绑定（一次性绑定码，仅存 HMAC 摘要）
  * 资料更新（昵称/头像）与 OWNER 租户治理（名称）
  * 密码重置（一次性重置令牌 + SECURITY DEFINER 消费，撤销全部会话）
  * 会话管理（refresh token 轮换、登出、会话列表）

数据库操作默认在 ``wealthsail_api`` 角色 + RLS 事务内执行（与
backend.common.database 的租户事务语义一致）；调用方已在租户事务内时，
可将连接传入 ``conn`` 参数以复用该事务。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from backend.common.auth import (
    create_access_token,
    hash_password as _hash_password,
    verify_password as _verify_password,
)
from backend.common.database import (
    Pool,
    apply_tenant_context,
    api_system_transaction,
    tenant_transaction,
)
from backend.errors import (
    AuthRequiredError,
    DataUnavailableError,
    ForbiddenError,
    IdempotencyConflictError as ConflictError,
    NotFoundError,
    ValidationError_ as ValidationError,
)

AsyncConnection = asyncpg.Connection
AsyncPool = Pool

logger = logging.getLogger("wealthsail.identity.domain")

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_BINDING_CHANNEL = "wecom"
REPORT_SUBSCRIPTION_COLUMNS = {
    "PRE_MARKET": "pre_market_report_enabled",
    "INTRADAY": "intraday_report_enabled",
    "FUND_1430": "fund_1430_report_enabled",
    "POST_MARKET": "post_market_report_enabled",
}


# ---------------------------------------------------------------------------
# 配置与租户上下文
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IdentitySettings:
    """身份服务配置（由部署侧从环境变量组装）。"""

    jwt_secret: str
    refresh_token_hmac_key: str
    binding_hmac_key: str
    password_reset_hmac_key: str | None = None
    routing_encryption_key: str | None = None
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_days: int = 14
    binding_token_ttl_seconds: int = 600
    binding_token_max_attempts: int = 5
    password_reset_token_ttl_minutes: int = 30
    jwt_algorithm: str = "HS256"
    auth_dev_mode: bool = False


@dataclass(frozen=True)
class TenantContext:
    """已认证用户的租户作用域（由访问令牌解码而来）。"""

    tenant_id: str
    user_id: str
    tenant_role: str = "VIEWER"

    def require_roles(self, *roles: str) -> None:
        """校验当前成员角色；不满足时抛 ForbiddenError。"""
        if self.tenant_role not in roles:
            raise ForbiddenError("当前角色无权执行该操作")


# ---------------------------------------------------------------------------
# 安全原语
# ---------------------------------------------------------------------------
def generate_token(entropy_bytes: int = 16) -> str:
    """CSPRNG 令牌（>=128 位熵），WS-XXXX-XXXX 分组 base32 格式。"""
    raw = secrets.token_bytes(entropy_bytes)
    b32 = base64.b32encode(raw).decode("ascii").rstrip("=").upper()
    groups = [b32[i : i + 4] for i in range(0, len(b32), 4)]
    return "WS-" + "-".join(groups)


def hmac_digest(key: str, value: str) -> str:
    """带服务端密钥的 HMAC-SHA256 摘要（令牌明文永不落库）。"""
    return hmac.new(
        key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def hash_password(password: str) -> str:
    """调用全站唯一的 bcrypt 实现。"""
    return _hash_password(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """调用全站唯一的常量时间 bcrypt 校验实现。"""
    return _verify_password(password, password_hash)


def credentials_are_valid(
    password: str, password_hash: str | None, *, auth_dev_mode: bool
) -> bool:
    """校验口令；仅当显式开启 dev 模式且用户确实没有哈希时才放行。"""
    del auth_dev_mode
    return verify_password(password, password_hash)


def _now() -> datetime:
    """当前上海时区时间（与项目 now_shanghai 惯例一致）。"""
    return datetime.now(SHANGHAI_TZ)


class IdentityService:
    """身份、租户、绑定与会话服务。"""

    def __init__(self, pool: AsyncPool, settings: IdentitySettings) -> None:
        self.pool = pool
        self.settings = settings

    # -- 事务辅助（共享实现见 backend/common/database.py）---------------------
    def _api_transaction(self):
        """API 角色事务（委托 backend.common.database）。"""
        return api_system_transaction(self.pool)

    def _tenant_transaction(self, user_id: str, tenant_id: str):
        """租户 RLS 事务（委托 backend.common.database）。"""
        return tenant_transaction(self.pool, user_id, tenant_id)

    @staticmethod
    async def _write_audit(
        conn: AsyncConnection,
        *,
        tenant_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """在调用方事务内追加一条不可变审计记录。"""
        await conn.execute(
            """
            INSERT INTO audit.audit_log
                (tenant_id, actor_type, actor_id, action, resource_type,
                 resource_id, correlation_id, details)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            tenant_id,
            actor_type,
            actor_id,
            action,
            resource_type,
            resource_id,
            None,
            json.dumps(details or {}, ensure_ascii=False),
        )

    def _refresh_token_hash(self, token: str) -> str:
        return hmac_digest(self.settings.refresh_token_hmac_key, token)

    def _password_reset_token_hash(self, token: str) -> str:
        key = (self.settings.password_reset_hmac_key or "").strip()
        if not key:
            raise DataUnavailableError("密码重置功能未配置")
        return hmac_digest(key, token)

    # -- 注册 / 邀请 ---------------------------------------------------------
    async def register(
        self,
        *,
        email: str,
        password: str,
        invitation_token: str,
        nickname: str | None = None,
    ) -> dict:
        """接受一次性邀请并创建试点用户（租户由邀请预先绑定）。

        令牌有效性（摘要、邮箱、过期、一次性）由数据库 SECURITY DEFINER
        函数 identity.accept_invitation 原子校验，服务层不自行判定。
        """
        email = (email or "").strip().lower()
        if not email or "@" not in email or "." not in email.split("@")[-1]:
            raise ValidationError("邮箱格式不正确")
        if not password or len(password) < 8:
            raise ValidationError("口令至少 8 个字符")
        if not invitation_token or len(invitation_token.strip()) < 16:
            raise ValidationError("邀请令牌无效")

        display_name = (nickname or "").strip() or email.split("@")[0]
        invitation_hash = hmac_digest(
            self.settings.binding_hmac_key, invitation_token.strip()
        )
        async with self._api_transaction() as conn:
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
                    raise ConflictError("该邮箱已注册，请直接登录") from exc
                raise
        if row is None:
            raise NotFoundError("邀请令牌无效、已使用或已过期")

        logger.info("用户注册成功: %s -> 租户 %s", email, row["out_tenant_id"])
        return {
            "userId": str(row["out_user_id"]),
            "email": email,
            "nickname": display_name,
            "tenantId": str(row["out_tenant_id"]),
            "tenantName": row["out_tenant_name"],
        }

    async def create_invitation(
        self,
        actor: TenantContext,
        *,
        email: str,
        role: str,
    ) -> dict:
        """为已有试点租户创建一次性邀请（OWNER/ADMIN 专属）。"""
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValidationError("邮箱格式不正确")
        if role not in {"ADMIN", "EDITOR", "VIEWER"}:
            raise ValidationError("邀请角色无效")
        actor.require_roles("OWNER", "ADMIN")

        token = generate_token(32)
        token_hash = hmac_digest(self.settings.binding_hmac_key, token)
        expires_at = _now() + timedelta(days=7)
        async with self._api_transaction() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM identity.create_invitation($1, $2, $3, $4, $5, $6)",
                actor.user_id,
                actor.tenant_id,
                email,
                role,
                token_hash,
                expires_at,
            )
        if row is None:
            raise ForbiddenError("邀请创建失败")

        logger.info("邀请已创建: %s -> %s (role=%s)", actor.tenant_id, email, role)
        return {
            "invitationId": str(row["out_invitation_id"]),
            "token": token,  # 明文仅此一次返回，库中只有摘要
            "email": email,
            "role": role,
            "expiresAt": expires_at,
        }

    async def create_personal_tenant_invitation(
        self,
        *,
        actor_user_id: str,
        actor_tenant_id: str,
        email: str,
        tenant_name: str,
    ) -> dict:
        """Create a platform-governed invitation that provisions one tenant.

        Platform authority is checked again by the SECURITY DEFINER function;
        a tenant ``ADMIN`` claim is intentionally insufficient.
        """

        email = (email or "").strip().lower()
        tenant_name = (tenant_name or "").strip()
        if not email or "@" not in email:
            raise ValidationError("邮箱格式不正确")
        if not 2 <= len(tenant_name) <= 64:
            raise ValidationError("个人租户名称须为 2 至 64 个字符")

        token = generate_token(32)
        token_hash = hmac_digest(self.settings.binding_hmac_key, token)
        expires_at = _now() + timedelta(days=7)
        try:
            # Apply the authenticated actor as PostgreSQL claims so the
            # SECURITY DEFINER function can reject a spoofed actor id.
            async with self._tenant_transaction(
                actor_user_id, actor_tenant_id
            ) as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM identity.create_personal_tenant_invitation(
                        $1, $2, $3, $4, $5
                    )
                    """,
                    actor_user_id,
                    email,
                    tenant_name,
                    token_hash,
                    expires_at,
                )
                if row is not None:
                    await self._write_audit(
                        conn,
                        tenant_id=actor_tenant_id,
                        actor_type="USER",
                        actor_id=actor_user_id,
                        action="PERSONAL_TENANT_INVITATION_CREATED",
                        resource_type="identity.invitations",
                        resource_id=str(row["out_invitation_id"]),
                        details={
                            "purpose": "CREATE_PERSONAL_TENANT",
                            "tenantName": tenant_name,
                            "expiresAt": expires_at.isoformat(),
                        },
                    )
        except asyncpg.PostgresError as exc:
            message = str(exc)
            if "PLATFORM_ADMIN_REQUIRED" in message:
                raise ForbiddenError("仅平台管理员可创建个人租户邀请") from exc
            if "EMAIL_ALREADY_REGISTERED" in message:
                raise ConflictError("该邮箱已注册，请使用租户成员邀请") from exc
            if "INVITATION_INVALID" in message:
                raise ValidationError("个人租户邀请参数无效") from exc
            raise
        if row is None:
            raise ForbiddenError("个人租户邀请创建失败")

        return {
            "invitationId": str(row["out_invitation_id"]),
            "token": token,
            "email": email,
            "tenantName": tenant_name,
            "purpose": "CREATE_PERSONAL_TENANT",
            "role": "OWNER",
            "expiresAt": expires_at,
        }

    # -- 登录 / 会话 ----------------------------------------------------------
    async def login(self, *, email: str, password: str | None) -> dict:
        """口令登录：校验 bcrypt 哈希，签发 access token + refresh session。

        Raises:
            AuthRequiredError: 账号不存在或口令错误（统一消息，防枚举）。
        """
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValidationError("邮箱格式不正确")
        if not password:
            raise ValidationError("缺少登录口令")

        async with self._api_transaction() as conn:
            rows = await conn.fetch(
                """
                SELECT p.id AS user_id, p.email, p.nickname, p.password_hash,
                       p.auth_version,
                       tm.tenant_id, t.name AS tenant_name, tm.role AS tenant_role,
                       up.default_tenant_id, up.default_asset_scope,
                       ARRAY(
                           SELECT pr.role
                             FROM identity.platform_roles pr
                            WHERE pr.user_id = p.id AND pr.status = 'ACTIVE'
                            ORDER BY pr.role
                       ) AS platform_roles
                FROM identity.profiles p
                JOIN identity.tenant_members tm
                  ON tm.user_id = p.id AND tm.status = 'ACTIVE'
                JOIN identity.tenants t
                  ON t.id = tm.tenant_id AND t.status = 'ACTIVE'
                LEFT JOIN identity.user_preferences up ON up.user_id = p.id
                WHERE lower(p.email) = lower($1)
                ORDER BY (tm.tenant_id = up.default_tenant_id) DESC,
                         tm.created_at ASC, tm.id ASC
                """,
                email,
            )

        if not rows:
            raise AuthRequiredError("邮箱或口令不正确")
        row = rows[0]
        if not credentials_are_valid(
            password, row["password_hash"], auth_dev_mode=self.settings.auth_dev_mode
        ):
            raise AuthRequiredError("邮箱或口令不正确")

        user_id = str(row["user_id"])
        tenant_id = str(row["tenant_id"])

        # A missing/stale preference must not make login fail.  Repair it only
        # after the password has been verified, so unauthenticated requests
        # cannot mutate identity state.
        if str(row.get("default_tenant_id") or "") != tenant_id:
            async with self._api_transaction() as conn:
                await conn.execute(
                    """
                    INSERT INTO identity.user_preferences(
                        user_id, default_tenant_id, default_asset_scope
                    ) VALUES ($1, $2, 'MINE')
                    ON CONFLICT (user_id) DO UPDATE
                       SET default_tenant_id = EXCLUDED.default_tenant_id,
                           updated_at = now()
                    """,
                    user_id,
                    tenant_id,
                )

        async with self._tenant_transaction(user_id, tenant_id) as conn:
            await self._write_audit(
                conn,
                tenant_id=tenant_id,
                actor_type="USER",
                actor_id=user_id,
                action="SESSION_LOGIN",
                resource_type="identity.session",
                resource_id=tenant_id,
                details={"method": "password"},
            )

        access_token, expires_in = create_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            email=row["email"],
            nickname=row["nickname"],
            tenant_role=row["tenant_role"],
            auth_version=int(row.get("auth_version", 1)),
            jwt_secret=self.settings.jwt_secret,
            ttl_seconds=self.settings.access_token_ttl_seconds,
            algorithm=self.settings.jwt_algorithm,
        )
        refresh_token = await self._create_refresh_session(user_id, tenant_id)
        logger.info("用户登录成功: %s", email)
        return self._login_payload(
            row, access_token, expires_in, refresh_token, tenant_id=tenant_id
        )

    async def _create_refresh_session(self, user_id: str, tenant_id: str) -> str:
        """生成 refresh token 并持久化其 HMAC 摘要。"""
        token = generate_token(32)
        expires_at = _now() + timedelta(days=self.settings.refresh_token_ttl_days)
        async with self._api_transaction() as conn:
            await conn.execute(
                """
                INSERT INTO identity.refresh_sessions
                    (user_id, tenant_id, token_hash, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                user_id,
                tenant_id,
                self._refresh_token_hash(token),
                expires_at,
            )
        return token

    def _login_payload(
        self,
        row: Any,
        access_token: str,
        expires_in: int,
        refresh_token: str,
        *,
        tenant_id: str | None = None,
    ) -> dict:
        """组装登录/刷新/切租户的统一响应。"""
        active_tenant = tenant_id or str(row["tenant_id"])
        return {
            "accessToken": access_token,
            "tokenType": "bearer",
            "expiresIn": expires_in,
            "_refreshToken": refresh_token,
            "user": {
                "id": str(row["user_id"]),
                "email": row["email"],
                "nickname": row["nickname"],
                "tenantId": active_tenant,
                "tenantName": row["tenant_name"],
                "role": row["tenant_role"],
                "platformRoles": list(row.get("platform_roles") or []),
            },
        }

    async def refresh_session(self, refresh_token: str) -> dict:
        """轮换 refresh token 并基于当前成员关系重签 access token。"""
        if not refresh_token:
            raise AuthRequiredError("刷新会话不存在")
        old_hash = self._refresh_token_hash(refresh_token)
        new_token = generate_token(32)
        new_hash = self._refresh_token_hash(new_token)
        expires_at = _now() + timedelta(days=self.settings.refresh_token_ttl_days)

        async with self._api_transaction() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT rs.id AS refresh_session_id, rs.user_id, rs.tenant_id,
                           p.email, p.nickname, p.auth_version,
                           t.name AS tenant_name,
                           tm.role AS tenant_role,
                           ARRAY(
                               SELECT pr.role
                                 FROM identity.platform_roles pr
                                WHERE pr.user_id = p.id AND pr.status = 'ACTIVE'
                                ORDER BY pr.role
                           ) AS platform_roles
                    FROM identity.refresh_sessions rs
                    JOIN identity.profiles p ON p.id = rs.user_id
                    JOIN identity.tenants t
                      ON t.id = rs.tenant_id AND t.status = 'ACTIVE'
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
                    "UPDATE identity.refresh_sessions SET revoked_at=now(), "
                    "rotated_at=now() WHERE id=$1",
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

        access_token, expires_in = create_access_token(
            user_id=str(row["user_id"]),
            tenant_id=str(row["tenant_id"]),
            email=row["email"],
            nickname=row["nickname"],
            tenant_role=row["tenant_role"],
            auth_version=int(row.get("auth_version", 1)),
            jwt_secret=self.settings.jwt_secret,
            ttl_seconds=self.settings.access_token_ttl_seconds,
            algorithm=self.settings.jwt_algorithm,
        )
        return self._login_payload(
            row, access_token, expires_in, new_token, tenant_id=str(row["tenant_id"])
        )

    async def revoke_refresh_session(self, refresh_token: str | None) -> None:
        """登出：撤销单个 refresh session（不存在时静默成功）。"""
        if not refresh_token:
            return
        async with self._api_transaction() as conn:
            await conn.execute(
                """
                UPDATE identity.refresh_sessions
                   SET revoked_at = COALESCE(revoked_at, now())
                 WHERE token_hash = $1
                """,
                self._refresh_token_hash(refresh_token),
            )
        logger.info("刷新会话已撤销")

    async def revoke_all_sessions(self, actor: TenantContext) -> int:
        """登出全部会话（密码重置等安全事件的配套动作）。"""
        async with self._api_transaction() as conn:
            updated = await conn.execute(
                """
                UPDATE identity.refresh_sessions
                   SET revoked_at = COALESCE(revoked_at, now())
                 WHERE user_id = $1 AND revoked_at IS NULL
                """,
                actor.user_id,
            )
        count = int(updated.split(" ")[-1]) if updated.split(" ")[-1].isdigit() else 0
        logger.info("用户 %s 全部会话已撤销", actor.user_id)
        return count

    async def list_sessions(self, actor: TenantContext) -> dict:
        """当前用户未撤销的刷新会话列表（不含令牌本身）。"""
        async with self._api_transaction() as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, expires_at, created_at, rotated_at
                FROM identity.refresh_sessions
                WHERE user_id = $1 AND revoked_at IS NULL
                ORDER BY created_at DESC
                LIMIT 50
                """,
                actor.user_id,
            )
        return {
            "sessions": [
                {
                    "id": str(r["id"]),
                    "tenantId": str(r["tenant_id"]),
                    "expiresAt": r["expires_at"],
                    "createdAt": r["created_at"],
                    "rotatedAt": r["rotated_at"],
                }
                for r in rows
            ]
        }

    async def switch_tenant(
        self, actor: TenantContext, *, tenant_id: str, refresh_token: str | None
    ) -> dict:
        """切换到用户已加入的另一活动租户并重签令牌。"""
        async with self._api_transaction() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.id AS user_id, p.email, p.nickname, p.auth_version,
                       tm.tenant_id, tm.role AS tenant_role, t.name AS tenant_name,
                       ARRAY(
                           SELECT pr.role
                             FROM identity.platform_roles pr
                            WHERE pr.user_id = p.id AND pr.status = 'ACTIVE'
                            ORDER BY pr.role
                       ) AS platform_roles
                FROM identity.profiles p
                JOIN identity.tenant_members tm ON tm.user_id=p.id AND tm.status='ACTIVE'
                JOIN identity.tenants t ON t.id=tm.tenant_id AND t.status='ACTIVE'
                WHERE p.id=$1 AND tm.tenant_id=$2
                """,
                actor.user_id,
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
                    self._refresh_token_hash(refresh_token),
                    actor.user_id,
                )
                if updated.endswith("0"):
                    raise AuthRequiredError("刷新会话无效，请重新登录")
            await conn.execute(
                """
                INSERT INTO identity.user_preferences(
                    user_id, default_tenant_id, default_asset_scope
                ) VALUES ($1, $2, 'MINE')
                ON CONFLICT (user_id) DO UPDATE
                   SET default_tenant_id = EXCLUDED.default_tenant_id,
                       updated_at = now()
                """,
                actor.user_id,
                tenant_id,
            )

        access_token, expires_in = create_access_token(
            user_id=str(row["user_id"]),
            tenant_id=str(row["tenant_id"]),
            email=row["email"],
            nickname=row["nickname"],
            tenant_role=row["tenant_role"],
            auth_version=int(row.get("auth_version", 1)),
            jwt_secret=self.settings.jwt_secret,
            ttl_seconds=self.settings.access_token_ttl_seconds,
            algorithm=self.settings.jwt_algorithm,
        )
        return self._login_payload(
            row, access_token, expires_in, refresh_token or "",
            tenant_id=str(row["tenant_id"]),
        )

    # -- 企业微信绑定 ----------------------------------------------------------
    async def create_binding_challenge(self, actor: TenantContext) -> dict:
        """生成一次性企微绑定码（明文仅返回一次，库中只有 HMAC 摘要）。"""
        token = generate_token(16)
        token_hash = hmac_digest(self.settings.binding_hmac_key, token)
        expires_at = _now() + timedelta(seconds=self.settings.binding_token_ttl_seconds)

        async with self._tenant_transaction(actor.user_id, actor.tenant_id) as conn:
            # 先作废该用户此渠道的悬空 PENDING 挑战，避免旧码复活
            await conn.execute(
                """
                UPDATE identity.binding_challenges
                   SET status = 'EXPIRED', resolved_at = now(),
                       version = version + 1
                 WHERE tenant_id = $1 AND user_id = $2
                   AND channel_type = $3 AND status = 'PENDING'
                """,
                actor.tenant_id,
                actor.user_id,
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
                actor.tenant_id,
                actor.user_id,
                _BINDING_CHANNEL,
                token_hash,
                expires_at,
                self.settings.binding_token_max_attempts,
                f"challenge:{actor.user_id}:{uuid.uuid4().hex}",
            )
            await self._write_audit(
                conn,
                tenant_id=actor.tenant_id,
                actor_type="USER",
                actor_id=actor.user_id,
                action="BINDING_CHALLENGE_CREATED",
                resource_type="identity.binding_challenges",
                resource_id=str(challenge_id),
                details={"channel_type": _BINDING_CHANNEL},
            )

        logger.info("企微绑定码已生成: user=%s", actor.user_id)
        return {
            "token": token,
            "expiresInSeconds": self.settings.binding_token_ttl_seconds,
            "channelType": _BINDING_CHANNEL,
        }

    async def consume_binding_challenge(
        self,
        *,
        token: str,
        external_user_id: str,
        external_username: str | None,
    ) -> dict:
        """消费绑定码，建立企微外部身份 → 用户的通道绑定。

        由企微回调入口调用；外部用户 ID 只以 HMAC 哈希 + 信封加密密文落库。
        """
        if not token or not external_user_id:
            raise ValidationError("绑定令牌或外部用户标识缺失")

        token_hash = hmac_digest(self.settings.binding_hmac_key, token.strip())
        external_hash = hmac_digest(
            self.settings.binding_hmac_key, external_user_id
        )
        external_ciphertext = _envelope_encrypt(
            self.settings.routing_encryption_key or self.settings.binding_hmac_key,
            external_user_id,
        )

        async with self._api_transaction() as conn:
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
                    raise ValidationError("绑定口令尝试次数超限，请重新生成") from exc
                raise
            if binding is None:
                raise NotFoundError("绑定口令无效或已过期")
            # The RPC reveals the tenant only after consuming the challenge.
            # Apply its RLS claims on this same transaction so the binding and
            # immutable audit fact either both commit or both roll back.
            await apply_tenant_context(
                conn,
                str(binding["out_user_id"]),
                str(binding["out_tenant_id"]),
            )
            await self._write_audit(
                conn,
                tenant_id=str(binding["out_tenant_id"]),
                actor_type="USER",
                actor_id=str(binding["out_user_id"]),
                action="WECOM_BOUND",
                resource_type="identity.channel_bindings",
                resource_id=str(binding["out_binding_id"]),
                details={"channel_type": binding["out_channel_type"]},
            )

        logger.info(
            "企微绑定成功: user=%s tenant=%s",
            binding["out_user_id"],
            binding["out_tenant_id"],
        )
        return {
            "bindingId": str(binding["out_binding_id"]),
            "status": binding["out_status"],
            "channelType": binding["out_channel_type"],
            "tenantId": str(binding["out_tenant_id"]),
        }

    async def get_binding_status(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
        *,
        channel_type: str = _BINDING_CHANNEL,
    ) -> dict:
        """查询当前用户+租户的活动绑定（须在调用方租户事务内）。"""
        binding = await conn.fetchrow(
            """
            SELECT id, channel_type, external_username, status, bound_at, last_used_at
            FROM identity.channel_bindings
            WHERE tenant_id = $1 AND user_id = $2
              AND channel_type = $3 AND status = 'ACTIVE'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            actor.tenant_id,
            actor.user_id,
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

    async def revoke_binding(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
        *,
        binding_id: str,
        password: str,
    ) -> dict:
        """密码复验后软撤销绑定（软删，保留审计痕迹）。"""
        password_hash = await conn.fetchval(
            "SELECT password_hash FROM identity.profiles WHERE id=$1", actor.user_id
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
            actor.tenant_id,
            actor.user_id,
        )
        if row is None:
            raise NotFoundError("活动绑定不存在或已撤销")
        await self._write_audit(
            conn,
            tenant_id=actor.tenant_id,
            actor_type="USER",
            actor_id=actor.user_id,
            action="WECOM_BINDING_REVOKED",
            resource_type="identity.channel_bindings",
            resource_id=str(row["id"]),
            details={"channel_type": row["channel_type"]},
        )
        return {"bindingId": str(row["id"]), "status": row["status"]}

    # -- 资料 ------------------------------------------------------------------
    async def get_me(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
    ) -> dict:
        """个人资料 + 活动租户 + 成员关系 + 绑定列表（须在租户事务内）。"""
        profile = await conn.fetchrow(
            """
            SELECT p.id, p.email, p.nickname, p.avatar_url, p.created_at,
                   up.default_tenant_id, up.default_asset_scope,
                   up.pre_market_report_enabled,
                   up.intraday_report_enabled,
                   up.fund_1430_report_enabled,
                   up.post_market_report_enabled,
                   ARRAY(
                       SELECT pr.role
                         FROM identity.platform_roles pr
                        WHERE pr.user_id = p.id AND pr.status = 'ACTIVE'
                        ORDER BY pr.role
                   ) AS platform_roles
            FROM identity.profiles p
            LEFT JOIN identity.user_preferences up ON up.user_id = p.id
            WHERE p.id = $1
            """,
            actor.user_id,
        )
        if profile is None:
            raise NotFoundError("用户资料不存在")
        tenants = await conn.fetch(
            """
            SELECT tm.tenant_id, t.name, tm.role, tm.status
            FROM identity.tenant_members tm
            JOIN identity.tenants t ON t.id = tm.tenant_id
            WHERE tm.user_id = $1 AND tm.status = 'ACTIVE'
            ORDER BY tm.created_at ASC
            """,
            actor.user_id,
        )
        members = await conn.fetch(
            """
            SELECT tm.user_id, p.nickname, p.email, tm.role, tm.status
            FROM identity.tenant_members tm
            JOIN identity.profiles p ON p.id = tm.user_id
            WHERE tm.tenant_id = $1 AND tm.status = 'ACTIVE'
            ORDER BY
                CASE tm.role
                    WHEN 'OWNER' THEN 1 WHEN 'ADMIN' THEN 2
                    WHEN 'EDITOR' THEN 3 ELSE 4
                END,
                lower(p.nickname), tm.user_id
            """,
            actor.tenant_id,
        )
        bindings = await conn.fetch(
            """
            SELECT id, channel_type, external_username, status, bound_at, last_used_at
            FROM identity.channel_bindings
            WHERE tenant_id = $1 AND user_id = $2
            ORDER BY created_at DESC
            """,
            actor.tenant_id,
            actor.user_id,
        )
        return {
            "profile": {
                "id": str(profile["id"]),
                "email": profile["email"],
                "nickname": profile["nickname"],
                "avatarUrl": profile["avatar_url"],
                "createdAt": profile["created_at"],
            },
            "activeTenantId": actor.tenant_id,
            "activeRole": actor.tenant_role,
            "defaultTenantId": (
                str(profile["default_tenant_id"])
                if profile.get("default_tenant_id") is not None
                else None
            ),
            "defaultAssetScope": profile.get("default_asset_scope") or "MINE",
            "platformRoles": list(profile.get("platform_roles") or []),
            "reportSubscriptions": {
                session: profile.get(column) is not False
                for session, column in REPORT_SUBSCRIPTION_COLUMNS.items()
            },
            "tenants": [
                {
                    "tenantId": str(t["tenant_id"]),
                    "name": t["name"],
                    "role": t["role"],
                    "status": t["status"],
                }
                for t in tenants
            ],
            "members": [
                {
                    "userId": str(member["user_id"]),
                    "nickname": member["nickname"],
                    "email": member["email"],
                    "role": member["role"],
                    "status": member["status"],
                }
                for member in members
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

    async def update_report_subscriptions(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
        changes: dict[str, bool],
    ) -> dict[str, bool]:
        """Update the current user's delivery preferences only.

        Report generation and audit persistence are deliberately unaffected;
        these booleans are consumed only when a report expands into Outbox rows.
        """
        if not changes:
            raise ValidationError("至少提交一个研报订阅开关")
        unknown = set(changes) - set(REPORT_SUBSCRIPTION_COLUMNS)
        if unknown:
            raise ValidationError("研报订阅类型无效")

        columns = [REPORT_SUBSCRIPTION_COLUMNS[session] for session in changes]
        insert_columns = ", ".join(("user_id", *columns))
        placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 2))
        updates = ", ".join(
            f"{column} = EXCLUDED.{column}" for column in columns
        )
        returning = ", ".join(REPORT_SUBSCRIPTION_COLUMNS.values())
        values: list[object] = [actor.user_id, *changes.values()]
        row = await conn.fetchrow(
            f"""
            INSERT INTO identity.user_preferences({insert_columns})
            VALUES ({placeholders})
            ON CONFLICT (user_id) DO UPDATE
               SET {updates}, updated_at = now()
            RETURNING {returning}
            """,
            *values,
        )
        if row is None:
            raise NotFoundError("用户偏好不存在")

        result = {
            session: row.get(column) is not False
            for session, column in REPORT_SUBSCRIPTION_COLUMNS.items()
        }
        await self._write_audit(
            conn,
            tenant_id=actor.tenant_id,
            actor_type="USER",
            actor_id=actor.user_id,
            action="REPORT_SUBSCRIPTIONS_UPDATED",
            resource_type="identity.user_preferences",
            resource_id=actor.user_id,
            details=result,
        )
        return result

    async def update_profile(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
        *,
        nickname: str | None = None,
        avatar_url: str | None = None,
    ) -> dict:
        """更新昵称/头像（RLS 限定只能改本人行）。"""
        if nickname is None and avatar_url is None:
            raise ValidationError("没有需要更新的字段")

        sets: list[str] = []
        values: list[object] = []
        details: dict[str, str] = {}
        if nickname is not None:
            nickname = nickname.strip()
            if not nickname:
                raise ValidationError("昵称不能为空")
            sets.append(f"nickname = ${len(values) + 1}")
            values.append(nickname)
            details["nickname"] = nickname
        if avatar_url is not None:
            avatar_url = avatar_url.strip()
            sets.append(f"avatar_url = ${len(values) + 1}")
            values.append(avatar_url)
            details["avatar_url"] = avatar_url

        values.append(actor.user_id)
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
        await self._write_audit(
            conn,
            tenant_id=actor.tenant_id,
            actor_type="USER",
            actor_id=actor.user_id,
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

    async def update_tenant_name(
        self,
        conn: AsyncConnection,
        actor: TenantContext,
        *,
        name: str,
    ) -> dict:
        """重命名当前活动租户；仅 OWNER 可执行且全程受 tenant RLS 限制。"""
        actor.require_roles("OWNER")
        normalized_name = (name or "").strip()
        if len(normalized_name) < 2:
            raise ValidationError("租户名称至少需要 2 个字符")
        if len(normalized_name) > 64:
            raise ValidationError("租户名称不能超过 64 个字符")

        existing = await conn.fetchrow(
            "SELECT name FROM identity.tenants WHERE id = $1",
            actor.tenant_id,
        )
        if existing is None:
            raise NotFoundError("当前租户不存在或无权访问")
        old_name = str(existing["name"])
        if old_name == normalized_name:
            return {"tenantId": actor.tenant_id, "name": old_name}

        row = await conn.fetchrow(
            """
            UPDATE identity.tenants
               SET name = $1, updated_at = now()
             WHERE id = $2
            RETURNING id, name
            """,
            normalized_name,
            actor.tenant_id,
        )
        if row is None:
            raise NotFoundError("当前租户不存在或无权更新")
        await self._write_audit(
            conn,
            tenant_id=actor.tenant_id,
            actor_type="USER",
            actor_id=actor.user_id,
            action="TENANT_NAME_UPDATED",
            resource_type="identity.tenants",
            resource_id=str(row["id"]),
            details={"old_name": old_name, "new_name": normalized_name},
        )
        return {"tenantId": str(row["id"]), "name": row["name"]}

    # -- 密码重置 --------------------------------------------------------------
    async def request_password_reset(self, *, email: str) -> dict:
        """生成一次性密码重置令牌（仅存 HMAC 摘要）。

        防枚举：无论邮箱是否存在，对外返回相同结果；不存在时不落库。
        """
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValidationError("邮箱格式不正确")

        async with self._api_transaction() as conn:
            user_id = await conn.fetchval(
                "SELECT id FROM identity.profiles WHERE lower(email) = lower($1)",
                email,
            )
            if user_id is not None:
                recently_issued = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM identity.password_reset_tokens
                        WHERE user_id = $1
                          AND created_at > now() - interval '60 seconds'
                          AND consumed_at IS NULL
                          AND expires_at > now()
                    )
                    """,
                    user_id,
                )
                if recently_issued:
                    return {"status": "sent", "token": None}
                token = generate_token(32)
                token_hash = self._password_reset_token_hash(token)
                expires_at = _now() + timedelta(
                    minutes=self.settings.password_reset_token_ttl_minutes
                )
                await conn.execute(
                    """
                    INSERT INTO identity.password_reset_tokens
                        (user_id, token_hash, expires_at)
                    VALUES ($1, $2, $3)
                    """,
                    user_id,
                    token_hash,
                    expires_at,
                )
                logger.info("密码重置令牌已签发: user=%s", user_id)
                return {"status": "sent", "token": token}

        # 未注册邮箱同样返回 sent（不泄露账号是否存在）
        return {"status": "sent", "token": None}

    async def reset_password(self, *, token: str, new_password: str) -> dict:
        """消费重置令牌并更新密码（原子：更新哈希 + 撤销全部会话 + 标记消费）。

        Raises:
            ValidationError: 新口令不满足强度要求。
            NotFoundError: 令牌无效、已使用或已过期。
        """
        if not token:
            raise ValidationError("重置令牌缺失")
        if not new_password or len(new_password) < 8:
            raise ValidationError("新口令至少 8 个字符")

        token_hash = self._password_reset_token_hash(token.strip())
        async with self._api_transaction() as conn:
            try:
                row = await conn.fetchrow(
                    "SELECT * FROM identity.reset_password($1, $2)",
                    token_hash,
                    hash_password(new_password),
                )
            except asyncpg.PostgresError as exc:
                if "RESET_TOKEN_INVALID" in str(exc):
                    raise NotFoundError("重置令牌无效、已使用或已过期") from exc
                raise
        if row is None:
            raise NotFoundError("重置令牌无效、已使用或已过期")

        logger.info("密码已重置: user=%s", row["out_user_id"])
        return {"userId": str(row["out_user_id"]), "email": row["out_email"]}


# ---------------------------------------------------------------------------
# 信封加密（AES-256-GCM，v1 版本前缀；密钥由任意字符串派生）
# ---------------------------------------------------------------------------
def _envelope_encrypt(key: str, plaintext: str) -> str:
    """'v1:<nonce_b64>:<ciphertext_b64>' —— 与 backend.common.security 同构。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aes = AESGCM(hashlib.sha256(key.encode("utf-8")).digest())
    nonce = secrets.token_bytes(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()
