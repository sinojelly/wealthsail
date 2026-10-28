"""A webpage refresh persists real unit NAV and reads its newest valid plan."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.market_data import Observation
from backend.investment import theme_graph
from backend.macro import alpha_repository

NOW = datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def _job():
    name = "backend.jobs.fund_nav_research"
    assert util.find_spec(name), "real webpage unit-NAV refresh persistence is missing"
    return import_module(name)


def _history(count=300, *, price="2", retrieved=None):
    days = []
    day = NOW.date() - timedelta(days=2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return {
        "fundCode": "110022",
        "asOf": days[0].isoformat(),
        "sourceUri": "https://fund.eastmoney.com/110022.html",
        "retrievedAt": (retrieved or NOW - timedelta(minutes=5)).isoformat(),
        "availableAt": (retrieved or NOW - timedelta(minutes=5)).isoformat(),
        "priceBasis": "UNIT_NAV",
        "points": [{"date": day.isoformat(), "close": price} for day in reversed(days)],
    }


class Store:
    def __init__(self, history=None):
        self.history = history
        self.active = 0
        self.inserts = []

    async def execute(self, sql, *args):
        self.inserts.append((sql, args))
        return "INSERT 0 1"

    async def fetch(self, sql, *args):
        if "fund_research_nav_histories" in sql:
            return [{"payload": json.dumps(self.history)}] if self.history else []
        history = self.history or _history()
        first = datetime.fromisoformat(history["points"][0]["date"]).date()
        return [
            {
                "calendar_date": first + timedelta(days=i),
                "is_trading_day": (first + timedelta(days=i)).weekday() < 5,
            }
            for i in range((NOW.date() - first).days + 1)
        ]


def _connection(monkeypatch, job, store):
    @asynccontextmanager
    async def connection(pool):
        store.active += 1
        try:
            yield store
        finally:
            store.active -= 1

    monkeypatch.setattr(job, "system_connection", connection)


@pytest.mark.asyncio
async def test_real_unit_nav_refresh_is_bounded_and_append_only_after_http(monkeypatch):
    job = _job()
    store = Store()
    _connection(monkeypatch, job, store)
    points = _history(580)["points"]

    async def fetch(code, start, end, client, *, page_size, max_pages):
        assert store.active == 0
        assert code == "110022" and end == NOW.date()
        assert (end - start).days >= 900
        assert page_size == 100 and max_pages == 32
        return [
            Observation(
                symbol=code,
                subject_type="FUND",
                field_code="NAV",
                value=Decimal(point["close"]),
                as_of=datetime.fromisoformat(point["date"]).replace(tzinfo=NOW.tzinfo),
                quality_status="STALE",
                unit="CNY/share",
                metadata={"officialUnitNav": True},
            )
            for point in points
        ]

    result = await job.refresh_fund_nav_history(
        object(),
        "110022",
        now=NOW,
        fetcher=fetch,
        now_provider=lambda: NOW + timedelta(seconds=2),
    )
    assert len(result["points"]) == 550
    assert result["priceBasis"] == "UNIT_NAV"
    assert result["asOf"] == "2026-10-16"
    assert result["availableAt"] == "2026-10-18T09:00:02+08:00"
    assert len(store.inserts) == 1
    sql, args = store.inserts[0]
    assert "INSERT INTO market.fund_research_nav_histories" in sql
    assert json.loads(args[-1]) == result


@pytest.mark.asyncio
async def test_refresh_rejects_empty_or_wrong_identity_and_never_writes_success(
    monkeypatch,
):
    job = _job()
    store = Store()
    _connection(monkeypatch, job, store)
    rows = [
        Observation(
            symbol="999999",
            subject_type="FUND",
            field_code="NAV",
            value=Decimal("99"),
            as_of=NOW - timedelta(days=2),
            quality_status="FRESH",
            metadata={"officialUnitNav": True},
        )
    ]
    with pytest.raises(Exception, match="净值"):
        await job.refresh_fund_nav_history(
            object(), "110022", now=NOW, fetcher=AsyncMock(return_value=rows)
        )
    assert store.inserts == []


@pytest.mark.asyncio
async def test_provider_failure_does_not_append_a_new_history(monkeypatch):
    job = _job()
    store = Store()
    _connection(monkeypatch, job, store)
    with pytest.raises(TimeoutError):
        await job.refresh_fund_nav_history(
            object(), "110022", now=NOW, fetcher=AsyncMock(side_effect=TimeoutError())
        )
    assert store.inserts == []


@pytest.mark.asyncio
async def test_web_history_beats_older_public_history_and_rechecks_calendar(
    monkeypatch,
):
    old = _history(price="99", retrieved=NOW - timedelta(hours=1))
    new = _history(price="2")
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"fundHistories": [old]}),
    )
    result = await theme_graph.cached_nav_analysis(Store(new), "110022", NOW)
    assert result["actionableZones"]["currentPrice"] == "2.0000"
    assert result["state"] == "READY"
    assert result["retrievedAt"] == "2026-10-18T08:55:00+08:00"
    assert result["seriesKind"] == "UNIT_NAV"
    assert result["performance"]["return252"] is None
    assert result["navChanges"]["change63"] == 0
    assert result["navChanges"]["change252"] == 0
    assert result["navChanges"]["basis"] == "UNIT_NAV_CHANGE"
    assert result["navChanges"]["missingWindows"] == []


@pytest.mark.asyncio
async def test_newer_public_observation_beats_recently_retrieved_old_web_history(
    monkeypatch,
):
    current = _history(price="3", retrieved=NOW - timedelta(hours=1))
    old = _history(price="2")
    old["points"].pop()
    old["asOf"] = old["points"][-1]["date"]
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"fundHistories": [current]}),
    )
    result = await theme_graph.cached_nav_analysis(Store(old), "110022", NOW)
    assert result["actionableZones"]["currentPrice"] == "3.0000"


@pytest.mark.asyncio
async def test_equal_observation_and_retrieval_prefers_explicit_web_refresh(
    monkeypatch,
):
    old = _history(price="99")
    new = _history(price="2")
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"fundHistories": [old]}),
    )
    result = await theme_graph.cached_nav_analysis(Store(new), "110022", NOW)
    assert result["actionableZones"]["currentPrice"] == "2.0000"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"fundCode": "999999"},
        {"availableAt": "2026-10-19T08:00:00+08:00"},
        {"priceBasis": "ACCUMULATED_NAV"},
    ],
)
async def test_invalid_web_history_cannot_hide_valid_public_facts(monkeypatch, change):
    new = {**_history(price="99"), **change}
    old = _history(price="2", retrieved=NOW - timedelta(hours=1))
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"fundHistories": [old]}),
    )
    result = await theme_graph.cached_nav_analysis(Store(new), "110022", NOW)
    assert result["actionableZones"]["currentPrice"] == "2.0000"


@pytest.mark.asyncio
async def test_short_unit_nav_history_describes_missing_windows_without_total_returns(
    monkeypatch,
):
    monkeypatch.setattr(
        alpha_repository, "read_alpha_public", AsyncMock(return_value={})
    )
    result = await theme_graph.cached_nav_analysis(Store(_history(21)), "110022", NOW)
    assert result is not None
    assert result["performance"]["return63"] is None
    assert result["performance"]["return252"] is None
    assert result["navChanges"]["change63"] is None
    assert result["navChanges"]["missingWindows"] == [63, 252]
    assert "64" in str(result["reasons"]) and "253" in str(result["reasons"])


@pytest.mark.asyncio
async def test_registered_fund_readback_uses_current_nav_analysis_instead_of_empty_snapshot(
    monkeypatch,
):
    from backend.investment import service

    fund_id = str(uuid4())
    nav = {
        "state": "READY",
        "dataQuality": "READY",
        "points": [{"close": "2"}],
        "asOf": "2026-10-16",
        "seriesKind": "UNIT_NAV",
        "actionableZones": {"subjectId": fund_id},
    }
    monkeypatch.setattr(
        service,
        "_research_analysis",
        AsyncMock(return_value={"state": "NOT_COLLECTED", "points": []}),
    )
    monkeypatch.setattr(theme_graph, "cached_nav_analysis", AsyncMock(return_value=nav))
    conn = type("Conn", (), {"fetchval": AsyncMock(return_value="110022")})()
    result = await service.analysis(conn, "fund", fund_id)
    assert result["state"] == "READY"
    assert result["points"] == [{"close": "2"}]
    assert result["seriesKind"] == "UNIT_NAV"


@pytest.mark.asyncio
async def test_registered_fund_retains_fresh_raw_nav_when_calendar_is_unknown(
    monkeypatch,
):
    from backend.investment import service

    nav = {
        "state": "UNVERIFIED",
        "dataQuality": "UNVERIFIED",
        "points": [{"date": "2026-10-16", "close": "2"}],
        "asOf": "2026-10-16",
        "retrievedAt": NOW.isoformat(),
        "seriesKind": "UNIT_NAV",
        "actionableZones": {"tracks": []},
    }
    monkeypatch.setattr(
        service,
        "_research_analysis",
        AsyncMock(return_value={"state": "NOT_COLLECTED", "points": []}),
    )
    monkeypatch.setattr(theme_graph, "cached_nav_analysis", AsyncMock(return_value=nav))
    conn = type("Conn", (), {"fetchval": AsyncMock(return_value="110022")})()
    result = await service.analysis(conn, "fund", str(uuid4()))
    assert result["state"] == "UNVERIFIED"
    assert result["points"][-1]["close"] == "2"
    sql = conn.fetchval.await_args.args[0]
    assert "market='FUND_CN'" in sql and "trade_form='FUND'" in sql


@pytest.mark.asyncio
async def test_old_public_nav_does_not_overwrite_a_newer_fund_research_snapshot(
    monkeypatch,
):
    from backend.investment import service

    snapshot = {
        "state": "READY",
        "asOf": "2026-10-16",
        "retrievedAt": NOW.isoformat(),
        "points": [{"close": "3"}],
        "actionableZones": {"currentPrice": "3"},
    }
    nav = {
        "state": "READY",
        "dataQuality": "READY",
        "asOf": "2026-10-16",
        "retrievedAt": (NOW - timedelta(hours=1)).isoformat(),
        "points": [{"close": "2"}],
        "actionableZones": {"currentPrice": "2"},
    }
    monkeypatch.setattr(service, "_research_analysis", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(theme_graph, "cached_nav_analysis", AsyncMock(return_value=nav))
    conn = type("Conn", (), {"fetchval": AsyncMock(return_value="110022")})()
    result = await service.analysis(conn, "fund", str(uuid4()))
    assert result["points"] == [{"close": "3"}]
