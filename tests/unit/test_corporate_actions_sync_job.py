"""Tests for the global, controlled corporate-action Worker task."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.corporate_actions import (
    CorporateAction,
    ProviderResult,
)
from backend.jobs.corporate_actions_sync import sync_corporate_actions


class _Connection:
    def __init__(self) -> None:
        self.persisted: list[tuple[object, ...]] = []

    async def fetch(self, query: str, *args: object):
        assert "ledger.position_snapshots" in query
        assert args == (["CN_STOCK", "EXCHANGE_FUND"],)
        return [
            {
                "instrument_id": "00000000-0000-0000-0000-000000000001",
                "symbol": "600519",
                "asset_type": "CN_STOCK",
            },
            {
                "instrument_id": "00000000-0000-0000-0000-000000000002",
                "symbol": "510300",
                "asset_type": "EXCHANGE_FUND",
            },
        ]

    async def fetchval(self, query: str, *args: object) -> str:
        assert "market.upsert_corporate_action_sync" in query
        self.persisted.append(args)
        return "00000000-0000-0000-0000-000000000099"


class _Pool:
    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


@pytest.mark.asyncio
async def test_sync_persists_supported_held_actions_through_controlled_function() -> None:
    conn = _Connection()
    seen: dict[str, tuple[str, ...]] = {}

    async def akshare(symbols: tuple[str, ...]) -> ProviderResult:
        seen["akshare"] = symbols
        return ProviderResult.available(
            "AKSHARE_CORPORATE_ACTIONS",
            [
                CorporateAction(
                    symbol="600519",
                    action_type="CASH_DIVIDEND",
                    ex_date=date(2026, 9, 8),
                    record_date=None,
                    pay_date=None,
                    params={},
                    source_ref="akshare/test",
                ),
                CorporateAction(
                    symbol="510300",
                    action_type="CASH_DIVIDEND",
                    ex_date=date(2026, 9, 9),
                    record_date=None,
                    pay_date=None,
                    params={},
                    source_ref="akshare/test",
                ),
            ],
        )

    async def baostock(symbols: tuple[str, ...], as_of: date) -> ProviderResult:
        seen["baostock"] = symbols
        assert as_of == date(2026, 9, 8)
        return ProviderResult.available("BAOSTOCK_CORPORATE_ACTIONS", [])

    result = await sync_corporate_actions(
        _Pool(conn),
        datetime(2026, 9, 8, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        akshare_fetcher=akshare,
        baostock_fetcher=baostock,
    )

    assert seen == {"akshare": ("600519", "510300"), "baostock": ("600519",)}
    assert result.scanned_instruments == 2
    assert result.persisted_actions == 2
    assert result.unavailable_instruments == 0
    assert len(conn.persisted) == 2
    assert conn.persisted[0][0] == "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_sync_records_unavailable_without_creating_a_fake_action() -> None:
    conn = _Connection()

    async def unavailable_akshare(symbols: tuple[str, ...]) -> ProviderResult:
        return ProviderResult.unavailable("AKSHARE_CORPORATE_ACTIONS", "timeout")

    async def unavailable_baostock(
        symbols: tuple[str, ...], as_of: date
    ) -> ProviderResult:
        return ProviderResult.unavailable("BAOSTOCK_CORPORATE_ACTIONS", "timeout")

    result = await sync_corporate_actions(
        _Pool(conn),
        datetime(2026, 9, 8, 6, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        akshare_fetcher=unavailable_akshare,
        baostock_fetcher=unavailable_baostock,
    )

    assert result.persisted_actions == 0
    assert result.unavailable_instruments == 2
    assert conn.persisted == []
