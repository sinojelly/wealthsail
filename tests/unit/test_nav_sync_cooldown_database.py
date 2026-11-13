"""Real PostgreSQL: authenticated claims, failure cooldown and concurrent callers."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned local NAV cooldown DB required"),
]
MEMBER = "SELECT * FROM market.claim_member_fund_nav_sync_attempt($1,$2,$3)"


@asynccontextmanager
async def fixture(*, committed=False):
    target = urlsplit(DSN)
    assert (target.hostname, target.port, target.path) == (
        "127.0.0.1",
        55448,
        "/wealthsail_manual_research_acceptance_sanitize",
    )
    conn = await asyncpg.connect(DSN)
    # Apply only the current migration to the existing 1–108 isolated DB.
    if not await conn.fetchval("SELECT to_regclass('market.fund_nav_sync_attempts')"):
        await conn.execute(
            (
                Path(__file__).resolve().parents[2]
                / "migrations/109_nav_sync_attempt_cooldown.sql"
            ).read_text(encoding="utf-8")
        )
    tx = conn.transaction()
    await tx.start()
    tenant, actor, stranger, instrument = [uuid4() for _ in range(4)]
    code = str(900000 + uuid4().int % 99999)
    while await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM reference.research_fund_catalog WHERE fund_code=$1)",
        code,
    ):
        code = str(900000 + uuid4().int % 99999)
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'NAV cooldown test')", tenant
    )
    for user in (actor, stranger):
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'NAV cooldown test')",
            user,
        )
    await conn.execute(
        "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'VIEWER')",
        tenant,
        actor,
    )
    await conn.execute(
        "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','NAV cooldown test')",
        instrument,
    )
    await conn.execute(
        "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market) VALUES($1,'TICKER',$2,'FUND_CN')",
        instrument,
        code,
    )
    if committed:
        await tx.commit()
    try:
        yield conn, tenant, actor, stranger, code
    finally:
        if committed:
            # Only this fixture's random identifiers are removed; no shared or
            # pre-existing test data is altered by concurrent verification.
            await conn.execute(
                "DELETE FROM market.fund_nav_sync_attempts WHERE fund_code=$1", code
            )
            await conn.execute(
                "DELETE FROM reference.instruments WHERE id=$1", instrument
            )
            await conn.execute("DELETE FROM identity.tenants WHERE id=$1", tenant)
            await conn.execute(
                "DELETE FROM identity.profiles WHERE id=ANY($1::uuid[])",
                [actor, stranger],
            )
        else:
            await tx.rollback()
        await conn.close()


async def test_member_claim_is_once_per_twelve_hours_and_not_a_table_write_grant():
    async with fixture() as (conn, tenant, actor, stranger, code):
        await apply_tenant_context(conn, str(actor), str(tenant))
        first = await conn.fetchrow(MEMBER, tenant, actor, code)
        again = await conn.fetchrow(MEMBER, tenant, actor, code)
        assert first["claimed"] is True and again["claimed"] is False
        assert first["last_sync_attempt_at"] == again["last_sync_attempt_at"]
        for sql in (
            "INSERT INTO market.fund_nav_sync_attempts(fund_code,last_sync_attempt_at) VALUES('999999',now())",
            "UPDATE market.fund_nav_sync_attempts SET last_sync_attempt_at=now()",
            "DELETE FROM market.fund_nav_sync_attempts",
            "SELECT * FROM market.claim_fund_nav_sync_attempt('999999')",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(sql)
        assert await conn.fetchval("SELECT 1") == 1
        assert not await conn.fetchval(
            "SELECT has_function_privilege('authenticated','market.claim_member_fund_nav_sync_attempt(uuid,uuid,text)','EXECUTE')"
        )
        assert not await conn.fetchval(
            "SELECT has_function_privilege('authenticated','market.claim_fund_nav_sync_attempt(text)','EXECUTE')"
        )


async def test_chart_poll_reads_own_queue_without_creating_or_advancing_a_job():
    from backend.funds.nav_history import get_nav_history, request_nav_history_refresh

    async with fixture() as (conn, tenant, actor, stranger, code):
        await conn.execute(
            "UPDATE identity.tenant_members SET role='OWNER' WHERE tenant_id=$1 AND user_id=$2",
            tenant,
            actor,
        )
        other_tenant = uuid4()
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'Other chart reader')",
            other_tenant,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'VIEWER')",
            other_tenant,
            stranger,
        )
        cutoff = datetime.now(timezone.utc)

        async def read(tenant_id=tenant, user_id=actor, now=cutoff):
            await apply_tenant_context(conn, str(user_id), str(tenant_id))
            return (await get_nav_history(
                conn, code, "all", tenant_id=tenant_id, user_id=user_id, now=now,
            ))["refresh"]

        assert (await read())["state"] == "IDLE"
        requested = await request_nav_history_refresh(
            conn, code, "all", tenant_id=tenant, user_id=actor, now=cutoff,
        )
        run_id = requested["refresh"]["runId"]
        assert (await read()) == requested["refresh"]
        assert (await read(other_tenant, stranger))["state"] == "IDLE"
        await conn.execute("RESET ROLE")
        assert await conn.fetchval(
            "SELECT count(*) FROM workflow.job_runs WHERE tenant_id=ANY($1::uuid[])",
            [tenant, other_tenant],
        ) == 1
        await conn.execute("UPDATE workflow.job_runs SET status='RUNNING' WHERE id=$1", run_id)
        assert (await read()) == {"state": "RUNNING", "runId": run_id, "pollAfterMs": 2000}

        await conn.execute("RESET ROLE")
        finished = cutoff + timedelta(seconds=1)
        await conn.execute(
            "UPDATE workflow.job_runs SET status='COMPLETED',finished_at=$2 WHERE id=$1",
            run_id,
            finished,
        )
        assert (await read())["state"] == "RUNNING"
        assert (await read(now=finished))["state"] == "IDLE"


async def test_web_research_worker_observes_cooldown_claimed_by_personal_refresh():
    from unittest.mock import AsyncMock

    from backend.errors import DataUnavailableError
    from backend.jobs.fund_nav_research import refresh_fund_nav_history

    async with fixture(committed=True) as (conn, tenant, actor, stranger, code):
        async with conn.transaction():
            await apply_tenant_context(conn, str(actor), str(tenant))
            assert (await conn.fetchrow(MEMBER, tenant, actor, code))["claimed"] is True

        class WorkerPool:
            @asynccontextmanager
            async def acquire(self):
                async with conn.transaction():
                    await conn.execute("SET LOCAL ROLE wealthsail_worker")
                    yield conn

        provider = AsyncMock()
        with pytest.raises(DataUnavailableError, match="冷却"):
            await refresh_fund_nav_history(WorkerPool(), code, fetcher=provider)
        provider.assert_not_awaited()


async def test_actor_tenant_membership_and_identity_boundaries_are_preserved():
    async with fixture() as (conn, tenant, actor, stranger, code):
        await apply_tenant_context(conn, str(actor), str(tenant))
        for wrong_tenant, wrong_actor in ((uuid4(), actor), (tenant, stranger)):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.fetchrow(MEMBER, wrong_tenant, wrong_actor, code)
        for invalid_code in ("bad", "000000"):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.fetchrow(MEMBER, tenant, actor, invalid_code)
        await conn.execute("RESET ROLE")
        await conn.execute(
            "UPDATE identity.tenant_members SET status='SUSPENDED' WHERE tenant_id=$1 AND user_id=$2",
            tenant,
            actor,
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchrow(MEMBER, tenant, actor, code)


@pytest.mark.parametrize("defect", ["currency", "foreign_name", "ambiguous"])
async def test_claim_rejects_non_cny_or_ambiguous_fund_identity(defect):
    async with fixture() as (conn, tenant, actor, stranger, code):
        if defect == "ambiguous":
            instrument = uuid4()
            await conn.execute(
                "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','Duplicate NAV identity')",
                instrument,
            )
            await conn.execute(
                "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER',$2,'FUND_CN',CURRENT_DATE-1)",
                instrument,
                code,
            )
        else:
            column, value = (
                ("trading_currency", "USD")
                if defect == "currency"
                else ("canonical_name", "美元现汇 NAV test")
            )
            # The column is one of two closed constants; no input becomes SQL.
            await conn.execute(
                f"UPDATE reference.instruments SET {column}=$1 WHERE id IN (SELECT instrument_id FROM reference.instrument_aliases WHERE alias_value=$2)",
                value,
                code,
            )
        await apply_tenant_context(conn, str(actor), str(tenant))
        with pytest.raises(asyncpg.PostgresError):
            async with conn.transaction():
                await conn.fetchrow(MEMBER, tenant, actor, code)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_nav_sync_attempts WHERE fund_code=$1",
                code,
            )
            == 0
        )


async def test_worker_shares_member_cooldown_and_releases_only_after_twelve_hours():
    async with fixture() as (conn, tenant, actor, stranger, code):
        await conn.execute(
            "INSERT INTO market.fund_nav_sync_attempts VALUES($1,statement_timestamp()-interval '11 hours 59 minutes')",
            code,
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        assert (
            await conn.fetchrow(
                "SELECT * FROM market.claim_fund_nav_sync_attempt($1)", code
            )
        )["claimed"] is False
        await conn.execute("RESET ROLE")
        await conn.execute(
            "UPDATE market.fund_nav_sync_attempts SET last_sync_attempt_at=statement_timestamp()-interval '12 hours 1 second' WHERE fund_code=$1",
            code,
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        assert (await conn.fetchrow(MEMBER, tenant, actor, code))["claimed"] is True
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        assert (
            await conn.fetchrow(
                "SELECT * FROM market.claim_fund_nav_sync_attempt($1)", code
            )
        )["claimed"] is False


async def test_concurrent_processes_obtain_one_durable_claim_without_provider_success():
    async with fixture(committed=True) as (_, tenant, actor, stranger, code):

        async def claim():
            conn = await asyncpg.connect(DSN)
            try:
                async with conn.transaction():
                    await apply_tenant_context(conn, str(actor), str(tenant))
                    return dict(await conn.fetchrow(MEMBER, tenant, actor, code))
            finally:
                await conn.close()

        results = await asyncio.gather(*(claim() for _ in range(4)))
        assert sum(row["claimed"] for row in results) == 1
        assert len({row["last_sync_attempt_at"] for row in results}) == 1
        # There is deliberately no successful provider record or history write.
        # A new process still observes the persisted attempt and must not retry.
        assert (await claim())["claimed"] is False


async def test_rejected_claim_savepoint_keeps_real_tenant_transaction_usable(
    monkeypatch,
):
    from backend.funds import nav_history
    from backend.dependencies import now_shanghai
    from unittest.mock import AsyncMock

    async with fixture() as (conn, tenant, actor, stranger, code):
        await apply_tenant_context(conn, str(actor), str(tenant))
        source = AsyncMock(
            side_effect=AssertionError("unauthorized claim must not fetch")
        )
        monkeypatch.setattr(nav_history, "fetch_on_demand_nav_history", source)
        report = await nav_history.ensure_nav_histories(
            conn,
            [code],
            now_shanghai(),
            tenant_id=tenant,
            user_id=stranger,
            latest_confirmed={},
            allow_external_fetch=True,
        )
        assert report["refresh"][code]["reason"] == "NAV_SYNC_CLAIM_UNAVAILABLE"
        source.assert_not_awaited()
        assert await conn.fetchval("SELECT 1") == 1


async def test_single_connection_pool_concurrent_refreshes_do_not_deadlock(monkeypatch):
    from contextvars import ContextVar
    from backend.common.database import tenant_transaction
    from backend.dependencies import now_shanghai
    from backend.funds import nav_history, latest_quotes
    from unittest.mock import AsyncMock

    async with fixture(committed=True) as (_, tenant, actor, stranger, code):
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=1)
        owns_connection = ContextVar("nav_refresh_owns_connection", default=False)

        @asynccontextmanager
        async def transaction():
            async with tenant_transaction(pool, str(actor), str(tenant)) as conn:
                token = owns_connection.set(True)
                try:
                    yield conn
                finally:
                    owns_connection.reset(token)

        async def quotes(*_args, **_kwargs):
            assert owns_connection.get() is False
            return {"items": []}

        async def fail(*_args):
            assert owns_connection.get() is False
            await asyncio.sleep(0.01)
            raise TimeoutError()

        source = AsyncMock(side_effect=fail)
        monkeypatch.setattr(latest_quotes.latest_quote_cache, "get", quotes)
        monkeypatch.setattr(nav_history, "fetch_on_demand_nav_history", source)

        async def refresh():
            return await nav_history.ensure_nav_histories(
                None,
                [code],
                now_shanghai(),
                tenant_id=tenant,
                user_id=actor,
                allow_external_fetch=True,
                transaction_factory=transaction,
            )

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(refresh() for _ in range(4))), timeout=5
            )
            assert source.await_count == 1
            assert (
                sum(
                    row["refresh"][code]["reason"] == "NAV_SYNC_COOLDOWN"
                    for row in results
                )
                == 3
            )
            assert (await refresh())["refresh"][code]["reason"] == "NAV_SYNC_COOLDOWN"
        finally:
            await pool.close()


async def test_cancelled_refresh_keeps_committed_claim_and_releases_pool_slot(
    monkeypatch,
):
    from backend.common.database import tenant_transaction
    from backend.dependencies import now_shanghai
    from backend.funds import nav_history
    from unittest.mock import AsyncMock

    async with fixture(committed=True) as (_, tenant, actor, stranger, code):
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=1)
        entered = asyncio.Event()

        async def blocked(*_args):
            assert pool.get_idle_size() == 1
            entered.set()
            await asyncio.Event().wait()

        def transaction():
            return tenant_transaction(pool, str(actor), str(tenant))

        async def refresh():
            return await nav_history.ensure_nav_histories(
                None,
                [code],
                now_shanghai(),
                tenant_id=tenant,
                user_id=actor,
                latest_confirmed={},
                allow_external_fetch=True,
                transaction_factory=transaction,
            )

        source = AsyncMock(side_effect=blocked)
        monkeypatch.setattr(nav_history, "fetch_on_demand_nav_history", source)
        task = asyncio.create_task(refresh())
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await asyncio.wait_for(refresh(), timeout=3))["refresh"][code][
                "reason"
            ] == "NAV_SYNC_COOLDOWN"
            assert source.await_count == 1 and pool.get_idle_size() == 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await pool.close()
