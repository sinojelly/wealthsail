"""Public representative identities and grants, using an isolated PostgreSQL."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
import pytest

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated DB required"),
]


async def test_industry_pool_has_verified_products_not_review_code_mismatches():
    from backend.investment.discovery import read_discovery_assets

    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch("SELECT * FROM reference.discovery_fund_universe")
        assert len(rows) >= 16
        by_code = {r["fund_code"]: r for r in rows}
        assert "020988" not in by_code and "001556" not in by_code
        assert "5G" in by_code["008086"]["fund_name"]
        assert "主要消费" in by_code["009179"]["fund_name"]
        assert by_code["270042"]["overseas_calendar_required"]
        assert by_code["162411"]["overseas_calendar_required"]
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE wealthsail_worker")
            assets = await read_discovery_assets(
                conn,
                datetime(2026, 10, 19, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
                [],
            )
            selected = {r["fundCode"] for r in assets[:32]}
            assert selected >= {
                "012733",
                "011035",
                "004069",
                "018344",
                "012737",
                "009179",
                "000216",
            }
        for role in ("wealthsail_api", "wealthsail_worker", "authenticated"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert await conn.fetchval(
                    "SELECT has_table_privilege($1,'reference.discovery_fund_universe',$2)",
                    role,
                    privilege,
                ) == (role != "authenticated" and privilege == "SELECT")
    finally:
        await conn.close()


async def test_future_seed_does_not_gain_priority_and_qdii_survives_cached_calendar():
    from backend.investment.discovery import read_discovery_assets
    from backend.investment.theme_graph import _calendar_proof

    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = datetime(2026, 10, 19, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        await conn.execute(
            "UPDATE reference.discovery_fund_universe SET verified_at='2099-01-01' WHERE fund_code='012733'"
        )
        assets = await read_discovery_assets(conn, now, [])
        assert not any(
            r.get("discoverySector") for r in assets if r["fundCode"] == "012733"
        )
        await conn.execute(
            "INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day) VALUES('SSE','2026-10-16',true) ON CONFLICT(market,calendar_date) DO NOTHING"
        )
        proof = await _calendar_proof(
            conn, {"fundCode": "270042", "points": [{"date": "2026-10-16"}]}, now
        )
        assert proof["overseasCalendarRequired"] is True
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.parametrize(
    "grant,role",
    [
        ("UPDATE", "wealthsail_worker"),
        ("UPDATE(enabled)", "wealthsail_worker"),
        ("INSERT(fund_code)", "wealthsail_worker"),
        ("SELECT(fund_code)", "authenticated"),
        ("SELECT(fund_code)", "PUBLIC"),
    ],
)
async def test_readback_rejects_widened_runtime_authority(grant, role):
    from scripts.migrate import _readback

    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(
            f"GRANT {grant} ON reference.discovery_fund_universe TO {role}"
        )
        with pytest.raises(RuntimeError, match="discovery industry universe"):
            await _readback(conn)
    finally:
        await tx.rollback()
        await conn.close()
