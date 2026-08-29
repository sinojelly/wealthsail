"""Portfolio DTO contracts for source-bound fund holding range metrics."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.portfolio import repository, service


class _QueryConnection:
    def __init__(self) -> None:
        self.sql = ""

    async def fetch(self, sql: str, *_args):
        self.sql = " ".join(sql.split())
        return []


def _position(**overrides):
    row = {
        "instrument_id": "fund-1",
        "account_id": "account-1",
        "ticker": "110022",
        "canonical_name": "测试基金",
        "asset_type": "OFF_EXCHANGE_FUND",
        "quantity": Decimal("100.0000"),
        "avg_cost": Decimal("1.0000"),
        "market_price": Decimal("1.2000"),
        "market_value": Decimal("120.00"),
        "unrealized_pnl": Decimal("20.00"),
        "data_quality_status": "STALE",
        "metric_as_of": date(2026, 8, 28),
        "holding_started_on": date(2024, 3, 15),
        "holding_days": 898,
        "holding_start_method": "TRANSACTION_RECONCILED",
        "holding_start_confidence": "HIGH",
        "current_nav": Decimal("1.2000"),
        "current_nav_date": date(2026, 8, 27),
        "period_high_nav": Decimal("1.5000"),
        "period_high_date": date(2025, 10, 8),
        "period_low_nav": Decimal("1.0000"),
        "period_low_date": date(2024, 4, 23),
        "from_high_pct": Decimal("-20.00"),
        "from_low_pct": Decimal("20.00"),
        "range_position_pct": Decimal("40.00"),
        "calculation_status": "READY",
        "quality_reasons": [],
        "calculation_version": "nav-range/v1",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_latest_positions_uses_one_indexed_lateral_metric_join() -> None:
    conn = _QueryConnection()

    await repository.latest_positions(conn, "tenant-1")

    assert "LEFT JOIN LATERAL" in conn.sql
    assert "ledger.position_period_metrics" in conn.sql
    assert "metric.tenant_id = ps.tenant_id" in conn.sql
    assert "metric.account_id = ps.account_id" in conn.sql
    assert "metric.instrument_id = ps.instrument_id" in conn.sql
    assert "ORDER BY metric.as_of DESC LIMIT 1" in conn.sql


@pytest.mark.asyncio
async def test_ready_metrics_are_nested_strings_and_never_relabeled_realtime(
    monkeypatch,
) -> None:
    async def positions(_conn, _tenant):
        return [_position()]

    async def nav(_conn, _tenant):
        return [{"total_nav": Decimal("120.00"), "as_of": date(2026, 8, 28)}]

    monkeypatch.setattr(repository, "latest_positions", positions)
    monkeypatch.setattr(repository, "latest_nav_per_account", nav)

    result = (await service.get_positions(object(), "tenant-1"))[0]

    assert result["holdingPeriod"] == {
        "startedOn": "2024-03-15",
        "days": 898,
        "method": "TRANSACTION_RECONCILED",
        "confidence": "HIGH",
    }
    assert result["rangeMetrics"] == {
        "high": "1.5000",
        "highDate": "2025-10-08",
        "low": "1.0000",
        "lowDate": "2024-04-23",
        "fromHighPct": "-20.00",
        "fromLowPct": "20.00",
        "rangePositionPct": "40.00",
        "status": "READY",
        "qualityReasons": [],
        "asOf": "2026-08-27",
    }
    assert "realtime" not in result["rangeMetrics"]


@pytest.mark.asyncio
async def test_pending_and_partial_metrics_preserve_nulls_and_reasons(
    monkeypatch,
) -> None:
    async def positions(_conn, _tenant):
        return [
            _position(
                instrument_id="pending",
                metric_as_of=None,
                holding_started_on=None,
                holding_days=None,
                holding_start_method="INSUFFICIENT_DATA",
                holding_start_confidence="NONE",
                current_nav=None,
                current_nav_date=None,
                period_high_nav=None,
                period_high_date=None,
                period_low_nav=None,
                period_low_date=None,
                from_high_pct=None,
                from_low_pct=None,
                range_position_pct=None,
                calculation_status="PENDING",
                quality_reasons=["HOLDING_START_NOT_AVAILABLE"],
            ),
            _position(
                instrument_id="partial",
                current_nav=None,
                current_nav_date=None,
                range_position_pct=None,
                calculation_status="PARTIAL",
                quality_reasons=["NAV_DATE_GAP", "PARTIAL_HOLDING_HISTORY"],
            ),
        ]

    async def nav(_conn, _tenant):
        return [{"total_nav": Decimal("240.00"), "as_of": date(2026, 8, 28)}]

    monkeypatch.setattr(repository, "latest_positions", positions)
    monkeypatch.setattr(repository, "latest_nav_per_account", nav)

    pending, partial = await service.get_positions(object(), "tenant-1")

    assert pending["rangeMetrics"]["high"] is None
    assert pending["rangeMetrics"]["rangePositionPct"] is None
    assert pending["rangeMetrics"]["status"] == "PENDING"
    assert pending["rangeMetrics"]["qualityReasons"] == ["HOLDING_START_NOT_AVAILABLE"]
    assert partial["rangeMetrics"]["rangePositionPct"] is None
    assert partial["rangeMetrics"]["status"] == "PARTIAL"
    assert partial["rangeMetrics"]["qualityReasons"] == [
        "NAV_DATE_GAP",
        "PARTIAL_HOLDING_HISTORY",
    ]


@pytest.mark.asyncio
async def test_non_fund_position_returns_null_metric_objects(monkeypatch) -> None:
    async def positions(_conn, _tenant):
        return [_position(asset_type="CN_STOCK", ticker="600519")]

    async def nav(_conn, _tenant):
        return [{"total_nav": Decimal("120.00"), "as_of": date(2026, 8, 28)}]

    monkeypatch.setattr(repository, "latest_positions", positions)
    monkeypatch.setattr(repository, "latest_nav_per_account", nav)

    result = (await service.get_positions(object(), "tenant-1"))[0]

    assert result["holdingPeriod"] is None
    assert result["rangeMetrics"] is None
