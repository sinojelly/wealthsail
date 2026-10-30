"""Opt-in real PostgreSQL rollback proof on a freshly created disposable DB.

MIGRATION_ATOMICITY_TEST_ADMIN_URL must explicitly select a loopback test server
on a non-default port. The test creates and removes only its own UUID-named DB.
It never reconciles an existing ledger or modifies an existing research record.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest

from scripts.migrate import load_migrations, migrate


ROOT = Path(__file__).resolve().parents[2]
ADMIN_DSN = os.environ.get("MIGRATION_ATOMICITY_TEST_ADMIN_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not ADMIN_DSN, reason="explicit isolated migration test server required"
)
async def test_095_ddl_and_ledger_rollback_together_then_apply_successfully():
    target = urlsplit(ADMIN_DSN)
    assert target.hostname in {"127.0.0.1", "localhost", "::1"}
    assert target.port is not None and 55400 <= target.port <= 55499
    assert not target.query and not target.fragment
    assert target.path == "/postgres" or target.path.startswith("/wealthsail_review")

    database = "wealthsail_review61_migration_" + uuid4().hex
    isolated_dsn = urlunsplit(target._replace(path="/" + database))
    migrations = load_migrations(ROOT / "migrations")
    pipeline = next(item for item in migrations if item.version == 95)
    admin = await asyncpg.connect(ADMIN_DSN)
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
        created = True
        await migrate(
            isolated_dsn,
            [item for item in migrations if item.version < 95],
            dry_run=False,
        )
        conn = await asyncpg.connect(isolated_dsn)
        try:
            await conn.execute(
                """CREATE FUNCTION public.reject_test_095_ledger() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                  IF NEW.version = 95 THEN
                    RAISE EXCEPTION 'injected 095 ledger write failure';
                  END IF;
                  RETURN NEW;
                END $$;
                CREATE TRIGGER reject_test_095_ledger
                BEFORE INSERT ON public.wealthsail_schema_migrations
                FOR EACH ROW EXECUTE FUNCTION public.reject_test_095_ledger();"""
            )
        finally:
            await conn.close()

        with pytest.raises(
            asyncpg.RaiseError, match="injected 095 ledger write failure"
        ):
            await migrate(isolated_dsn, [pipeline], dry_run=False)

        conn = await asyncpg.connect(isolated_dsn)
        try:
            assert (
                await conn.fetchval(
                    "SELECT to_regclass('research.daily_research_pipelines')"
                )
                is None
            )
            assert not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.wealthsail_schema_migrations WHERE version=95)"
            )
            await conn.execute(
                """DROP TRIGGER reject_test_095_ledger ON public.wealthsail_schema_migrations;
                DROP FUNCTION public.reject_test_095_ledger();"""
            )
        finally:
            await conn.close()

        await migrate(isolated_dsn, [pipeline], dry_run=False)
        conn = await asyncpg.connect(isolated_dsn)
        try:
            assert await conn.fetchval(
                "SELECT to_regclass('research.daily_research_pipelines')"
            )
            assert (
                await conn.fetchval(
                    "SELECT checksum_sha256 FROM public.wealthsail_schema_migrations WHERE version=95"
                )
                == pipeline.checksum
            )
        finally:
            await conn.close()
    finally:
        if created:
            # database is generated here, never supplied by an environment variable.
            await admin.execute(f'DROP DATABASE "{database}"')
        await admin.close()
