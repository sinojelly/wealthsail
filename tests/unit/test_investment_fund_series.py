"""Unit NAV and explicitly verified provider returns have separate semantics."""

from datetime import date, datetime
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolate_fundamental_sources(monkeypatch):
    from backend.jobs import investment_research
    monkeypatch.setattr(investment_research, "refresh_fundamentals", AsyncMock(return_value={}))


def nav_rows():
    # A cash distribution can lower unit NAV while reported total return rises.
    return [
        {"净值日期": "2026-10-08", "单位净值": 1, "日增长率": None},
        {"净值日期": "2026-10-09", "单位净值": 0.6, "日增长率": 10},
        {"净值日期": "2026-10-12", "单位净值": 0.66, "日增长率": 10},
    ]


@pytest.mark.asyncio
async def test_fetch_fund_history_retains_unit_nav_without_claiming_total_return(monkeypatch):
    from backend.jobs.investment_research import fetch_history

    def provider(**kwargs):
        assert kwargs["indicator"] == "单位净值走势"
        assert kwargs["period"] == "成立来"
        return pd.DataFrame(nav_rows())

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(fund_open_fund_info_em=provider))
    points, kind, source = await fetch_history(
        {"code": "110022", "trade_form": "FUND", "metadata": {"research": {
            "sourceUri": "https://fund.example.org/110022", "sourceVerified": True,
        }}}, "fund", date(2026, 10, 8), date(2026, 10, 12),
    )
    assert kind == "UNIT_NAV"
    assert [p["close"] for p in points] == [1, 0.6, 0.66]
    assert [p["unitNav"] for p in points] == [1, 0.6, 0.66]
    assert source == "https://fund.example.org/110022"


def test_verified_provider_returns_retain_unit_nav_and_do_not_use_its_ratios():
    from backend.market.research_history import fund_history

    points, kind = fund_history(nav_rows(), return_verified=True,
                                start=date(2026, 10, 8), end=date(2026, 10, 12))
    assert kind == "PROVIDER_RETURN_INDEX"
    assert [p["close"] for p in points] == pytest.approx([100, 110, 121])
    assert [p["unitNav"] for p in points] == [1, 0.6, 0.66]
    assert [p["returnPct"] for p in points] == [None, 10, 10]
    assert all(p["high"] is None and p["low"] is None for p in points)


@pytest.mark.parametrize("growth", [None, "bad", float("nan"), float("inf"), -100, -101])
def test_bad_return_growth_retains_unit_nav_and_marks_conflict(growth):
    from backend.market.research_history import fund_history

    rows = nav_rows()
    rows[1]["日增长率"] = growth
    points, kind = fund_history(rows, return_verified=True,
                                start=date(2026, 10, 8), end=date(2026, 10, 12))
    assert kind == "UNIT_NAV"
    assert [p["close"] for p in points] == [1, 0.6, 0.66]
    assert points[1]["returnQuality"] == "CONFLICT"
    assert points[1]["returnPct"] != 0
    assert "NaN" not in json.dumps(points, allow_nan=False)


def test_fund_series_clips_both_ends_before_anchoring_return_index():
    from backend.market.research_history import fund_history

    rows = nav_rows() + [
        {"净值日期": "2020-01-01", "单位净值": 5, "日增长率": None},
        {"净值日期": "2026-10-13", "单位净值": 8, "日增长率": None},
    ]
    points, kind = fund_history(rows, return_verified=True,
                                start=date(2026, 10, 9), end=date(2026, 10, 12))
    assert kind == "PROVIDER_RETURN_INDEX"
    assert [p["date"] for p in points] == ["2026-10-09", "2026-10-12"]
    assert [p["close"] for p in points] == pytest.approx([100, 110])


