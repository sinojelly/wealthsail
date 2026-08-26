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
        raise RuntimeError("runtime group roles are missing or unexpectedly LOGIN-enabled")
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
        raise RuntimeError("account NAV projection uses an incompatible known-value column")

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
            print("READBACK OK: roles, browser grants and demo-user guard")
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
    if args.environment == "production" and target.startswith(("localhost/", "127.0.0.1/")):
        raise SystemExit("production environment cannot target a loopback database")
    print(f"TARGET environment={args.environment} database={target} dry_run={args.dry_run}")
    asyncio.run(migrate(dsn, load_migrations(ROOT / "migrations"), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
