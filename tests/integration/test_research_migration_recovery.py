"""104 -> 106 recovery on an explicitly disposable PostgreSQL 16+ cluster.

Set RESEARCH_MIGRATION_TEST_ADMIN_DATABASE_URL (or NAS_TEST_ADMIN_DATABASE_URL)
to a disposable loopback server's /postgres database as migration_test_admin or
nas_test_admin, on an explicit port. This suite creates and
drops only UUID-named databases. Migrations create the normal cluster roles, so
the server must not be shared with local development or deployed applications.
No workspace .env or deployment credentials are read.
"""

from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import os
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from scripts.migrate import LOCK_KEY, _readback, load_migrations, migrate


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = load_migrations(ROOT / "migrations")
pytestmark = pytest.mark.asyncio(loop_scope="module")


def _admin_url():
    dsn = os.environ.get(
        "RESEARCH_MIGRATION_TEST_ADMIN_DATABASE_URL"
    ) or os.environ.get("NAS_TEST_ADMIN_DATABASE_URL", "")
    if not dsn:
        pytest.skip("disposable migration recovery PostgreSQL is not configured")
    target = urlsplit(dsn)
    assert target.scheme in {"postgres", "postgresql"}
    assert target.hostname in {"127.0.0.1", "localhost", "::1"}
    assert target.username in {"migration_test_admin", "nas_test_admin"}
    assert target.port is not None
    assert target.path == "/postgres" and not target.fragment
    assert all(
        key == "sslmode" and value == "disable"
        for key, value in parse_qsl(target.query)
    )
    return dsn


@asynccontextmanager
async def _database(admin_url, *, template=None):
    database = "wealthsail_migration_recovery_" + uuid4().hex
    dsn = urlunsplit(urlsplit(admin_url)._replace(path="/" + database))
    admin = await asyncpg.connect(admin_url)
    created = False
    try:
        suffix = f' TEMPLATE "{template}"' if template else ""
        await admin.execute(f'CREATE DATABASE "{database}"{suffix}')
        created = True
        yield dsn
    finally:
        if created:
            await admin.execute(f'DROP DATABASE "{database}"')
        await admin.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated_template():
    admin_url = _admin_url()
    async with _database(admin_url) as dsn:
        await migrate(dsn, MIGRATIONS, dry_run=False)
        yield admin_url, urlsplit(dsn).path[1:]


@pytest_asyncio.fixture(loop_scope="module")
async def database(migrated_template):
    admin_url, template = migrated_template
    async with _database(admin_url, template=template) as dsn:
        yield dsn


async def test_104_upgrade_to_106_and_repeat_run_all_skip(migrated_template, capsys):
    admin_url, _ = migrated_template
    async with _database(admin_url) as dsn:
        await migrate(
            dsn, [item for item in MIGRATIONS if item.version <= 104], dry_run=False
        )
        capsys.readouterr()
        await migrate(dsn, MIGRATIONS, dry_run=False)
        upgrade = capsys.readouterr().out
        assert "APPLIED 105_research_queue_control.sql" in upgrade
        assert "APPLIED 106_research_local_exit_evidence.sql" in upgrade
        assert "READBACK OK:" in upgrade

        conn = await asyncpg.connect(dsn)
        try:
            await _readback(conn)
            ledger_before = await conn.fetch(
                "SELECT * FROM public.wealthsail_schema_migrations ORDER BY version"
            )
        finally:
            await conn.close()
        await migrate(dsn, MIGRATIONS, dry_run=False)
        repeated = capsys.readouterr().out
        assert repeated.count("SKIP ") == len(MIGRATIONS)
        assert "APPLIED " not in repeated and "READBACK OK:" in repeated
        for name in (
            "104_opportunity_fund_cache.sql",
            "105_research_queue_control.sql",
            "106_research_local_exit_evidence.sql",
        ):
            assert f"SKIP {name} (already applied)" in repeated
        await migrate(dsn, MIGRATIONS, dry_run=False, readback_only=True)
        resumed = capsys.readouterr().out
        assert resumed.count("SKIP ") == len(MIGRATIONS)
        assert "APPLIED " not in resumed and "READBACK OK:" in resumed
        conn = await asyncpg.connect(dsn)
        try:
            assert (
                await conn.fetch(
                    "SELECT * FROM public.wealthsail_schema_migrations ORDER BY version"
                )
                == ledger_before
            )
        finally:
            await conn.close()


