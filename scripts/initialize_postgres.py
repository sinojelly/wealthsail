"""Install or upgrade WealthSail in a dedicated, self-hosted PostgreSQL database.

Run through the NAS db-init Compose service. Admin and initial Web passwords
are read with getpass and are never stored in the application's env files.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import getpass
import os
from pathlib import Path
import sys
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.common.database import create_pool, verify_runtime_login_boundary  # noqa: E402
from scripts.configure_nas import database_dsn, validate_database_name  # noqa: E402
from scripts.migrate import load_migrations, migrate  # noqa: E402

SETUP_LOCK = 5_788_747_803_222
DEDICATED_ROLES = (
    "wealthsail_api",
    "wealthsail_worker",
    "wealthsail_api_login",
    "wealthsail_worker_login",
)


@dataclass(frozen=True)
class RuntimeTarget:
    host: str
    port: int
    database: str
    api_dsn: str
    worker_dsn: str

    @property
    def display(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}/{self.database}"

    def admin_dsn(self, user: str, password: str, *, maintenance: bool = False) -> str:
        base = database_dsn(
            host=self.host,
            port=self.port,
            database="postgres" if maintenance else self.database,
            user=user,
            password=password,
        )
        # Preserve TLS options; the asyncpg statement cache is a client option.
        query = urlencode(
            [
                (k, v)
                for k, v in parse_qsl(urlsplit(self.api_dsn).query)
                if k != "statement_cache_size"
            ]
        )
        return urlunsplit(urlsplit(base)._replace(query=query))


def validate_runtime_targets(
    api_dsn: str, worker_dsn: str, confirmation: str
) -> RuntimeTarget:
    parsed = [urlsplit(api_dsn), urlsplit(worker_dsn)]
    for item, role in zip(
        parsed, ("wealthsail_api_login", "wealthsail_worker_login"), strict=True
    ):
        if (
            item.scheme not in {"postgres", "postgresql"}
            or not item.hostname
            or unquote(item.username or "") != role
            or not item.password
            or item.fragment
        ):
            raise ValueError(
                "runtime DSNs require separate PostgreSQL API/Worker login credentials"
            )
        validate_database_name(unquote(item.path.lstrip("/")))
    api, worker = parsed
    if (api.hostname, api.port or 5432, api.path) != (
        worker.hostname,
        worker.port or 5432,
        worker.path,
    ):
        raise ValueError(
            "API and Worker DSNs must point to the same host, port and database"
        )
    if unquote(api.password) == unquote(worker.password):
        raise ValueError("API and Worker database passwords must be independent")
    target = RuntimeTarget(
        api.hostname,
        api.port or 5432,
        unquote(api.path.lstrip("/")),
        api_dsn,
        worker_dsn,
    )
    if confirmation != target.display:
        raise ValueError(f"confirm-target must exactly match {target.display}")
    return target


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


async def _validate_authenticated_role(conn: asyncpg.Connection) -> None:
    if not await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated')"
    ):
        return
    dangerous = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_roles r
             WHERE r.oid <> 'authenticated'::regrole
               AND (pg_has_role('authenticated', r.oid, 'SET')
                    OR pg_has_role('authenticated', r.oid, 'USAGE'))
        ) OR EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname='authenticated'
               AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole
                    OR rolcreatedb OR rolreplication)
        ) OR EXISTS (
            SELECT 1 FROM pg_auth_members
             WHERE member = 'authenticated'::regrole
        )
        """
    )
    if dangerous:
        raise RuntimeError(
            "existing authenticated role has unsafe cluster privileges; use an isolated PostgreSQL cluster"
        )


_EXPECTED_MEMBERSHIPS = {
    ("wealthsail_api", "authenticated"): True,
    ("wealthsail_worker", "wealthsail_api"): False,
    ("wealthsail_api_login", "wealthsail_api"): False,
    ("wealthsail_worker_login", "wealthsail_worker"): False,
}


