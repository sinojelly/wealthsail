import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_read_uses_today_after_the_verified_publication_hour(monkeypatch):
    from backend.investment import service

    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "result": {
            "score": 85,
            "state": "STRONG",
            "market": "SSE",
            "publicationHour": 16,
        },
        "as_of": date(2026, 10, 12),
        "input_hash": "h",
        "source_uri": "fixture://history",
    }
    conn.fetchval.return_value = date(2026, 10, 13)
    monkeypatch.setattr(
        service,
        "now_shanghai",
        lambda: datetime(2026, 10, 13, 18, tzinfo=ZoneInfo("Asia/Shanghai")),
        raising=False,
    )
    result = await service.analysis(conn, "index", "hs300")
    assert date(2026, 10, 13) in conn.fetchval.await_args.args
    assert result["score"] is None


@pytest.mark.asyncio
async def test_worker_drops_unpublished_bars_before_scoring(monkeypatch):
    from backend.jobs import investment_research as worker

    conn = AsyncMock()
    conn.fetch.return_value = [{"calendar_date": date(2026, 10, 12)}]

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "system_connection", connection)
    monkeypatch.setattr(
        worker,
        "load_subject",
        AsyncMock(
            return_value={
                "code": "000300",
                "market": "SSE",
                "verified_at": True,
                "publish_hour": 16,
            }
        ),
    )
    rows = [
        {
            "date": (date(2026, 10, 13) - timedelta(days=259 - i)).isoformat(),
            "close": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
        }
        for i in range(260)
    ]
    monkeypatch.setattr(
        worker,
        "fetch_history",
        AsyncMock(return_value=(rows, "INDEX_PRICE", "fixture://history")),
    )
    await worker.refresh_subject(
        object(),
        object(),
        "index",
        "hs300",
        date(2026, 10, 13),
        now=datetime(2026, 10, 13, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    args = conn.execute.await_args.args
    assert args[6] == date(2026, 10, 12)
    assert json.loads(args[-1])[-1]["date"] == "2026-10-12"


def test_fully_analyzed_etf_is_not_reported_as_not_collected():
    from backend.investment.service import fund_dto

    item = fund_dto(
        {
            "instrument_id": "id",
            "fund_code": "510330",
            "fund_name": "ETF",
            "fund_type": "ETF",
            "trade_form": "ETF",
            "market": "SSE",
            "currency": "CNY",
            "technical_score": 75,
            "technical_state": "STRONG",
            "history_days": 260,
        }
    )
    assert item["dataState"] == "READY"
