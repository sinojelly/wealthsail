"""Weighted personal costs use only visible, currently held actor positions."""

from datetime import timedelta
from decimal import Decimal
import os

import pytest

from backend.investment import fund_watch, personal_funds, study_repository
from tests.unit.test_fund_watch_database import add_track, seed, as_actor, NOW
from tests.unit.test_research_acceptance_database import fixture

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
        reason="owned acceptance database required",
    ),
]


async def test_real_personal_cost_aggregation_respects_owner_clocks_and_zero_positions():
    async with fixture(now=NOW) as (conn, *_):
        tenant, actor, other, instruments, codes, _ = await seed(conn)
        # The second owned account has 20 shares at 2; the first has 10 at 1.
        await conn.execute(
            "UPDATE ledger.position_snapshots SET avg_cost=2 WHERE tenant_id=$1 AND instrument_id=$2 AND quantity=20",
            tenant,
            instruments[0],
        )
        account = await conn.fetchval(
            "SELECT account_id FROM ledger.position_snapshots WHERE tenant_id=$1 AND instrument_id=$2 AND quantity=10",
            tenant,
            instruments[0],
        )
        await conn.execute(
            """INSERT INTO ledger.position_snapshots(tenant_id,account_id,instrument_id,as_of,quantity,avg_cost,created_at)
            VALUES($1,$2,$3,$4,100,99,$5)""",
            tenant,
            account,
            instruments[0],
            NOW.date(),
            NOW + timedelta(minutes=1),
        )
        await as_actor(conn, tenant, actor)
        current = await fund_watch.list_watch_context(
            conn, tenant, actor, NOW, include_prices=False
        )
        held = {row["instrumentId"]: row for row in current["heldFunds"]}
        assert Decimal(held[str(instruments[0])]["shares"]) == 30
        assert Decimal(held[str(instruments[0])]["totalCost"]) == 50
        assert str(instruments[6]) not in held  # Newest visible position is zero.
        assert str(instruments[7]) not in held  # Collaboration is not ownership.
        earlier = await fund_watch.list_watch_context(
            conn, tenant, actor, NOW - timedelta(minutes=15), include_prices=False
        )
        earlier = {row["instrumentId"]: row for row in earlier["heldFunds"]}
        assert Decimal(earlier[str(instruments[0])]["shares"]) == 10
        assert Decimal(earlier[str(instruments[0])]["totalCost"]) == 10
        assert str(instruments[6]) in earlier
        await as_actor(conn, tenant, other)
        other_rows = await fund_watch.list_watch_context(
            conn, tenant, other, NOW, include_prices=False
        )
        assert {row["instrumentId"] for row in other_rows["heldFunds"]} == {
            str(instruments[7])
        }
        assert codes[0] not in {row["fundCode"] for row in other_rows["heldFunds"]}


async def test_personal_inventory_and_refresh_respect_latest_visible_track_expiry():
    async with fixture(now=NOW) as (conn, *_):
        tenant, actor, _, _, codes, _ = await seed(conn)
        expired, expires_today = codes[6:8]  # Neither has a current actor holding.

        async def append_expiry(track, version, expiry, created_at):
            await conn.execute(
                """INSERT INTO research.study_versions(tenant_id,user_id,track_id,version,generation,
                  snapshot,reason,command_key,command_hash,created_at)
                VALUES($1,$2,$3,$4,1,jsonb_build_object('expiresOn',$5::text),'fixture',
                  $4::integer::text,'fixture',$6)""",
                tenant, actor, track, version, expiry.isoformat(), created_at,
            )

        for code, expiry in (
            (expired, NOW.date() - timedelta(days=1)),
            (expires_today, NOW.date()),
        ):
            track = await add_track(
                conn, tenant, actor, {"fundCode": code, "fundMarket": "FUND_CN"}
            )
            await append_expiry(track, 2, expiry, NOW - timedelta(hours=1))
            await append_expiry(
                track, 3, NOW.date() + timedelta(days=1), NOW + timedelta(hours=1)
            )

        await as_actor(conn, tenant, actor)
        list_data = await study_repository.list_tracks(conn, tenant, actor, NOW)
        projected = {row["scope"].get("fundCode"): row for row in list_data["items"]}
        assert projected[expired]["status"] == "OBSERVING"  # No worker check yet.
        inventory = await personal_funds._inventory(
            conn, tenant, actor, NOW, include_research_mentions=False
        )
        assert expired not in {row["fundCode"] for row in inventory}
        assert next(row for row in inventory if row["fundCode"] == expires_today)[
            "relationships"
        ] == ["TRACKED"]
        codes_to_refresh = await personal_funds.personal_nav_refresh_codes(
            conn, tenant, actor, NOW
        )
        assert expired not in codes_to_refresh
        assert expires_today in codes_to_refresh