async def _validate_dedicated_roles(
    conn: asyncpg.Connection,
    *,
    require_complete: bool = False,
    allow_initial_worker_inherit: bool = False,
) -> None:
    """Reject privilege drift rather than repairing unknown cluster identities.

    Missing roles/edges are allowed before migrations and provisioning. The
    short-lived INHERIT Worker created by 009 is accepted only on a verified
    pre-013 retry; it has no inherited parent role at that point.
    """
    rows = await conn.fetch(
        """SELECT rolname, rolcanlogin, rolinherit, rolbypassrls, rolsuper,
                  rolcreatedb, rolcreaterole, rolreplication
             FROM pg_roles WHERE rolname = ANY($1::text[])""",
        list(DEDICATED_ROLES),
    )
    if require_complete and {row["rolname"] for row in rows} != set(DEDICATED_ROLES):
        raise RuntimeError("runtime cluster role matrix is incomplete: missing roles")
    for row in rows:
        name = row["rolname"]
        if name not in DEDICATED_ROLES:
            raise RuntimeError("unsafe runtime cluster role attributes")
        expected_inherit = name == "wealthsail_api"
        initial_worker = (
            name == "wealthsail_worker"
            and allow_initial_worker_inherit
            and row["rolinherit"]
        )
        if (
            any(
                row[key]
                for key in (
                    "rolsuper",
                    "rolcreatedb",
                    "rolcreaterole",
                    "rolreplication",
                )
            )
            or row["rolcanlogin"] != name.endswith("_login")
            or row["rolbypassrls"] != (name == "wealthsail_worker")
            or (row["rolinherit"] != expected_inherit and not initial_worker)
        ):
            raise RuntimeError(f"unsafe runtime cluster role attributes: {name}")
    memberships = await conn.fetch(
        """SELECT member_role.rolname AS member_name, parent_role.rolname AS role_name,
                  m.admin_option, m.inherit_option, m.set_option
             FROM pg_auth_members m
             JOIN pg_roles member_role ON member_role.oid = m.member
             JOIN pg_roles parent_role ON parent_role.oid = m.roleid
            WHERE member_role.rolname = ANY($1::text[])""",
        list(DEDICATED_ROLES),
    )
    observed = set()
    for row in memberships:
        pair = (row["member_name"], row["role_name"])
        observed.add(pair)
        if (
            pair not in _EXPECTED_MEMBERSHIPS
            or row["admin_option"]
            or not row["set_option"]
            or row["inherit_option"] != _EXPECTED_MEMBERSHIPS[pair]
            or (
                allow_initial_worker_inherit
                and row["member_name"] == "wealthsail_worker"
                and any(
                    r["rolname"] == "wealthsail_worker" and r["rolinherit"]
                    for r in rows
                )
            )
        ):
            raise RuntimeError("unsafe runtime cluster role membership")
    if require_complete and observed != set(_EXPECTED_MEMBERSHIPS):
        raise RuntimeError("runtime cluster role membership matrix is incomplete")


async def _validate_role_database_dependencies(
    conn: asyncpg.Connection,
    database: str,
) -> None:
    """Include database ACL/owner dependencies, whose dbid is zero."""
    dependencies = await conn.fetch(
        """
        SELECT DISTINCT r.rolname, db.datname AS database_name
          FROM pg_shdepend dependency
          JOIN pg_roles r ON r.oid = dependency.refobjid
          JOIN pg_database db ON db.oid = CASE
              WHEN dependency.dbid = 0
                   AND dependency.classid = 'pg_database'::regclass
              THEN dependency.objid ELSE dependency.dbid END
         WHERE dependency.refclassid = 'pg_authid'::regclass
           AND r.rolname = ANY($1::text[])
           AND db.datname <> $2
        """,
        list(DEDICATED_ROLES),
        database,
    )
    if dependencies:
        raise RuntimeError(
            "WealthSail cluster role conflict: roles have dependencies in another database"
        )


_LEDGER_COLUMNS = [
    ("version", "integer", True),
    ("filename", "text", True),
    ("checksum_sha256", "character(64)", True),
    ("applied_at", "timestamp with time zone", True),
]
_MARKER_COLUMNS = [
    ("singleton", "boolean", True),
    ("initial_cleanup_done", "boolean", True),
    ("created_at", "timestamp with time zone", True),
]


