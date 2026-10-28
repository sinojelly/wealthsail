"""Fund watch ownership/lifecycle SQL proofs in its dedicated disposable database."""

from datetime import datetime, timedelta
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from backend.investment import fund_watch

DSN = os.environ.get("FUND_WATCH_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DSN, reason="explicit disposable fund-watch database required"
    ),
]
NOW = datetime(2026, 10, 28, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def guard():
    url = urlsplit(DSN)
    assert (url.hostname, url.port, url.path, url.username) == (
        "127.0.0.1",
        56492,
        "/postgres",
        "postgres",
    ), "fund watch tests only use their dedicated disposable loopback database"


async def as_actor(conn, tenant, actor):
    await conn.execute("RESET ROLE")
    await conn.execute(
        "SELECT set_config('request.jwt.claims',$1,true)",
        json.dumps({"sub": str(actor), "tenant_id": str(tenant)}),
    )
    await conn.execute("SELECT set_config('app.user_id',$1,true)", str(actor))
    await conn.execute("SET LOCAL ROLE wealthsail_api")


async def add_track(conn, tenant, actor, scope):
    track, version = uuid4(), uuid4()
    await conn.execute(
        """INSERT INTO research.study_tracks(id,tenant_id,user_id,origin_key,title,scope,created_at,updated_at)
        VALUES($1,$2,$3,$4,'fund-watch fixture',$5::jsonb,$6,$6)""",
        track,
        tenant,
        actor,
        uuid4().hex,
        json.dumps(scope),
        NOW - timedelta(days=2),
    )
    await conn.execute(
        """INSERT INTO research.study_versions(id,tenant_id,user_id,track_id,version,generation,
          snapshot,reason,command_key,command_hash,created_at)
        VALUES($1,$2,$3,$4,1,1,$5::jsonb,'fixture','fixture','fixture',$6)""",
        version,
        tenant,
        actor,
        track,
        json.dumps({"scope": scope}),
        NOW - timedelta(days=2),
    )
    return track


async def seed(conn):
    tenant, actor, other = uuid4(), uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO identity.tenants(id,name) VALUES($1,'fund-watch fixture')", tenant
    )
    for user in (actor, other):
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'fund-watch fixture')",
            user,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
            tenant,
            user,
        )
    accounts = [uuid4() for _ in range(3)]
    for index, account in enumerate(accounts):
        await conn.execute(
            "INSERT INTO identity.accounts(id,tenant_id,account_type,name) VALUES($1,$2,'MANUAL','fund-watch fixture')",
            account,
            tenant,
        )
        user = other if index == 2 else actor
        await conn.execute(
            """INSERT INTO identity.account_members(tenant_id,account_id,user_id,relationship,permission_role,created_by,effective_from)
            VALUES($1,$2,$3,'BENEFICIAL_OWNER','OWNER',$3,$4)""",
            tenant,
            account,
            user,
            NOW.date() - timedelta(days=100),
        )
    await conn.execute(
        """INSERT INTO identity.account_members(tenant_id,account_id,user_id,relationship,permission_role,created_by,effective_from)
        VALUES($1,$2,$3,'COLLABORATOR','EDITOR',$3,$4)""",
        tenant,
        accounts[2],
        actor,
        NOW.date() - timedelta(days=100),
    )
    statuses = [
        "ACTIVE",
        "SUSPENDED",
        "DELISTED",
        "LIQUIDATED",
        "MERGED",
        "ACTIVE",
        "ACTIVE",
        "ACTIVE",
        "ACTIVE",
    ]
    instruments = [uuid4() for _ in statuses]
    codes = [str(800000 + uuid4().int % 100000) for _ in statuses]
    for index, (instrument, status) in enumerate(
        zip(instruments, statuses, strict=True)
    ):
        await conn.execute(
            """INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name,lifecycle_status)
            VALUES($1,$2,'FUND_CN',$3,$4)""",
            instrument,
            "CN_STOCK" if index == 8 else "OFF_EXCHANGE_FUND",
            f"fund-watch fixture {index}",
            status,
        )
        if (
            index != 5
        ):  # Missing ticker is still a held fund with an exact instrument identity.
            await conn.execute(
                """INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from,effective_to)
                VALUES($1,'TICKER',$2,'FUND_CN',$3,$4)""",
                instrument,
                codes[index],
                NOW.date() - timedelta(days=100),
                NOW.date() - timedelta(days=30) if index == 1 else None,
            )
        owner = accounts[2] if index == 7 else accounts[0]
        await conn.execute(
            """INSERT INTO ledger.position_snapshots(tenant_id,account_id,instrument_id,as_of,quantity,avg_cost,created_at)
            VALUES($1,$2,$3,$4,10,1,$5)""",
            tenant,
            owner,
            instrument,
            NOW.date() - timedelta(days=1),
            NOW - timedelta(minutes=20),
        )
    # A later zero snapshot must replace an earlier positive snapshot.
    await conn.execute(
        """INSERT INTO ledger.position_snapshots(tenant_id,account_id,instrument_id,as_of,quantity,avg_cost,created_at)
        VALUES($1,$2,$3,$4,0,1,$5)""",
        tenant,
        accounts[0],
        instruments[6],
        NOW.date(),
        NOW - timedelta(minutes=10),
    )
    # The same fund in two owned accounts must appear just once.
    await conn.execute(
        """INSERT INTO ledger.position_snapshots(tenant_id,account_id,instrument_id,as_of,quantity,avg_cost,created_at)
        VALUES($1,$2,$3,$4,20,1,$5)""",
        tenant,
        accounts[1],
        instruments[0],
        NOW.date(),
        NOW - timedelta(minutes=10),
    )
    tracks = [
        await add_track(
            conn, tenant, actor, {"fundCode": codes[0], "fundMarket": "FUND_CN"}
        ),
        await add_track(conn, tenant, actor, {"instrumentId": str(instruments[0])}),
        await add_track(
            conn, tenant, other, {"fundCode": codes[0], "fundMarket": "FUND_CN"}
        ),
        await add_track(conn, tenant, actor, {"instrumentId": str(instruments[5])}),
    ]
    return tenant, actor, other, instruments, codes, tracks


