"""CSI research uses actual SQL and least-privilege roles in the isolated DB."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from backend.common.database import create_pool, system_connection
from backend.dependencies import now_shanghai

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated DB required"),
]


class SingleConnection:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def first_fixture_slot(conn):
    """Keep this rollback-only fixture ahead of existing cross-tenant jobs.

    The real worker intentionally consumes a global queue. These tests must
    neither assume it is empty nor complete another test's pending request.
    """
    return await conn.fetchval("""SELECT LEAST(now(),COALESCE(min(available_at),now()))
      - interval '1 day' FROM workflow.job_runs""")


async def test_default_sources_and_persistent_lease_concurrency_restart_and_retry():
    from backend.jobs import index_research as job
    from backend.market.index_policy import POLICY

    worker = await create_pool(
        DSN, min_size=1, max_size=3, runtime_role="wealthsail_worker"
    )
    now = now_shanghai()
    try:
        async with system_connection(worker) as conn:
            rows = await conn.fetch(
                "SELECT * FROM reference.research_indices WHERE id IN ('hs300','a500','zz500')"
            )
        assert len(rows) == 3
        assert all(
            json.loads(row["collection_policy"]) == POLICY
            and row["publish_hour"] is None
            for row in rows
        )
        subject = {**dict(rows[0]), "id": f"isolated-{uuid4()}"}

        async def claim(stamp, force=False):
            async with system_connection(worker) as conn:
                return await job.claim_index(conn, subject, stamp, force=force)

        claims = await asyncio.gather(claim(now), claim(now))
        won = next(row for row in claims if row)
        assert sum(row is not None for row in claims) == 1
        assert await claim(now, force=True) is None
        async with system_connection(worker) as conn:
            await job.finish_index(
                conn, won, now + timedelta(seconds=1), {"status": "FAILED"}
            )
        assert await claim(now + timedelta(minutes=59)) is None
        retried = await claim(now + timedelta(hours=1, seconds=1))
        assert retried
        async with system_connection(worker) as conn:
            await job.finish_index(
                conn,
                retried,
                now + timedelta(hours=1, seconds=2),
                {"status": "COMPLETED"},
            )
        assert await claim(now + timedelta(hours=24)) is None
        assert await claim(now + timedelta(hours=25, seconds=2))
    finally:
        await worker.close()


async def test_only_index_web_refresh_is_consumed_by_dedicated_worker(monkeypatch):
    from backend.config import Settings
    from backend.jobs import index_research as job

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        tenant = uuid4()
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'isolated index queue')",
            tenant,
        )
        ids = []
        first_slot = await first_fixture_slot(conn)
        for offset, kind in enumerate(("fund", "nav", "index")):
            ids.append(
                await conn.fetchval(
                    """INSERT INTO workflow.job_runs(tenant_id,job_key,job_type,payload,status,available_at)
              VALUES($1,$2,'INVESTMENT_RESEARCH_REFRESH',$3::jsonb,'PENDING',$4) RETURNING id""",
                    tenant,
                    str(uuid4()),
                    json.dumps({"kind": kind, "id": "hs300"}),
                    first_slot + timedelta(seconds=offset),
                )
            )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        refresh = AsyncMock(
            return_value={
                "status": "COMPLETED",
                "indices": {"hs300": {"status": "COMPLETED"}},
            }
        )
        monkeypatch.setattr(job, "sync_index_research", refresh)
        assert await job.consume_index_refresh(
            SingleConnection(conn), Settings(_env_file=None)
        )
        statuses = [
            await conn.fetchval("SELECT status FROM workflow.job_runs WHERE id=$1", key)
            for key in ids
        ]
        assert statuses == ["PENDING", "PENDING", "COMPLETED"]
        refresh.assert_awaited_once()
    finally:
        await transaction.rollback()
        await conn.close()


async def test_busy_index_lease_requeues_web_request_and_cancel_releases_it(
    monkeypatch,
):
    from backend.config import Settings
    from backend.jobs import index_research as job

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        tenant = uuid4()
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'isolated busy index')",
            tenant,
        )
        key = await conn.fetchval(
            """INSERT INTO workflow.job_runs(tenant_id,job_key,job_type,payload,status,available_at)
          VALUES($1,$2,'INVESTMENT_RESEARCH_REFRESH','{"kind":"index","id":"hs300"}','PENDING',$3) RETURNING id""",
            tenant,
            str(uuid4()),
            await first_fixture_slot(conn),
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        monkeypatch.setattr(
            job,
            "sync_index_research",
            AsyncMock(return_value={"indices": {"hs300": {"status": "SKIPPED"}}}),
        )
        assert await job.consume_index_refresh(
            SingleConnection(conn), Settings(_env_file=None)
        )
        row = await conn.fetchrow(
            "SELECT status,lease_token FROM workflow.job_runs WHERE id=$1", key
        )
        assert row["status"] == "PENDING" and row["lease_token"] is None
        await conn.execute(
            "UPDATE workflow.job_runs SET next_retry_at=now() WHERE id=$1", key
        )
        monkeypatch.setattr(
            job, "sync_index_research", AsyncMock(side_effect=asyncio.CancelledError())
        )
        with pytest.raises(asyncio.CancelledError):
            await job.consume_index_refresh(
                SingleConnection(conn), Settings(_env_file=None)
            )
        assert (
            await conn.fetchval("SELECT status FROM workflow.job_runs WHERE id=$1", key)
            == "PENDING"
        )
    finally:
        await transaction.rollback()
        await conn.close()


async def test_repeated_web_refresh_after_completion_remains_queueable():
    from backend.common.database import apply_tenant_context
    from backend.investment.schemas import RefreshRequest
    from backend.investment.service import enqueue_refresh

    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        tenant, actor = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'isolated repeat queue')",
            tenant,
        )
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'isolated actor')",
            actor,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
            tenant,
            actor,
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        first = await enqueue_refresh(
            conn, tenant, RefreshRequest(kind="index", id="hs300")
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await conn.execute(
            "UPDATE workflow.job_runs SET status='COMPLETED' WHERE id=$1",
            first["runId"],
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        repeated = await enqueue_refresh(
            conn, tenant, RefreshRequest(kind="index", id="hs300")
        )
        assert repeated["runId"] == first["runId"] and repeated["created"] is True
    finally:
        await transaction.rollback()
        await conn.close()


async def test_official_snapshot_is_readable_under_api_and_revocation_blocks_it(
    monkeypatch,
):
    from backend.investment import service
    from backend.jobs import investment_research as job
    import test_index_research_pipeline as fixtures

    now = now_shanghai()
    monkeypatch.setattr(fixtures, "NOW", now)
    _, points, calendar, _ = fixtures.fixture()
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.executemany(
            """INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day)
          VALUES('SSE',$1,$2) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=EXCLUDED.is_trading_day""",
            [(r["calendar_date"], r["is_trading_day"]) for r in calendar],
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        async def fetch(code, start, end):
            return {
                "points": points,
                "pe": {
                    "state": "READY",
                    "metric": "PE_TTM",
                    "sourceUri": "https://www.csindex.com.cn/zh-CN/indices/index-detail/000300",
                    "points": [{"date": points[-1]["date"], "value": 12.5}],
                },
                "retrievedAt": now.isoformat(),
            }

        monkeypatch.setattr(job, "fetch_index_history", fetch)
        materialized = await job.refresh_subject(
            SingleConnection(conn),
            None,
            "index",
            "hs300",
            now.date(),
            now=now,
            _claimed=True,
        )
        assert materialized["dataQuality"] == "READY"
        monkeypatch.setattr(service, "now_shanghai", lambda: now + timedelta(seconds=1))
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        visible = await service.analysis(conn, "index", "hs300")
        assert visible["score"] is not None and visible["indicators"]["ma200"] > 0
        assert visible["valuation"]["state"] == "PARTIAL"
        assert visible["valuation"]["pe"]["latest"] == 12.5
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE reference.research_indices SET collection_policy='{}' WHERE id='hs300'"
                )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        await conn.execute(
            "UPDATE reference.research_indices SET verified_at=NULL WHERE id='hs300'"
        )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        revoked = await service.analysis(conn, "index", "hs300")
        assert revoked["score"] is None and revoked["dataQuality"] == "UNVERIFIED"
    finally:
        await transaction.rollback()
        await conn.close()
