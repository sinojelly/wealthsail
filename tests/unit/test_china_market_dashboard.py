from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

from asyncpg import InsufficientPrivilegeError
import httpx
import pandas as pd
import pytest

from backend.integrations.china_market_trends import (
    TURNOVER_CLOSE,
    covered_turnover,
    fetch_index_rows,
    TURNOVER_METHOD,
)
from backend.macro.china_market_dashboard import read_turnover


NOW = datetime.fromisoformat("2026-11-13T18:00:00+08:00")


@pytest.mark.asyncio
async def test_permission_failure_rolls_back_only_the_optional_read():
    from backend.macro.china_market_dashboard import add_live_dashboard_series

    conn = AsyncMock()
    rollbacks = []

    @asynccontextmanager
    async def transaction():
        try:
            yield
        except InsufficientPrivilegeError:
            rollbacks.append(True)
            raise

    conn.transaction = transaction
    conn.fetch.side_effect = [InsufficientPrivilegeError(), []]
    rates = {"code": "US_T10_REAL", "value": 2}
    rows = await add_live_dashboard_series(conn, [rates], NOW)
    assert rows[0] == rates
    assert rows[-1]["code"] == "CN_A_TURNOVER"
    assert rollbacks == [True]


def test_optional_equity_chart_failures_do_not_downgrade_existing_macro_sources():
    from backend.jobs.opportunity_market import _status

    assert (
        _status(
            {
                "UST_REAL": {"status": "SUCCESS"},
                "SSE_COMPOSITE_HISTORY": {"status": "FAILED", "rejected": 1},
            },
            rejected=1,
            inserted=5,
        )
        == "COMPLETED"
    )


@pytest.mark.asyncio
async def test_verified_identity_supplies_unadjusted_index_close_history():
    def endpoint(request):
        assert request.url.params["secid"] == "1.000001"
        assert request.url.params["fqt"] == "0"
        return httpx.Response(
            200,
            json={
                "data": {
                    "code": "000001",
                    "market": 1,
                    "klines": ["2026-11-12,4000,4100", "2026-11-13,4100,4150"],
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        rows = await fetch_index_rows(client, "CN_SH_COMP", NOW)
    assert [row["value"] for row in rows] == ["4100", "4150"]
    assert rows[-1]["referenceDate"] == "2026-11-13"
    assert rows[-1]["publishedAt"] is None


def test_turnover_requires_post_close_distinct_symbols_and_three_exchange_coverage():
    symbols = [
        f"{prefix}{i:03d}"
        for prefix, count in (
            ("600", 1000),
            ("601", 1000),
            ("000", 1000),
            ("300", 1000),
            ("920", 500),
        )
        for i in range(count)
    ]
    frame = pd.DataFrame({"代码": symbols, "成交额": [100_000_000] * len(symbols)})
    scheduled_close = NOW.replace(hour=15, minute=30)
    assert covered_turnover(frame, scheduled_close) == Decimal(4500)
    assert covered_turnover(frame, scheduled_close.replace(minute=29)) is None
    frame["成交额"] = frame["成交额"].astype(float)
    frame.loc[4499, "成交额"] = float("nan")
    assert covered_turnover(frame, scheduled_close) == Decimal(4499)
    frame.loc[4494:4499, "成交额"] = float("nan")
    assert covered_turnover(frame, scheduled_close) is None
    frame["成交额"] = 100_000_000
    frame.loc[4499, "代码"] = frame.loc[4498, "代码"]
    assert covered_turnover(frame, NOW) is None


@pytest.mark.asyncio
async def test_dashboard_turnover_projects_proven_close_snapshots_as_owners_units():
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "trade_date": NOW.date(),
            "source_as_of": NOW,
            "retrieved_at": NOW,
            "turnover": Decimal("18954.21"),
        }
    ]
    item = await read_turnover(conn, NOW)
    assert item["value"] == Decimal("18954.21")
    assert item["unit"] == "亿元"
    query, method, _, _, close = conn.fetch.await_args.args
    assert method == TURNOVER_METHOD
    assert close == TURNOVER_CLOSE
    assert "day.is_trading_day" in query


def test_official_csi300_snapshot_requires_the_known_source_and_complete_dates():
    from backend.macro.china_market_dashboard import csi300_fallback

    snapshot = {
        "source": "https://www.csindex.com.cn/zh-CN/indices/index-detail/000300",
        "trendQuality": "READY",
        "retrievedAt": NOW.isoformat(),
        "points": [
            {"date": "2026-11-12", "close": "4123"},
            {"date": "2026-11-13", "close": "4145"},
        ],
    }
    assert csi300_fallback(snapshot, NOW)["value"] == Decimal("4145")
    assert (
        csi300_fallback({**snapshot, "source": "https://example.com/000300"}, NOW)
        is None
    )
