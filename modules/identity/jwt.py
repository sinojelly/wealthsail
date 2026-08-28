"""HS256 JWT 签发与校验（纯标准库，与 backend/common/auth.py 载荷完全兼容）。

领域层刻意不依赖 python-jose：HS256 只是 HMAC-SHA256 + base64url，标准库
即可实现；载荷结构（``sub``/``role``/``tenant_id``/``email``/``nickname``/
``tenant_role``/``iat``/``exp``/``iss``）与 backend 签发/校验的令牌一致，
因此 modules 签发的令牌可以被 backend 校验，反之亦然。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from modules.errors import AuthRequiredError

_CLAIM_ROLE = "role"  # JWT "role" claim（Supabase 惯例：authenticated）


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


@dataclass(frozen=True)
class AuthSession:
    """解码后的可信身份（与 backend.common.auth.AuthSession 等价）。"""

    user_id: str
    tenant_id: str
    email: str | None = None
    nickname: str | None = None
    tenant_role: str = "VIEWER"
    extra: dict[str, Any] = field(default_factory=dict)


def create_access_token(
    *,
    user_id: str,
    tenant_id: str,
    email: str | None,
    nickname: str | None,
    tenant_role: str,
    jwt_secret: str,
    ttl_seconds: int,
    algorithm: str = "HS256",
) -> tuple[str, int]:
    """签发访问令牌；返回 (token, expires_in_seconds)。

    Args:
        user_id: 用户（identity.profiles.id）UUID 字符串。
        tenant_id: 活动租户 UUID 字符串。
        email / nickname / tenant_role: 随令牌携带的应用声明。
        jwt_secret: HS256 签名密钥。
        ttl_seconds: 过期时间（秒）。

    Returns:
        (JWT 字符串, 有效期秒数)。

    Raises:
        ValueError: 算法不支持或关键声明缺失。
    """
    if algorithm != "HS256":
        raise ValueError(f"unsupported jwt algorithm: {algorithm}")
    if not user_id or not tenant_id:
        raise ValueError("user_id 与 tenant_id 为必填声明")

    now = datetime.now(timezone.utc)
    expires_in = timedelta(seconds=ttl_seconds)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        _CLAIM_ROLE: "authenticated",
        "tenant_id": str(tenant_id),
        "email": email,
        "nickname": nickname,
        "tenant_role": tenant_role,
        "iat": now,
        "exp": now + expires_in,
        "iss": "wealthsail-api",
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url_encode(json.dumps(payload, separators=(",", ":"), default=_json_default).encode("utf-8"))
    )
    signature = hmac.new(
        jwt_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}", ttl_seconds


def _json_default(value: object) -> object:
    """datetime 序列化为 RFC 3339（与 python-jose 行为一致，解签不依赖该格式）。"""
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def decode_access_token(
    token: str, jwt_secret: str, algorithm: str = "HS256"
) -> AuthSession:
    """校验并解码访问令牌（失败一律抛 AuthRequiredError，fail closed）。

    Args:
        token: JWT 字符串。
        jwt_secret: 签名密钥。
        algorithm: 仅支持 HS256。

    Returns:
        解码后的 AuthSession。

    Raises:
        AuthRequiredError: 令牌缺失/过期/签名不符/载荷缺关键声明。
    """
    if not token:
        raise AuthRequiredError("缺少访问令牌")
    if algorithm != "HS256":
        raise AuthRequiredError("不支持的令牌算法")

    parts = token.split(".")
    if len(parts) != 3:
        raise AuthRequiredError("访问令牌格式无效")

    header_segment, payload_segment, signature_segment = parts
    try:
        signing_input = f"{header_segment}.{payload_segment}"
        expected = hmac.new(
            jwt_secret.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual = _b64url_decode(signature_segment)
        if not hmac.compare_digest(expected, actual):
            raise AuthRequiredError("访问令牌签名无效")

        header = json.loads(_b64url_decode(header_segment))
        payload = json.loads(_b64url_decode(payload_segment))
        if header.get("alg") != "HS256":
            raise AuthRequiredError("访问令牌算法不匹配")
        if payload.get("iss") != "wealthsail-api":
            raise AuthRequiredError("访问令牌签发方无效")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AuthRequiredError("访问令牌无法解码") from exc

    exp_raw = payload.get("exp")
    if isinstance(exp_raw, str):
        try:
            exp_raw = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AuthRequiredError("访问令牌过期时间无效") from exc
    if not isinstance(exp_raw, datetime):
        raise AuthRequiredError("访问令牌缺少过期时间")
    if exp_raw.tzinfo is None:
        exp_raw = exp_raw.replace(tzinfo=timezone.utc)
    if exp_raw <= datetime.now(timezone.utc):
        raise AuthRequiredError("访问令牌已过期")

    user_id = str(payload.get("sub") or "")
    tenant_id = str(payload.get("tenant_id") or "")
    if not user_id or not tenant_id:
        raise AuthRequiredError("访问令牌缺少用户或租户声明")

    return AuthSession(
        user_id=user_id,
        tenant_id=tenant_id,
        email=payload.get("email"),
        nickname=payload.get("nickname"),
        tenant_role=payload.get("tenant_role") or "VIEWER",
        extra=payload,
    )
