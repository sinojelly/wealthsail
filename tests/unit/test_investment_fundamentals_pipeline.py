"""Shared index valuation and fund-cost facts remain auditable on failures."""

import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
FUND_ID = "00000000-0000-0000-0000-000000000001"
CONFIG = {
    "provider": "LEGULEGU",
    "symbol": "沪深300",
    "sourceUri": "https://legulegu.com/stockdata/index-basic?indexCode=000300",
    "verified": True,
    "publishHour": 18,
}


def _index(**changes):
    subject = {"id": "hs300", "code": "000300", "market": "SSE", "valuation": CONFIG}
    subject.update(changes)
    return subject


def _valuation(state="READY"):
    return {
        "state": state,
        "sourceUri": CONFIG["sourceUri"],
        "pe": {
            "state": state,
            "points": [{"date": "2026-10-13", "value": 12.0}],
            "historyEnd": "2026-10-13",
        },
        "pb": {
            "state": state,
            "points": [{"date": "2026-10-13", "value": 1.2}],
            "historyEnd": "2026-10-13",
        },
    }


def _costs(state="READY"):
    return {
        "state": state,
        "managementRate": 0.005,
        "custodyRate": 0.001,
        "salesServiceRate": 0.0,
        "totalRate": 0.006,
        "sourceUri": "https://fundf10.eastmoney.com/jjfl_000001.html",
        "reasons": [],
    }


def _connection(monkeypatch, worker, *, links=None, cached=None):
    conn = AsyncMock()
    conn.fetch.return_value = links or []

    async def fetchval(sql, *args):
        if "trading_calendars" in sql:
            return date(2026, 10, 13)
        return None

    conn.fetchval.side_effect = fetchval
    conn.fetchrow.return_value = cached

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "system_connection", connection)
    return conn


def _writes(conn):
    return [
        call.args
        for call in conn.execute.await_args_list
        if "investment_fundamental_snapshots" in call.args[0]
    ]


@pytest.mark.asyncio
async def test_index_valuation_uses_published_calendar_day_and_immutable_fact(
    monkeypatch,
):
    from backend.jobs import investment_fundamentals as worker

    conn = _connection(monkeypatch, worker)
    fetch = AsyncMock(return_value=_valuation())
    monkeypatch.setattr(worker, "fetch_index_valuation", fetch)
    result = await worker.refresh_fundamentals(
        object(), "index", "hs300", _index(), NOW.date(), now=NOW
    )
    assert fetch.await_args.args[2] == date(2026, 10, 13)
    assert conn.fetchval.await_args.args[1:] == ("SSE", date(2026, 10, 13), date(2026, 10, 13))
    assert result["valuation"]["expectedAsOf"] == "2026-10-13"
    assert result["mapped"] is True and result["indexId"] == "hs300"
    assert result["valuationConfig"] == CONFIG
    writes = _writes(conn)
    assert len(writes) == 1 and writes[0][1:4] == ("index", "hs300", "VALUATION")
    assert writes[0][6] == date(2026, 10, 13) and writes[0][7] == NOW
    assert "DO NOTHING" in writes[0][0] and "DO UPDATE" not in writes[0][0]


@pytest.mark.asyncio
async def test_fund_uses_verified_tracking_mapping_and_records_unknown_fee_period(
    monkeypatch,
):
    from backend.jobs import investment_fundamentals as worker

    conn = _connection(monkeypatch, worker, links=[_index()])
    monkeypatch.setattr(
        worker, "fetch_index_valuation", AsyncMock(return_value=_valuation())
    )
    monkeypatch.setattr(worker, "fetch_fund_costs", AsyncMock(return_value=_costs()))
    subject = {
        "code": "000001",
        "market": "FUND_CN",
        "metadata": {
            "research": {
                "sourceVerified": True,
                "sourceUri": "https://fund.example/history",
            }
        },
    }
    result = await worker.refresh_fundamentals(
        object(), "fund", FUND_ID, subject, NOW.date(), now=NOW
    )
    assert result["mapped"] is True and result["indexId"] == "hs300"
    assert result["valuationConfig"] == CONFIG
    assert (
        result["costs"]["state"] == "READY"
        and result["costs"]["retrievedAt"] == NOW.isoformat()
    )
    writes = _writes(conn)
    assert {(row[1], row[3]) for row in writes} == {
        ("index", "VALUATION"),
        ("fund", "VALUATION"),
        ("fund", "COSTS"),
    }
    fee = next(row for row in writes if row[3] == "COSTS")
    assert fee[6] is None and fee[7] == NOW
    assert json.loads(fee[8])["sourceVerified"] is True
    fund_valuation = next(
        row for row in writes if row[1] == "fund" and row[3] == "VALUATION"
    )
    assert json.loads(fund_valuation[8])["valuationConfig"] == CONFIG


