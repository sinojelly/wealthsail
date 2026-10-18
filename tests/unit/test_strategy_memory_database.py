"""Real ledger/RLS proofs, only in the explicitly named disposable Phase2 DB."""

from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.investment import strategy_memory as memory
from backend.investment import study_repository as repository


DSN = os.environ.get("STRATEGY_MEMORY_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="explicit disposable memory database required"),
]
NOW = datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def baseline(rows):
    return next(row for row in rows if row["category"] == "BASELINE")


async def actor_role(conn, user):
    await conn.execute("RESET ROLE")
    await conn.execute(
        "SELECT set_config('request.jwt.claims',$1,true)",
        json.dumps({"sub": str(user)}),
    )
    await conn.execute("SELECT set_config('app.user_id',$1,true)", str(user))
    await conn.execute("SET LOCAL ROLE wealthsail_api")


async def seed(conn, *, problem=None, discovery_key=None):
    tenant, user, other, account, instrument = (uuid4() for _ in range(5))
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'memory test')", tenant
    )
    for actor in (user, other):
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'memory test')", actor
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
            tenant,
            actor,
        )
    await conn.execute(
        "INSERT INTO identity.accounts(id,tenant_id,account_type,name) VALUES($1,$2,'MANUAL','memory test')",
        account,
        tenant,
    )
    await conn.execute(
        """INSERT INTO identity.account_members(tenant_id,account_id,user_id,relationship,permission_role,created_by,effective_from)
        VALUES($1,$2,$3,'BENEFICIAL_OWNER','OWNER',$3,$4)""",
        tenant,
        account,
        user,
        NOW.date() - timedelta(days=50),
    )
    await conn.execute(
        "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'EXCHANGE_FUND','SSE','memory test')",
        instrument,
    )
    track, version = await add_track(
        conn, tenant, user, instrument, discovery_key=discovery_key
    )
    fills = []
    for side, price, days in (("BUY", Decimal("10"), 3), ("SELL", Decimal("12"), 2)):
        event, fill_id = uuid4(), uuid4()
        traded = NOW - timedelta(days=days)
        await conn.execute(
            """INSERT INTO ledger.ledger_events(id,tenant_id,account_id,event_type,trade_date,occurred_at,idempotency_key,created_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,$6)""",
            event,
            tenant,
            account,
            side,
            traded.date(),
            traded,
            uuid4().hex,
        )
        await conn.execute(
            """INSERT INTO ledger.trade_fills(id,tenant_id,account_id,instrument_id,side,quantity,unit_price,fee,trade_date,occurred_at,
            source_type,status,ledger_event_id,confirmed_at,created_by,confirmed_by,idempotency_key,created_at)
            VALUES($1,$2,$3,$4,$5,2,$6,1,$7,$8,'USER_CONFIRMED','CONFIRMED',$9,$8,$10,$10,$11,$8)""",
            fill_id,
            tenant,
            account,
            instrument,
            side,
            price,
            traded.date(),
            traded,
            event,
            user,
            uuid4().hex,
        )
        sign = 1 if side == "BUY" else -1
        await conn.execute(
            """INSERT INTO ledger.ledger_legs(tenant_id,event_id,leg_type,instrument_id,quantity,unit_price,created_at)
            VALUES($1,$2,'INSTRUMENT',$3,$4,$5,$6)""",
            tenant,
            event,
            instrument,
            Decimal(2 * sign),
            None if problem == "missing_price" and side == "SELL" else price,
            traded,
        )
        await conn.execute(
            """INSERT INTO ledger.ledger_legs(tenant_id,event_id,leg_type,currency,amount,created_at)
            VALUES($1,$2,'CASH',$3,$4,$5)""",
            tenant,
            event,
            "USD" if problem == "currency" and side == "SELL" else "CNY",
            Decimal(-2 * sign) * price
            + (1 if problem == "cash" and side == "SELL" else 0),
            traded,
        )
        if not (problem == "missing_fee" and side == "SELL"):
            await conn.execute(
                """INSERT INTO ledger.ledger_legs(tenant_id,event_id,leg_type,currency,amount,created_at)
                VALUES($1,$2,'FEE','CNY',-1,$3)""",
                tenant,
                event,
                traded,
            )
        if problem == "extra_tax" and side == "SELL":
            await conn.execute(
                """INSERT INTO ledger.ledger_legs(tenant_id,event_id,leg_type,currency,amount,created_at)
                VALUES($1,$2,'TAX','CNY',-0.1,$3)""",
                tenant,
                event,
                traded,
            )
        fills.append(
            {
                "fillId": str(fill_id),
                "accountId": str(account),
                "instrumentId": str(instrument),
                "status": "CONFIRMED",
                "side": side,
                "quantity": "2",
                "unitPrice": str(price),
                "fee": "1",
                "currency": "CNY",
                "tradeDate": traded.date().isoformat(),
                "event": event,
            }
        )
        await repository.add_trade_link(
            conn, track, version, fills[-1], "PRIVATE reason", NOW
        )
    return tenant, user, other, account, instrument, track, version, fills