async def test_readback_only_requires_existing_ledger_without_creating_it(
    migrated_template,
):
    async with _database(migrated_template[0]) as dsn:
        with pytest.raises(RuntimeError, match="ledger.*missing|missing.*ledger"):
            await migrate(dsn, MIGRATIONS, dry_run=False, readback_only=True)
        conn = await asyncpg.connect(dsn)
        try:
            assert (
                await conn.fetchval(
                    "SELECT to_regclass('public.wealthsail_schema_migrations')"
                )
                is None
            )
        finally:
            await conn.close()


@pytest.mark.parametrize(
    "tamper, error",
    [
        (
            "DELETE FROM public.wealthsail_schema_migrations WHERE version=105",
            "missing",
        ),
        (
            "INSERT INTO public.wealthsail_schema_migrations(version,filename,checksum_sha256) VALUES(999,'999_unknown.sql',repeat('0',64))",
            "unknown|unexpected",
        ),
        (
            "UPDATE public.wealthsail_schema_migrations SET filename='105_wrong.sql' WHERE version=105",
            "filename",
        ),
        (
            "UPDATE public.wealthsail_schema_migrations SET checksum_sha256=repeat('0',64) WHERE version=105",
            "checksum drift",
        ),
    ],
)
async def test_readback_only_rejects_ledger_drift_without_writes(
    database, tamper, error, capsys
):
    conn = await asyncpg.connect(database)
    try:
        await conn.execute(tamper)
        before = await conn.fetch(
            "SELECT * FROM public.wealthsail_schema_migrations ORDER BY version"
        )
        with pytest.raises(RuntimeError, match=error):
            await migrate(database, MIGRATIONS, dry_run=False, readback_only=True)
        assert (
            await conn.fetch(
                "SELECT * FROM public.wealthsail_schema_migrations ORDER BY version"
            )
            == before
        )
        assert "APPLIED " not in capsys.readouterr().out
    finally:
        await conn.close()


async def test_readback_only_uses_read_only_transaction(database, monkeypatch):
    async def attempt_write(conn):
        await conn.execute("CREATE TABLE public.readback_must_never_write(id integer)")

    monkeypatch.setattr("scripts.migrate._readback", attempt_write)
    with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
        await migrate(database, MIGRATIONS, dry_run=False, readback_only=True)