@pytest.mark.asyncio
async def test_unregistered_fund_never_guesses_mapping_or_fee_verification(monkeypatch):
    from backend.jobs import investment_fundamentals as worker

    conn = _connection(monkeypatch, worker)
    valuation_fetch = AsyncMock(return_value=_valuation())
    monkeypatch.setattr(worker, "fetch_index_valuation", valuation_fetch)
    monkeypatch.setattr(worker, "fetch_fund_costs", AsyncMock(return_value=_costs()))
    subject = {"code": "000001", "name": "沪深300指数基金", "metadata": {}}
    result = await worker.refresh_fundamentals(
        object(), "fund", FUND_ID, subject, NOW.date(), now=NOW
    )
    assert result["mapped"] is False and result["indexId"] is None
    assert result["valuation"]["state"] == "UNVERIFIED"
    assert result["costs"]["state"] == "UNVERIFIED"
    assert valuation_fetch.await_count == 0
    assert {(row[1], row[3]) for row in _writes(conn)} == {
        ("fund", "VALUATION"),
        ("fund", "COSTS"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {},
        {**CONFIG, "verified": False},
        {**CONFIG, "publishHour": True},
        {**CONFIG, "sourceUri": " "},
    ],
)
async def test_unverified_index_or_publication_schedule_never_fetches_valuation(
    monkeypatch, config
):
    from backend.jobs import investment_fundamentals as worker

    conn = _connection(monkeypatch, worker)
    fetch = AsyncMock(return_value=_valuation())
    monkeypatch.setattr(worker, "fetch_index_valuation", fetch)
    result = await worker.refresh_fundamentals(
        object(), "index", "hs300", _index(valuation=config), NOW.date(), now=NOW
    )
    assert result["valuation"]["state"] == "UNVERIFIED"
    assert fetch.await_count == 0
    assert json.loads(_writes(conn)[0][8])["state"] == "UNVERIFIED"


@pytest.mark.asyncio
async def test_unknown_calendar_prevents_ready_valuation(monkeypatch):
    from backend.jobs import investment_fundamentals as worker

    conn = _connection(monkeypatch, worker)
    conn.fetchval.side_effect = None
    conn.fetchval.return_value = None
    fetch = AsyncMock(return_value=_valuation())
    monkeypatch.setattr(worker, "fetch_index_valuation", fetch)
    result = await worker.refresh_fundamentals(
        object(), "index", "hs300", _index(), NOW.date(), now=NOW
    )
    assert result["valuation"]["state"] == "UNVERIFIED" and fetch.await_count == 0