async def test_real_holdings_include_inactive_and_unaliased_funds_with_actor_scoped_cancel():
    guard()
    conn = await asyncpg.connect(DSN)
    transaction = conn.transaction()
    await transaction.start()
    try:
        tenant, actor, other, instruments, codes, tracks = await seed(conn)
        snapshots = await conn.fetch(
            "SELECT * FROM ledger.position_snapshots WHERE tenant_id=$1 ORDER BY id",
            tenant,
        )
        await as_actor(conn, tenant, actor)
        data = await fund_watch.list_watch_context(conn, tenant, actor, NOW)
        funds = {row["instrumentId"]: row for row in data["heldFunds"]}
        assert set(funds) == {str(instrument) for instrument in instruments[:6]}
        assert (
            funds[str(instruments[1])]["fundCode"] == codes[1]
        )  # Historic alias survives suspension.
        assert funds[str(instruments[5])]["fundCode"] is None

        await fund_watch.set_watch_status(
            conn, tenant, actor, codes[0], "FUND_CN", False, NOW
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.study_tracks WHERE id=ANY($1::uuid[]) AND NOT enabled",
                tracks[:2],
            )
            == 2
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.study_notes WHERE track_id=ANY($1::uuid[]) AND payload->>'operation'='STOPPED'",
                tracks[:2],
            )
            == 2
        )
        assert (
            f"FUND_CN:{codes[0]}"
            in (await fund_watch.list_watch_context(conn, tenant, actor, NOW))[
                "suppressedFundKeys"
            ]
        )

        await fund_watch.set_watch_status(
            conn,
            tenant,
            actor,
            None,
            "FUND_CN",
            False,
            NOW,
            instrument_id=instruments[5],
        )
        assert (
            await conn.fetchval(
                "SELECT enabled FROM research.study_tracks WHERE id=$1", tracks[3]
            )
            is False
        )
        assert (
            f"FUND_CN:instrument:{instruments[5]}"
            in (await fund_watch.list_watch_context(conn, tenant, actor, NOW))[
                "suppressedFundKeys"
            ]
        )

        # Other actors see their own holdings/preferences even in the same tenant;
        # the API role cannot read or mutate this actor's preference by spoofed args.
        await as_actor(conn, tenant, other)
        other_context = await fund_watch.list_watch_context(conn, tenant, other, NOW)
        assert {row["instrumentId"] for row in other_context["heldFunds"]} == {
            str(instruments[7])
        }
        assert other_context["suppressedFundKeys"] == []
        assert (
            await conn.fetchval(
                "SELECT enabled FROM research.study_tracks WHERE id=$1", tracks[2]
            )
            is True
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.fund_watch_preferences WHERE user_id=$1",
                actor,
            )
            == 0
        )
        assert (
            await conn.execute(
                "UPDATE research.fund_watch_preferences SET enabled=TRUE WHERE user_id=$1",
                actor,
            )
            == "UPDATE 0"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.study_tracks WHERE id=ANY($1::uuid[])",
                tracks[:2],
            )
            == 0
        )

        await as_actor(conn, tenant, actor)
        await fund_watch.resume_existing_study(conn, tenant, actor, tracks[0], NOW)
        assert (
            await conn.fetchval(
                "SELECT enabled FROM research.study_tracks WHERE id=$1", tracks[0]
            )
            is True
        )
        assert (
            f"FUND_CN:{codes[0]}"
            not in (await fund_watch.list_watch_context(conn, tenant, actor, NOW))[
                "suppressedFundKeys"
            ]
        )
        await fund_watch.resume_existing_study(conn, tenant, actor, tracks[3], NOW)
        assert (
            f"FUND_CN:instrument:{instruments[5]}"
            not in (await fund_watch.list_watch_context(conn, tenant, actor, NOW))[
                "suppressedFundKeys"
            ]
        )
        await conn.execute("RESET ROLE")
        assert snapshots == await conn.fetch(
            "SELECT * FROM ledger.position_snapshots WHERE tenant_id=$1 ORDER BY id",
            tenant,
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM research.study_versions WHERE tenant_id=$1",
                tenant,
            )
            == 4
        )
    finally:
        await transaction.rollback()
        await conn.close()