async def test_readback_only_rechecks_ledger_after_obtaining_migration_lock(database):
    conn = await asyncpg.connect(database)
    task = None
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        task = asyncio.create_task(
            migrate(database, MIGRATIONS, dry_run=False, readback_only=True)
        )
        for _ in range(100):
            waiting = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)"
            )
            if waiting:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("readback runner did not wait for the migration lock")
        await conn.execute(
            "DELETE FROM public.wealthsail_schema_migrations WHERE version=105"
        )
        await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        with pytest.raises(RuntimeError, match="missing"):
            await task
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.wealthsail_schema_migrations WHERE version=105)"
        )
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await conn.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "REVOKE UPDATE(execution_stage) ON research.daily_research_refresh_requests FROM wealthsail_worker",
        "GRANT UPDATE(execution_stage) ON research.daily_research_refresh_requests TO wealthsail_api",
        "ALTER TABLE research.daily_research_refresh_requests DROP CONSTRAINT research_refresh_stage_shape",
        "GRANT SELECT ON research.local_research_recovery_audit TO wealthsail_api",
        "GRANT UPDATE(owner_token) ON research.daily_research_model_guards TO wealthsail_worker",
        "ALTER TABLE research.daily_research_model_guards DROP CONSTRAINT daily_research_model_guards_release_proof_check",
        "GRANT EXECUTE ON FUNCTION research.lock_local_research_maintenance() TO authenticated",
        "ALTER FUNCTION research.expire_queued_research_refresh(uuid,uuid,timestamptz) SECURITY INVOKER",
        "ALTER TABLE research.daily_research_refresh_requests DROP CONSTRAINT research_refresh_status, ADD CONSTRAINT research_refresh_status CHECK (true)",
        "ALTER TABLE research.daily_research_refresh_requests DROP CONSTRAINT research_refresh_state_shape, ADD CONSTRAINT research_refresh_state_shape CHECK (true)",
        "ALTER TABLE research.daily_research_refresh_requests DROP CONSTRAINT daily_research_refresh_requests_execution_stage_check, ADD CONSTRAINT daily_research_refresh_requests_execution_stage_check CHECK (true)",
        "ALTER TABLE research.daily_research_refresh_requests DROP CONSTRAINT research_refresh_stage_shape, ADD CONSTRAINT research_refresh_stage_shape CHECK (true)",
        "ALTER TABLE research.daily_research_model_guards DROP CONSTRAINT daily_research_model_guards_exit_evidence_check, ADD CONSTRAINT daily_research_model_guards_exit_evidence_check CHECK (true)",
        "REVOKE UPDATE(exit_evidence) ON research.daily_research_model_guards FROM wealthsail_worker",
        "GRANT SELECT(exit_evidence) ON research.daily_research_model_guards TO wealthsail_api",
        "GRANT UPDATE(proof) ON research.local_research_recovery_audit TO wealthsail_worker",
        "ALTER TABLE research.local_research_recovery_audit NO FORCE ROW LEVEL SECURITY",
        "ALTER FUNCTION research.expire_queued_research_refresh(uuid,uuid,timestamptz) SET search_path=public",
        "ALTER FUNCTION research.expire_queued_research_refresh(uuid,uuid,timestamptz) OWNER TO wealthsail_api",
        "GRANT EXECUTE ON FUNCTION research.research_refresh_control_actor(uuid,uuid,boolean) TO wealthsail_worker",
        "GRANT EXECUTE ON FUNCTION research.lock_local_research_maintenance() TO PUBLIC",
        "ALTER TABLE research.local_research_recovery_audit DROP CONSTRAINT local_research_recovery_audit_proof_check, ADD CONSTRAINT local_research_recovery_audit_proof_check CHECK (true)",
        "ALTER TABLE research.local_research_recovery_audit DROP CONSTRAINT local_research_recovery_audit_check1",
        "ALTER TABLE research.local_research_recovery_audit DROP CONSTRAINT local_research_recovery_audit_guard_snapshot_check",
        "ALTER TABLE research.local_research_recovery_audit DROP CONSTRAINT local_research_recovery_audit_expired_queued_check",
        "REVOKE INSERT ON research.local_research_recovery_audit FROM wealthsail_worker; GRANT INSERT(id) ON research.local_research_recovery_audit TO wealthsail_worker",
        "DROP POLICY research_refresh_control_owner ON research.daily_research_refresh_requests",
        "ALTER POLICY research_guard_control_owner ON research.daily_research_model_guards TO wealthsail_worker",
        "ALTER POLICY local_research_recovery_owner_cutoff ON research.local_research_recovery_audit USING(false)",
    ],
)
async def test_complete_readback_rejects_105_106_boundary_drift(database, tamper):
    conn = await asyncpg.connect(database)
    try:
        await conn.execute(tamper)
        with pytest.raises(RuntimeError, match="boundary|constraint"):
            await _readback(conn)
    finally:
        await conn.close()


