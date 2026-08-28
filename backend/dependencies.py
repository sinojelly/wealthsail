"""FastAPI dependency injection container.

Provides: DB pool, settings, the authenticated TenantContext, request-id, and
audit-log helpers. Modules import these via FastAPI's ``Depends``.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.common.auth import AuthSession, decode_access_token
from backend.common.database import Pool, api_system_transaction, create_pool
from backend.common.tenancy import TenantContext, resolve_tenant_context
from backend.config import Settings
from backend.errors import AuthRequiredError

logger = logging.getLogger("wealthsail.dependencies")

_bearer = HTTPBearer(auto_error=False)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def today_shanghai() -> date:
    return now_shanghai().date()


async def get_pool(request: Request) -> Pool:
    pool: Pool = request.app.state.db_pool
    return pool


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def _extract_bearer(
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    if credentials is None or not credentials.credentials:
        raise AuthRequiredError("缺少 Authorization 头")
    return credentials.credentials


async def get_auth_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthSession:
    token = _extract_bearer(credentials)
    settings: Settings = request.app.state.settings
    session = decode_access_token(token, settings.jwt_secret, settings.jwt_algorithm)
    token_version = int(session.extra.get("auth_version", 1))
    pool: Pool = request.app.state.db_pool
    async with api_system_transaction(pool) as conn:
        current_version = await conn.fetchval(
            "SELECT auth_version FROM identity.profiles WHERE id = $1",
            session.user_id,
        )
    if current_version is None or int(current_version) != token_version:
        raise AuthRequiredError("访问会话已撤销，请重新登录")
    return session


def get_tenant_context(
    request: Request,
    session: AuthSession = Depends(get_auth_session),
) -> TenantContext:
    header_tenant = request.headers.get("X-Tenant-Id")
    return resolve_tenant_context(
        user_id=session.user_id,
        tenant_id=session.tenant_id,
        header_tenant_id=header_tenant,
        email=session.email,
        nickname=session.nickname,
        tenant_role=session.tenant_role,
    )


def get_request_id(request: Request) -> str:
    from backend.errors import request_id_of

    return request_id_of(request)


async def write_audit(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    actor_type: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    correlation_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Append an immutable audit row inside the caller's tenant transaction.

    audit.audit_log is append-only (trigger-protected); this helper is the only
    sanctioned write path from Domain API code.
    """
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
        correlation_id,
        json.dumps(details or {}, ensure_ascii=False),
    )


@asynccontextmanager
async def lifespan_pool(settings: Settings) -> AsyncIterator[Pool]:
    """Create/dispose the API connection pool across the app lifespan."""
    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    try:
        yield pool
    finally:
        await pool.close()
