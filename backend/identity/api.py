"""Identity API: /api/v1/auth/login · wecom/binding · me."""

from __future__ import annotations

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

router = APIRouter(prefix="/auth", tags=["identity"])


class LoginRequest(BaseModel):
    email: str = Field(description="登录邮箱")
    password: str = Field(min_length=1, description="登录口令")


class RegisterRequest(BaseModel):
    email: str = Field(description="注册邮箱")
    password: str = Field(min_length=8, description="登录口令（至少 8 个字符）")
    nickname: str | None = Field(default=None, description="显示昵称（缺省取邮箱前缀）")
    invitationToken: str = Field(min_length=16, description="一次性受邀注册令牌")


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
    data = await service.register(
        pool,
        settings,
        email=body.email,
        password=body.password,
        nickname=body.nickname,
        invitation_token=body.invitationToken,
    )
    return ok(data, request_id=request_id, as_of=as_of)


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
    data = await service.login(
        pool, settings, email=body.email, password=body.password
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
    data = await service.refresh_session(pool, settings, token)
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
    await service.revoke_refresh_session(
        pool, settings, request.cookies.get(settings.refresh_cookie_name)
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
    data = await service.switch_tenant(
        pool,
        settings,
        ctx,
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
    data = await service.create_invitation(
        pool, settings, ctx, email=body.email, role=body.role
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
    data = await service.create_binding_challenge(pool, ctx, settings)
    return ok(data, request_id=request_id, as_of=as_of)


@router.get("/wecom/binding/status")
async def wecom_binding_status(
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """查询当前用户在企业微信的绑定状态。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_binding_status(conn, ctx)
    return ok(data, request_id=request_id, as_of=as_of)


@router.delete("/wecom/binding")
async def revoke_wecom_binding_current(
    body: RevokeBindingRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """口令二次认证后解除当前用户的企业微信绑定（软撤销，保留审计）。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.revoke_current_binding(
            conn, ctx, password=body.password
        )
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/wecom/bindings/{binding_id}/revoke")
async def revoke_wecom_binding(
    binding_id: str,
    body: RevokeBindingRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """二次认证后软撤销本人、当前租户的绑定，历史审计仍保留。"""
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.revoke_binding(
            conn, ctx, binding_id=binding_id, password=body.password
        )
    return ok(data, request_id=request_id, as_of=now_shanghai())


@router.patch("/profile")
async def update_profile(
    body: ProfileUpdateRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    request_id: str = Depends(get_request_id),
) -> dict:
    """更新当前用户资料（昵称 / 头像 URL）。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.update_profile(
            conn,
            ctx,
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
    request_id: str = Depends(get_request_id),
) -> dict:
    """当前用户资料、活动租户、租户成员关系与渠道绑定列表。"""
    as_of = now_shanghai()
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        data = await service.get_me(conn, ctx)
    return ok(data, request_id=request_id, as_of=as_of)
