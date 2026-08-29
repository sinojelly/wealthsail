"""Forward-only PostgreSQL migration runner with checksum and target guard.

Usage:
  MIGRATION_DATABASE_URL=... python scripts/migrate.py \
      --environment production --confirm-target db.example:5432/wealthsail

Development seeds are intentionally outside ``migrations/`` and can never be
selected by this command.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATTERN = re.compile(r"^(\d{3})_[a-zA-Z0-9_.-]+\.sql$")
LOCK_KEY = 853_271_904


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path
    checksum: str
    sql: str


def discover_migrations(directory: Path) -> list[Path]:
    paths = [
        path
        for path in directory.iterdir()
        if path.is_file() and MIGRATION_PATTERN.fullmatch(path.name)
    ]
    return sorted(paths, key=lambda path: (int(path.name[:3]), path.name))


def safe_target_name(dsn: str) -> str:
    parsed = urlsplit(dsn)
    if not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("MIGRATION_DATABASE_URL must include host and database")
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{host}/{parsed.path.strip('/')}"


def is_loopback_database(dsn: str) -> bool:
    """Return whether the DSN hostname is an explicit loopback target.

    This check is based on the parsed hostname rather than the redacted display
    string so adding a port cannot bypass the production migration guard.
    """

    hostname = (urlsplit(dsn).hostname or "").lower()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def load_migrations(directory: Path) -> list[Migration]:
    result: list[Migration] = []
    seen: set[int] = set()
    for path in discover_migrations(directory):
        version = int(path.name[:3])
        if version in seen:
            raise RuntimeError(f"duplicate migration version: {version:03d}")
        seen.add(version)
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        result.append(Migration(version, path, checksum, sql))
    if not result:
        raise RuntimeError("no migrations found")
    return result


async def _ensure_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.wealthsail_schema_migrations (
            version INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            checksum_sha256 CHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


async def _readback(conn: asyncpg.Connection) -> None:
    roles = await conn.fetch(
        """
        SELECT rolname, rolcanlogin, rolinherit, rolbypassrls
          FROM pg_roles
         WHERE rolname IN ('wealthsail_api','wealthsail_worker')
        """
    )
    role_map = {row["rolname"]: row for row in roles}
    if set(role_map) != {"wealthsail_api", "wealthsail_worker"} or any(
        row["rolcanlogin"] for row in roles
    ):
        raise RuntimeError(
            "runtime group roles are missing or unexpectedly LOGIN-enabled"
        )
    worker_role = role_map["wealthsail_worker"]
    if worker_role["rolinherit"] or not worker_role["rolbypassrls"]:
        raise RuntimeError("worker role must be NOINHERIT BYPASSRLS")
    if not await conn.fetchval(
        "SELECT pg_has_role('wealthsail_worker','wealthsail_api','MEMBER')"
    ):
        raise RuntimeError("worker cannot explicitly SET ROLE wealthsail_api")

    exposed_writes = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM information_schema.role_table_grants
         WHERE grantee='authenticated'
           AND privilege_type IN ('INSERT','UPDATE','DELETE')
           AND table_schema IN ('reporting','workflow','audit')
        """
    )
    if exposed_writes:
        raise RuntimeError("authenticated still has system-fact write grants")

    demo_users = await conn.fetchval(
        """
        SELECT COUNT(*) FROM identity.profiles
         WHERE lower(email) IN ('zhangming@example.com','lihua@example.com','wangfang@example.com')
           AND password_hash IS NOT NULL
        """
    )
    if demo_users:
        raise RuntimeError("production migration readback found login-ready demo users")

    projection_columns = {
        row["column_name"]
        for row in await conn.fetch(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema='ledger'
               AND table_name='account_nav_snapshots'
               AND column_name IN ('known_value','known_market_value')
            """
        )
    }
    if projection_columns != {"known_value"}:
        raise RuntimeError(
            "account NAV projection uses an incompatible known-value column"
        )

    tiantian_columns = {
        "portfolio_source_bindings": {
            "tenant_id",
            "account_id",
            "provider_code",
            "external_account_key",
            "status",
            "created_by_batch_id",
        },
        "portfolio_import_source_records": {
            "tenant_id",
            "batch_id",
            "record_type",
            "external_record_id",
            "normalized_payload",
            "content_hash",
        },
        "portfolio_source_transactions": {
            "tenant_id",
            "source_binding_id",
            "source_import_batch_id",
            "external_transaction_id",
            "business_type",
            "source_payload",
        },
        "position_period_metrics": {
            "tenant_id",
            "account_id",
            "instrument_id",
            "source_binding_id",
            "holding_started_on",
            "period_high_nav",
            "period_low_nav",
            "range_position_pct",
            "calculation_status",
        },
    }
    tiantian_rows = await conn.fetch(
        """
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema='ledger'
           AND table_name = ANY($1::text[])
        """,
        list(tiantian_columns),
    )
    actual_columns: dict[str, set[str]] = {
        table_name: set() for table_name in tiantian_columns
    }
    for row in tiantian_rows:
        actual_columns[row["table_name"]].add(row["column_name"])
    missing_columns = {
        table_name: sorted(required - actual_columns[table_name])
        for table_name, required in tiantian_columns.items()
        if required - actual_columns[table_name]
    }
    if missing_columns:
        raise RuntimeError(f"Tiantian import schema columns missing: {missing_columns}")

    terminal_columns = {
        row["column_name"]
        for row in await conn.fetch(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema='ledger'
               AND table_name='portfolio_import_batches'
               AND column_name IN (
                   'terminal_command_type','terminal_idempotency_key',
                   'terminal_content_hash'
               )
            """
        )
    }
    if terminal_columns != {
        "terminal_command_type",
        "terminal_idempotency_key",
        "terminal_content_hash",
    }:
        raise RuntimeError("Tiantian terminal command idempotency columns missing")

    rls_tables = {
        row["relname"]
        for row in await conn.fetch(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname='ledger'
               AND c.relname = ANY($1::text[])
               AND c.relrowsecurity
               AND c.relforcerowsecurity
            """,
            list(tiantian_columns),
        )
    }
    if rls_tables != set(tiantian_columns):
        raise RuntimeError(
            "Tiantian import tables missing ENABLE/FORCE RLS: "
            + ", ".join(sorted(set(tiantian_columns) - rls_tables))
        )

    contract_definitions = "\n".join(
        row["definition"]
        for row in await conn.fetch(
            """
            SELECT pg_get_constraintdef(c.oid) AS definition
              FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
              JOIN pg_namespace n ON n.oid = t.relnamespace
             WHERE n.nspname IN ('ledger','workflow')
               AND t.relname IN (
                   'portfolio_import_batches','portfolio_import_items',
                   'portfolio_source_bindings','portfolio_import_source_records',
                   'portfolio_source_transactions','position_period_metrics','job_runs'
               )
            """
        )
    )
    for token in (
        "TIANTIAN_FUND_ZIP",
        "POSITION_SNAPSHOT",
        "FUND_NAV_BACKFILL",
        "POSITION_PERIOD_METRICS_REBUILD",
    ):
        if token not in contract_definitions:
            raise RuntimeError(
                f"Tiantian import database constraint missing token: {token}"
            )

    required_triggers = {
        "protect_portfolio_source_bindings_trigger",
        "protect_portfolio_import_source_records_trigger",
        "protect_portfolio_source_transactions_trigger",
        "protect_portfolio_import_batches_trigger",
    }
    actual_triggers = {
        row["tgname"]
        for row in await conn.fetch(
            """
            SELECT tgname
              FROM pg_trigger
             WHERE NOT tgisinternal
               AND tgname = ANY($1::text[])
            """,
            list(required_triggers),
        )
    }
    if actual_triggers != required_triggers:
        raise RuntimeError(
            "Tiantian import integrity triggers missing: "
            + ", ".join(sorted(required_triggers - actual_triggers))
        )

    tiantian_privileges = {
        "api_binding_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.portfolio_source_bindings','SELECT')"
        ),
        "api_binding_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.portfolio_source_bindings','INSERT')"
        ),
        "api_source_record_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.portfolio_import_source_records','INSERT')"
        ),
        "api_source_transaction_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.portfolio_source_transactions','INSERT')"
        ),
        "api_metrics_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.position_period_metrics','SELECT')"
        ),
        "api_terminal_command_update": await conn.fetchval(
            """
            SELECT has_column_privilege(
                'wealthsail_api','ledger.portfolio_import_batches',
                'terminal_content_hash','UPDATE'
            )
            """
        ),
        "api_holding_start_decision_update": await conn.fetchval(
            """
            SELECT has_column_privilege(
                'wealthsail_api','ledger.portfolio_import_items',
                'user_holding_started_on','UPDATE'
            )
            """
        ),
        "worker_source_transaction_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.portfolio_source_transactions','SELECT')"
        ),
        "worker_source_binding_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.portfolio_source_bindings','SELECT')"
        ),
        "worker_import_batch_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.portfolio_import_batches','SELECT')"
        ),
        "worker_import_item_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.portfolio_import_items','SELECT')"
        ),
        "worker_metrics_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.position_period_metrics','INSERT')"
        ),
        "worker_metrics_update": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.position_period_metrics','UPDATE')"
        ),
    }
    missing_tiantian_privileges = [
        name for name, allowed in tiantian_privileges.items() if not allowed
    ]
    if missing_tiantian_privileges:
        raise RuntimeError(
            "Tiantian import runtime privileges missing: "
            + ", ".join(missing_tiantian_privileges)
        )

    import_item_update_policy = await conn.fetchrow(
        """
        SELECT qual, with_check
          FROM pg_policies
         WHERE schemaname='ledger'
           AND tablename='portfolio_import_items'
           AND policyname='domain_api_update'
           AND cmd='UPDATE'
           AND 'wealthsail_api' = ANY(roles)
        """
    )
    required_update_policy_tokens = (
        "identity.has_tenant_role",
        "portfolio_import_batches",
        "status = 'DRAFT'",
    )
    if import_item_update_policy is None or any(
        token not in str(import_item_update_policy[field] or "")
        for field in ("qual", "with_check")
        for token in required_update_policy_tokens
    ):
        raise RuntimeError(
            "portfolio import item UPDATE policy is missing or too broad"
        )

    browser_import_writes = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM information_schema.role_table_grants
         WHERE grantee='authenticated'
           AND privilege_type IN ('INSERT','UPDATE','DELETE')
           AND table_schema='ledger'
           AND table_name IN (
               'portfolio_source_bindings','portfolio_import_source_records',
               'portfolio_source_transactions','position_period_metrics'
           )
        """
    )
    if browser_import_writes:
        raise RuntimeError("authenticated has direct writes to Tiantian import tables")

    auth_version_column = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema='identity' AND table_name='profiles'
          AND column_name='auth_version' AND is_nullable='NO'
        """
    )
    if auth_version_column != 1:
        raise RuntimeError("identity.profiles.auth_version is missing or nullable")

    password_reset_rls = await conn.fetchrow(
        """
        SELECT c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname='identity' AND c.relname='password_reset_tokens'
        """
    )
    if not password_reset_rls or not all(password_reset_rls.values()):
        raise RuntimeError("password reset tokens are missing ENABLE/FORCE RLS")

    forbidden_l1_functions = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname='identity'
          AND p.proname IN ('register_with_tenant','create_tenant')
        """
    )
    if forbidden_l1_functions:
        raise RuntimeError("L1-prohibited self-registration RPCs are still installed")

    reset_function = await conn.fetchrow(
        """
        SELECT p.prosecdef, pg_get_functiondef(p.oid) AS definition
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname='identity' AND p.proname='reset_password'
          AND pg_get_function_identity_arguments(p.oid)=
              'p_token_hash text, p_new_password_hash text'
        """
    )
    if (
        not reset_function
        or not reset_function["prosecdef"]
        or "auth_version = auth_version + 1" not in reset_function["definition"]
    ):
        raise RuntimeError("reset_password does not rotate the access-token version")

    reset_privileges = {
        "api": await conn.fetchval(
            "SELECT has_function_privilege('wealthsail_api',"
            "'identity.reset_password(text,text)','EXECUTE')"
        ),
        "public": await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.routine_privileges
                WHERE routine_schema='identity'
                  AND routine_name='reset_password'
                  AND grantee='PUBLIC'
                  AND privilege_type='EXECUTE'
            )
            """
        ),
        "authenticated": await conn.fetchval(
            "SELECT has_function_privilege('authenticated',"
            "'identity.reset_password(text,text)','EXECUTE')"
        ),
    }
    if (
        not reset_privileges["api"]
        or reset_privileges["public"]
        or reset_privileges["authenticated"]
    ):
        raise RuntimeError("reset_password EXECUTE privileges are not least-privileged")

    confirmation_constraints = "\n".join(
        row["definition"]
        for row in await conn.fetch(
            """
            SELECT pg_get_constraintdef(c.oid) AS definition
            FROM pg_constraint c
            WHERE c.conrelid='ledger.trade_fills'::regclass
              AND c.conname='fk_trade_fills_confirmed_by_tenant'
            """
        )
    )
    if not all(
        token in confirmation_constraints
        for token in ("tenant_id", "confirmed_by", "tenant_members")
    ):
        raise RuntimeError("trade fill confirmation actor is not tenant-bound")

    unique_event_link = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM pg_index index_entry
        JOIN pg_class relation ON relation.oid = index_entry.indexrelid
        WHERE relation.relname='uq_trade_fills_ledger_event'
          AND index_entry.indisunique AND index_entry.indisvalid
        """
    )
    if unique_event_link != 1:
        raise RuntimeError("trade fill ledger-event uniqueness index is missing")

    confirmation_policy = await conn.fetchval(
        """
        SELECT with_check
        FROM pg_policies
        WHERE schemaname='ledger' AND tablename='trade_fills'
          AND policyname='domain_api_update'
        """
    )
    if (
        not confirmation_policy
        or "confirmed_by = auth.uid()" not in confirmation_policy
    ):
        raise RuntimeError("trade fill confirmation policy does not bind auth.uid()")

    replay_table = await conn.fetchval(
        "SELECT to_regclass('workflow.wecom_callback_claims') IS NOT NULL"
    )
    replay_function = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname='workflow'
          AND p.proname='claim_wecom_callback'
          AND p.prosecdef
        """
    )
    if not replay_table or replay_function != 1:
        raise RuntimeError("WeCom callback replay guard is missing")

    market_hot_table = await conn.fetchval(
        "SELECT to_regclass('market.market_hot_snapshots') IS NOT NULL"
    )
    if not market_hot_table:
        raise RuntimeError("Worker-owned market-hot read model is missing")

    privilege_checks = {
        "worker_snapshot_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','market.source_snapshots','INSERT')"
        ),
        "worker_observation_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','market.market_observations','INSERT')"
        ),
        "api_projection_delete": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','ledger.position_snapshots','DELETE')"
        ),
        "api_known_value_update": await conn.fetchval(
            """
            SELECT has_column_privilege(
                'wealthsail_api','ledger.account_nav_snapshots','known_value','UPDATE'
            )
            """
        ),
        "api_market_hot_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'market.market_hot_snapshots','SELECT')"
        ),
        "worker_market_hot_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'market.market_hot_snapshots','INSERT')"
        ),
        "browser_market_hot_select": not await conn.fetchval(
            "SELECT has_table_privilege('authenticated',"
            "'market.market_hot_snapshots','SELECT')"
        ),
    }
    missing = [name for name, allowed in privilege_checks.items() if not allowed]
    if missing:
        raise RuntimeError(f"runtime role privileges missing: {', '.join(missing)}")

    seed_versions = await conn.fetchval(
        "SELECT COUNT(*) FROM public.wealthsail_schema_migrations WHERE version IN (6,10)"
    )
    if seed_versions:
        raise RuntimeError("development seed entered the production migration ledger")


async def migrate(dsn: str, migrations: list[Migration], *, dry_run: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        await _ensure_ledger(conn)
        applied = {
            row["version"]: row
            for row in await conn.fetch(
                "SELECT version, filename, checksum_sha256 FROM public.wealthsail_schema_migrations"
            )
        }
        for migration in migrations:
            previous = applied.get(migration.version)
            if previous:
                if previous["checksum_sha256"].strip() != migration.checksum:
                    raise RuntimeError(
                        f"checksum drift at migration {migration.version:03d}: {migration.path.name}"
                    )
                print(f"SKIP {migration.path.name} (already applied)")
                continue
            if dry_run:
                print(f"PLAN {migration.path.name} sha256={migration.checksum[:12]}")
                continue
            async with conn.transaction():
                await conn.execute(migration.sql)
                await conn.execute(
                    """
                    INSERT INTO public.wealthsail_schema_migrations
                        (version, filename, checksum_sha256)
                    VALUES ($1,$2,$3)
                    """,
                    migration.version,
                    migration.path.name,
                    migration.checksum,
                )
            print(f"APPLIED {migration.path.name}")
        if not dry_run:
            await _readback(conn)
            print(
                "READBACK OK: roles, RLS, runtime grants, Tiantian import, "
                "identity session and trade-integrity contracts"
            )
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        finally:
            await conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment", required=True, choices=("development", "staging", "production")
    )
    parser.add_argument(
        "--confirm-target",
        required=True,
        help="Exact host[:port]/database printed by this command",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dsn = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("MIGRATION_DATABASE_URL is required; no default is allowed")
    target = safe_target_name(dsn)
    if args.confirm_target != target:
        raise SystemExit(
            f"target confirmation mismatch; expected --confirm-target {target}"
        )
    if args.environment == "production" and is_loopback_database(dsn):
        raise SystemExit("production environment cannot target a loopback database")
    print(
        f"TARGET environment={args.environment} database={target} dry_run={args.dry_run}"
    )
    asyncio.run(
        migrate(dsn, load_migrations(ROOT / "migrations"), dry_run=args.dry_run)
    )


if __name__ == "__main__":
    main()