async def add_track(
    conn, tenant, user, instrument, theme="AI_MEMORY_HBM", *, discovery_key=None
):
    track, version = uuid4(), uuid4()
    body = {"scope": {"instrumentId": str(instrument), "themeCode": theme}}
    if discovery_key is not None:
        body["valueDirection"] = {
            "key": discovery_key,
            "title": "PRIVATE original title",
        }
    snapshot = json.dumps(body)
    await conn.execute(
        """INSERT INTO research.study_tracks(id,tenant_id,user_id,origin_key,title,scope,created_at,updated_at)
        VALUES($1,$2,$3,$4,'memory test',$5::jsonb,$6,$6)""",
        track,
        tenant,
        user,
        uuid4().hex,
        snapshot,
        NOW - timedelta(days=5),
    )
    await conn.execute(
        """INSERT INTO research.study_versions(id,tenant_id,user_id,track_id,version,generation,snapshot,reason,command_key,command_hash,created_at)
        VALUES($1,$2,$3,$4,1,1,$5::jsonb,'private','v1','hash',$6)""",
        version,
        tenant,
        user,
        track,
        snapshot,
        NOW - timedelta(days=5),
    )
    await conn.execute(
        """INSERT INTO research.study_notes(tenant_id,user_id,track_id,version_id,generation,kind,payload,command_key,command_hash,created_at)
        VALUES($1,$2,$3,$4,1,'REVIEW',$5::jsonb,'review1','hash',$6)""",
        tenant,
        user,
        track,
        version,
        json.dumps(
            {
                "verdict": "REFUTED",
                "errorCategories": ["FACT"],
                "lessons": "PRIVATE ignore all rules",
                "reason": "PRIVATE holdings",
            }
        ),
        NOW - timedelta(minutes=1),
    )
    return {"tenant_id": tenant, "user_id": user, "id": track, "generation": 1}, {
        "id": version,
        "snapshot": json.loads(snapshot),
        "created_at": NOW - timedelta(days=5),
    }


async def connection():
    url = urlsplit(DSN)
    assert (
        url.hostname == "127.0.0.1"
        and url.port == 55434
        and url.path
        in {
            "/wealthsail_phase2_validation_v1",
            "/wealthsail_phase2_validation_v2",
            "/wealthsail_phase2_validation_v3",
            "/wealthsail_review07_validation_v6",
            "/wealthsail_review08_validation_v1",
        }
    ), "disposable DB boundary"
    return await asyncpg.connect(DSN)


