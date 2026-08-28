"""Market sentiment is a governed Worker snapshot and an authenticated read model."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.integrations import market_data
from backend.main import app
from backend.common.tenancy import TenantContext
from backend.market import api as market_api
from backend.market import service


def test_sentiment_endpoint_rejects_anonymous_provider_trigger() -> None:
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/market/sentiment"
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_sentiment_read_model_never_calls_a_provider() -> None:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    row = {
        "trade_date": now.date(),
        "total_up": 3000,
        "total_down": 1900,
        "total_flat": 100,
        "limit_up": 60,
        "limit_down": 5,
        "turnover": Decimal("12345.67"),
        "north_flow": None,
        "sentiment_score": Decimal("62.50"),
        "source_name": "AkShare-OpenSource",
        "source_as_of": now,
        "retrieved_at": now,
        "quality_status": "FRESH",
        "coverage_pct": Decimal("99.00"),
        "method_version": "sentiment/v2",
        "updated_at": now,
    }
    conn = object()
    with (
        patch(
            "backend.market.service.repository.get_latest_market_sentiment",
            AsyncMock(return_value=[row]),
        ),
        patch(
            "backend.market.service.md.fetch_market_sentiment",
            AsyncMock(side_effect=AssertionError("API reads must not call providers")),
        ),
    ):
        result = await service.get_market_sentiment(conn)

    assert result["source"] == "AkShare-OpenSource"
    assert result["qualityStatus"] == "FRESH"
    assert result["coveragePct"] == "99.00"
    assert result["methodVersion"] == "sentiment/v2"


@pytest.mark.asyncio
async def test_previous_trade_date_sentiment_cannot_remain_fresh() -> None:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    previous_day = now - timedelta(days=1)
    row = {
        "trade_date": previous_day.date(),
        "total_up": 1,
        "total_down": 1,
        "total_flat": 0,
        "limit_up": 0,
        "limit_down": 0,
        "turnover": Decimal("1"),
        "north_flow": None,
        "sentiment_score": Decimal("50"),
        "source_name": "AkShare-OpenSource",
        "source_as_of": previous_day,
        "retrieved_at": previous_day,
        "quality_status": "FRESH",
        "coverage_pct": Decimal("99"),
        "method_version": "sentiment/v2",
        "updated_at": previous_day,
    }
    with patch(
        "backend.market.service.repository.get_latest_market_sentiment",
        AsyncMock(return_value=[row]),
    ):
        result = await service.get_market_sentiment(object())

    assert result["qualityStatus"] == "STALE"


@pytest.mark.asyncio
async def test_market_hot_read_model_never_calls_a_provider() -> None:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    row = {
        "items": [{"symbol": "600519", "name": "贵州茅台", "price": 1500}],
        "source_name": "AkShare-OpenSource",
        "source_as_of": now,
        "retrieved_at": now,
        "quality_status": "FRESH",
        "method_version": "market-hot/v1",
        "updated_at": now,
    }
    with (
        patch(
            "backend.market.service.repository.get_latest_market_hot",
            AsyncMock(return_value=row),
        ),
        patch(
            "backend.market.service.md.get_hot_stocks_snapshot",
            AsyncMock(side_effect=AssertionError("API reads must not call providers")),
        ),
    ):
        result = await service.get_market_hot(object())

    assert result["items"][0]["symbol"] == "600519"
    assert result["source"] == "AkShare-OpenSource"
    assert result["qualityStatus"] == "FRESH"


@pytest.mark.asyncio
async def test_market_hot_without_snapshot_is_explicitly_unavailable() -> None:
    with patch(
        "backend.market.service.repository.get_latest_market_hot",
        AsyncMock(return_value=None),
    ):
        result = await service.get_market_hot(object())

    assert result["items"] == []
    assert result["qualityStatus"] == "UNAVAILABLE"
    assert result["source"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["sentiment", "hot"])
async def test_market_envelope_meta_matches_read_model_quality(
    endpoint: str, monkeypatch
) -> None:
    @asynccontextmanager
    async def fake_transaction(*_args):
        yield object()

    payload = {"qualityStatus": "UNAVAILABLE", "items": []}
    monkeypatch.setattr(market_api, "tenant_transaction", fake_transaction)
    monkeypatch.setattr(
        service,
        f"get_market_{endpoint}",
        AsyncMock(return_value=payload),
    )
    actor = TenantContext(user_id="user-1", tenant_id="tenant-1", tenant_role="OWNER")

    handler = getattr(market_api, f"market_{endpoint}")
    response = await handler(actor, object(), "req_market_quality")

    assert response["data"]["qualityStatus"] == "UNAVAILABLE"
    assert response["meta"]["qualityStatus"] == "UNAVAILABLE"


@pytest.mark.parametrize(
    ("symbol", "name", "expected"),
    [
        ("600519", "贵州茅台", Decimal("10")),
        ("300750", "宁德时代", Decimal("20")),
        ("688001", "华兴源创", Decimal("20")),
        ("920001", "北交所样例", Decimal("30")),
        ("600000", "ST 浦发", Decimal("5")),
    ],
)
def test_price_limit_threshold_respects_board_and_st_status(
    symbol: str, name: str, expected: Decimal
) -> None:
    assert market_data.price_limit_pct(symbol, name) == expected