async def _validate_installation_table(
    conn: asyncpg.Connection,
    name: str,
    *,
    marker: bool,
) -> None:
    structure = await conn.fetchrow(
        """
        SELECT c.relkind = 'r' AS is_table,
               c.relowner = (SELECT oid FROM pg_roles WHERE rolname=current_user)
                   AS owner_matches,
               EXISTS (
                   SELECT 1 FROM pg_constraint p JOIN pg_attribute a
                     ON a.attrelid=p.conrelid AND a.attnum=p.conkey[1]
                    WHERE p.conrelid=c.oid AND p.contype='p'
                      AND cardinality(p.conkey)=1 AND a.attname=$2
               ) AS primary_key_valid,
               EXISTS (
                   SELECT 1 FROM pg_constraint p
                    WHERE p.conrelid=c.oid AND p.contype='c' AND p.convalidated
                      AND regexp_replace(pg_get_expr(p.conbin,p.conrelid),
                                         '[()[:space:]]', '', 'g')='singleton'
               ) AS singleton_check_valid
          FROM pg_class c WHERE c.oid=$1::regclass
        """,
        name,
        "singleton" if marker else "version",
    )
    if (
        structure is None
        or not structure["is_table"]
        or not structure["owner_matches"]
        or not structure["primary_key_valid"]
        or (marker and not structure["singleton_check_valid"])
    ):
        raise RuntimeError(f"untrusted installation table owner or structure: {name}")
    columns = await conn.fetch(
        """SELECT attname AS column_name, format_type(atttypid,atttypmod) AS data_type,
                  attnotnull AS not_null
             FROM pg_attribute WHERE attrelid=$1::regclass
              AND attnum>0 AND NOT attisdropped ORDER BY attnum""",
        name,
    )
    actual = [
        (row["column_name"], row["data_type"], row["not_null"]) for row in columns
    ]
    if actual != (_MARKER_COLUMNS if marker else _LEDGER_COLUMNS):
        raise RuntimeError(f"untrusted installation table column structure: {name}")


async def _inspect_existing_target(conn: asyncpg.Connection) -> tuple[bool, bool]:
    """Return (recognized application DB, pending installation marker exists)."""
    ledger = await conn.fetchval(
        "SELECT to_regclass('public.wealthsail_schema_migrations') IS NOT NULL"
    )
    marker = await conn.fetchval(
        "SELECT to_regclass('public.wealthsail_nas_installation') IS NOT NULL"
    )
    cleanup_done = False
    if marker:
        await _validate_installation_table(
            conn, "public.wealthsail_nas_installation", marker=True
        )
        state = await conn.fetchrow(
            """SELECT count(*) AS row_count, bool_and(singleton) AS singleton_valid,
                      bool_and(initial_cleanup_done) AS cleanup_done
                 FROM public.wealthsail_nas_installation"""
        )
        if (
            state is None
            or state["row_count"] != 1
            or state["singleton_valid"] is not True
        ):
            raise RuntimeError("untrusted installation marker singleton state")
        cleanup_done = state["cleanup_done"]
    applied = []
    if ledger:
        await _validate_installation_table(
            conn, "public.wealthsail_schema_migrations", marker=False
        )
        applied = await conn.fetch(
            "SELECT version, filename, checksum_sha256 FROM public.wealthsail_schema_migrations"
        )
        known = {item.version: item for item in load_migrations(ROOT / "migrations")}
        for row in applied:
            expected = known.get(row["version"])
            if (
                expected is None
                or row["filename"] != expected.path.name
                or row["checksum_sha256"].strip() != expected.checksum
            ):
                raise RuntimeError("untrusted migration ledger history or checksum")
        if applied:
            if not any(row["version"] == 1 for row in applied):
                raise RuntimeError("untrusted migration ledger lacks initial migration")
            ddl_present = await conn.fetchval(
                """SELECT to_regprocedure('auth.uid()') IS NOT NULL AND (
                       SELECT count(*)=10 AND bool_and(nspowner=(
                           SELECT oid FROM pg_roles WHERE rolname=current_user))
                         FROM pg_namespace WHERE nspname=ANY($1::text[]))""",
                [
                    "auth",
                    "identity",
                    "reference",
                    "ledger",
                    "market",
                    "research",
                    "reporting",
                    "risk",
                    "workflow",
                    "audit",
                ],
            )
            if not ddl_present:
                raise RuntimeError("untrusted migration ledger has missing schema DDL")
    if not applied and (not marker or cleanup_done):
        if ledger or marker:
            raise RuntimeError(
                "untrusted empty migration ledger or completed installation marker"
            )
    if marker or applied:
        return True, bool(marker)
    objects = await conn.fetchval(
        """
        SELECT (
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND left(n.nspname,3) <> 'pg_'
               AND c.relkind IN ('r','p','v','m','S','f')
        ) + (
            SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND left(n.nspname,3) <> 'pg_'
        ) + (
            SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND left(n.nspname,3) <> 'pg_'
        ) + (
            SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql'
        )
        """
    )
    schemas = await conn.fetchval(
        """SELECT count(*) FROM pg_namespace
            WHERE nspname NOT IN ('pg_catalog','information_schema','public')
              AND left(nspname,3) <> 'pg_'"""
    )
    if objects or schemas:
        raise RuntimeError(
            "target database is not an empty or recognized WealthSail database"
        )
    return False, False


