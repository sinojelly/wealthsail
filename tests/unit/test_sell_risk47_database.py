"""Real API-role risk reads in the exact disposable review48 database."""

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

from backend.common.database import apply_tenant_context
from backend.investment.sell_risk import sell_risk_alerts
from backend.integrations.nav_history_proof import nav_points_digest

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="explicit disposable review48 DB required"),
]


@asynccontextmanager
async def fixture(*, qdii=False):
    uri = urlsplit(DSN)
    assert (uri.hostname, uri.port, uri.path) == (
        "127.0.0.1",
        55436,
        "/wealthsail_review48_validation_v1",
    )
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    clock = datetime(3100, 1, 10, 14, 40, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(
        days=uuid4().int % 1000
    )
    fund_code = "161129" if qdii else "008281"
    nav_lag = 5 if qdii else 1
    tenant, actor, other, account, instrument = [uuid4() for _ in range(5)]
    try:
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'risk48')", tenant
        )
        for user in (actor, other):
            await conn.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'risk48')", user
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
                tenant,
                user,
            )
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','risk48 fund')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER',$2,'FUND_CN','2000-01-01')",
            instrument,
            fund_code,
        )
        await conn.execute(
            "INSERT INTO identity.accounts(id,tenant_id,account_type,name) VALUES($1,$2,'MANUAL','risk48')",
            account,
            tenant,
        )
        await conn.execute(
            "INSERT INTO identity.account_members(tenant_id,account_id,user_id,relationship,permission_role,created_by,effective_from) VALUES($1,$2,$3,'BENEFICIAL_OWNER','OWNER',$3,'2000-01-01')",
            tenant,
            account,
            actor,
        )
        await conn.execute(
            "INSERT INTO ledger.position_snapshots(tenant_id,account_id,instrument_id,as_of,created_at,data_quality_status,quantity,avg_cost,market_price,market_value) VALUES($1,$2,$3,$4,$5,'FRESH',2,1,1.249,2.498)",
            tenant,
            account,
            instrument,
            clock.date() - timedelta(days=nav_lag),
            clock,
        )
        days = [
            clock.date() - timedelta(days=i)
            for i in range(249 + nav_lag, nav_lag - 1, -1)
        ]
        await conn.executemany(
            "INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day) VALUES('SSE',$1,true) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=true",
            [(clock.date() - timedelta(days=i),) for i in range(260)],
        )
        history = {
            "fundCode": fund_code,
            "asOf": str(days[-1]),
            "retrievedAt": clock.isoformat(),
            "availableAt": clock.isoformat(),
            "priceBasis": "UNIT_NAV",
            "sourceUri": f"https://fund.eastmoney.com/{fund_code}.html",
            "points": [
                {"date": str(day), "close": str(Decimal("1") + Decimal(i) / 1000)}
                for i, day in enumerate(days)
            ],
        }
        if qdii:
            history["overseasCalendarRequired"] = True
            history["providerHistoryProof"] = {
                "methodVersion": "eastmoney-nav-history-v1",
                "fundCode": fund_code,
                "sourceUri": "https://api.fund.eastmoney.com/f10/lsjz",
                "queryStart": str(days[0]),
                "queryEnd": str(clock.date()),
                "totalCount": len(days),
                "receivedRowCount": len(days),
                "uniquePointCount": len(days),
                "pointCount": len(days),
                "pageCount": 1,
                "requiredPages": 1,
                "complete": True,
                "firstDate": str(days[0]),
                "lastDate": str(days[-1]),
                "pointsDigest": nav_points_digest(history["points"]),
                "retrievedAt": clock.isoformat(),
            }
        await conn.execute(
            "INSERT INTO market.fund_research_nav_histories(fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$2,$3::jsonb)",
            fund_code,
            clock,
            json.dumps(history),
        )
        yield conn, clock, tenant, actor, other, account, instrument
    finally:
        await tx.rollback()
        await conn.close()


