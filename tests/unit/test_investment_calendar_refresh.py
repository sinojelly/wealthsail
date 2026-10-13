import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from backend.market import service


def source_dates(start, end, *, closed=()):
    days = []
    while start <= end:
        if start.weekday() < 5 and start not in closed:
            days.append(start)
        start += timedelta(days=1)
    return pd.DataFrame({"trade_date": days})


def install(monkeypatch, now, frame, existing=None):
    stored = dict(existing or {})
    calls = []
    clock = {"day": now}

    class Connection:
        async def fetchval(self, sql, *args):
            if len(args) == 2:
                return sum(args[0] <= day <= args[1] for day in stored)
            if len(args) == 1:
                return sum(day > args[0] for day in stored)
            return len(stored)

    @asynccontextmanager
    async def connection(pool):
        yield Connection()

    def fetch():
        calls.append("provider")
        if isinstance(frame, Exception):
            raise frame
        return frame

    async def upsert(conn, rows):
        calls.append(list(rows))
        stored.update(rows)
        return len(rows)

    monkeypatch.setattr(service, "system_connection", connection)
    monkeypatch.setattr(service.repository, "upsert_trading_calendar_rows", upsert)
    monkeypatch.setattr(
        service,
        "now_shanghai",
        lambda: datetime.combine(clock["day"], datetime.min.time()),
    )
    monkeypatch.setitem(
        sys.modules, "akshare", SimpleNamespace(tool_trade_date_hist_sina=fetch)
    )
    return stored, calls, clock


@pytest.mark.asyncio
async def test_fill_covers_five_years_and_never_manufactures_future_workdays(
    monkeypatch,
):
    today, start = date(2026, 10, 14), date(2021, 10, 14)
    holiday = date(2026, 10, 1)
    frame = source_dates(start, date(2026, 12, 31), closed={holiday})
    stored, calls, _ = install(monkeypatch, today, frame)

    inserted = await service.fill_trading_calendar(object())

    assert inserted == (today - start).days + 1
    assert min(stored) == start and max(stored) == today
    assert stored[holiday] is False
    assert stored[date(2026, 10, 10)] is False
    assert stored[today] is True
    assert calls[0] == "provider"


@pytest.mark.asyncio
async def test_partial_existing_rows_cannot_disable_history_backfill(monkeypatch):
    today, start = date(2026, 10, 14), date(2021, 10, 14)
    stored, _, _ = install(
        monkeypatch, today, source_dates(start, today), {today: True}
    )

    result = await service.ensure_trading_calendar(object())

    assert result["status"] == "COMPLETED"
    assert start in stored
    assert len(stored) == (today - start).days + 1


@pytest.mark.asyncio
async def test_complete_current_coverage_stays_database_only(monkeypatch):
    today, start = date(2026, 10, 14), date(2021, 10, 14)
    existing = {
        start + timedelta(days=i): False for i in range((today - start).days + 1)
    }
    _, calls, _ = install(monkeypatch, today, RuntimeError("must not fetch"), existing)

    result = await service.ensure_trading_calendar(object())

    assert result["status"] == "SKIPPED"
    assert calls == []


@pytest.mark.asyncio
async def test_year_rollover_refreshes_latest_cutoff_and_keeps_real_holiday(
    monkeypatch,
):
    previous, today = date(2026, 12, 31), date(2027, 1, 4)
    start = date(2022, 1, 4)
    old_start = date(2021, 12, 31)
    existing = {
        old_start + timedelta(days=i): True
        for i in range((previous - old_start).days + 1)
    }
    stored, _, _ = install(
        monkeypatch,
        today,
        source_dates(old_start, today, closed={date(2027, 1, 1)}),
        existing,
    )

    result = await service.ensure_trading_calendar(object())

    assert result["status"] == "COMPLETED"
    assert stored[today] is True
    assert stored[date(2027, 1, 1)] is False
    assert all(
        start + timedelta(days=i) in stored for i in range((today - start).days + 1)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame", [pd.DataFrame({"trade_date": []}), RuntimeError("down")]
)
async def test_empty_or_failed_source_preserves_existing_real_calendar(
    monkeypatch, frame
):
    existing = {date(2026, 10, 9): True, date(2026, 10, 10): False}
    stored, calls, _ = install(monkeypatch, date(2026, 10, 14), frame, existing)

    inserted = await service.fill_trading_calendar(object())

    assert inserted == 0
    assert stored == existing
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_source_window_does_not_overwrite_dates_before_its_real_coverage(
    monkeypatch,
):
    today = date(2026, 10, 14)
    original = {date(2022, 1, 4): True}
    stored, _, _ = install(
        monkeypatch, today, source_dates(date(2026, 10, 9), today), original
    )

    result = await service.ensure_trading_calendar(object())

    assert stored[date(2022, 1, 4)] is True
    assert date(2025, 1, 2) not in stored
    assert result["status"] == "PARTIAL"
    assert result["reason"] == "PROVIDER_INCOMPLETE"


@pytest.mark.asyncio
async def test_prebuilt_future_rows_are_untouched_and_cannot_mask_missing_history(
    monkeypatch,
):
    today, start, future = date(2026, 10, 14), date(2021, 10, 14), date(2026, 12, 31)
    stored, _, _ = install(
        monkeypatch, today, source_dates(start, today), {future: True}
    )

    result = await service.ensure_trading_calendar(object())

    assert result["status"] == "COMPLETED"
    assert stored[future] is True
    assert start in stored


@pytest.mark.asyncio
async def test_prebuilt_future_rows_force_current_holiday_reverification(monkeypatch):
    today, start, future = date(2026, 10, 1), date(2021, 10, 1), date(2026, 12, 31)
    existing = {
        start + timedelta(days=i): True for i in range((today - start).days + 1)
    }
    existing[future] = True
    stored, _, _ = install(
        monkeypatch,
        today,
        source_dates(date(2021, 9, 29), future, closed={today}),
        existing,
    )

    result = await service.ensure_trading_calendar(object())

    assert result["status"] == "COMPLETED"
    assert stored[today] is False
    assert stored[future] is True