async def _ensure_fresh_marker(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE public.wealthsail_nas_installation (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            initial_cleanup_done BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        INSERT INTO public.wealthsail_nas_installation(singleton) VALUES (TRUE);
        """
    )


async def verify_runtime_connections(api_dsn: str, worker_dsn: str) -> None:
    for process, dsn in (("api", api_dsn), ("worker", worker_dsn)):
        pool = await create_pool(
            dsn,
            min_size=1,
            max_size=1,
            runtime_role="wealthsail_worker" if process == "worker" else None,
        )
        try:
            await verify_runtime_login_boundary(pool, process=process)
        finally:
            await pool.close()


async def initialize_database(
    api_dsn: str,
    worker_dsn: str,
    *,
    admin_user: str,
    admin_password: str,
    confirm_target: str,
    bootstrap: dict[str, str] | None = None,
) -> dict:
    target = validate_runtime_targets(api_dsn, worker_dsn, confirm_target)
    if not admin_user or admin_user in DEDICATED_ROLES or not admin_password:
        raise ValueError(
            "an independent PostgreSQL administrator and password are required"
        )
    maintenance = await asyncpg.connect(
        target.admin_dsn(admin_user, admin_password, maintenance=True)
    )
    conn = None
    locked = False
    try:
        if not await maintenance.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ):
            raise RuntimeError(
                "installation requires a PostgreSQL superuser for extensions and BYPASSRLS roles"
            )
        version = int(await maintenance.fetchval("SHOW server_version_num"))
        if version < 160000:
            raise RuntimeError(
                "installation requires PostgreSQL 16 or newer (NAS target is 18)"
            )
        await maintenance.execute("SELECT pg_advisory_lock($1)", SETUP_LOCK)
        locked = True
        await _validate_authenticated_role(maintenance)
        roles = await maintenance.fetch(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[])",
            list(DEDICATED_ROLES),
        )
        exists = await maintenance.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=$1)",
            target.database,
        )
        if roles and not exists:
            raise RuntimeError(
                "WealthSail cluster role conflict: target database does not exist"
            )
        created = not exists
        if created:
            await maintenance.execute(
                f"CREATE DATABASE {_identifier(target.database)} TEMPLATE template0 ENCODING 'UTF8'"
            )
        conn = await asyncpg.connect(target.admin_dsn(admin_user, admin_password))
        recognized, has_marker = await _inspect_existing_target(conn)
        if roles and not recognized:
            raise RuntimeError(
                "WealthSail cluster role conflict: target database has no installation ledger"
            )
        await _validate_role_database_dependencies(maintenance, target.database)
        has_ledger = await conn.fetchval(
            "SELECT to_regclass('public.wealthsail_schema_migrations') IS NOT NULL"
        )
        has_013 = has_ledger and await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM public.wealthsail_schema_migrations WHERE version=13)"
        )
        await _validate_dedicated_roles(
            maintenance,
            allow_initial_worker_inherit=not has_013,
        )
        if not recognized:
            await _ensure_fresh_marker(conn)
            has_marker = True
        print(f"Target verified: {target.display}; PostgreSQL {version // 10000}")
        await migrate(
            target.admin_dsn(admin_user, admin_password),
            load_migrations(ROOT / "migrations"),
            dry_run=False,
        )
        await _validate_dedicated_roles(maintenance)
        # The historical filename is also used by local/CI callers. Its SQL
        # handles a cluster whose administrator is admin rather than postgres.
        async with conn.transaction():
            await conn.execute(
                (ROOT / "scripts/provision_supabase_roles.sql").read_text("utf-8")
            )
            await _validate_dedicated_roles(conn, require_complete=True)
            for role, dsn in (
                ("wealthsail_api_login", api_dsn),
                ("wealthsail_worker_login", worker_dsn),
            ):
                literal = await conn.fetchval(
                    "SELECT quote_literal($1::text)", unquote(urlsplit(dsn).password)
                )
                await conn.execute(
                    f"ALTER ROLE {_identifier(role)} WITH PASSWORD {literal}"
                )
            await conn.execute(
                f"REVOKE ALL ON DATABASE {_identifier(target.database)} FROM PUBLIC"
            )
            await conn.execute(
                f"GRANT CONNECT ON DATABASE {_identifier(target.database)} TO wealthsail_api_login, wealthsail_worker_login"
            )
        result: dict = {"target": target.display, "createdDatabase": created}
        async with conn.transaction():
            pending = has_marker and await conn.fetchval(
                "SELECT NOT initial_cleanup_done FROM public.wealthsail_nas_installation WHERE singleton"
            )
            if pending:
                from scripts.fresh_install_data import clean_fresh_install_data

                result.update(await clean_fresh_install_data(conn))
            if bootstrap is not None:
                from scripts.bootstrap_admin import bootstrap_admin

                result["administrator"] = await bootstrap_admin(conn, **bootstrap)
            if pending:
                await conn.execute(
                    "UPDATE public.wealthsail_nas_installation SET initial_cleanup_done=TRUE WHERE singleton"
                )
        await verify_runtime_connections(api_dsn, worker_dsn)
        print(
            "Runtime API/Worker login boundaries verified. No development seeds were executed."
        )
        return result
    finally:
        if conn is not None:
            await conn.close()
        try:
            if locked:
                await maintenance.execute("SELECT pg_advisory_unlock($1)", SETUP_LOCK)
        finally:
            await maintenance.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-target", required=True, help="exact host:port/database"
    )
    parser.add_argument(
        "--bootstrap-email",
        help="create the first Web platform administrator in an empty installation",
    )
    parser.add_argument("--tenant-name", default="我的投研工作台")
    parser.add_argument("--nickname", default="管理员")
    args = parser.parse_args()
    try:
        api_dsn = os.environ.get("DATABASE_URL", "")
        worker_dsn = os.environ.get("WORKER_DATABASE_URL", "")
        validate_runtime_targets(api_dsn, worker_dsn, args.confirm_target)
        admin_password = getpass.getpass(
            "PostgreSQL administrator password (not stored): "
        )
        bootstrap = None
        if args.bootstrap_email:
            password = getpass.getpass(
                "Initial Web administrator password (12+ characters): "
            )
            if password != getpass.getpass("Repeat Web administrator password: "):
                raise ValueError("Web administrator passwords do not match")
            bootstrap = {
                "email": args.bootstrap_email,
                "password": password,
                "nickname": args.nickname,
                "tenant_name": args.tenant_name,
            }
        result = asyncio.run(
            initialize_database(
                api_dsn,
                worker_dsn,
                admin_user=os.environ.get("POSTGRES_ADMIN_USER", "admin"),
                admin_password=admin_password,
                confirm_target=args.confirm_target,
                bootstrap=bootstrap,
            )
        )
    except (ValueError, RuntimeError) as exc:
        parser.exit(2, f"Installation stopped: {exc}\n")
    except (asyncpg.PostgresError, OSError) as exc:
        # SQL statements can contain role password literals. Do not dump a
        # traceback, DSN or server error details into deployment logs.
        parser.exit(
            2,
            f"Installation failed ({type(exc).__name__}); check connectivity, permissions and the last migration step.\n",
        )
    print(f"Ready: {result['target']}")
    if "administrator" in result:
        print(
            "Web administrator created."
            if result["administrator"]["created"]
            else "Existing Web administrator retained; password unchanged."
        )
    if "removedApproximateHoldings" in result:
        print(
            f"First-install approximate holdings removed: {result['removedApproximateHoldings']}"
        )


if __name__ == "__main__":
    main()