async def test_real_personal_holding_rsi_does_not_need_complete_account_valuation_and_revocation_hides_it():
    async with fixture() as (conn, clock, tenant, actor, other, account, instrument):
        await apply_tenant_context(conn, str(actor), str(tenant))
        result = await sell_risk_alerts(conn, tenant, actor, clock)
        assert len(result["items"]) == 1
        assert result["items"][0]["relationship"] == ["HELD"]
        assert result["items"][0]["state"] == "RSI_OVERBOUGHT"
        assert result["items"][0]["rsi14"] == 100
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM ledger.account_nav_snapshots WHERE tenant_id=$1)",
            tenant,
        )
        await apply_tenant_context(conn, str(other), str(tenant))
        assert (await sell_risk_alerts(conn, tenant, other, clock))["items"] == []
        await conn.execute("RESET ROLE")
        await conn.execute(
            "DELETE FROM identity.account_members WHERE tenant_id=$1 AND user_id=$2",
            tenant,
            actor,
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        assert (await sell_risk_alerts(conn, tenant, actor, clock))["items"] == []


async def test_real_frozen_tracking_survives_closed_holding_but_not_disabled_tracking():
    async with fixture() as (conn, clock, tenant, actor, other, account, instrument):
        track, version = uuid4(), uuid4()
        plan = {
            "subjectKind": "nav",
            "subjectId": "008281",
            "fundCode": "008281",
            "market": "FUND_CN",
            "priceBasis": "UNIT_NAV",
            "unit": "CNY/份",
            "asOf": str(clock.date() - timedelta(days=1)),
            "selectedTrack": "LEFT_VALUE",
            "tracks": [
                {
                    "track": "LEFT_VALUE",
                    "state": "WATCH",
                    "priceState": "READY",
                    "entryZoneMin": "0.9",
                    "entryZoneMax": "1.1",
                    "stopLossPrice": "0.8",
                    "takeProfitT1": "1.15",
                    "takeProfitT2": "1.2",
                }
            ],
        }
        scope = {"fundCode": "008281", "fundMarket": "FUND_CN"}
        await conn.execute(
            "INSERT INTO research.study_tracks(id,tenant_id,user_id,origin_key,title,scope,created_at,updated_at) VALUES($1,$2,$3,'risk48','PRIVATE_TITLE',$4::jsonb,$5,$5)",
            track,
            tenant,
            actor,
            json.dumps(scope),
            clock,
        )
        await conn.execute(
            "INSERT INTO research.study_versions(id,tenant_id,user_id,track_id,version,generation,snapshot,reason,command_key,command_hash,created_at) VALUES($1,$2,$3,$4,1,1,$5::jsonb,'risk48','risk48','risk48',$6)",
            version,
            tenant,
            actor,
            track,
            json.dumps(
                {
                    "actionablePlan": plan,
                    "thesis": "PRIVATE_NOTE",
                    "expiresOn": str(clock.date() + timedelta(days=30)),
                }
            ),
            clock,
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        item = (await sell_risk_alerts(conn, tenant, actor, clock))["items"][0]
        assert item["relationship"] == ["HELD", "TRACKED"]
        assert item["state"] == "TAKE_PROFIT_T2" and item["takeProfitT2"] == "1.2"
        assert "PRIVATE_NOTE" not in str(item) and "PRIVATE_TITLE" not in str(item)
        await conn.execute("RESET ROLE")
        await conn.execute(
            "UPDATE identity.accounts SET status='CLOSED' WHERE id=$1", account
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        assert (await sell_risk_alerts(conn, tenant, actor, clock))["items"][0][
            "relationship"
        ] == ["TRACKED"]
        await conn.execute(
            "UPDATE research.study_tracks SET enabled=false WHERE id=$1", track
        )
        assert (await sell_risk_alerts(conn, tenant, actor, clock))["items"] == []


async def test_real_qdii_lagged_quantity_remains_held_until_latest_zero_position():
    async with fixture(qdii=True) as (
        conn,
        clock,
        tenant,
        actor,
        other,
        account,
        instrument,
    ):
        await apply_tenant_context(conn, str(actor), str(tenant))
        result = await sell_risk_alerts(conn, tenant, actor, clock)
        item = result["items"][0]
        assert item["fundCode"] == "161129"
        assert item["relationship"] == ["HELD"]
        assert item["holdingsAsOf"] == str(clock.date() - timedelta(days=5))
        assert item["asOf"] == item["holdingsAsOf"]
        assert item["rsi14"] == 100
        await conn.execute("RESET ROLE")
        await conn.execute(
            "INSERT INTO ledger.position_snapshots(tenant_id,account_id,instrument_id,as_of,created_at,data_quality_status,quantity,avg_cost,market_price,market_value) VALUES($1,$2,$3,$4,$5,'FRESH',0,0,1.249,0)",
            tenant,
            account,
            instrument,
            clock.date(),
            clock,
        )
        await apply_tenant_context(conn, str(actor), str(tenant))
        assert (await sell_risk_alerts(conn, tenant, actor, clock))["items"] == []
