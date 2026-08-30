"""Portfolio projection/read-model invariants from Review 09."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.portfolio import api, repository, service


class _QueryConnection:
    def __init__(self) -> None:
        self.sql = ""

    async def fetch(self, sql: str, *_args):
        self.sql = " ".join(sql.split())
        return []


@pytest.mark.asyncio
async def test_latest_position_identity_includes_account_and_instrument() -> None:
    """The same instrument held in two accounts must produce two rows."""
    conn = _QueryConnection()

    await repository.latest_positions(conn, "tenant-1")

    assert "DISTINCT ON (ps.account_id, ps.instrument_id)" in conn.sql
    assert "ORDER BY ps.account_id, ps.instrument_id, ps.as_of DESC" in conn.sql


@pytest.mark.asyncio
@pytest.mark.parametrize("since", [None, date(2026, 8, 1)])
async def test_nav_history_does_not_sum_partial_account_valuations(since) -> None:
    """One unpriced account must make the portfolio-day total unavailable."""
    conn = _QueryConnection()

    await repository.nav_history(conn, "tenant-1", since=since)

    sql = conn.sql.upper()
    assert "COUNT(*) = COUNT(TOTAL_NAV)" in sql
    assert "COUNT(*) = COUNT(DAILY_PNL)" in sql


@pytest.mark.asyncio
async def test_missing_quote_is_not_rendered_as_zero_and_sort_is_numeric(monkeypatch) -> None:
    rows = [
        {
            "instrument_id": "instrument-unpriced",
            "account_id": "account-1",
            "ticker": "NOQUOTE",
            "canonical_name": "未估值基金",
            "asset_type": "OFF_EXCHANGE_FUND",
            "quantity": Decimal("10"),
            "avg_cost": Decimal("1"),
            "market_price": None,
            "market_value": None,
            "unrealized_pnl": None,
            "data_quality_status": "UNAVAILABLE",
        },
        {
            "instrument_id": "instrument-9",
            "account_id": "account-1",
            "ticker": "SMALL",
            "canonical_name": "小市值",
            "asset_type": "CN_STOCK",
            "quantity": Decimal("1"),
            "avg_cost": Decimal("1"),
            "market_price": Decimal("9"),
            "market_value": Decimal("9"),
            "unrealized_pnl": Decimal("8"),
            "data_quality_status": "FRESH",
        },
        {
            "instrument_id": "instrument-100",
            "account_id": "account-2",
            "ticker": "LARGE",
            "canonical_name": "大市值",
            "asset_type": "CN_STOCK",
            "quantity": Decimal("1"),
            "avg_cost": Decimal("1"),
            "market_price": Decimal("100"),
            "market_value": Decimal("100"),
            "unrealized_pnl": Decimal("99"),
            "data_quality_status": "FRESH",
        },
    ]

    async def latest_positions(_conn, _tenant):
        return rows

    async def latest_nav(_conn, _tenant):
        return [{"total_nav": Decimal("109"), "as_of": date(2026, 8, 26)}]

    monkeypatch.setattr(repository, "latest_positions", latest_positions)
    monkeypatch.setattr(repository, "latest_nav_per_account", latest_nav)

    result = await service.get_positions(object(), "tenant-1")

    assert [row["symbol"] for row in result] == ["LARGE", "SMALL", "NOQUOTE"]
    unpriced = result[-1]
    assert unpriced["marketValue"] is None
    assert unpriced["pnl"] is None
    assert unpriced["weight"] is None
    assert unpriced["quality"] == "MISSING"


@pytest.mark.asyncio
async def test_summary_as_of_is_oldest_account_snapshot_not_request_clock(monkeypatch) -> None:
    async def latest_nav(_conn, _tenant):
        return [
            {
                "total_nav": Decimal("50"),
                "cash_balance": Decimal("50"),
                "positions_market_value": Decimal("0"),
                "daily_pnl": Decimal("0"),
                "cumulative_pnl": Decimal("0"),
                "data_quality_status": "FRESH",
                "as_of": date(2026, 8, 25),
            },
            {
                "total_nav": Decimal("50"),
                "cash_balance": Decimal("50"),
                "positions_market_value": Decimal("0"),
                "daily_pnl": Decimal("0"),
                "cumulative_pnl": Decimal("0"),
                "data_quality_status": "FRESH",
                "as_of": date(2026, 8, 26),
            },
        ]

    async def no_positions(_conn, _tenant):
        return []

    monkeypatch.setattr(repository, "latest_nav_per_account", latest_nav)
    monkeypatch.setattr(repository, "latest_positions", no_positions)

    result = await service.get_summary(
        object(), "tenant-1", as_of=date(2030, 1, 1)  # type: ignore[arg-type]
    )

    assert result["asOf"] == date(2026, 8, 25)


@pytest.mark.asyncio
async def test_summary_exposes_known_value_and_coverage_without_fake_total(monkeypatch) -> None:
    async def latest_nav(_conn, _tenant):
        return [
            {
                "total_nav": None,
                "known_value": Decimal("150"),
                "valuation_coverage_pct": Decimal("50"),
                "unpriced_position_count": 1,
                "cash_balance": Decimal("100"),
                "positions_market_value": None,
                "daily_pnl": None,
                "cumulative_pnl": None,
                "data_quality_status": "UNAVAILABLE",
                "as_of": date(2026, 8, 26),
            }
        ]

    async def no_positions(_conn, _tenant):
        return []

    monkeypatch.setattr(repository, "latest_nav_per_account", latest_nav)
    monkeypatch.setattr(repository, "latest_positions", no_positions)

    result = await service.get_summary(object(), "tenant-1", as_of=date(2026, 8, 26))  # type: ignore[arg-type]

    assert result["totalAsset"] is None
    assert result["knownValue"] == "150.00"
    assert result["valuationCoveragePct"] == "50.00"
    assert result["unpricedPositionCount"] == 1
    assert result["qualityStatus"] == "MISSING"


@pytest.mark.asyncio
async def test_empty_portfolio_summary_is_a_missing_read_model_not_an_error(
    monkeypatch,
) -> None:
    """A tenant that has not imported assets is a valid empty state, not an outage."""

    async def no_nav(_conn, _tenant):
        return []

    monkeypatch.setattr(repository, "latest_nav_per_account", no_nav)

    result = await service.get_summary(
        object(), "tenant-empty", as_of=date(2026, 8, 29)  # type: ignore[arg-type]
    )

    assert result == {
        "totalAsset": None,
        "knownValue": "0.00",
        "valuationCoveragePct": "0.00",
        "unpricedPositionCount": 0,
        "currency": "CNY",
        "cashBalance": "0.00",
        "positionsMarketValue": None,
        "dailyPnl": None,
        "dailyPnlRate": None,
        "cumulativePnl": None,
        "cumulativePnlRate": None,
        "allocation": [],
        "qualityStatus": "MISSING",
        "asOf": None,
        "positionCount": 0,
    }


@pytest.mark.asyncio
async def test_empty_nav_history_returns_an_empty_series(monkeypatch) -> None:
    """No NAV observations must render an empty chart instead of HTTP 503."""

    async def no_history(_conn, _tenant, *, limit):
        assert limit == 30
        return []

    monkeypatch.setattr(repository, "nav_history", no_history)

    result = await service.get_nav_trend(object(), "tenant-empty", days=30)

    assert result == {
        "points": [],
        "benchmark": None,
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
    }


@pytest.mark.asyncio
async def test_empty_nav_api_marks_success_envelope_as_missing(monkeypatch) -> None:
    """The success envelope must not describe an empty time series as fresh."""

    @asynccontextmanager
    async def fake_tenant_transaction(_pool, _user_id, _tenant_id):
        yield object()

    async def empty_nav(_conn, _tenant_id, *, days, scope):
        assert days == 30
        assert scope.kind == "MINE"
        return {
            "points": [],
            "benchmark": None,
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
        }

    monkeypatch.setattr(api, "tenant_transaction", fake_tenant_transaction)
    monkeypatch.setattr(api.service, "get_nav_trend", empty_nav)

    result = await api.portfolio_nav_trend(
        days=30,
        pool=object(),  # type: ignore[arg-type]
        ctx=SimpleNamespace(user_id="user-1", tenant_id="tenant-empty"),
        request_id="req_empty_nav",
    )

    assert result["data"]["points"] == []
    assert result["meta"]["qualityStatus"] == "MISSING"


def test_empty_summary_api_returns_the_complete_missing_success_contract(
    monkeypatch,
) -> None:
    """The HTTP endpoint must expose the empty DTO and its missing quality."""

    @asynccontextmanager
    async def fake_tenant_transaction(_pool, _user_id, _tenant_id):
        yield object()

    async def empty_summary(_conn, _tenant_id, _as_of, *, scope):
        assert scope.kind == "MINE"
        return {
            "totalAsset": None,
            "knownValue": "0.00",
            "valuationCoveragePct": "0.00",
            "unpricedPositionCount": 0,
            "currency": "CNY",
            "cashBalance": "0.00",
            "positionsMarketValue": None,
            "dailyPnl": None,
            "dailyPnlRate": None,
            "cumulativePnl": None,
            "cumulativePnlRate": None,
            "allocation": [],
            "qualityStatus": "MISSING",
            "asOf": None,
            "positionCount": 0,
        }

    monkeypatch.setattr(api, "tenant_transaction", fake_tenant_transaction)
    monkeypatch.setattr(api.service, "get_summary", empty_summary)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_pool] = lambda: object()
    app.dependency_overrides[api.get_tenant_context] = lambda: SimpleNamespace(
        user_id="user-1", tenant_id="tenant-empty"
    )
    app.dependency_overrides[api.get_request_id] = lambda: "req_empty_summary"

    response = TestClient(app).get("/portfolio/summary")
    result = response.json()

    assert response.status_code == 200
    assert result["data"] == {
        "totalAsset": None,
        "knownValue": "0.00",
        "valuationCoveragePct": "0.00",
        "unpricedPositionCount": 0,
        "currency": "CNY",
        "cashBalance": "0.00",
        "positionsMarketValue": None,
        "dailyPnl": None,
        "dailyPnlRate": None,
        "cumulativePnl": None,
        "cumulativePnlRate": None,
        "allocation": [],
        "qualityStatus": "MISSING",
        "asOf": None,
        "positionCount": 0,
    }
    assert result["meta"]["qualityStatus"] == "MISSING"
