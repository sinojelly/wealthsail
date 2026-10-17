from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
import json
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest


def worker_fixture(monkeypatch, *, series="ETF_HFQ", verified=True):
    from backend.jobs import investment_research as worker
    conn = AsyncMock()
    end = date(2026, 10, 12)
    points = [{"date": (end - timedelta(days=252-i)).isoformat(),
               "close": 100+i, "high": 101+i, "low": 99+i}
              for i in range(253)]
    conn.fetch.return_value = [{"calendar_date": date.fromisoformat(p["date"])} for p in points]

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(worker, "system_connection", connection)
    monkeypatch.setattr(worker, "load_subject", AsyncMock(return_value={
        "code": "510300", "market": "FUND_CN", "trade_form": "FUND", "metadata": {
            "research": {"sourceVerified": True, "sourceUri": "https://example.org/nav",
                         "publishHour": 23, "returnSeriesVerified": verified},
        },
    }))
    fetch = AsyncMock(return_value=(points, series, "https://example.org/nav"))
    monkeypatch.setattr(worker, "fetch_history", fetch)
    monkeypatch.setattr(worker, "refresh_fundamentals", AsyncMock(return_value={}), raising=False)
    return worker, conn, fetch


@pytest.mark.asyncio
async def test_refresh_persists_independent_performance_and_method_version(monkeypatch):
    worker, conn, _ = worker_fixture(monkeypatch, series="PROVIDER_RETURN_INDEX")
    now = datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker.refresh_subject(object(), object(), "fund", "key", now.date(), now=now)
    args = conn.execute.await_args.args
    result = json.loads(args[-2])
    assert result["performance"]["eligible"] is True
    assert result["performance"]["return252"] == pytest.approx(2.52)
    assert result["performance"]["maxDrawdown"] == 0
    from backend.indicators.performance import VERSION
    assert VERSION in args[-3]


@pytest.mark.asyncio
async def test_unit_nav_cannot_gain_eligibility_from_technical_result(monkeypatch):
    worker, conn, _ = worker_fixture(monkeypatch, series="UNIT_NAV", verified=False)
    now = datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker.refresh_subject(object(), object(), "fund", "key", now.date(), now=now)
    performance = json.loads(conn.execute.await_args.args[-2])["performance"]
    assert performance["eligible"] is False
    assert performance["state"] == "UNSUPPORTED"
    assert performance["return252"] is None


@pytest.mark.asyncio
async def test_refresh_materializes_independent_value_and_cost_evidence(monkeypatch):
    from tests.unit.test_value_evidence import valuation, costs
    worker, conn, _ = worker_fixture(monkeypatch)
    facts = {"valuation": valuation(), "costs": costs(), "mapped": True,
             "indexId": "hs300", "valuationConfig": {"publishHour": 18}}
    monkeypatch.setattr(worker, "refresh_fundamentals", AsyncMock(return_value=facts), raising=False)
    now = datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker.refresh_subject(object(), object(), "fund", "key", now.date(), now=now)
    result = json.loads(conn.execute.await_args.args[-2])
    assert result["valueEvidence"]["eligible"] is True
    assert result["valueEvidence"]["parameters"]["maximumAnnualOperatingCost"] == 0.008
    assert result["costs"]["managementRate"] == 0.005
    assert result["fundamentals"]["indexId"] == "hs300"


@pytest.mark.asyncio
async def test_fetch_uses_published_window_before_building_return_index(monkeypatch):
    worker, _, fetch = worker_fixture(monkeypatch)
    now = datetime(2026, 10, 13, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker.refresh_subject(object(), object(), "fund", "key", now.date(), now=now)
    assert fetch.await_args.args[3] == date(2026, 10, 12)
    assert fetch.await_args.args[2] == date(2021, 10, 13)


@pytest.mark.asyncio
async def test_stale_nav_performance_is_revoked_even_without_technical_score(monkeypatch):
    from backend.investment import service
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "result": {"score": None, "state": "UNSUPPORTED", "dataQuality": "READY",
                   "market": "FUND_CN", "calendarMarket": "SSE", "publicationHour": 23,
                   "performance": {"eligible": True, "state": "READY", "return252": 0.2}},
        "as_of": date(2026, 10, 9), "input_hash": "hash", "source_uri": "https://example.org/nav",
        "series_kind": "PROVIDER_RETURN_INDEX", "points": [],
    }
    conn.fetchval.return_value = date(2026, 10, 12)
    monkeypatch.setattr(service, "now_shanghai", lambda: datetime(
        2026, 10, 13, 6, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = await service.analysis(conn, "fund", "key")
    assert result["performance"]["eligible"] is False
    assert result["performance"]["state"] == "STALE"
    assert result["performance"]["return252"] == 0.2
    assert any(call.args[1]=="SSE" for call in conn.fetchval.await_args_list)


def test_fund_list_exposes_historical_risk_with_its_own_state():
    from backend.investment.service import fund_dto
    row = {"instrument_id": None, "fund_code": "123456", "fund_name": "fund",
           "fund_type": "INDEX", "trade_form": "FUND", "market": "FUND_CN", "currency": "CNY",
           "performance": {"state": "STALE", "eligible": False, "return252": 0.1,
                           "maxDrawdown": 0.2, "volatility20": 0.1},
           "performance_state": "STALE"}
    result = fund_dto(row)
    assert result["performanceState"] == "STALE"
    assert result["return252"] == 0.1
    assert result["maxDrawdown"] == 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"sourceVerified": False}, {"returnSeriesVerified": False},
                                    {"sourceUri": "https://example.org/changed"}])
