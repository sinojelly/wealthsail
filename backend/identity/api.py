"""Identity API: /api/v1/auth/login · wecom/binding · me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
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
from backend.errors import ok
from backend.identity import service

router = APIRouter(prefix="/auth", tags=["identity"])


class LoginRequest(BaseModel):
    email: str = Field(description="登录邮箱")
    password: str = Field(min_length=1, description="登录口令")


class RegisterRequest(BaseModel):
    email: str = Field(description="注册邮箱")
    password: str = Field(min_length=8, description="登录口令（至少 8 个字符）")
    nickname: str | None = Field(default=None, description="显示昵称（缺省取邮箱前缀）")


class WeComBindingRequest(BaseModel):
    token: str | None = Field(default=None, description="一次性绑定口令（企微回调消费）")
    externalUserId: str | None = Field(default=None, description="企微外部用户 ID（仅回调使用）")
    externalUsername: str | None = Field(default=None)


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
    """注册本地用户（bcrypt 口令哈希 + 默认租户与投资账户）。"""
    as_of = now_shanghai()
    data = await service.register(
        pool, settings, email=body.email, password=body.password, nickname=body.nickname
    )
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/login")
async def login(
    body: LoginRequest,
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """登录并签发访问令牌（Supabase 兼容 JWT）。"""
    as_of = now_shanghai()
    data = await service.login(
        pool, settings, email=body.email, password=body.password
    )
    return ok(data, request_id=request_id, as_of=as_of)


@router.post("/wecom/binding")
async def wecom_binding(
    body: WeComBindingRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """创建企业微信一次性绑定口令（仅返回一次，库中只存 HMAC 摘要）。

    若携带 token + externalUserId，则执行回调侧的原子消费（供适配器复用）。
    """
    as_of = now_shanghai()
    if body.token and body.externalUserId:
        data = await service.consume_binding_challenge(
            pool,
            settings,
            token=body.token,
            external_user_id=body.externalUserId,
            external_username=body.externalUsername,
        )
    else:
        data = await service.create_binding_challenge(pool, ctx, settings)
    return ok(data, request_id=request_id, as_of=as_of)


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
    """上传头像：保存到 static/avatars/ 并更新用户 avatar_url。"""
    as_of = now_shanghai()
    data = await service.save_avatar(
        pool,
        ctx,
        filename=file.filename or "",
        content_type=file.content_type,
        data=await file.read(),
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
