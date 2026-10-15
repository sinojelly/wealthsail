"""CSI collection and API reads apply the same conservative cutoff policy."""

from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from test_index_official import NOW


def fixture():
    from backend.market.index_policy import POLICY, config_hash, source_uri

    subject = dict(
        code="000300",
        market="SSE",
        provider_symbol="000300",
        source_uri=source_uri("000300"),
        verified_at=NOW - timedelta(days=1),
        publish_hour=None,
        valuation={},
        collection_policy=POLICY,
    )
    points, calendar = [], []
    for n in range(401):
        day = NOW.date() - timedelta(days=401 - n)
        calendar.append({"calendar_date": day, "is_trading_day": day.weekday() < 5})
        if day.weekday() < 5:
            points.append(
                {
                    "date": day.isoformat(),
                    "open": 100 + n,
                    "high": 102 + n,
                    "low": 99 + n,
                    "close": 101 + n,
                    "volume": None,
                }
            )
    return subject, points, calendar, config_hash(subject)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quality,trend,status",
    [
        ("STALE", "STALE", "FAILED"),
        ("CONFLICT", "INSUFFICIENT", "FAILED"),
        ("CONFLICT", "READY", "PARTIAL"),
    ],
)
async def test_full_worker_cannot_complete_ineligible_official_analysis(
    monkeypatch, quality, trend, status
):
    from backend.jobs import investment_research as job, index_research as collector
    from backend.errors import DataUnavailableError

    subject, _, _, _ = fixture()

    @asynccontextmanager
    async def connection(pool):
        yield AsyncMock()

    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(job, "load_subject", AsyncMock(return_value=subject))
    monkeypatch.setattr(
        collector, "claim_index", AsyncMock(return_value={"id": "claim"})
    )
    finish = AsyncMock()
    monkeypatch.setattr(collector, "finish_index", finish)
    monkeypatch.setattr(
        job,
        "_refresh_official_index",
        AsyncMock(return_value={"dataQuality": quality, "trendQuality": trend}),
    )
    if status == "FAILED":
        with pytest.raises(DataUnavailableError):
            await job.refresh_subject(
                object(), None, "index", "hs300", NOW.date(), now=NOW
            )
    else:
        assert await job.refresh_subject(
            object(), None, "index", "hs300", NOW.date(), now=NOW
        )
    assert finish.await_args.args[-1]["status"] == status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "close_only,gap", [(False, None), (True, None), (False, 10), (False, -20)]
)
async def test_refresh_materializes_trend_without_guessed_publication_hour(
    monkeypatch, close_only, gap
):
    from backend.jobs import investment_research as job

    subject, points, calendar, configuration = fixture()
    if close_only:
        points[0].update(open=None, high=None, low=None)
    if gap is not None:
        points.pop(gap)
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetch.return_value = calendar
    conn.fetchval.return_value = NOW.date() - timedelta(days=1)
    active = 0

    @asynccontextmanager
    async def connection(pool):
        nonlocal active
        active += 1
        try:
            yield conn
        finally:
            active -= 1

    async def fetch(code, start, end):
        assert active == 0
        assert code == "000300" and end == NOW.date() - timedelta(days=1)
        return {
            "points": points,
            "pe": {
                "state": "READY",
                "metric": "PE_TTM",
                "points": [{"date": points[-1]["date"], "value": 12.5}],
            },
            "retrievedAt": (NOW + timedelta(seconds=2)).isoformat(),
            "publishedAt": None,
            "timeBasis": "RETRIEVAL_ONLY",
        }

    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(job, "load_subject", AsyncMock(return_value=subject))
    monkeypatch.setattr(job, "fetch_index_history", fetch, raising=False)
    old_facts = AsyncMock()
    monkeypatch.setattr(job, "refresh_fundamentals", old_facts)
    result = await job.refresh_subject(
        object(), object(), "index", "hs300", NOW.date(), now=NOW, _claimed=True
    )
    assert result["dataQuality"] == ("CONFLICT" if gap is not None else "READY")
    assert (result["score"] is None) is (close_only or gap is not None)
    if close_only:
        assert (
            result["state"] == "READY" and result["technicalCoverage"] == "CLOSE_ONLY"
        )
    if gap != -20:
        assert (
            result["indicators"]["ma200"]
            == sum(p["close"] for p in points[-200:]) / 200
        )
        assert (
            result["trendQuality"] == "READY"
            and result["trendWindow"]["sampleCount"] >= 200
        )
    else:
        assert result["indicators"]["ma200"] is None
        assert result["trendQuality"] == "INSUFFICIENT"
    assert result["publicationHour"] is None
    assert result["configurationHash"] == configuration
    assert result["valuation"]["state"] == "PARTIAL"
    assert result["valuation"]["pb"]["state"] == "UNAVAILABLE"
    assert result["officialPE"]["points"][-1]["value"] == 12.5
    assert result["retrievedAt"] == (NOW + timedelta(seconds=2)).isoformat()
    old_facts.assert_not_awaited()
    assert conn.execute.await_count >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed,failure", [(False, False), (True, False), (False, True)]
)
async def test_reader_validates_same_policy_and_revoked_config(
    monkeypatch, changed, failure
):
    from backend.investment import service
    from backend.indicators.technical import analyze
    from backend.market.index_policy import POLICY

    subject, points, _, configuration = fixture()
    result = {
        **analyze(points, quality="READY"),
        "dataQuality": "READY",
        "market": "SSE",
        "calendarMarket": "SSE",
        "publicationHour": None,
        "collectionPolicy": POLICY,
        "configurationHash": configuration,
        "trendQuality": "READY",
        "performance": {"state": "UNSUPPORTED"},
        "valuation": {"state": "PARTIAL"},
    }
    row = {
        "result": result,
        "points": points,
        "as_of": NOW.date() - timedelta(days=1),
        "input_hash": "input",
        "source_uri": subject["source_uri"],
        "retrieved_at": NOW - timedelta(minutes=1),
        "series_kind": "INDEX_PRICE",
    }
    governance = {**subject, "verified_at": None} if changed else subject
    conn = AsyncMock()
    attempt = (
        {
            "phase": "CLAIMED",
            "recorded_at": NOW,
            "lease_until": NOW + timedelta(minutes=10),
            "result": {},
            "last_result": {"status": "FAILED"},
            "last_recorded_at": NOW - timedelta(seconds=10),
        }
        if failure
        else None
    )
    conn.fetchrow.side_effect = [row, governance, attempt]
    conn.fetchval.return_value = NOW.date() - timedelta(days=1)
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW)
    current = await service.analysis(conn, "index", "hs300")
    assert current["dataQuality"] == ("UNVERIFIED" if changed or failure else "READY")
    assert (current["score"] is not None) is (not (changed or failure))
    assert current["trendQuality"] == ("UNVERIFIED" if changed or failure else "READY")
    assert current["valuation"]["state"] != "READY"
    assert conn.fetchval.await_args.args[-2] == NOW.date() - timedelta(days=1)
    assert conn.fetchval.await_args.args[-2] == NOW.date() - timedelta(days=1)
