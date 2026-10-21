"""Real API-role chart requeue works without granting Worker lease writes."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context


DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not DSN, reason="owned NAV chart validation database required")
async def test_api_can_requeue_only_verified_chart_jobs_through_narrow_helper():
    target = urlsplit(DSN)
    assert target.hostname == "127.0.0.1" and (
        target.path == "/wealthsail_investment_check_20261014"
        or target.path.startswith("/wealthsail_review48_validation_")
    ), "NAV enqueue test requires an isolated validation database"
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        root = Path(__file__).resolve().parents[2]
        if not await conn.fetchval(
            "SELECT to_regclass('market.fund_nav_chart_histories')"
        ):
            ddl = (root / "migrations/089_fund_nav_chart_history.sql").read_text(
                encoding="utf-8"
            )
            await conn.execute(ddl.removeprefix("BEGIN;").removesuffix("COMMIT;\n"))
        ddl = (root / "migrations/090_fund_nav_chart_enqueue.sql").read_text(
            encoding="utf-8"
        )
        await conn.execute(ddl.removeprefix("BEGIN;").removesuffix("COMMIT;\n"))
        tenant, actor, stranger, instrument = (uuid4() for _ in range(4))
        code = f"{900000 + uuid4().int % 100000:06}"
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'NAV chart fixture')",
            tenant,
        )
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'NAV chart fixture')",
            actor,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
            tenant,
            actor,
        )
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','NAV chart fixture')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER',$2,'FUND_CN',CURRENT_DATE)",
            instrument,
            code,
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        sql = "SELECT * FROM workflow.enqueue_fund_nav_chart_fetch($1,$2,$3)"
        first = await conn.fetchrow(sql, tenant, actor, code)
        assert first["status"] == "PENDING" and first["reason"] is None
        assert (await conn.fetchrow(sql, tenant, actor, code))["id"] == first["id"]
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE workflow.job_runs SET payload='{}' WHERE id=$1", first["id"]
                )
        await conn.execute("RESET ROLE")
        await conn.execute(
            "UPDATE workflow.job_runs SET status='COMPLETED',finished_at=now()-interval '2 hours',attempt_count=3 WHERE id=$1",
            first["id"],
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        requeued = await conn.fetchrow(sql, tenant, actor, code)
        assert requeued["id"] == first["id"] and requeued["status"] == "PENDING"
        row = await conn.fetchrow(
            "SELECT started_at,attempt_count,payload FROM workflow.job_runs WHERE id=$1",
            first["id"],
        )
        assert row["started_at"] is not None and row["attempt_count"] == 0
        assert code in row["payload"] and str(actor) in row["payload"]
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchrow(sql, tenant, stranger, code)
        assert not await conn.fetchval(
            "SELECT has_function_privilege('authenticated','workflow.enqueue_fund_nav_chart_fetch(uuid,uuid,text)','EXECUTE')"
        )
    finally:
        await transaction.rollback()
        await conn.close()
