"""Market sentiment is a governed Worker snapshot and an authenticated read model."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import ANY, AsyncMock, patch
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
async def test_market_hot_bootstrap_refreshes_when_snapshot_table_is_empty() -> None:
    snapshot = {
        "items": [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "price": 1500,
                "change_pct": 1.25,
                "volume": 100,
                "turnover": 150000,
            }
        ],
        "source_name": "Sina-Finance-Quote",
        "source_as_of": datetime(2026, 8, 29, tzinfo=ZoneInfo("Asia/Shanghai")),
        "retrieved_at": datetime(2026, 8, 29, tzinfo=ZoneInfo("Asia/Shanghai")),
        "quality_status": "ESTIMATED",
        "method_version": "market-hot/v1-candidate-pool",
    }

    conn = AsyncMock()

    @asynccontextmanager
    async def fake_system_connection(_pool):
        yield conn

    with (
        patch(
            "backend.market.service.system_connection",
            fake_system_connection,
        ),
        patch(
            "backend.market.service.repository.get_latest_market_hot",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.market.service.md.get_hot_stocks_snapshot",
            AsyncMock(return_value=snapshot),
        ) as provider,
        patch(
            "backend.market.service.repository.insert_market_hot_snapshot",
            AsyncMock(return_value="snapshot-1"),
        ) as insert,
    ):
        result = await service.ensure_market_hot_snapshot(object())

    assert result == {
        "status": "COMPLETED",
        "snapshotId": "snapshot-1",
        "qualityStatus": "ESTIMATED",
    }
    provider.assert_awaited_once_with(limit=10)
    insert.assert_awaited_once_with(ANY, data=snapshot)


@pytest.mark.asyncio
async def test_market_hot_bootstrap_skips_provider_when_snapshot_exists() -> None:
    conn = AsyncMock()

    @asynccontextmanager
    async def fake_system_connection(_pool):
        yield conn

    with (
        patch(
            "backend.market.service.system_connection",
            fake_system_connection,
        ),
        patch(
            "backend.market.service.repository.get_latest_market_hot",
            AsyncMock(return_value={"quality_status": "STALE"}),
        ),
        patch(
            "backend.market.service.md.get_hot_stocks_snapshot",
            AsyncMock(side_effect=AssertionError("existing snapshots must not refetch")),
        ) as provider,
        patch(
            "backend.market.service.repository.insert_market_hot_snapshot",
            AsyncMock(side_effect=AssertionError("existing snapshots must not rewrite")),
        ) as insert,
    ):
        result = await service.ensure_market_hot_snapshot(object())

    assert result == {
        "status": "SKIPPED",
        "reason": "SNAPSHOT_EXISTS",
        "qualityStatus": "STALE",
    }
    provider.assert_not_awaited()
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_market_hot_bootstrap_has_one_provider_winner() -> None:
    advisory_lock = asyncio.Lock()
    persisted: dict[str, object] = {"row": None}
    snapshot = {
        "items": [{"symbol": "600519", "name": "贵州茅台", "price": 1500}],
        "source_name": "Sina-Finance-Quote",
        "source_as_of": datetime(2026, 8, 29, tzinfo=ZoneInfo("Asia/Shanghai")),
        "retrieved_at": datetime(2026, 8, 29, tzinfo=ZoneInfo("Asia/Shanghai")),
        "quality_status": "ESTIMATED",
        "method_version": "market-hot/v1-candidate-pool",
    }

    class Connection:
        async def execute(self, sql: str, *_args):
            if "pg_advisory_unlock" in sql:
                advisory_lock.release()
            elif "pg_advisory_lock" in sql:
                await advisory_lock.acquire()

    @asynccontextmanager
    async def fake_system_connection(_pool):
        yield Connection()

    async def latest(_conn):
        observed = persisted["row"]
        # Give a second unlocked bootstrap time to observe the same empty
        # state. A correct advisory lock keeps it outside this section.
        await asyncio.sleep(0.01)
        return observed

    async def insert(_conn, *, data):
        persisted["row"] = {"quality_status": data["quality_status"]}
        return "snapshot-1"

    with (
        patch("backend.market.service.system_connection", fake_system_connection),
        patch(
            "backend.market.service.repository.get_latest_market_hot",
            AsyncMock(side_effect=latest),
        ) as latest_query,
        patch(
            "backend.market.service.md.get_hot_stocks_snapshot",
            AsyncMock(return_value=snapshot),
        ) as provider,
        patch(
            "backend.market.service.repository.insert_market_hot_snapshot",
            AsyncMock(side_effect=insert),
        ) as persist,
    ):
        results = await asyncio.gather(
            service.ensure_market_hot_snapshot(object()),
            service.ensure_market_hot_snapshot(object()),
        )

    assert sorted(result["status"] for result in results) == ["COMPLETED", "SKIPPED"]
    assert latest_query.await_count == 2
    assert provider.await_count == 1
    assert persist.await_count == 1


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
