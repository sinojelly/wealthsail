"""Price-watch SELECTs run as the real API role, only in the owned review44 DB."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.investment import study_service as service
from test_study_quant_scan_database import seed, request, public_facts
from test_strategy_memory_database import actor_role

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="owned review44 price DB required"),
]


@pytest.mark.parametrize("estimate", [False, True, "holding_no_cache"])
async def test_price_watch_real_actor_list_detail_and_existing_1430_facts(
    monkeypatch, estimate
):
    uri = urlsplit(DSN)
    assert uri.hostname == "127.0.0.1" and uri.port in {55434, 55436}
    assert uri.path in {
        "/wealthsail_review44_validation_v1",
        "/wealthsail_review45_validation_v1",
        "/wealthsail_review48_validation_v1",
    }
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    clock = datetime(3000, 1, 1, 14, 40, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(
        days=uuid4().int % 20000
    )
    monkeypatch.setattr(service, "load_observations", public_facts)
    try:
        tenant, actor, other, run, _ = await seed(conn, clock)
        account, instrument = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','watch44')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER','008281','FUND_CN','2000-01-01')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO identity.accounts(id,tenant_id,account_type,name) VALUES($1,$2,'MANUAL','watch44')",
            account,
            tenant,
        )
        await conn.execute(
            "INSERT INTO identity.account_members(tenant_id,account_id,user_id,relationship,permission_role,created_by,effective_from) VALUES($1,$2,$3,'BENEFICIAL_OWNER','OWNER',$3,$4)",
            tenant,
            account,
            actor,
            clock.date() - timedelta(days=15),
        )
        latest = clock.date() - timedelta(days=1)
        # Creating a new study reprices its source plan from the same complete
        # two-year NAV window as the chart. The price-watch assertions below
        # exercise that real freeze path rather than bypassing current checks.
        calendar = [
            {
                "date": str(day),
                "isTradingDay": day <= latest
                and (day.weekday() < 5 or day >= latest - timedelta(days=2)),
            }
            for day in (clock.date() - timedelta(days=i) for i in reversed(range(900)))
        ]
        await conn.executemany(
            "INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day) VALUES('SSE',$1,$2) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=excluded.is_trading_day",
            [
                (date.fromisoformat(row["date"]), row["isTradingDay"])
                for row in calendar
            ],
        )
        for table, cols, values in (
            (
                "position_snapshots",
                "instrument_id,quantity,avg_cost,market_price,market_value",
                "$5,1,1,1.02,1.02",
            ),
            (
                "account_nav_snapshots",
                "cash_balance,positions_market_value,total_nav,valuation_coverage_pct,unpriced_position_count",
                "0,1.02,1.02,100,0",
            ),
        ):
            args = [tenant, account, latest, clock - timedelta(minutes=20)]
            if table == "position_snapshots":
                args.append(instrument)
            await conn.execute(
                f"INSERT INTO ledger.{table}(tenant_id,account_id,as_of,created_at,data_quality_status,{cols}) VALUES($1,$2,$3,$4,'FRESH',{values})",
                *args,
            )
        history = {
            "fundCode": "008281",
            "asOf": str(latest),
            "priceBasis": "UNIT_NAV",
            "currency": "CNY",
            "market": "FUND_CN",
            "unit": "CNY/份",
            "sourceUri": "https://fund.eastmoney.com/008281.html",
            "retrievedAt": clock.isoformat(),
            "availableAt": clock.isoformat(),
            "points": [
                {
                    "date": row["date"],
                    "close": str(Decimal("1.00") + Decimal(i % 100) / 500),
                }
                for i, row in enumerate(
                    [row for row in calendar if row["isTradingDay"]][-550:]
                )
            ],
            "calendarProof": {
                "market": "SSE",
                "checkedThrough": clock.date().isoformat(),
                "days": calendar,
            },
        }
        history["points"][-1]["close"] = "1.02"
        cache_id = await conn.fetchval(
            "INSERT INTO market.fund_research_nav_histories(fund_code,retrieved_at,available_at,payload) VALUES('008281',$1,$1,$2::jsonb) RETURNING id",
            clock,
            json.dumps(history),
        )
        await actor_role(conn, actor)
        created = await service.create_study(conn, tenant, actor, request(run), clock)
        track, version = created["track"]["id"], created["version"]["id"]
        frozen = created["version"]["snapshot"]["actionablePlan"]
        if estimate == "holding_no_cache":
            # No cache is a later read-model condition: the original study was
            # validly frozen before its locally persisted NAV became unavailable.
            await conn.execute("RESET ROLE")
            await conn.execute(
                "DELETE FROM market.fund_research_nav_histories WHERE id=$1",
                cache_id,
            )
            await actor_role(conn, actor)
        if estimate:
            await conn.execute("RESET ROLE")
            check = {
                "trackId": track,
                "versionId": version,
                "state": "STOP_LOSS",
                "price": "1.02" if estimate == "holding_no_cache" else "0.95",
                "asOf": clock.replace(minute=30).isoformat(),
                "selectedTrack": "RIGHT_TREND",
                "planAsOf": frozen["asOf"],
                "priceBasis": "UNIT_NAV",
                "unit": frozen["unit"],
                "marketSourceUri": "https://fund.example.org/008281/estimate",
                **{
                    key: frozen["tracks"][0][key]
                    for key in (
                        "entryZoneMin",
                        "entryZoneMax",
                        "stopLossPrice",
                        "takeProfitT1",
                    )
                },
            }
            await conn.execute(
                "INSERT INTO research.alpha_opportunity_runs(tenant_id,user_id,run_date,slot,status,claimed_at,finished_at,result) VALUES($1,$2,$3,'FUND_1430','COMPLETED',$4,$4,$5::jsonb)",
                tenant,
                actor,
                clock.date(),
                clock,
                json.dumps({"checks": [check]}),
            )
            await actor_role(conn, actor)
        data = await service.list_studies(conn, tenant, actor, clock)
        watch = data["items"][0]["priceWatch"]
        assert watch["state"] == ("STOP_LOSS" if estimate is True else "HOLDING")
        if estimate == "holding_no_cache":
            assert watch["confirmedNav"] is None
        else:
            assert Decimal(watch["confirmedNav"]["value"]) == Decimal("1.02")
        assert watch["frozenPlan"] == frozen
        detail = await service.get_detail(
            conn, tenant, actor, track, clock, observations=[]
        )
        assert detail["priceWatch"] == watch
        await actor_role(conn, other)
        assert (await service.list_studies(conn, tenant, other, clock))["items"] == []
        if not estimate:
            await conn.execute("RESET ROLE")
            await conn.execute(
                "UPDATE identity.accounts SET status='CLOSED' WHERE id=$1", account
            )
            await actor_role(conn, actor)
            closed = await service.list_studies(conn, tenant, actor, clock)
            assert closed["items"][0]["priceWatch"]["state"] == "OBSERVING"
            paused = await service.set_status(
                conn,
                tenant,
                actor,
                track,
                {"expectedGeneration": 1, "enabled": False},
                clock,
            )
            assert paused["priceWatch"]["frozenPlan"] == frozen
            resumed = await service.set_status(
                conn,
                tenant,
                actor,
                track,
                {"expectedGeneration": 2, "enabled": True},
                clock,
            )
            assert resumed["priceWatch"]["frozenPlan"] == frozen
    finally:
        await tx.rollback()
        await conn.close()