@pytest.mark.asyncio
async def test_new_provider_failure_is_stored_instead_of_reusing_older_good_index(
    monkeypatch,
):
    from backend.jobs import investment_fundamentals as worker

    conn = _connection(monkeypatch, worker, cached={"result": _valuation()})
    monkeypatch.setattr(
        worker, "fetch_index_valuation", AsyncMock(side_effect=RuntimeError("outage"))
    )
    result = await worker.refresh_fundamentals(
        object(), "index", "hs300", _index(), NOW.date(), now=NOW
    )
    assert result["valuation"]["state"] == "UNAVAILABLE"
    assert json.loads(_writes(conn)[0][8])["state"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_peer_fund_reuses_same_day_index_result_including_latest_failure(
    monkeypatch,
):
    from backend.jobs import investment_fundamentals as worker

    cached = {
        **_valuation("UNAVAILABLE"),
        "expectedAsOf": "2026-10-13",
        "retrievedAt": NOW.isoformat(),
    }
    conn = _connection(
        monkeypatch, worker, links=[_index()], cached={"result": json.dumps(cached)}
    )
    fetch = AsyncMock(return_value=_valuation())
    monkeypatch.setattr(worker, "fetch_index_valuation", fetch)
    monkeypatch.setattr(worker, "fetch_fund_costs", AsyncMock(return_value=_costs()))
    result = await worker.refresh_fundamentals(
        object(),
        "fund",
        FUND_ID,
        {"code": "000001", "metadata": {}},
        NOW.date(),
        now=NOW,
    )
    assert result["valuation"]["state"] == "UNAVAILABLE" and fetch.await_count == 0
    assert {(row[1], row[3]) for row in _writes(conn)} == {
        ("fund", "VALUATION"),
        ("fund", "COSTS"),
    }
    sql = conn.fetchrow.await_args.args[0]
    assert (
        "configurationHash" in sql and "expectedAsOf" in sql and "retrieved_at" in sql
    )


@pytest.mark.asyncio
async def test_fee_source_failure_does_not_block_index_valuation(monkeypatch):
    from backend.jobs import investment_fundamentals as worker

    conn = _connection(monkeypatch, worker, links=[_index()])
    monkeypatch.setattr(
        worker, "fetch_index_valuation", AsyncMock(return_value=_valuation())
    )
    monkeypatch.setattr(
        worker, "fetch_fund_costs", AsyncMock(side_effect=RuntimeError("outage"))
    )
    result = await worker.refresh_fundamentals(
        object(),
        "fund",
        FUND_ID,
        {"code": "000001", "metadata": {}},
        NOW.date(),
        now=NOW,
    )
    assert result["costs"]["state"] == "UNAVAILABLE"
    assert result["valuation"]["state"] == "READY"
    assert len(_writes(conn)) == 3


@pytest.mark.asyncio
async def test_multiple_tracking_indices_are_not_arbitrarily_selected(monkeypatch):
    from backend.jobs import investment_fundamentals as worker

    _connection(
        monkeypatch, worker, links=[_index(), _index(id="zz500", code="000905")]
    )
    monkeypatch.setattr(worker, "fetch_fund_costs", AsyncMock(return_value=_costs()))
    fetch = AsyncMock(return_value=_valuation())
    monkeypatch.setattr(worker, "fetch_index_valuation", fetch)
    result = await worker.refresh_fundamentals(
        object(), "fund", FUND_ID, {"code": "000001"}, NOW.date(), now=NOW
    )
    assert result["mapped"] is False and result["valuation"]["state"] == "UNVERIFIED"
    assert fetch.await_count == 0


@pytest.mark.asyncio
async def test_blank_fund_source_uri_cannot_qualify_disclosed_fees(monkeypatch):
    from backend.jobs import investment_fundamentals as worker

    _connection(monkeypatch, worker)
    monkeypatch.setattr(worker, "fetch_fund_costs", AsyncMock(return_value=_costs()))
    subject = {
        "code": "000001",
        "metadata": {"research": {"sourceVerified": True, "sourceUri": " "}},
    }
    result = await worker.refresh_fundamentals(
        object(), "fund", FUND_ID, subject, NOW.date(), now=NOW
    )
    assert result["costs"]["state"] == "UNVERIFIED"
    assert result["costs"]["sourceVerified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["index", "fund"])
async def test_provider_io_never_holds_a_database_pool_connection(monkeypatch, kind):
    from backend.jobs import investment_fundamentals as worker

    conn = _connection(monkeypatch, worker, links=[_index()])
    active_connections = 0
    provider_leases, persistence_leases = [], []

    @asynccontextmanager
    async def connection(pool):
        nonlocal active_connections
        active_connections += 1
        try:
            yield conn
        finally:
            active_connections -= 1

    async def costs(code):
        provider_leases.append(("costs", active_connections))
        return _costs()

    async def valuation(subject, start, end):
        provider_leases.append(("valuation", active_connections))
        return _valuation()

    async def persist(sql, *args):
        persistence_leases.append(active_connections)

    conn.execute.side_effect = persist
    monkeypatch.setattr(worker, "system_connection", connection)
    monkeypatch.setattr(worker, "fetch_fund_costs", costs)
    monkeypatch.setattr(worker, "fetch_index_valuation", valuation)
    subject = (
        _index()
        if kind == "index"
        else {
            "code": "000001",
            "metadata": {
                "research": {
                    "sourceVerified": True,
                    "sourceUri": "https://fund.example/history",
                }
            },
        }
    )
    result = await worker.refresh_fundamentals(
        object(),
        kind,
        "hs300" if kind == "index" else FUND_ID,
        subject,
        NOW.date(),
        now=NOW,
    )
    assert result["valuation"]["state"] == "READY"
    assert provider_leases == (
        [("valuation", 0)] if kind == "index" else [("costs", 0), ("valuation", 0)]
    )
    assert persistence_leases and all(leases == 1 for leases in persistence_leases)
    assert active_connections == 0
