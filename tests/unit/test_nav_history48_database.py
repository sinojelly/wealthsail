"""Real PostgreSQL chart cache/queue permissions in the exact owned review48 DB."""

from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.common.database import apply_tenant_context
from backend.funds.nav_history import get_nav_history
from backend.jobs.fund_nav_full_history import (
    refresh_full_nav_history,
    validate_full_payload,
)

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned review48 database required"),
]
NOW = datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


class Pool:
    def __init__(self, conn):
        self.conn, self.borrowed = conn, 0

    @asynccontextmanager
    async def acquire(self):
        self.borrowed += 1
        try:
            yield self.conn
        finally:
            self.borrowed -= 1


@asynccontextmanager
async def fixture():
    parts = urlsplit(DSN)
    assert (parts.hostname, parts.port, parts.path) == (
        "127.0.0.1",
        55436,
        "/wealthsail_review48_validation_v1",
    )
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    tenant, owner, viewer, instrument = [uuid4() for _ in range(4)]
    code = str(900000 + uuid4().int % 99999)
    while await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM reference.research_fund_catalog WHERE fund_code=$1)",
        code,
    ):
        code = str(900000 + uuid4().int % 99999)
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'NAV chart fixture')", tenant
    )
    for actor, role in ((owner, "OWNER"), (viewer, "VIEWER")):
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'NAV chart fixture')",
            actor,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,$3)",
            tenant,
            actor,
            role,
        )
    await conn.execute(
        "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','NAV chart fixture')",
        instrument,
    )
    await conn.execute(
        "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market) VALUES($1,'TICKER',$2,'FUND_CN')",
        instrument,
        code,
    )
    try:
        yield conn, Pool(conn), tenant, owner, viewer, code
    finally:
        await tx.rollback()
        await conn.close()


def raw(code):
    return {
        "fundCode": code,
        "sourceUri": f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
        "queryStart": "1900-01-01",
        "queryEnd": "2026-10-20",
        "complete": True,
        "receivedRowCount": 3,
        "points": [
            {"date": "2026-08-01", "close": "1"},
            {"date": "2026-09-01", "close": "2"},
            {"date": "2026-10-19", "close": "1.5"},
        ],
    }


async def test_owner_queues_once_and_viewer_reads_without_dispatch():
    async with fixture() as (conn, pool, tenant, owner, viewer, code):
        await apply_tenant_context(conn, str(viewer), str(tenant))
        result = await get_nav_history(
            conn, code, "all", tenant_id=tenant, user_id=viewer, now=NOW
        )
        assert result["refresh"]["state"] == "IDLE" and result["points"] == []
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM workflow.job_runs WHERE tenant_id=$1", tenant
            )
            == 0
        )
        await apply_tenant_context(conn, str(owner), str(tenant))
        first = await get_nav_history(
            conn, code, "all", tenant_id=tenant, user_id=owner, now=NOW
        )
        again = await get_nav_history(
            conn, code, "2y", tenant_id=tenant, user_id=owner, now=NOW
        )
        assert first["refresh"]["state"] == again["refresh"]["state"] == "QUEUED"
        assert first["refresh"]["runId"] == again["refresh"]["runId"]
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM workflow.job_runs WHERE tenant_id=$1", tenant
            )
            == 1
        )


async def test_api_cannot_append_or_modify_worker_cache_and_worker_only_appends():
    async with fixture() as (conn, pool, tenant, owner, viewer, code):
        await apply_tenant_context(conn, str(owner), str(tenant))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO market.fund_nav_chart_histories(fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$2,'{}')",
                    code,
                    NOW,
                )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        history = validate_full_payload(raw(code), code, NOW)
        await conn.execute(
            "INSERT INTO market.fund_nav_chart_histories(fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$2,$3::jsonb)",
            code,
            NOW,
            json.dumps(history),
        )
        for sql in (
            "UPDATE market.fund_nav_chart_histories SET available_at=now()",
            "DELETE FROM market.fund_nav_chart_histories",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(sql)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_nav_chart_histories WHERE fund_code=$1",
                code,
            )
            == 1
        )


async def test_complete_three_month_fund_is_ready_for_all_without_requeueing():
    async with fixture() as (conn, pool, tenant, owner, viewer, code):
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        async def source(actual_code, end):
            assert pool.borrowed == 0 and actual_code == code and end == NOW.date()
            return raw(code)

        await refresh_full_nav_history(
            pool, code, now=NOW, fetcher=source, now_provider=lambda: NOW
        )
        await apply_tenant_context(conn, str(owner), str(tenant))
        for period in ("all", "2y"):
            result = await get_nav_history(
                conn, code, period, tenant_id=tenant, user_id=owner, now=NOW
            )
            assert (
                result["state"] == "READY" and result["coverage"]["fullHistory"] is True
            )
            assert (
                result["coverage"]["sampleCount"] == 3
                and result["coverage"]["startDate"] == "2026-08-01"
            )
            assert result["refresh"]["state"] == "IDLE"
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM workflow.job_runs WHERE tenant_id=$1", tenant
            )
            == 0
        )


async def test_queued_request_loses_dispatch_authority_after_revocation(monkeypatch):
    from types import SimpleNamespace
    from backend.jobs.fund_nav_full_history import process_full_history_task

    async with fixture() as (conn, pool, tenant, owner, viewer, code):
        await conn.execute(
            "UPDATE identity.tenant_members SET role='VIEWER' WHERE tenant_id=$1 AND user_id=$2",
            tenant,
            owner,
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        async def no_supplier(*args):
            raise AssertionError("revoked request must not contact supplier")

        monkeypatch.setattr(
            "backend.jobs.fund_nav_full_history.fetch_full_payload", no_supplier
        )
        with pytest.raises(ValueError, match="no longer authorized"):
            await process_full_history_task(
                pool,
                SimpleNamespace(
                    tenant_id=tenant,
                    payload={"fundCode": code, "requestedBy": str(owner)},
                ),
            )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_nav_chart_histories WHERE fund_code=$1",
                code,
            )
            == 0
        )
