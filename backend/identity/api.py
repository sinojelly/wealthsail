"""Identity API: /api/v1/auth/login · wecom/binding · me."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, Field

from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
    now_shanghai,
)
from backend.errors import ValidationError_, ok
from backend.identity import service
from backend.domain import domain_tenant, identity_service
from backend.integrations.email import send_password_reset_email

router = APIRouter(prefix="/auth", tags=["identity"])
logger = logging.getLogger("wealthsail.identity.api")


class LoginRequest(BaseModel):
    email: str = Field(description="登录邮箱")
    password: str = Field(min_length=1, description="登录口令")


class RegisterRequest(BaseModel):
    email: str = Field(description="注册邮箱")
    password: str = Field(min_length=8, description="登录口令（至少 8 个字符）")
    nickname: str | None = Field(default=None, description="显示昵称（缺省取邮箱前缀）")
    invitationToken: str = Field(min_length=16, description="一次性受邀注册令牌")


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=16)
    newPassword: str = Field(min_length=8, max_length=128)


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    baseCurrency: str = Field(default="CNY", min_length=3, max_length=3)


class SelfRegisterRequest(TenantCreateRequest):
    email: str
    password: str = Field(min_length=8, max_length=128)
    nickname: str | None = Field(default=None, max_length=32)


class InvitationRequest(BaseModel):
    email: str = Field(description="被邀请邮箱")
    role: str = Field(default="VIEWER", description="ADMIN | EDITOR | VIEWER")


class SwitchTenantRequest(BaseModel):
    tenantId: str = Field(description="目标活动租户 ID")


class WeComBindingRequest(BaseModel):
    """Reserved request body; external identity is accepted only from verified callback."""


class RevokeBindingRequest(BaseModel):
    password: str = Field(min_length=1, description="高风险操作二次认证口令")


class ProfileUpdateRequest(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=32, description="新昵称")
    avatar_url: str | None = Field(default=None, max_length=500, description="头像 URL")


@router.post("/register")
async def register(
    body: RegisterRequest,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """使用一次性、租户绑定的邀请令牌注册封闭试点用户。"""
    as_of = now_shanghai()
    data = await identity_service(pool, settings).register(
        email=body.email,
        password=body.password,
        nickname=body.nickname,
        invitation_token=body.invitationToken,
    )
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/register-with-tenant", status_code=201)
async def register_with_tenant(
    body: SelfRegisterRequest,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    data = await identity_service(pool, settings).self_register(
        email=body.email,
        password=body.password,
        nickname=body.nickname,
        tenant_name=body.name,
        base_currency=body.baseCurrency,
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/password-reset/request")
async def request_password_reset(
    body: PasswordResetRequest,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    result = await identity_service(pool, settings).request_password_reset(email=body.email)
    token = result.pop("token", None)
    if token:
        try:
            await send_password_reset_email(
                settings, recipient=body.email.strip().lower(), token=token
            )
        except Exception:
            # Keep the public response identical for registered and unknown
            # addresses, including during a transient SMTP outage.
            logger.exception("password-reset email delivery failed")
    return ok({"status": "sent"}, request_id=request_id, as_of=now_shanghai())


@router.post("/password-reset/confirm")
async def confirm_password_reset(
    body: PasswordResetConfirmRequest,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    data = await identity_service(pool, settings).reset_password(
        token=body.token, new_password=body.newPassword
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/tenants", status_code=201)
async def create_tenant(
    body: TenantCreateRequest,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    data = await identity_service(pool, settings).create_tenant(
        domain_tenant(ctx), name=body.name, base_currency=body.baseCurrency
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """登录并签发访问令牌（Supabase 兼容 JWT）。"""
    as_of = now_shanghai()
    data = await identity_service(pool, settings).login(
        email=body.email, password=body.password
    )
    _set_refresh_cookie(response, settings, data.pop("_refreshToken"))
    return ok(data, request_id=request_id, as_of=as_of)


def _set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path=f"{settings.api_prefix}/auth",
    )


@router.post("/refresh")
async def refresh(
    response: Response,
    request: Request,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """轮换 HttpOnly refresh cookie，并签发新的短期访问令牌。"""
    token = request.cookies.get(settings.refresh_cookie_name, "")
    data = await identity_service(pool, settings).refresh_session(token)
    _set_refresh_cookie(response, settings, data.pop("_refreshToken"))
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/logout")
async def logout(
    response: Response,
    request: Request,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """服务端撤销当前刷新会话；重复注销保持幂等。"""
    await identity_service(pool, settings).revoke_refresh_session(
        request.cookies.get(settings.refresh_cookie_name)
    )
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=f"{settings.api_prefix}/auth",
        secure=settings.is_production,
        httponly=True,
        samesite="strict",
    )
    return ok({"revoked": True}, request_id=request_id, as_of=now_shanghai())


@router.post("/switch-tenant")
async def switch_tenant(
    body: SwitchTenantRequest,
    request: Request,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """重新验证成员关系并签发绑定到目标租户的新访问令牌。"""
    data = await identity_service(pool, settings).switch_tenant(
        domain_tenant(ctx),
        tenant_id=body.tenantId,
        refresh_token=request.cookies.get(settings.refresh_cookie_name),
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/invitations")
async def create_invitation(
    body: InvitationRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """为当前租户生成一次性邀请；令牌明文只在本响应出现一次。"""
    data = await identity_service(pool, settings).create_invitation(
        domain_tenant(ctx), email=body.email, role=body.role
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.post("/wecom/binding")
async def wecom_binding(
    body: WeComBindingRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """创建企业微信一次性绑定口令（仅返回一次，库中只存 HMAC 摘要）。"""
    as_of = now_shanghai()
    data = await identity_service(pool, settings).create_binding_challenge(domain_tenant(ctx))
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/wecom/binding/status")
async def wecom_binding_status(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """查询当前用户在企业微信的绑定状态。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await identity_service(pool, settings).get_binding_status(
            conn, domain_tenant(ctx)
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.delete("/wecom/binding")
async def revoke_wecom_binding_current(
    body: RevokeBindingRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """口令二次认证后解除当前用户的企业微信绑定（软撤销，保留审计）。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        status = await identity_service(pool, settings).get_binding_status(
            conn, domain_tenant(ctx)
        )
        binding = status.get("binding")
        if binding is None:
            from modules.errors import NotFoundError

            raise NotFoundError("活动绑定不存在或已撤销")
        data = await identity_service(pool, settings).revoke_binding(
            conn, domain_tenant(ctx), binding_id=binding["id"], password=body.password
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/wecom/bindings/{binding_id}/revoke")
async def revoke_wecom_binding(
    binding_id: str,
    body: RevokeBindingRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """二次认证后软撤销本人、当前租户的绑定，历史审计仍保留。"""
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await identity_service(pool, settings).revoke_binding(
            conn, domain_tenant(ctx), binding_id=binding_id, password=body.password
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.patch("/profile")
async def update_profile(
    body: ProfileUpdateRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """更新当前用户资料（昵称 / 头像 URL）。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await identity_service(pool, settings).update_profile(
            conn,
            domain_tenant(ctx),
            nickname=body.nickname,
            avatar_url=body.avatar_url,
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(..., description="头像图片（jpg/png/webp/gif，≤2MiB）"),
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """流式限额读取头像，解码/重编码后更新用户 avatar_url。"""
    as_of = now_shanghai()
    chunks = bytearray()
    try:
        while chunk := await file.read(64 * 1024):
            chunks.extend(chunk)
            if len(chunks) > service.AVATAR_MAX_BYTES:
                raise ValidationError_("头像文件不能超过 2 MiB")
    finally:
        await file.close()
    data = await service.save_avatar(
        pool,
        ctx,
        filename=file.filename or "",
        content_type=file.content_type,
        data=bytes(chunks),
    )
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/me")
async def me(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """当前用户资料、活动租户、租户成员关系与渠道绑定列表。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await identity_service(pool, settings).get_me(conn, domain_tenant(ctx))
    return ok(data, request_id=request_id, as_of=as_of)