async def test_worker_progress_and_exit_evidence_use_only_authorized_columns(database):
    conn = await asyncpg.connect(database)
    tenant, actor, request, run = (uuid4() for _ in range(4))
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO identity.tenants(id,name) VALUES($1,'migration recovery fixture')",
                tenant,
            )
            await conn.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'migration recovery fixture')",
                actor,
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
                tenant,
                actor,
            )
            started = await conn.fetchval("SELECT clock_timestamp()")
            await conn.execute(
                """INSERT INTO research.daily_research_refresh_requests
                (id,tenant_id,user_id,command_key,requested_at,status,started_at)
                VALUES($1,$2,$3,$4,$5,'RUNNING',$5)""",
                request,
                tenant,
                actor,
                uuid4(),
                started,
            )
            await conn.execute(
                """INSERT INTO research.daily_research_runs
                (run_id,slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet)
                VALUES($1,($2::timestamptz AT TIME ZONE 'Asia/Shanghai')::date,$2,$2,'fixture','fixture','{}')""",
                run,
                started,
            )
            await conn.execute(
                "INSERT INTO research.daily_research_model_guards(run_id,owner_token,acquired_at) VALUES($1,$2,$3)",
                run,
                uuid4(),
                started,
            )
            await conn.execute("SET LOCAL ROLE wealthsail_worker")
            assert (
                await conn.execute(
                    "UPDATE research.daily_research_refresh_requests SET execution_stage='MODEL_RUNNING',stage_updated_at=started_at WHERE id=$1",
                    request,
                )
                == "UPDATE 1"
            )
            assert (
                await conn.execute(
                    """UPDATE research.daily_research_model_guards
                SET exit_evidence='{"evidenceState":"LOCAL_EXIT_UNCONFIRMED"}',
                    release_proof='{"kind":"LOCAL_WINDOWS_JOB"}' WHERE run_id=$1""",
                    run,
                )
                == "UPDATE 1"
            )
            for sql in (
                "UPDATE research.daily_research_refresh_requests SET requested_at=requested_at",
                "UPDATE research.daily_research_refresh_requests SET tenant_id=tenant_id",
                "UPDATE research.daily_research_model_guards SET owner_token=owner_token",
                "UPDATE research.daily_research_model_guards SET acquired_at=acquired_at",
                "UPDATE research.local_research_recovery_audit SET proof=proof",
                "DELETE FROM research.local_research_recovery_audit",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction():
                        await conn.execute(sql)
            for sql in (
                "UPDATE research.daily_research_refresh_requests SET execution_stage='FORGED_STAGE'",
                "UPDATE research.daily_research_refresh_requests SET stage_updated_at=NULL",
                "UPDATE research.daily_research_refresh_requests SET stage_updated_at=started_at-interval '1 second'",
                "UPDATE research.daily_research_refresh_requests SET status='CANCELLED',completed_at=started_at,error_class='Fixture'",
                "UPDATE research.daily_research_model_guards SET exit_evidence='[]'",
                "UPDATE research.daily_research_model_guards SET release_proof='{}'",
                "UPDATE research.daily_research_model_guards SET release_proof='{"
                + '"kind":"UNTRUSTED"'
                + "}'",
            ):
                with pytest.raises(asyncpg.CheckViolationError):
                    async with conn.transaction():
                        await conn.execute(sql)

            await conn.execute("SET LOCAL ROLE wealthsail_api")
            for sql in (
                "UPDATE research.daily_research_refresh_requests SET execution_stage='MODEL_RUNNING',stage_updated_at=started_at",
                "SELECT owner_token FROM research.daily_research_model_guards",
                "SELECT proof FROM research.local_research_recovery_audit",
                "SELECT research.lock_local_research_maintenance()",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction():
                        await conn.execute(sql)

            await conn.execute("SET LOCAL ROLE authenticated")
            for sql in (
                "SELECT * FROM research.daily_research_refresh_requests",
                "SELECT * FROM research.daily_research_model_guards",
                "SELECT * FROM research.local_research_recovery_audit",
                "INSERT INTO research.local_research_recovery_audit DEFAULT VALUES",
                "SELECT research.lock_local_research_maintenance()",
                "SELECT research.expire_queued_research_refresh(NULL,NULL,clock_timestamp())",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction():
                        await conn.execute(sql)
    finally:
        await conn.close()
