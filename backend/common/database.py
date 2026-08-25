"""asyncpg connection pooling and the transaction-level RLS tenancy handshake.

Every business transaction that touches tenant-scoped data MUST run through
``tenant_transaction``: it acquires a pooled connection, opens a transaction,
switches to the restricted ``authenticated`` role and injects the JWT claims
via ``set_config('request.jwt.claims', ..., is_local=true)``. PostgreSQL then
enforces row isolation through the ``identity.is_tenant_member()`` policies —
even if application code mis-scopes a query, RLS is the final boundary.

The background Worker uses ``system_connection`` with its own BYPASSRLS role
(service-role semantics): it processes rows for ALL tenants and enforces
tenant_id in application code instead.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlsplit

import asyncpg
from asyncpg.pool import Pool

RLS_ROLE = "authenticated"


def _dsn_statement_cache_size(dsn: str) -> int | None:
    """Read ``statement_cache_size`` from the DSN query string, if present.

    asyncpg does NOT parse this non-PostgreSQL parameter from the URL itself,
    so callers writing ``?statement_cache_size=0`` in a pooler DSN must pass it
    explicitly. Returns None when absent or unparsable.
    """
    try:
        values = parse_qs(urlsplit(dsn).query).get("statement_cache_size")
        if values:
            return int(values[0])
    except (ValueError, TypeError):
        return None
    return None


async def create_pool(
    dsn: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
    statement_cache_size: int | None = None,
    **pool_kwargs: Any,
) -> Pool:
    """Create an asyncpg pool.

    When connecting through a transaction pooler (PgBouncer, e.g. Supabase
    Session Pooler port 6543) the DSN itself carries
    ``statement_cache_size=0`` — asyncpg does not read it from the URL, so it
    is parsed here and passed explicitly. An explicit ``statement_cache_size``
    argument always wins over the DSN value.
    """
    if statement_cache_size is None:
        statement_cache_size = _dsn_statement_cache_size(dsn)
    pool_args: dict[str, Any] = {
        "dsn": dsn,
        "min_size": min_size,
        "max_size": max_size,
        "command_timeout": 60,
    }
    if statement_cache_size is not None:
        pool_args["statement_cache_size"] = statement_cache_size
    return await asyncpg.create_pool(**pool_args, **pool_kwargs)


def _claims_json(user_id: str, tenant_id: str, role: str) -> str:
    return json.dumps(
        {
            "sub": str(user_id),
            "role": role,
            "tenant_id": str(tenant_id),
        },
        separators=(",", ":"),
    )


@asynccontextmanager
async def tenant_transaction(
    pool: Pool,
    user_id: str,
    tenant_id: str,
    role: str = RLS_ROLE,
) -> AsyncIterator[asyncpg.Connection]:
    """Open a transaction with the tenant RLS handshake injected.

    ``SET LOCAL`` scopes both the role switch and the claims to the current
    transaction only, so pooled connections are never left in a privileged or
    cross-tenant state.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {role};")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                _claims_json(user_id, tenant_id, role),
            )
            yield conn


@asynccontextmanager
async def system_connection(pool: Pool) -> AsyncIterator[asyncpg.Connection]:
    """Borrow a pooled connection without any RLS role switch.

    Intended for the Worker (BYPASSRLS login role) and read-only bootstrap
    queries. Never use for tenant-scoped API reads.
    """
    async with pool.acquire() as conn:
        yield conn
