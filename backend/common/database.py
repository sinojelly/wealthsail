"""asyncpg connection pooling and the transaction-level RLS tenancy handshake.

Every business transaction that touches tenant-scoped data MUST run through
``tenant_transaction``: it acquires a pooled connection, opens a transaction,
switches to the restricted ``wealthsail_api`` role and injects the JWT claims
via ``set_config('request.jwt.claims', ..., is_local=true)`` plus the explicit
``app.user_id`` used by audience-scoped policies. PostgreSQL then
enforces row isolation through the ``identity.is_tenant_member()`` policies —
even if application code mis-scopes a query, RLS is the final boundary.

The background Worker pool explicitly switches to the ``wealthsail_worker``
BYPASSRLS role. PostgreSQL role attributes are not inherited merely through
membership, so this switch is part of the trust boundary.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
from asyncpg.pool import Pool

# The browser-facing Supabase ``authenticated`` role is deliberately read-only.
# Domain commands run as a separate NOLOGIN role that deployment login roles
# inherit; this prevents direct Data API clients from acquiring write policies.
RLS_ROLE = "wealthsail_api"

logger = logging.getLogger("wealthsail.database")


def _uses_supabase_transaction_pooler(dsn: str) -> bool:
    parts = urlsplit(dsn)
    hostname = (parts.hostname or "").lower()
    return parts.port == 6543 and hostname.endswith(".pooler.supabase.com")


def normalize_asyncpg_dsn(dsn: str) -> tuple[str, dict[str, int]]:
    """Separate asyncpg-only connection options from a PostgreSQL DSN.

    ``statement_cache_size`` is an asyncpg client option, not a PostgreSQL
    startup parameter.  Leaving it in the URL makes PgBouncer/Supabase pass it
    to PostgreSQL and can fail the connection before asyncpg applies the
    explicit option.  The original encoded authority is preserved while only
    that client-only query key is removed.
    """
    parts = urlsplit(dsn)
    kept: list[tuple[str, str]] = []
    options: dict[str, int] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "statement_cache_size":
            try:
                options[key] = int(value)
            except ValueError as exc:
                raise ValueError("statement_cache_size must be an integer") from exc
        else:
            kept.append((key, value))
    clean_dsn = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
    )
    return clean_dsn, options


async def create_pool(
    dsn: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
    statement_cache_size: int | None = None,
    runtime_role: str | None = None,
    **pool_kwargs: Any,
) -> Pool:
    """Create an asyncpg pool.

    When connecting through a transaction pooler (PgBouncer, e.g. Supabase
    Session Pooler port 6543) the DSN itself carries
    ``statement_cache_size=0`` — asyncpg does not read it from the URL, so it
    is parsed here and passed explicitly. An explicit ``statement_cache_size``
    argument always wins over the DSN value.
    """
    dsn, dsn_options = normalize_asyncpg_dsn(dsn)
    if statement_cache_size is None:
        statement_cache_size = dsn_options.get("statement_cache_size")
    if statement_cache_size is None and _uses_supabase_transaction_pooler(dsn):
        statement_cache_size = 0
        logger.info("disabled asyncpg statement cache for Supabase transaction pooler")
    pool_args: dict[str, Any] = {
        "dsn": dsn,
        "min_size": min_size,
        "max_size": max_size,
        "command_timeout": 60,
    }
    if statement_cache_size is not None:
        pool_args["statement_cache_size"] = statement_cache_size
    if runtime_role is not None:
        if runtime_role not in {"wealthsail_api", "wealthsail_worker"}:
            raise ValueError("unsupported database runtime role")
        if "setup" in pool_kwargs:
            raise ValueError("runtime_role cannot be combined with a custom pool setup")

        async def setup_runtime_role(conn: asyncpg.Connection) -> None:
            # asyncpg resets session state when a connection is returned to the
            # pool.  ``setup`` runs on every acquire, whereas ``init`` only runs
            # once when the physical connection is created.  The role switch is
            # therefore an acquire-time trust-boundary operation.
            await conn.execute(f"SET ROLE {runtime_role};")

        pool_args["setup"] = setup_runtime_role
    return await asyncpg.create_pool(**pool_args, **pool_kwargs)


async def verify_runtime_login_boundary(
    pool: Pool, *, process: str, skip: bool = False
) -> None:
    """Fail startup when one runtime credential can assume the other role.

    ``SET ROLE`` in application code is useful only when the login credential
    itself is least-privileged.  A shared ``postgres``/superuser credential or
    API-to-Worker membership would let a compromised API assume BYPASSRLS, so
    the process performs this readback before serving traffic.  The reverse
    direction is intentional: the already-trusted Worker downgrades to the API
    role for tenant-scoped report transactions.

    ``skip=True`` disables the readback entirely (local development against a
    superuser/pooler connection).  Production startup must never pass skip —
    ``backend/main.py`` / ``backend/worker.py`` pass
    ``skip=not settings.is_production``, so the check stays mandatory when
    ``APP_ENV=production``.
    """
    if process not in {"api", "worker"}:
        raise ValueError("process must be api or worker")
    if skip:
        logger.warning(
            "runtime role boundary check skipped for process=%s "
            "(non-production startup)",
            process,
        )
        return
    expected_parent = "wealthsail_api" if process == "api" else "wealthsail_worker"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            WITH role_policy(role_name, login_allowed, inherit_allowed,
                             bypass_allowed, parent_name, parent_inherited,
                             reachable_names) AS (
                VALUES
                  ('authenticated', FALSE, NULL::boolean, FALSE, NULL::text, FALSE,
                   ARRAY['authenticated']::text[]),
                  ('wealthsail_api', FALSE, TRUE, FALSE, 'authenticated', TRUE,
                   ARRAY['wealthsail_api','authenticated']::text[]),
                  ('wealthsail_worker', FALSE, FALSE, TRUE, 'wealthsail_api', FALSE,
                   ARRAY['wealthsail_worker','wealthsail_api','authenticated']::text[]),
                  (session_user::text, TRUE, FALSE, FALSE, '{expected_parent}', FALSE,
                   ARRAY[session_user::text,'{expected_parent}',
                         'wealthsail_api','authenticated']::text[])
            ), checked_roles AS (
                SELECT r.*, policy.login_allowed, policy.inherit_allowed,
                       policy.bypass_allowed, policy.parent_name,
                       policy.parent_inherited, policy.reachable_names
                  FROM pg_roles r JOIN role_policy policy ON policy.role_name=r.rolname
            )
            SELECT session_user::text AS session_user,
                   current_user::text AS current_user,
                   login.rolsuper AS session_superuser,
                   login.rolbypassrls AS session_bypassrls,
                   pg_has_role(session_user, 'wealthsail_api', 'SET') AS can_set_api,
                   pg_has_role(session_user, 'wealthsail_worker', 'SET') AS can_set_worker,
                   EXISTS (
                       SELECT 1 FROM checked_roles role
                        WHERE role.rolsuper OR role.rolcreatedb OR role.rolcreaterole
                           OR role.rolreplication
                           OR role.rolcanlogin <> role.login_allowed
                           OR role.rolinherit <> role.inherit_allowed
                           OR role.rolbypassrls <> role.bypass_allowed
                   ) OR EXISTS (
                       SELECT 1 FROM checked_roles role JOIN pg_roles reachable
                         ON reachable.oid <> role.oid
                        AND (pg_has_role(role.oid,reachable.oid,'SET')
                             OR pg_has_role(role.oid,reachable.oid,'USAGE'))
                        WHERE NOT reachable.rolname = ANY(role.reachable_names)
                   ) OR EXISTS (
                       SELECT 1 FROM checked_roles role
                         JOIN pg_auth_members membership ON membership.member=role.oid
                         JOIN pg_roles parent ON parent.oid=membership.roleid
                        WHERE parent.rolname IS DISTINCT FROM role.parent_name
                           OR membership.admin_option OR NOT membership.set_option
                           OR membership.inherit_option <> role.parent_inherited
                   ) AS has_unsafe_runtime_role
            FROM pg_roles login
            WHERE login.rolname = session_user
            """
        )
    if row is None:
        raise RuntimeError("database session login role could not be inspected")
    if bool(row.get("session_superuser", False)) or bool(row["session_bypassrls"]):
        raise RuntimeError("runtime database login must not be superuser/BYPASSRLS")
    if bool(row.get("has_unsafe_runtime_role", False)):
        raise RuntimeError("runtime database role has unsafe attributes or role memberships")
    if process == "api":
        if not bool(row["can_set_api"]):
            raise RuntimeError("API login cannot SET ROLE wealthsail_api")
        if bool(row["can_set_worker"]):
            raise RuntimeError("API login must not SET ROLE wealthsail_worker")
    else:
        if not bool(row["can_set_worker"]):
            raise RuntimeError("Worker login cannot SET ROLE wealthsail_worker")
        if not bool(row["can_set_api"]):
            raise RuntimeError(
                "Worker login cannot SET ROLE wealthsail_api for tenant transactions"
            )
        if row["current_user"] != "wealthsail_worker":
            raise RuntimeError("Worker pool did not activate wealthsail_worker")


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
            await apply_tenant_context(conn, user_id, tenant_id, role=role)
            yield conn


