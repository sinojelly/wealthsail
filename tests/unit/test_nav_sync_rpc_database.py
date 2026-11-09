"""The API may append validated public cohorts without acquiring Worker rights."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.dependencies import now_shanghai
from tests.unit.test_on_demand_nav_sync import history

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not DSN, reason="existing isolated investment database required")
async def test_nav_append_rpc_preserves_actor_and_source_boundary():
    target = urlsplit(DSN)
    assert (
        target.hostname == "127.0.0.1"
        and target.path == "/wealthsail_investment_check_20261014"
    )
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        root = Path(__file__).resolve().parents[2]
        if not await conn.fetchval(
            "SELECT to_regclass('market.fund_nav_chart_histories')"
        ):
            prerequisite = (
                root / "migrations/089_fund_nav_chart_history.sql"
            ).read_text(encoding="utf-8")
            await conn.execute(
                prerequisite.replace("BEGIN;", "", 1).rsplit("COMMIT;", 1)[0]
            )
        migration = root / "migrations/107_on_demand_nav_history.sql"
        ddl = (
            migration.read_text(encoding="utf-8")
            .replace("BEGIN;", "", 1)
            .rsplit("COMMIT;", 1)[0]
        )
        await conn.execute(ddl)
        tenant, actor, stranger, instrument = [uuid4() for _ in range(4)]
        code = str(900000 + uuid4().int % 99999)
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'NAV sync fixture')",
            tenant,
        )
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'NAV sync fixture')",
            actor,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'VIEWER')",
            tenant,
            actor,
        )
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','NAV sync fixture')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER',$2,'FUND_CN',CURRENT_DATE)",
            instrument,
            code,
        )
        payload = history(code, count=130)
        now = now_shanghai()
        payload.update(retrievedAt=now.isoformat(), availableAt=now.isoformat())
        await apply_tenant_context(conn, str(actor), str(tenant))
        sql = "SELECT market.append_fund_nav_sync_history($1,$2,$3,$4::jsonb)"
        first = await conn.fetchval(sql, tenant, actor, code, json.dumps(payload))
        assert (
            await conn.fetchval(sql, tenant, actor, code, json.dumps(payload)) == first
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_nav_chart_histories WHERE fund_code=$1",
                code,
            )
            == 1
        )
        for forged in (
            {**payload, "sourceUri": "https://example.com/fake"},
            {**payload, "currency": "USD"},
            {**payload, "points": [{"date": "2099-01-01", "close": "1"}]},
        ):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.fetchval(sql, tenant, actor, code, json.dumps(forged))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval(sql, tenant, stranger, code, json.dumps(payload))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO market.fund_nav_chart_histories(fund_code,retrieved_at,available_at,payload) VALUES($1,now(),now(),$2::jsonb)",
                    code,
                    json.dumps(payload),
                )
        assert not await conn.fetchval(
            "SELECT has_function_privilege('authenticated','market.append_fund_nav_sync_history(uuid,uuid,text,jsonb)','EXECUTE')"
        )
    finally:
        await transaction.rollback()
        await conn.close()
