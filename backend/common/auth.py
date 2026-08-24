"""Supabase-compatible JWT issuance and validation + bcrypt password auth.

The Domain API issues and validates HS256 JWTs whose claims mirror
Supabase Auth: ``sub`` (user/profile id), ``role`` = "authenticated", plus the
application claims ``tenant_id`` / ``email`` / ``nickname`` / ``role``. The
same claims are injected into the database via ``set_config('request.jwt.claims')``
so ``auth.uid()`` and the RLS policies evaluate identically.

Passwords are bcrypt-hashed with the native ``bcrypt`` library (passlib is
incompatible with bcrypt>=4.1). Only the hash is ever stored; plaintext never
touches the database or logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from backend.errors import AuthRequiredError

_CLAIM_ROLE = "role"  # JWT "role" claim (Supabase convention)

# ---------------------------------------------------------------------------
# bcrypt password primitives
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """bcrypt hash of a plaintext password (UTF-8, 72-byte cap enforced)."""
    if not password:
        raise ValueError("password cannot be empty")
    raw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-time bcrypt verification; fail closed on missing hash."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8")[:72], password_hash.encode("ascii")
        )
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class AuthSession:
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
    """Mint an access token; returns (token, expires_in_seconds)."""
    now = datetime.now(timezone.utc)
    expires_in = timedelta(seconds=ttl_seconds)
    payload: dict[str, Any] = {
        "sub": user_id,
        _CLAIM_ROLE: "authenticated",
        "tenant_id": tenant_id,
        "email": email,
        "nickname": nickname,
        "tenant_role": tenant_role,
        "iat": now,
        "exp": now + expires_in,
        "iss": "wealthsail-api",
    }
    token = jwt.encode(payload, jwt_secret, algorithm=algorithm)
    return token, ttl_seconds


def decode_access_token(token: str, jwt_secret: str, algorithm: str = "HS256") -> AuthSession:
    """Validate and decode an access token into an AuthSession.

    Raises AuthRequiredError for any invalid/expired/malformed token (fail closed).
    """
    if not token:
        raise AuthRequiredError("缺少访问令牌")
    try:
        claims = jwt.decode(
            token,
            jwt_secret,
            algorithms=[algorithm],
            options={"verify_aud": False, "require_sub": True},
        )
    except JWTError as exc:
        raise AuthRequiredError("访问令牌无效或已过期") from exc

    user_id = str(claims.get("sub") or "")
    tenant_id = str(claims.get("tenant_id") or "")
    if not user_id or not tenant_id:
        raise AuthRequiredError("访问令牌缺少用户或租户声明")

    return AuthSession(
        user_id=user_id,
        tenant_id=tenant_id,
        email=claims.get("email"),
        nickname=claims.get("nickname"),
        tenant_role=claims.get("tenant_role") or "VIEWER",
        extra=claims,
    )
