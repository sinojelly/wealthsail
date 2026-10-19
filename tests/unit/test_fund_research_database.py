"""Fund refresh lease isolation and append-only NAV SQL in the owned test DB."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.config import Settings
from backend.integrations.market_data import Observation
from backend.jobs import data_tasks, fund_research
from backend.jobs.fund_nav_research import refresh_fund_nav_history

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned fund research DB required"),
]
NOW = datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


class SingleConnection:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@asynccontextmanager
async def fixture():
    parts = urlsplit(DSN)
    assert parts.hostname == "127.0.0.1" and parts.port == 55434
    assert parts.path.startswith("/wealthsail_review07_validation_v") or parts.path in {
        "/wealthsail_review08_validation_v1",
        "/wealthsail_review09_validation_v1",
        "/wealthsail_review44_validation_v1",
        "/wealthsail_review45_validation_v1",
    }
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        tenant, instrument = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'fund worker fixture')",
            tenant,
        )
        code = str(900000 + uuid4().int % 99999)
        while await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM reference.instrument_aliases WHERE alias_value=$1 AND market='FUND_CN')",
            code,
        ):
            code = str(900000 + uuid4().int % 99999)
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','fund worker fixture')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market) VALUES($1,'TICKER',$2,'FUND_CN')",
            instrument,
            code,
        )
        slot = await conn.fetchval(
            "SELECT LEAST(now(),COALESCE(min(available_at),now()))-interval '1 day' FROM workflow.job_runs"
        )
        yield conn, tenant, instrument, code, slot
    finally:
        await transaction.rollback()
        await conn.close()


async def enqueue(conn, tenant, slot, job_type, payload):
    return await conn.fetchval(
        """INSERT INTO workflow.job_runs(tenant_id,job_key,job_type,payload,status,available_at)
        VALUES($1,$2,$3,$4::jsonb,'PENDING',$5) RETURNING id""",
        tenant,
        str(uuid4()),
        job_type,
        json.dumps(payload),
        slot,
    )


async def test_worker_claims_only_otc_fund_and_nav_requests_without_index_or_holdings():
    async with fixture() as (conn, tenant, instrument, code, slot):
        etf = uuid4()
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'EXCHANGE_FUND','SSE','ETF untouched')",
            etf,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market) VALUES($1,'TICKER',$2,'SSE')",
            etf,
            code,
        )
        untouched = []
        for job_type, payload in (
            ("INVESTMENT_RESEARCH_REFRESH", {"kind": "index", "id": "hs300"}),
            ("INVESTMENT_RESEARCH_REFRESH", {"kind": "fund", "id": str(etf)}),
            ("FUND_NAV_BACKFILL", {}),
            ("POSITION_PERIOD_METRICS_REBUILD", {}),
            (None, {"phase": "DAILY_REPORT"}),
        ):
            untouched.append(await enqueue(conn, tenant, slot, job_type, payload))
        fund = await enqueue(
            conn,
            tenant,
            slot,
            "INVESTMENT_RESEARCH_REFRESH",
            {"kind": "fund", "id": str(instrument)},
        )
        nav = await enqueue(
            conn, tenant, slot, "FUND_NAV_RANGE_FETCH", {"fundCode": code}
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        pool = SingleConnection(conn)
        claimed = []
        for _ in range(2):
            lease = await fund_research.claim_fund_refresh(
                pool, owner="isolated-fund", lease_seconds=30
            )
            assert lease is not None
            claimed.append(lease.run_id)
            assert await data_tasks.complete_data_task(pool, lease)
        assert set(claimed) == {str(fund), str(nav)}
        assert await conn.fetchval(
            "SELECT bool_and(status='PENDING') FROM workflow.job_runs WHERE id=ANY($1::uuid[])",
            untouched,
        )


async def test_expired_lease_reclaim_prevents_old_generation_heartbeat_and_terminal_writes():
    async with fixture() as (conn, tenant, instrument, code, slot):
        run = await enqueue(
            conn, tenant, slot, "FUND_NAV_RANGE_FETCH", {"fundCode": code}
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        pool = SingleConnection(conn)
        old = await fund_research.claim_fund_refresh(
            pool, owner="crashed", lease_seconds=30
        )
        await conn.execute(
            "UPDATE workflow.job_runs SET lease_until=now()-interval '1 second' WHERE id=$1",
            run,
        )
        new = await fund_research.claim_fund_refresh(
            pool, owner="restarted", lease_seconds=30
        )
        assert old.run_id == new.run_id and old.lease_token != new.lease_token
        assert new.attempt_count == 2
        assert not await data_tasks.renew_data_task(pool, old, 30)
        assert not await data_tasks.complete_data_task(pool, old)
        assert not await data_tasks.fail_data_task(
            pool, old, Settings(_env_file=None), RuntimeError()
        )
        assert await data_tasks.renew_data_task(pool, new, 30)
        assert await data_tasks.complete_data_task(pool, new)
        assert (
            await conn.fetchval("SELECT status FROM workflow.job_runs WHERE id=$1", run)
            == "COMPLETED"
        )


async def test_cancelled_real_lease_is_requeued_without_spending_an_attempt(
    monkeypatch,
):
    async with fixture() as (conn, tenant, instrument, code, slot):
        run = await enqueue(
            conn,
            tenant,
            slot,
            "INVESTMENT_RESEARCH_REFRESH",
            {"kind": "fund", "id": str(instrument)},
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        async def cancel(*args):
            raise asyncio.CancelledError()

        monkeypatch.setattr(fund_research, "refresh_fund_nav_history", cancel)
        with pytest.raises(asyncio.CancelledError):
            await fund_research.consume_fund_refresh(
                SingleConnection(conn), Settings(_env_file=None)
            )
        row = await conn.fetchrow(
            "SELECT status,lease_token,attempt_count,next_retry_at>now() AS delayed FROM workflow.job_runs WHERE id=$1",
            run,
        )
        assert dict(row) == {
            "status": "PENDING",
            "lease_token": None,
            "attempt_count": 0,
            "delayed": True,
        }


async def test_worker_append_and_api_readback_use_real_unit_nav_and_deny_mutation(
    monkeypatch,
):
    from backend.investment import theme_graph
    from backend.macro import alpha_repository

    async with fixture() as (conn, tenant, instrument, code, slot):
        start = NOW.date() - timedelta(days=100)
        await conn.executemany(
            """INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day)
            VALUES('SSE',$1,$2) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=EXCLUDED.is_trading_day""",
            [
                (start + timedelta(days=i), (start + timedelta(days=i)).weekday() < 5)
                for i in range(101)
            ],
        )

        async def fetch(requested_code, first, end, client, **kwargs):
            assert requested_code == code
            return [
                Observation(
                    symbol=code,
                    subject_type="FUND",
                    field_code="NAV",
                    value=Decimal("2"),
                    as_of=datetime.combine(
                        start + timedelta(days=i), datetime.min.time(), NOW.tzinfo
                    ),
                    quality_status="STALE",
                    metadata={"officialUnitNav": True},
                )
                for i in range(99)
                if (start + timedelta(days=i)).weekday() < 5
            ]

        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        history = await refresh_fund_nav_history(
            SingleConnection(conn),
            code,
            now=NOW,
            fetcher=fetch,
            now_provider=lambda: NOW,
        )
        assert history["priceBasis"] == "UNIT_NAV"
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_research_nav_histories WHERE fund_code=$1",
                code,
            )
            == 1
        )
        for statement in (
            "UPDATE market.fund_research_nav_histories SET available_at=now() WHERE fund_code=$1",
            "DELETE FROM market.fund_research_nav_histories WHERE fund_code=$1",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(statement, code)
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        monkeypatch.setattr(alpha_repository, "read_alpha_public", AsyncRead())
        result = await theme_graph.cached_nav_analysis(conn, code, NOW)
        assert result["state"] == "READY"
        assert result["actionableZones"]["currentPrice"] == "2.0000"
        assert result["navChanges"]["missingWindows"] == [252]
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO market.fund_research_nav_histories(fund_code,retrieved_at,available_at,payload) VALUES($1,now(),now(),'{}')",
                    code,
                )


class AsyncRead:
    async def __call__(self, conn, now):
        return {}