async def test_current_source_revocation_invalidates_old_snapshot(monkeypatch, change):
    from backend.investment import service
    config = {"sourceVerified": True, "returnSeriesVerified": True,
              "sourceUri": "https://example.org/nav", "publishHour": 23, **change}
    conn = AsyncMock()
    conn.fetchrow.side_effect = [{
        "result": {"score": None, "state": "UNSUPPORTED", "dataQuality": "READY",
                   "market": "FUND_CN", "calendarMarket": "SSE", "publicationHour": 23,
                   "performance": {"eligible": True, "state": "READY", "return252": 0.2}},
        "as_of": date(2026, 10, 12), "input_hash": "hash", "source_uri": "https://example.org/nav",
        "series_kind": "PROVIDER_RETURN_INDEX", "points": [],
    }, {"metadata": {"research": config}, "mapping_valid": False}]
    conn.fetchval.return_value = date(2026, 10, 12)
    monkeypatch.setattr(service, "now_shanghai", lambda: datetime(
        2026, 10, 13, 6, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = await service.analysis(conn, "fund", "key")
    assert result["performance"]["eligible"] is False
    assert result["performance"]["state"] == "UNVERIFIED"


@pytest.mark.asyncio
async def test_non_trading_observation_cannot_count_towards_eligibility(monkeypatch):
    worker, conn, _ = worker_fixture(monkeypatch)
    conn.fetch.return_value[20]["is_trading_day"] = False
    now = datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker.refresh_subject(object(), object(), "fund", "key", now.date(), now=now)
    result = json.loads(conn.execute.await_args.args[-2])
    assert result["dataQuality"] == "CONFLICT"
    assert result["performance"]["eligible"] is False


@pytest.mark.asyncio
async def test_calendar_missing_early_history_does_not_certify_five_years(monkeypatch):
    worker, conn, _ = worker_fixture(monkeypatch)
    conn.fetch.return_value = conn.fetch.return_value[10:]
    now = datetime(2026, 10, 12, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
    await worker.refresh_subject(object(), object(), "fund", "key", now.date(), now=now)
    result = json.loads(conn.execute.await_args.args[-2])
    assert result["dataQuality"] == "UNVERIFIED"
    assert result["performance"]["eligible"] is False


@pytest.mark.asyncio
async def test_disclosed_nav_on_closed_day_is_preserved_but_not_a_trading_sample(monkeypatch):
    worker, conn, _ = worker_fixture(monkeypatch, series='PROVIDER_RETURN_INDEX')
    conn.fetch.return_value[20]['is_trading_day']=False
    now=datetime(2026,10,12,23,tzinfo=ZoneInfo('Asia/Shanghai'))
    await worker.refresh_subject(object(),object(),'fund','key',now.date(),now=now)
    result=json.loads(conn.execute.await_args.args[-2])
    points=json.loads(conn.execute.await_args.args[-1])
    assert result['dataQuality']=='READY'
    assert result['performance']['sampleCount']==252
    assert result['performance']['state']=='INSUFFICIENT'
    assert len(result['navObservations'])==253
    assert len(points)==252


@pytest.mark.asyncio
async def test_old_calendar_tail_cannot_certify_current_history(monkeypatch):
    worker,conn,_=worker_fixture(monkeypatch)
    now=datetime(2026,10,13,23,tzinfo=ZoneInfo('Asia/Shanghai'))
    await worker.refresh_subject(object(),object(),'fund','key',now.date(),now=now)
    result=json.loads(conn.execute.await_args.args[-2])
    assert result['dataQuality']=='UNVERIFIED'
    assert result['performance']['eligible'] is False