@pytest.mark.parametrize("market", ["FUND_CN", "SSE", "SZSE"])
@pytest.mark.asyncio
async def test_fund_quality_reuses_mainland_calendar_and_keeps_unscored_indicators(monkeypatch, market):
    from backend.jobs import investment_research as worker

    conn = AsyncMock()
    conn.fetch.return_value = [{"calendar_date": date(2026, 10, 8)},
                              {"calendar_date": date(2026, 10, 9)},
                              {"calendar_date": date(2026, 10, 12)}]

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "system_connection", connection)
    monkeypatch.setattr(worker, "load_subject", AsyncMock(return_value={
        "code": "110022", "market": market, "trade_form": "FUND", "metadata": {
            "research": {"sourceVerified": True, "sourceUri": "https://fund.example.org/110022",
                         "publishHour": 23, "returnSeriesVerified": False},
        },
    }))
    monkeypatch.setattr(worker, "fetch_history", AsyncMock(return_value=(
        [{"date": "2026-10-08", "close": 1}, {"date": "2026-10-09", "close": 0.6},
         {"date": "2026-10-12", "close": 0.66}], "UNIT_NAV", "https://fund.example.org/110022",
    )))
    await worker.refresh_subject(object(), object(), "fund", "fund-key", date(2026, 10, 12),
                                 now=datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert conn.fetch.await_args.args[1] == "SSE"
    result = json.loads(conn.execute.await_args.args[-2])
    assert result["dataQuality"] == "READY"
    assert result["score"] is None
    assert result["state"] == "INSUFFICIENT"
    assert result["calendarMarket"] == "SSE"
    assert result["returnSeriesVerified"] is False
    assert "rsi14" in result["indicators"]


@pytest.mark.asyncio
async def test_conflicted_fund_return_rates_are_not_hidden_by_unsupported_score(monkeypatch):
    from backend.jobs import investment_research as worker

    conn = AsyncMock()
    conn.fetch.return_value = [{"calendar_date": date(2026, 10, 12)}]

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "system_connection", connection)
    monkeypatch.setattr(worker, "load_subject", AsyncMock(return_value={
        "code": "110022", "market": "FUND_CN", "trade_form": "FUND", "metadata": {
            "research": {"sourceVerified": True, "sourceUri": "https://fund.example.org/110022",
                         "publishHour": 23, "returnSeriesVerified": True},
        },
    }))
    monkeypatch.setattr(worker, "fetch_history", AsyncMock(return_value=(
        [{"date": "2026-10-12", "close": 1, "returnQuality": "CONFLICT"}],
        "UNIT_NAV", "https://fund.example.org/110022",
    )))
    await worker.refresh_subject(object(), object(), "fund", "fund-key", date(2026, 10, 12),
                                 now=datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = json.loads(conn.execute.await_args.args[-2])
    assert result["dataQuality"] == "CONFLICT"
    assert result["state"] == "CONFLICT"
    assert result["score"] is None


def test_calendar_ending_before_observed_latest_is_unverified():
    from backend.market.research_history import quality

    assert quality(latest=date(2026, 10, 12), expected=date(2026, 10, 9),
                   verified=True, missing=False) == "UNVERIFIED"


@pytest.mark.asyncio
async def test_return_verification_changes_immutable_input_hash(monkeypatch):
    from backend.jobs import investment_research as worker

    conn = AsyncMock()
    conn.fetch.return_value = [{"calendar_date": date(2026, 10, 12)}]

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "system_connection", connection)
    config = {"sourceVerified": True, "sourceUri": "https://fund.example.org/110022",
              "publishHour": 23, "returnSeriesVerified": False}
    monkeypatch.setattr(worker, "load_subject", AsyncMock(return_value={
        "code": "110022", "market": "FUND_CN", "trade_form": "FUND",
        "metadata": {"research": config},
    }))
    monkeypatch.setattr(worker, "fetch_history", AsyncMock(return_value=(
        [{"date": "2026-10-12", "close": 1}], "UNIT_NAV", "https://fund.example.org/110022",
    )))
    now = datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker.refresh_subject(object(), object(), "fund", "fund-key", now.date(), now=now)
    unverified_hash = conn.execute.await_args.args[3]
    config["returnSeriesVerified"] = True
    await worker.refresh_subject(object(), object(), "fund", "fund-key", now.date(), now=now)
    verified_hash = conn.execute.await_args.args[3]
    assert verified_hash != unverified_hash


@pytest.mark.asyncio
async def test_no_market_calendar_cannot_publish_complete_scoring(monkeypatch):
    from backend.jobs import investment_research as worker

    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "system_connection", connection)
    monkeypatch.setattr(worker, "load_subject", AsyncMock(return_value={
        "code": "510300", "market": "SSE", "trade_form": "ETF", "metadata": {
            "research": {"sourceVerified": True, "sourceUri": "https://fund.example.org/510300",
                         "publishHour": 16},
        },
    }))
    monkeypatch.setattr(worker, "fetch_history", AsyncMock(return_value=(
        [{"date": "2026-10-12", "close": 1, "high": 2, "low": 0.5}],
        "ETF_HFQ", "https://fund.example.org/510300",
    )))
    now = datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker.refresh_subject(object(), object(), "fund", "fund-key", now.date(), now=now)
    result = json.loads(conn.execute.await_args.args[-2])
    assert result["dataQuality"] == "UNVERIFIED"
    assert result["state"] == "UNVERIFIED"
    assert result["score"] is None


@pytest.mark.asyncio
async def test_automatic_research_pool_includes_registered_unmapped_fund(monkeypatch):
    from backend.jobs import investment_research as worker
    from backend.jobs import investment_macro

    async def candidates(sql):
        if "research_indices" in sql:
            return []
        # Only the source registration can add this fund to the automatic pool.
        if "metadata" in sql and "sourceVerified" in sql:
            return [{"instrument_id": "registered-unmapped"}, {"instrument_id": "mapped"}]
        return [{"instrument_id": "mapped"}]

    conn = AsyncMock()
    conn.fetch.side_effect = candidates

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "system_connection", connection)
    monkeypatch.setattr(investment_macro, "sync_macro", AsyncMock(return_value={"observations": 0}))
    refreshed = []

    async def refresh(pool, settings, kind, key, day, *, now):
        refreshed.append((kind, key))

    monkeypatch.setattr(worker, "refresh_subject", refresh)
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock())
    now = datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = await worker.sync_research(object(), object(), now)
    assert refreshed == [("fund", "registered-unmapped"), ("fund", "mapped")]
    assert result == {"succeeded": 2, "failed": 0}
