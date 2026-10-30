"""Opt-in integration coverage against an explicitly isolated loopback database."""

import os
from urllib.parse import urlsplit

import asyncpg
import pytest


def database_url():
    dsn = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("isolated investment database not configured")
    if urlsplit(dsn).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("investment integration tests require loopback")
    return dsn


@pytest.mark.asyncio
async def test_sql_and_python_publication_cutoffs_agree():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from backend.market.research_history import published_day

    conn = await asyncpg.connect(database_url())
    try:
        for hour in (10, 16, 18):
            now = datetime(2026, 10, 13, hour, tzinfo=ZoneInfo("Asia/Shanghai"))
            for publication in (None, 0, 16, 23):
                actual = await conn.fetchval(
                    "SELECT reference.research_available_day($1,$2,$3)",
                    publication,
                    now.date(),
                    now,
                )
                assert actual == published_day(now.date(), publication, now)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_expired_score_is_excluded_from_real_database_ranking():
    import json
    from datetime import timedelta
    from backend.investment.schemas import ScreenQuery
    from backend.investment.repository import screen
    from backend.investment.service import analysis, fund_dto
    conn=await asyncpg.connect(database_url())
    transaction=conn.transaction()
    await transaction.start()
    try:
        today=await conn.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
        await conn.execute("INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day) VALUES('SSE',$1,true) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=true",today)
        # Freshness requires calendar coverage from the snapshot through cutoff.
        # This fixture must not depend on another test having seeded yesterday.
        await conn.execute("INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day) VALUES('SSE',$1,true) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=true",today-timedelta(days=1))
        key="00000000-0000-0000-0000-00000000c499"
        await conn.execute("INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'EXCHANGE_FUND','SSE','review-identity') ON CONFLICT(id) DO NOTHING",key)
        await conn.execute("INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market) VALUES($1,'TICKER','999999','SSE') ON CONFLICT DO NOTHING",key)
        await conn.execute("""INSERT INTO market.investment_analysis_snapshots(subject_kind,subject_key,input_hash,source_uri,series_kind,as_of,available_at,method_version,result,points)
          VALUES('fund',$1,'fixture-expired','fixture://review','ETF_HFQ',$2,now(),'fixture-v1',$3::jsonb,'[]') ON CONFLICT DO NOTHING""",key,today-timedelta(days=1),json.dumps({"score":75,"state":"STRONG","market":"SSE","publicationHour":0}))
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        rows,total=await screen(conn,ScreenQuery(keyword="review-identity",scorableOnly=True))
        assert total==0 and rows==[]
        rows,total=await screen(conn,ScreenQuery(keyword="review-identity"))
        assert total==1
        assert fund_dto(dict(rows[0]))["dataState"]=="STALE"
        assert (await analysis(conn,"fund",key))["score"] is None
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_current_migrations_pass_deploy_readback():
    from scripts.migrate import _readback

    conn = await asyncpg.connect(database_url())
    try:
        await _readback(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_refresh_enqueue_deduplicates_and_status_does_not_cross_tenants():
    from uuid import uuid4
    from backend.common.database import apply_tenant_context
    from backend.investment.schemas import RefreshRequest
    from backend.investment.service import enqueue_refresh, refresh_status
    from backend.errors import NotFoundError

    conn = await asyncpg.connect(database_url())
    user, tenant, other_tenant = uuid4(), uuid4(), uuid4()
    transaction = conn.transaction()
    await transaction.start()
    try:
        # Seed the actor explicitly: a fresh isolated database must enforce the
        # same member-only API write policy without fixtures from another test.
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'refresh fixture')",
            user,
        )
        for identity in (tenant, other_tenant):
            await conn.execute(
                "INSERT INTO identity.tenants(id,name) VALUES($1,'refresh fixture')",
                identity,
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
                identity,
                user,
            )
        await apply_tenant_context(conn, str(user), str(tenant))
        assert await conn.fetchval("SELECT current_user") == "wealthsail_api"
        first = await enqueue_refresh(
            conn, tenant, RefreshRequest(kind="index", id="hs300")
        )
        second = await enqueue_refresh(
            conn, tenant, RefreshRequest(kind="index", id="hs300")
        )
        assert second["runId"] == first["runId"]
        assert second["created"] is False
        assert (await refresh_status(conn, tenant, first["runId"]))[
            "status"
        ] == "QUEUED"
        with pytest.raises(NotFoundError):
            await refresh_status(conn, other_tenant, first["runId"])
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_screener_queries_run_on_postgres_without_provider_io():
    from backend.investment import repository
    from backend.investment.schemas import ScreenQuery

    conn = await asyncpg.connect(database_url())
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE wealthsail_api")
            assert await repository.directions(conn)
            assert len(await repository.indices(conn, "a-share:broad")) == 3
            assert await repository.macro(conn)
            rows, total = await repository.screen(
                conn, ScreenQuery(page=2, keyword="test")
            )
            assert isinstance(total, int)
            assert isinstance(rows, list)
            assert (
                await conn.fetchval(
                    "SELECT has_table_privilege('wealthsail_api','market.investment_analysis_snapshots','INSERT')"
                )
                is False
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_macro_worker_persists_facts_with_unverified_publication(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from backend.common.database import create_pool
    from backend.jobs import investment_macro
    from backend.investment.service import market_context
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        investment_macro,
        "fetch_macro_history",
        AsyncMock(
            return_value=[
                {"name": "中国制造业PMI", "date": "2026-09", "value": "51", "unit": ""}
            ]
        ),
    )
    pool = await create_pool(
        database_url(), min_size=1, max_size=1, runtime_role="wealthsail_worker"
    )
    try:
        assert (
            await investment_macro.sync_macro(
                pool, datetime(2026, 10, 13, tzinfo=ZoneInfo("Asia/Shanghai"))
            )
        )["observations"] == 1
        async with pool.acquire() as conn:
            result = await market_context(conn)
            pmi = next(x for x in result["items"] if x["code"] == "PMI")
            assert pmi["value"] == 51
            assert pmi["publishedAt"] is None
            assert pmi["assessment"] is None
    finally:
        await pool.close()
