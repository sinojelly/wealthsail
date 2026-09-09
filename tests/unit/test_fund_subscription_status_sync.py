"""全局天天基金申赎状态同步任务的行为契约。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.fund_subscription_status import FundSubscriptionStatus
from backend.jobs.fund_subscription_status_sync import sync_fund_subscription_status


class _Connection:
    async def fetch(self, query: str, *_args):
        assert "ledger.position_snapshots" in query
        return [
            {
                "instrument_id": "fund-1",
                "symbol": "110022",
            },
            # 同一只基金被不同租户持有，任务边界仍只能请求一次。
            {
                "instrument_id": "fund-1",
                "symbol": "110022",
            },
            {
                "instrument_id": "fund-2",
                "symbol": "012733",
            },
        ]


@asynccontextmanager
async def _connection(conn):
    yield conn


@pytest.mark.asyncio
async def test_sync_deduplicates_held_funds_and_persists_recognized_statuses() -> None:
    """同一基金只抓取一次；缺失状态不被写成开放。"""
    fetched_symbols: list[tuple[str, ...]] = []
    persisted: list[tuple[str, str, str]] = []

    async def fetcher(symbols: tuple[str, ...]):
        fetched_symbols.append(symbols)
        return {
            "110022": FundSubscriptionStatus(
                symbol="110022",
                purchase_status="SUSPENDED",
                redemption_status="OPEN",
                source_ref="akshare/fund_purchase_em",
                metadata={"purchaseStatusRaw": "限制大额申购"},
            )
        }

    async def writer(_conn, instrument_id, status, _now):
        persisted.append(
            (instrument_id, status.purchase_status, status.redemption_status)
        )
        return True

    with patch(
        "backend.jobs.fund_subscription_status_sync.system_connection",
        return_value=_connection(_Connection()),
    ):
        stats = await sync_fund_subscription_status(
            MagicMock(),
            datetime(2026, 9, 9, 6, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
            fetcher=fetcher,
            writer=writer,
        )

    assert fetched_symbols == [("110022", "012733")]
    assert persisted == [("fund-1", "SUSPENDED", "OPEN")]
    assert (
        stats.scanned_instruments,
        stats.available_instruments,
        stats.changed_instruments,
        stats.unchanged_instruments,
        stats.unavailable_instruments,
        stats.failed_instruments,
    ) == (2, 1, 1, 0, 1, 0)


@pytest.mark.asyncio
async def test_sync_isolates_one_fund_write_failure() -> None:
    """单基金写入失败不能阻止后续基金状态同步。"""
    persisted: list[str] = []

    async def fetcher(_symbols: tuple[str, ...]):
        return {
            symbol: FundSubscriptionStatus(
                symbol=symbol,
                purchase_status="OPEN",
                redemption_status="OPEN",
                source_ref="akshare/fund_purchase_em",
                metadata={},
            )
            for symbol in ("110022", "012733")
        }

    async def writer(_conn, instrument_id, _status, _now):
        if instrument_id == "fund-1":
            raise RuntimeError("database unavailable")
        persisted.append(instrument_id)
        return False

    with patch(
        "backend.jobs.fund_subscription_status_sync.system_connection",
        return_value=_connection(_Connection()),
    ):
        stats = await sync_fund_subscription_status(
            MagicMock(),
            datetime(2026, 9, 9, 6, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
            fetcher=fetcher,
            writer=writer,
        )

    assert persisted == ["fund-2"]
    assert (stats.changed_instruments, stats.unchanged_instruments) == (0, 1)
    assert stats.failed_instruments == 1