async def apply_tenant_context(
    conn: asyncpg.Connection,
    user_id: str,
    tenant_id: str,
    *,
    role: str = RLS_ROLE,
) -> None:
    """Apply the RLS role and claims inside an already-open transaction.

    This is used when a privileged Domain RPC discovers the tenant only after
    it has atomically consumed a token, but the following tenant audit row must
    still commit or roll back in that same transaction.
    """
    await conn.execute(f"SET LOCAL ROLE {role};")
    await conn.execute(
        "SELECT set_config('request.jwt.claims', $1, true);",
        _claims_json(user_id, tenant_id, role),
    )
    await conn.execute(
        "SELECT set_config('app.user_id', $1, true);",
        str(user_id),
    )


@asynccontextmanager
async def api_system_transaction(pool: Pool) -> AsyncIterator[asyncpg.Connection]:
    """Borrow a pooled connection with the API role applied transactionally.

    Unlike ``system_connection`` (which relies on the connection's default
    role), this explicitly switches to ``wealthsail_api`` for the duration of
    the transaction. Transaction-pooled deployments (Supabase Supavisor /
    PgBouncer transaction mode) reuse backend connections across clients
    without resetting session-level ``SET ROLE``, so the default role of a
    pooled connection is not predictable — another client's ``SET ROLE`` can
    leak into it. The identity tables and privileged helper RPCs are granted
    to ``wealthsail_api`` only, so every identity/session operation must run
    under that role explicitly.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE wealthsail_api;")
            yield conn


@asynccontextmanager
async def system_connection(pool: Pool) -> AsyncIterator[asyncpg.Connection]:
    """Borrow a pooled connection without any RLS role switch.

    Intended for the Worker (BYPASSRLS login role) and read-only bootstrap
    queries. Never use for tenant-scoped API reads.
    """
    async with pool.acquire() as conn:
        yield conn
