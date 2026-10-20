"""Industry coverage must survive a large catalog within the same NAV budget."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from backend.investment import discovery
from test_phase2_research import NOW


def catalog_row(code, sector=None):
    return {
        "fund_code": code,
        "fund_name": f"公开测试基金{code}",
        "market": "FUND_CN",
        "instrument_id": None,
        "mapping_count": 1,
        "discovery_sector": sector,
        "seed_priority": 1,
        "identity_source_uri": "https://example.org/product",
        "identity_verified_at": NOW,
        "overseas_calendar_required": sector == "US_TECH",
    }


@pytest.mark.asyncio
async def test_industry_representatives_and_catalog_expansion_share_32_slots():
    conn = AsyncMock()
    conn.fetch.return_value = [catalog_row(f"{i:06}") for i in range(40)] + [
        catalog_row(f"{100 + i:06}", sector)
        for i, sector in enumerate(
            ["BROAD", "AI", "RESOURCE", "BROKER", "ROBOT", "DRUG", "GOLD", "US_TECH"]
        )
    ]
    assets = await discovery.read_discovery_assets(conn, NOW, [])
    selected = assets[:32]
    assert {row.get("discoverySector") for row in selected} >= {
        "AI",
        "RESOURCE",
        "BROKER",
        "ROBOT",
        "DRUG",
        "GOLD",
        "US_TECH",
    }
    assert sum("discoverySector" not in row for row in selected) >= 8
    assert next(row for row in selected if row["fundCode"] == "000107")[
        "overseasCalendarRequired"
    ]
    assert len({row["fundCode"] for row in selected}) == 32
    assert assets == await discovery.read_discovery_assets(conn, NOW, [])


@pytest.mark.asyncio
async def test_duplicate_sector_does_not_starve_other_industries():
    conn = AsyncMock()
    conn.fetch.return_value = [catalog_row(f"{i:06}", "BROAD") for i in range(40)] + [
        catalog_row("999998", "DRUG"),
        catalog_row("999999", "AI"),
    ]
    selected = (await discovery.read_discovery_assets(conn, NOW, []))[:32]
    assert {row["fundCode"] for row in selected} >= {"999998", "999999"}
    assert len(selected) == 32


def test_sixth_previous_direction_retains_catalysts_and_risks():
    old = {
        "report": {
            "valueDirections": [
                {
                    "key": f"industry-{i}",
                    "title": "公开方向",
                    "plans": [],
                    "catalysts": [{"summary": "提议体现积极信号", "sourceIds": ["s1"]}],
                    "risks": [{"summary": "关注执行进度", "sourceIds": ["s1"]}],
                }
                for i in range(6)
            ]
        }
    }
    before = deepcopy(old)
    prior = discovery.discovery_packet([], [], old, NOW)["previousValueDirections"]
    assert len(prior) == 6
    assert (
        prior[-1]["catalysts"] == before["report"]["valueDirections"][-1]["catalysts"]
    )
    assert prior[-1]["risks"] == before["report"]["valueDirections"][-1]["risks"]
    assert old == before


@pytest.mark.asyncio
async def test_packet_discloses_real_selected_history_and_price_coverage():
    conn = AsyncMock()
    conn.fetch.return_value = [
        catalog_row("012733", "AI"),
        catalog_row("011035", "RESOURCE"),
    ]
    result = await discovery.build_discovery_packet(conn, {}, {}, [], {}, NOW)
    assert result["discoveryCoverage"] == {
        "universeCount": 2,
        "coreFundCodes": ["012733", "011035"],
        "coreMissingFundCodes": [
            code
            for code in discovery.CORE_FUND_CODES
            if code not in {"012733", "011035"}
        ],
        "dynamicFundCodes": [],
        "selectedFundCodes": ["012733", "011035"],
        "historyFundCodes": [],
        "pricedFundCodes": [],
        "sectorCodes": ["AI", "RESOURCE"],
        "selectionLimit": 46,
    }


@pytest.mark.asyncio
async def test_candidate_sql_requires_visible_seed_and_preserves_catalog_identity():
    conn = AsyncMock()
    conn.fetch.return_value = []
    await discovery.read_discovery_assets(conn, NOW, [])
    sql = conn.fetch.await_args.args[0]
    assert "reference.discovery_fund_universe" in sql
    assert "verified_at<=$1" in sql
    assert "enabled" in sql
    assert "mapping_count" in sql
