"""Identity facade and avatar persistence helpers.

Legacy function-shaped call sites delegate to the canonical class-based
implementation in this same vertical domain. New HTTP code constructs the
class directly through :mod:`backend.domain`.
"""

from __future__ import annotations

import os
import secrets
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.common.database import Pool, tenant_transaction
from backend.common.security import envelope_encrypt
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import write_audit
from backend.domain import domain_tenant, identity_service
from backend.errors import NotFoundError, ValidationError_
from backend.identity.domain_service import (
    IdentityService,
    IdentitySettings,
    credentials_are_valid,
    hmac_digest,
)

__all__ = ["IdentityService", "IdentitySettings", "credentials_are_valid"]


def refresh_token_hash(settings: Settings, token: str) -> str:
    return hmac_digest(settings.refresh_token_hmac_key, token)


def protect_routing_value(settings: Settings, external_user_id: str) -> tuple[str, str]:
    return hmac_digest(settings.binding_hmac_key, external_user_id), envelope_encrypt(
        settings.routing_encryption_key, external_user_id
    )


async def load_login_identity(conn, email: str):
    return await conn.fetch(
        """SELECT p.id AS user_id, p.email, p.nickname, p.password_hash,
                  tm.tenant_id, t.name AS tenant_name, tm.role AS tenant_role
             FROM identity.profiles p
             JOIN identity.tenant_members tm ON tm.user_id=p.id AND tm.status='ACTIVE'
             JOIN identity.tenants t ON t.id=tm.tenant_id AND t.status='ACTIVE'
            WHERE lower(p.email)=lower($1)
            ORDER BY tm.created_at ASC LIMIT 1""",
        email,
    )


async def register(pool: Pool, settings: Settings, **kwargs):
    return await identity_service(pool, settings).register(**kwargs)


async def login(pool: Pool, settings: Settings, **kwargs):
    return await identity_service(pool, settings).login(**kwargs)


async def refresh_session(pool: Pool, settings: Settings, token: str):
    return await identity_service(pool, settings).refresh_session(token)


async def revoke_refresh_session(pool: Pool, settings: Settings, token: str | None):
    return await identity_service(pool, settings).revoke_refresh_session(token)


async def switch_tenant(pool: Pool, settings: Settings, ctx: TenantContext, **kwargs):
    return await identity_service(pool, settings).switch_tenant(domain_tenant(ctx), **kwargs)


async def create_invitation(pool: Pool, settings: Settings, ctx: TenantContext, **kwargs):
    return await identity_service(pool, settings).create_invitation(domain_tenant(ctx), **kwargs)


async def create_binding_challenge(pool: Pool, ctx: TenantContext, settings: Settings):
    return await identity_service(pool, settings).create_binding_challenge(domain_tenant(ctx))


async def consume_binding_challenge(pool: Pool, settings: Settings, **kwargs):
    return await identity_service(pool, settings).consume_binding_challenge(**kwargs)


def _connection_service() -> IdentityService:
    return IdentityService(
        None,
        IdentitySettings(jwt_secret="", refresh_token_hmac_key="", binding_hmac_key=""),
    )


async def get_binding_status(conn, ctx: TenantContext):
    return await _connection_service().get_binding_status(conn, domain_tenant(ctx))


async def revoke_binding(conn, ctx: TenantContext, **kwargs):
    return await _connection_service().revoke_binding(conn, domain_tenant(ctx), **kwargs)


async def revoke_current_binding(conn, ctx: TenantContext, *, password: str):
    status = await get_binding_status(conn, ctx)
    binding = status.get("binding")
    if binding is None:
        raise NotFoundError("活动绑定不存在或已撤销")
    return await revoke_binding(conn, ctx, binding_id=binding["id"], password=password)


async def get_me(conn, ctx: TenantContext):
    return await _connection_service().get_me(conn, domain_tenant(ctx))


async def update_profile(conn, ctx: TenantContext, **kwargs):
    return await _connection_service().update_profile(conn, domain_tenant(ctx), **kwargs)


AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_MAX_PIXELS = 16_000_000
AVATAR_MAX_DIMENSION = 1024
AVATAR_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


def normalize_avatar_image(data: bytes, content_type: str | None) -> tuple[bytes, str]:
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
            mode = "RGBA" if source.mode in {"RGBA", "LA"} else "RGB"
            frame = ImageOps.exif_transpose(source.convert(mode))
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
    del filename
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
    old_url = None
    try:
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            old_url = await conn.fetchval("SELECT avatar_url FROM identity.profiles WHERE id=$1", ctx.user_id)
            await conn.execute(
                "UPDATE identity.profiles SET avatar_url=$1, updated_at=now() WHERE id=$2",
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
        old_path = (avatars_dir / old_url.removeprefix(prefix)).resolve()
        if old_path.parent == avatars_dir.resolve() and old_path != final_path.resolve():
            old_path.unlink(missing_ok=True)
    return {"avatarUrl": avatar_url}