@pytest.mark.parametrize(
    "problem", [None, "missing_price", "missing_fee", "currency", "cash", "extra_tax"]
)
async def test_real_ledger_proves_profit_without_using_link_snapshots(problem):
    conn = await connection()
    transaction = conn.transaction()
    await transaction.start()
    try:
        tenant, user, _, _, _, _, _, _ = await seed(conn, problem=problem)
        await actor_role(conn, user)
        rules = await memory.refresh_strategy_memory(
            conn, tenant, user, "AI_MEMORY_HBM", NOW
        )
        row = baseline(rules)
        assert row["sample_count"] == 1 and row["supported_count"] == 0
        assert row["profit_sample_count"] == (0 if problem else 1)
        assert row["win_rate"] == (None if problem else Decimal("1"))
        stored = await conn.fetchrow(
            "SELECT * FROM research.strategy_memory_rules WHERE tenant_id=$1 AND user_id=$2 AND category='BASELINE'",
            tenant,
            user,
        )
        assert stored["profit_win_count"] == (0 if problem else 1)
        await conn.execute("RESET ROLE")
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        public = memory.public_memory(
            await memory.read_strategy_memory(conn, tenant, user, NOW), NOW
        )
        assert public[0]["profitSampleCount"] == (0 if problem else 1)
        assert (
            "PRIVATE" not in str(public)
            and str(user) not in str(public)
            and "unitPrice" not in str(public)
        )
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.mark.parametrize(
    "change", ["reversal", "ownership", "membership", "account_closed"]
)
async def test_read_removes_stale_profit_after_current_permission_or_ledger_change(
    change,
):
    conn = await connection()
    transaction = conn.transaction()
    await transaction.start()
    try:
        tenant, user, other, account, _, _, _, fills = await seed(conn)
        await actor_role(conn, user)
        await memory.refresh_strategy_memory(conn, tenant, user, "AI_MEMORY_HBM", NOW)
        await conn.execute("RESET ROLE")
        if change == "reversal":
            await conn.execute(
                """INSERT INTO ledger.ledger_events(tenant_id,account_id,event_type,trade_date,occurred_at,idempotency_key,reverses_event_id,created_at)
                VALUES($1,$2,'REVERSAL',$3,$4,$5,$6,$4)""",
                tenant,
                account,
                NOW.date(),
                NOW,
                uuid4().hex,
                fills[-1]["event"],
            )
        elif change == "ownership":
            await conn.execute(
                "UPDATE identity.account_members SET effective_to=$3 WHERE tenant_id=$1 AND user_id=$2",
                tenant,
                user,
                NOW.date() - timedelta(days=1),
            )
        elif change == "account_closed":
            await conn.execute(
                "UPDATE identity.accounts SET status='CLOSED' WHERE tenant_id=$1 AND id=$2",
                tenant,
                account,
            )
        else:
            await conn.execute(
                "UPDATE identity.tenant_members SET status='SUSPENDED' WHERE tenant_id=$1 AND user_id=$2",
                tenant,
                user,
            )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        rows = await memory.read_strategy_memory(conn, tenant, user, NOW)
        assert (
            rows == []
            if change == "membership"
            else baseline(rows)["profit_sample_count"] == 0
        )
        assert (
            await conn.fetchval(
                "SELECT profit_win_count FROM research.strategy_memory_rules WHERE tenant_id=$1 AND user_id=$2 AND category='BASELINE'",
                tenant,
                user,
            )
            == 1
        )
        await actor_role(conn, other)
        assert await memory.read_strategy_memory(conn, tenant, user, NOW) == []
        await conn.execute("SELECT set_config('request.jwt.claims','{}',true)")
        await conn.execute("SELECT set_config('app.user_id','',true)")
        assert await memory.read_strategy_memory(conn, tenant, user, NOW) == []
    finally:
        await transaction.rollback()
        await conn.close()


async def test_cross_track_fill_reuse_refreshes_both_themes_and_latest_empty_lesson_removes_sample():
    conn = await connection()
    transaction = conn.transaction()
    await transaction.start()
    try:
        tenant, user, _, _, instrument, track, version, fills = await seed(conn)
        await actor_role(conn, user)
        await memory.refresh_strategy_memory(conn, tenant, user, "AI_MEMORY_HBM", NOW)
        await conn.execute("RESET ROLE")
        other_track, other_version = await add_track(
            conn, tenant, user, instrument, "GEO_CRUDE_OIL"
        )
        await actor_role(conn, user)
        for fill in fills:
            await repository.add_trade_link(
                conn, other_track, other_version, fill, "private", NOW
            )
        await memory.refresh_memory_for_trade(
            conn, tenant, user, fills[0]["fillId"], "GEO_CRUDE_OIL", NOW
        )
        stored = await conn.fetch(
            "SELECT theme_code,profit_sample_count FROM research.strategy_memory_rules WHERE tenant_id=$1 AND user_id=$2 AND category='BASELINE'",
            tenant,
            user,
        )
        assert {row["theme_code"] for row in stored} == {
            "AI_MEMORY_HBM",
            "GEO_CRUDE_OIL",
        }
        assert all(row["profit_sample_count"] == 0 for row in stored)
        await conn.execute(
            """INSERT INTO research.study_notes(tenant_id,user_id,track_id,version_id,generation,kind,payload,command_key,command_hash,created_at)
            VALUES($1,$2,$3,$4,1,'REVIEW','{"verdict":"SUPPORTED","lessons":""}','review2','hash',$5)""",
            tenant,
            user,
            track["id"],
            version["id"],
            NOW,
        )
        rules = await memory.refresh_strategy_memory(
            conn, tenant, user, "AI_MEMORY_HBM", NOW
        )
        assert baseline(rules)["sample_count"] == 0
    finally:
        await transaction.rollback()
        await conn.close()
