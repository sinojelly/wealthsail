"""Real database validation for governed investment sources and evidence."""
from uuid import uuid4
from datetime import date, timedelta
import json

import asyncpg
import pytest

from tests.integration.test_investment_database import database_url


@pytest.mark.asyncio
@pytest.mark.parametrize("series", ["UNIT_NAV", "PROVIDER_RETURN_INDEX"])
async def test_latest_nav_uses_nontrading_disclosure_without_changing_research_window(series):
    from backend.investment.repository import screen
    from backend.investment.schemas import ScreenQuery
    from backend.investment.service import fund_dto
    conn = await asyncpg.connect(database_url())
    tx = conn.transaction()
    await tx.start()
    key = uuid4()
    try:
        await conn.execute("""INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name)
          VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN',$2)""", key, str(key))
        await conn.execute("""INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market)
          VALUES($1,'TICKER','989897','FUND_CN')""", key)
        friday = {"date": "2026-10-09", "close": 130 if series == "PROVIDER_RETURN_INDEX" else 1.3, "unitNav": 1.3}
        sunday = {"date": "2026-10-11", "close": 131 if series == "PROVIDER_RETURN_INDEX" else 1.31, "unitNav": 1.31}
        await conn.execute("""INSERT INTO market.investment_analysis_snapshots
          (subject_kind,subject_key,input_hash,source_uri,series_kind,as_of,available_at,method_version,result,points)
          VALUES('fund',$1,$1,'https://example.org/nav',$2,$3,now(),'fixture',$4::jsonb,$5::jsonb)""",
          str(key), series, date(2026,10,9), json.dumps({"navObservations": [friday, sunday]}), json.dumps([friday]))
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        rows, total = await screen(conn, ScreenQuery(keyword=str(key)))
        assert total == 1
        item = fund_dto(dict(rows[0]))
        assert float(item["latestValue"]) == 1.31
        assert item["latestDate"] == "2026-10-11"
        assert item["latestValueBasis"] == "UNIT_NAV"
        assert item["historyDays"] == 1
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_known_closed_day_preserves_last_trade_but_unknown_tail_is_not_ready():
    from datetime import date
    from backend.market.research_history import expected_trading_day
    conn=await asyncpg.connect(database_url())
    tx=conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM reference.trading_calendars WHERE market='SGE'")
        await conn.execute("""INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day)
          VALUES('SGE','2026-10-16',true),('SGE','2026-10-17',false)""")
        assert await expected_trading_day(conn,'SGE',date(2026,10,17),date(2026,10,16))==date(2026,10,16)
        assert await expected_trading_day(conn,'SGE',date(2026,10,19),date(2026,10,16)) is None
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_worker_can_refresh_calendar_without_widening_api_writes(monkeypatch):
    from backend.common.database import create_pool
    from backend.market.service import fill_trading_calendar
    import akshare
    import pandas as pd
    from datetime import date
    monkeypatch.setattr(akshare,'tool_trade_date_hist_sina',lambda:pd.DataFrame({
        'trade_date':[date(2021,10,14),date(2026,10,13)]}))
    pool=await create_pool(database_url(),min_size=1,max_size=1,runtime_role='wealthsail_worker')
    try:
        assert await fill_trading_calendar(pool)>0
    finally:
        await pool.close()
    conn=await asyncpg.connect(database_url())
    try:
        assert not await conn.fetchval("SELECT has_table_privilege('wealthsail_api','reference.trading_calendars','INSERT')")
        assert not await conn.fetchval("SELECT has_column_privilege('wealthsail_worker','reference.trading_calendars','market','UPDATE')")
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "source", "return", "calendar", "tail_calendar", "revised_closed_day", "second_mapping", "cost_stale", "valuation_config"])
async def test_value_screening_revokes_old_evidence_when_governance_changes(change):
    from backend.investment.repository import screen
    from backend.investment.schemas import ScreenQuery
    from backend.investment.service import analysis, fund_dto
    from tests.unit.test_value_evidence import valuation, costs
    conn = await asyncpg.connect(database_url())
    tx = conn.transaction()
    await tx.start()
    key, index_id = uuid4(), f"test-{uuid4()}"
    try:
        now = await conn.fetchval("SELECT now()")
        today = await conn.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
        market = 'SGE' if change == 'calendar' else 'SSE'
        if change == 'calendar':
            await conn.execute("DELETE FROM reference.trading_calendars WHERE market='SGE'")
        else:
            await conn.execute("""INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day)
              VALUES('SSE',$1,true) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=true""", today)
        config = {"sourceVerified": True,"returnSeriesVerified": True,
                  "sourceUri": "https://example.org/nav", "publishHour": 0}
        val_config = {"provider": "LEGULEGU", "symbol": "沪深300", "verified": True,
                      "sourceUri": "https://legulegu.com/stockdata/hs300-ttm-lyr", "publishHour": 0}
        await conn.execute("""INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name,metadata)
          VALUES($1,'OFF_EXCHANGE_FUND',$2,$3,$4::jsonb)""", key,market,str(key),json.dumps({"research": config}))
        await conn.execute("""INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market)
          VALUES($1,'TICKER','989898',$2)""",key,market)
        await conn.execute("""INSERT INTO reference.research_indices(id,code,name,market,verified_at,publish_hour,valuation)
          VALUES($1,$1,'fixture','SSE',now(),0,$2::jsonb)""",index_id,json.dumps(val_config))
        await conn.execute("""INSERT INTO reference.fund_index_links(instrument_id,index_id,effective_from,source_uri,verified_at)
          VALUES($1,$2,$3,'https://example.org/tracking',now())""",key,index_id,today)
        v = valuation()
        snapshot_day=today-timedelta(days=1) if change=='tail_calendar' else today
        if change=='tail_calendar':
            await conn.execute("DELETE FROM reference.trading_calendars WHERE market='SSE' AND calendar_date=$1",today)
        if change=='revised_closed_day':
            await conn.execute("""INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day)
              VALUES('SSE',$1,true) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=true""",today-timedelta(days=1))
            await conn.execute("UPDATE reference.trading_calendars SET is_trading_day=false WHERE market='SSE' AND calendar_date=$1",today)
        shift = snapshot_day - __import__('datetime').date.fromisoformat(v['requestedEnd'])
        v['requestedEnd'] = snapshot_day.isoformat()
        for part in ('pe','pb'):
            for point in v[part]['points']:
                point['date'] = (__import__('datetime').date.fromisoformat(point['date'])+shift).isoformat()
        c = costs(retrievedAt=now.isoformat())
        result = {"score": None,"state": "UNSUPPORTED","dataQuality": "READY","publicationHour": 0,
                  "market": market,"calendarMarket": market,
                  "performance": {"eligible": True,"state": "READY","maxDrawdown": 0.35},
                  "valueEvidence": {"eligible": True,"state": "RESEARCH_CANDIDATE"},
                  "fundamentals": {"valuation": v,"costs": c,"mapped": True,"indexId": index_id,"valuationConfig": val_config}}
        if change=='cost_stale':
            c['retrievedAt']=(now-timedelta(days=46)).isoformat()
        await conn.execute("""INSERT INTO market.investment_analysis_snapshots
          (subject_kind,subject_key,input_hash,source_uri,series_kind,as_of,available_at,method_version,result,points)
          VALUES('fund',$1,$1,'https://example.org/nav','PROVIDER_RETURN_INDEX',$2,now(),'fixture',$3::jsonb,'[]')""",str(key),snapshot_day,json.dumps(result))
        if change in ('source','return'):
            config['sourceVerified' if change=='source' else 'returnSeriesVerified']=False
            await conn.execute("UPDATE reference.instruments SET metadata=$2::jsonb WHERE id=$1",key,json.dumps({"research":config}))
        if change=='valuation_config':
            await conn.execute("UPDATE reference.research_indices SET valuation='{}' WHERE id=$1",index_id)
        if change=='second_mapping':
            await conn.execute("UPDATE reference.research_indices SET verified_at=now() WHERE id='hs300'")
            await conn.execute("""INSERT INTO reference.fund_index_links(instrument_id,index_id,effective_from,source_uri,verified_at)
              VALUES($1,'hs300',$2,'https://example.org/other',now())""",key,today)
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        rows,total=await screen(conn,ScreenQuery(keyword=str(key),valueCandidateOnly=True))
        assert total==(1 if change=='none' else 0)
        all_rows,_=await screen(conn,ScreenQuery(keyword=str(key)))
        assert fund_dto(dict(all_rows[0]))['valueEligible'] is (change=='none')
        diagnosis=await analysis(conn,'fund',str(key))
        assert diagnosis['valueEvidence']['eligible'] is (change=='none')
        if change in ('calendar','tail_calendar','revised_closed_day'):
            assert fund_dto(dict(all_rows[0]))['performanceState']=='UNVERIFIED'
            assert diagnosis['performance']['state']=='UNVERIFIED'
            assert diagnosis['performance']['eligible'] is False
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_macro_context_catalogue_covers_growth_inflation_and_liquidity():
    from backend.investment.repository import macro
    conn = await asyncpg.connect(database_url())
    try:
        await conn.execute("SET ROLE wealthsail_api")
        catalog = {row["field_code"]: row["dimension"] for row in await macro(conn)}
        assert catalog["PMI"] == "growth"
        assert catalog["CPI_YOY"] == "inflation"
        assert catalog["PPI_YOY"] == "inflation"
        assert catalog["M2_YOY"] == "liquidity"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_index_refresh_loads_registered_valuation_config():
    from backend.jobs.investment_research import load_subject
    conn = await asyncpg.connect(database_url())
    tx = conn.transaction()
    await tx.start()
    try:
        config = {"provider": "LEGULEGU", "symbol": "沪深300", "verified": True,
                  "sourceUri": "https://legulegu.com/stockdata/hs300-ttm-lyr", "publishHour": 18}
        await conn.execute("UPDATE reference.research_indices SET valuation=$1::jsonb WHERE id='hs300'", json.dumps(config))
        subject = await load_subject(conn, 'index', 'hs300')
        assert json.loads(subject['valuation']) == config
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_worker_can_register_fund_source_without_changing_identity():
    from backend.common.database import create_pool
    from scripts.register_investment_reference import ReferenceImport, register

    conn = await asyncpg.connect(database_url())
    key = uuid4()
    try:
        await conn.execute("""INSERT INTO reference.instruments
          (id,asset_type,primary_market,canonical_name,metadata)
          VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','source-contract',
            '{"unrelated":7,"research":{"keep":true}}')""", key)
        pool = await create_pool(database_url(), min_size=1, max_size=1,
                                 runtime_role="wealthsail_worker")
        try:
            await register(pool, ReferenceImport.model_validate({"funds": [{
                "instrumentId": str(key), "sourceUri": "https://example.org/fund/nav",
                "publishHour": 23, "returnSeriesVerified": True,
            }]}))
        finally:
            await pool.close()
        import json
        metadata = json.loads(await conn.fetchval(
            "SELECT metadata FROM reference.instruments WHERE id=$1", key))
        assert metadata["unrelated"] == 7
        assert metadata["research"]["keep"] is True
        assert metadata["research"]["returnSeriesVerified"] is True
        assert not await conn.fetchval("""SELECT has_column_privilege(
          'wealthsail_api','reference.instruments','metadata','UPDATE')""")
        assert not await conn.fetchval("""SELECT has_column_privilege(
          'wealthsail_worker','reference.instruments','canonical_name','UPDATE')""")
    finally:
        await conn.execute("DELETE FROM reference.instruments WHERE id=$1", key)
        await conn.close()
