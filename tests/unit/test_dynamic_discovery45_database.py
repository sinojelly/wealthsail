"""Market-wide lookup and append-only NAV cohorts in the exact review45 DB."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date, timedelta
import json
import os
from urllib.parse import urlsplit
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from backend.investment import discovery, theme_graph
from backend.investment.dynamic_discovery import read_dynamic_assets
from test_dynamic_discovery45 import asset, complete_history, event
from test_theme_graph import NOW

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated review45 DB required"),
]


async def connect():
    target = urlsplit(DSN)
    assert target.hostname in {"127.0.0.1", "localhost"}
    assert target.port in {55434, 55436}
    assert target.path in {"/wealthsail_review45_validation_v1", "/wealthsail_review48_validation_v1"}
    return await asyncpg.connect(DSN)


async def test_dynamic_market_lookup_reaches_beyond_legacy_catalog_cap_under_worker_role():
    conn = await connect()
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.executemany(
            "INSERT INTO reference.fund_master(fund_code,fund_name,fund_type,updated_at) VALUES($1,$2,'股票型-指数',$3)",
            [(f"979{i:03}", f"隔离测试普通指数{i}A", NOW) for i in range(110)]
            + [
                ("979991", "隔离卫星ETF联接A", NOW),
                ("979992", "隔离卫星指数C", NOW),
                ("979993", "隔离卫星指数A", NOW),
                ("979994", "隔离卫星清盘基金A", NOW),
                ("979995", "隔离卫星已终止指数A", NOW),
                ("979981", "隔离卫星ETF联接A", NOW),
                ("979980", "隔离卫星ETF联接C（美元现汇）", NOW),
                ("979979", "隔离卫星指数C（人民币份额）", NOW),
                ("979978", "隔离卫星主题基金后收费", NOW),
                ("979977", "隔离卫星ETF联接A（美元现汇）", NOW),
                ("979976", "隔离卫星主题指数港币A", NOW),
            ],
        )
        inactive = uuid4()
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name,lifecycle_status) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','隔离卫星已终止指数A','LIQUIDATED')",
            inactive,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER','979995','FUND_CN',$2)",
            inactive,
            NOW.date() - timedelta(days=1),
        )
        for offset in range(17):
            ambiguous = uuid4()
            await conn.execute(
                "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','隔离卫星ETF联接A')",
                ambiguous,
            )
            await conn.execute(
                "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER','979981','FUND_CN',$2)",
                ambiguous,
                NOW.date() - timedelta(days=offset + 1),
            )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        legacy = await discovery.read_discovery_assets(conn, NOW, [])
        assert "979991" not in {row["fundCode"] for row in legacy}
        selected = await read_dynamic_assets(
            conn, NOW, [event("卫星互联网新增订单")], []
        )
        own = [row for row in selected if row["fundCode"].startswith("979")]
        assert [row["fundCode"] for row in own] == ["979991", "979993"]
        assert all(row["subjectKind"] == "nav" for row in own)
        assert all(row["dynamicThemes"] == ["SATELLITE"] for row in own)
        assert len(selected) <= 10
        assert not await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','market.fund_research_nav_histories','DELETE')"
        )
    finally:
        await tx.rollback()
        await conn.close()


async def test_conflict_repair_appends_replacement_cohort_without_deleting_original(
    monkeypatch,
):
    conn = await connect()
    tx = conn.transaction()
    await tx.start()
    try:
        old = complete_history(count=119)
        old["points"].append({**old["points"][-1], "close": "99"})
        old["asOf"] = NOW.date().isoformat()
        original = deepcopy(old)
        old_id = await conn.fetchval(
            "INSERT INTO market.fund_research_nav_histories(fund_code,retrieved_at,available_at,payload) VALUES('008281',$1,$1,$2::jsonb) RETURNING id",
            NOW - timedelta(minutes=1),
            json.dumps(old),
        )
        fresh = complete_history(count=220)
        fresh.update(retrievedAt=NOW.isoformat(), availableAt=NOW.isoformat())
        await conn.execute("SET LOCAL ROLE wealthsail_worker")

        @asynccontextmanager
        async def acquire(pool):
            yield conn

        monkeypatch.setattr(discovery, "system_connection", acquire)
        monkeypatch.setattr(
            discovery,
            "select_research_assets",
            AsyncMock(return_value=[asset("008281")]),
        )
        monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            theme_graph,
            "collect_theme_nav_histories",
            AsyncMock(
                return_value={
                    "fundHistories": [fresh],
                    "themeNavSourceResults": {
                        "008281": {"status": "SUCCESS", "records": 220}
                    },
                }
            ),
        )
        await discovery.collect_discovery_nav_histories(object(), NOW)
        assert (
            json.loads(
                await conn.fetchval(
                    "SELECT payload::text FROM market.fund_research_nav_histories WHERE id=$1",
                    old_id,
                )
            )
            == original
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM market.fund_research_nav_histories WHERE fund_code='008281' AND available_at<=$1",
                NOW,
            )
            == 2
        )
        cached = await discovery._cached_histories(conn, ["008281"], NOW)
        assert cached["008281"]["points"] == fresh["points"]
    finally:
        await tx.rollback()
        await conn.close()


async def test_cached_cohorts_share_one_full_calendar_read_and_keep_qdii_distinct():
    conn = await connect()
    tx = conn.transaction()
    await tx.start()
    try:
        domestic = complete_history(code="979997", count=120)
        overseas = complete_history(code="162411", count=120)
        await conn.executemany(
            "INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day) VALUES('SSE',$1,$2) ON CONFLICT(market,calendar_date) DO UPDATE SET is_trading_day=excluded.is_trading_day",
            [
                (date.fromisoformat(row["date"]), row["isTradingDay"])
                for row in domestic["calendarProof"]["days"]
            ],
        )
        for history in (domestic, overseas):
            await conn.execute(
                "INSERT INTO market.fund_research_nav_histories(fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$2,$3::jsonb)",
                history["fundCode"],
                NOW,
                json.dumps(history),
            )
        await conn.execute("SET LOCAL ROLE wealthsail_api")

        class CountReads:
            def __init__(self):
                self.calls = []

            async def fetch(self, sql, *args):
                self.calls.append(sql)
                return await conn.fetch(sql, *args)

        reads = CountReads()
        histories = await discovery._cached_histories(reads, ["979997", "162411"], NOW)
        assert len(reads.calls) == 2
        assert (
            theme_graph.nav_plan(histories["979997"], now=NOW)["historyQuality"]
            == "READY"
        )
        assert histories["162411"]["calendarProof"]["overseasCalendarRequired"] is True
        assert (
            theme_graph.nav_plan(histories["162411"], now=NOW)["historyQuality"]
            == "UNVERIFIED"
        )
        assert histories["979997"]["retrievedAt"] == domestic["retrievedAt"]
        assert len(histories["979997"]["points"]) == 120
    finally:
        await tx.rollback()
        await conn.close()
