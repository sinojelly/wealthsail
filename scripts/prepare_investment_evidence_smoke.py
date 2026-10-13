"""Seed synthetic research evidence only in a dedicated loopback test database.

Apply migrations first, set INVESTMENT_TEST_DATABASE_URL, then run:
python -m scripts.prepare_investment_evidence_smoke
The accompanying browser test is frontend/scripts/investment-evidence-smoke.cjs.
No runtime login roles, real holdings or production sources are changed.
"""

import asyncio
from datetime import date, datetime, timedelta
import ipaddress
import json
import os
from urllib.parse import urlsplit


def validate_target(value):
    parts = urlsplit(value)
    try:
        loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = parts.hostname == "localhost"
    if not (
        parts.scheme == "postgresql"
        and loopback
        and parts.username == "postgres"
        and parts.path.startswith("/wealthsail_investment_check_")
    ):
        raise ValueError(
            "Fixture seed requires postgres on loopback and a dedicated wealthsail_investment_check_* database"
        )


def dsn():
    value = os.environ.get("INVESTMENT_TEST_DATABASE_URL", "")
    validate_target(value)
    return value


async def seed():
    import asyncpg
    from backend.common.auth import hash_password
    from tests.unit.test_value_evidence import valuation, costs

    conn = await asyncpg.connect(dsn())
    now = await conn.fetchval("SELECT now()")
    today = await conn.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
    user = "00000000-0000-0000-0000-00000000c101"
    tenant = "00000000-0000-0000-0000-00000000c102"
    account = "00000000-0000-0000-0000-00000000c103"
    fund = "00000000-0000-0000-0000-00000000c144"
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'Isolated Research Acceptance') ON CONFLICT DO NOTHING",
        tenant,
    )
    await conn.execute(
        """INSERT INTO identity.profiles(id,email,nickname,password_hash)
      VALUES($1,'ci-smoke@wealthsail.example','Acceptance Only',$2)
      ON CONFLICT(id) DO UPDATE SET password_hash=EXCLUDED.password_hash""",
        user,
        hash_password("SmokeLogin-Only-2026!"),
    )
    await conn.execute(
        "INSERT INTO identity.tenant_members(tenant_id,user_id,role,status) VALUES($1,$2,'VIEWER','ACTIVE') ON CONFLICT DO NOTHING",
        tenant,
        user,
    )
    await conn.execute(
        "INSERT INTO identity.accounts(id,tenant_id,account_type,name) VALUES($1,$2,'MANUAL','Empty Acceptance') ON CONFLICT DO NOTHING",
        account,
        tenant,
    )
    await conn.execute(
        """INSERT INTO ledger.account_nav_snapshots(tenant_id,account_id,as_of,cash_balance,positions_market_value,total_nav,
      daily_pnl,cumulative_pnl,data_quality_status,known_value,valuation_coverage_pct,unpriced_position_count)
      VALUES($1,$2,$3,0,0,0,0,0,'FRESH',0,100,0) ON CONFLICT DO NOTHING""",
        tenant,
        account,
        today,
    )
    config = {
        "sourceVerified": True,
        "returnSeriesVerified": True,
        "sourceUri": "https://example.org/acceptance-nav",
        "publishHour": 0,
    }
    val_config = {
        "provider": "LEGULEGU",
        "symbol": "沪深300",
        "verified": True,
        "sourceUri": "https://example.org/acceptance-valuation",
        "publishHour": 0,
    }
    await conn.execute(
        """INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name,metadata)
      VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','验收基金（测试数据）',$2::jsonb)
      ON CONFLICT(id) DO UPDATE SET metadata=EXCLUDED.metadata""",
        fund,
        json.dumps({"research": config}),
    )
    await conn.execute(
        """INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market)
      VALUES($1,'TICKER','989898','FUND_CN') ON CONFLICT DO NOTHING""",
        fund,
    )
    await conn.execute(
        "UPDATE reference.research_indices SET verified_at=now(),publish_hour=0,valuation=$1::jsonb WHERE id='hs300'",
        json.dumps(val_config),
    )
    await conn.execute(
        """INSERT INTO reference.fund_index_links(instrument_id,index_id,effective_from,source_uri,verified_at)
      VALUES($1,'hs300',$2,'https://example.org/acceptance-mapping',now()) ON CONFLICT DO NOTHING""",
        fund,
        today,
    )
    await conn.execute(
        """INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day)
      VALUES('SSE',$1,true) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=true""",
        today,
    )
    points = [
        {
            "date": (today - timedelta(days=999 - i)).isoformat(),
            "close": 100 + i * 0.03,
            "unitNav": 1 + i * 0.0003,
        }
        for i in range(1000)
    ]
    v = valuation()
    offset = today - date.fromisoformat(v["requestedEnd"])
    v["requestedEnd"] = today.isoformat()
    for part in ("pe", "pb"):
        v[part]["sourceUri"] = val_config["sourceUri"]
        for p in v[part]["points"]:
            p["date"] = (date.fromisoformat(p["date"]) + offset).isoformat()
    c = costs(retrievedAt=now.isoformat())
    performance = {
        "state": "READY",
        "eligible": True,
        "return63": 0.04,
        "return252": 0.12,
        "maxDrawdown": 0.2,
        "volatility20": 0.15,
        "historyStart": points[0]["date"],
        "historyEnd": points[-1]["date"],
        "sampleCount": 1000,
        "reasons": [],
    }
    from backend.indicators.value_evidence import evaluate

    result = {
        "state": "UNSUPPORTED",
        "score": None,
        "indicators": {},
        "reasons": [],
        "dataQuality": "READY",
        "market": "FUND_CN",
        "calendarMarket": "SSE",
        "publicationHour": 0,
        "performance": performance,
        "costs": c,
        "valuation": v,
        "valueEvidence": evaluate(v, c, performance, mapped=True),
        "fundamentals": {
            "costs": c,
            "valuation": v,
            "mapped": True,
            "indexId": "hs300",
            "valuationConfig": val_config,
        },
    }
    await conn.execute(
        """INSERT INTO market.investment_analysis_snapshots(subject_kind,subject_key,input_hash,source_uri,
      series_kind,as_of,available_at,method_version,result,points)
      VALUES('fund',$1,$2,$3,'PROVIDER_RETURN_INDEX',$4,now(),'ACCEPTANCE ONLY',$5::jsonb,$6::jsonb) ON CONFLICT DO NOTHING""",
        fund,
        now.isoformat(),
        config["sourceUri"],
        today,
        json.dumps(result),
        json.dumps(points),
    )
    source_id = await conn.fetchval("""INSERT INTO reference.data_sources(name,source_type,license_model,allowed_usage,priority,status)
      VALUES('ISOLATED_ACCEPTANCE_FIXTURE','MACRO','PUBLIC_API','PRICE_OBSERVATION',80,'ACTIVE') ON CONFLICT(name) DO UPDATE SET status='ACTIVE' RETURNING id""")
    from backend.market.repository import insert_observations, insert_source_snapshot

    snapshot = await insert_source_snapshot(
        conn,
        source_id=source_id,
        retrieval_key=now.isoformat(),
        observations=[],
        quality_status="STALE",
        retrieved_at=now,
    )
    from uuid import uuid5, NAMESPACE_URL

    month = today.year * 12 + today.month - 1
    for n in range(36):
        number = month - n - 1
        day = today.replace(year=number // 12, month=number % 12 + 1, day=1)
        period = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo)
        await insert_observations(
            conn,
            [
                (
                    "MACRO_SERIES",
                    str(uuid5(NAMESPACE_URL, "acceptance:PMI")),
                    "PMI",
                    49 + n * 0.03,
                    None,
                    None,
                    "",
                    "CNY",
                    period,
                    now,
                    snapshot,
                    source_id,
                    "STALE",
                    "fixture-only",
                    json.dumps({"publicationVerified": False}),
                )
            ],
        )
    await conn.close()
    print("Isolated synthetic acceptance fixture ready")


if __name__ == "__main__":
    asyncio.run(seed())
