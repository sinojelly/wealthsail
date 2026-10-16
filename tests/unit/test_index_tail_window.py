"""An old non-session observation cannot erase a verified current trading tail."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from test_index_research_pipeline import fixture
from test_index_official import NOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,expected",
    [
        ("old_weekend", "READY"),
        ("tail_200", "READY"),
        ("tail_199", "INSUFFICIENT"),
        ("recent_weekend", "INSUFFICIENT"),
        ("future", "CONFLICT"),
    ],
)
async def test_current_tail_starts_after_last_missing_or_nontrading_observation(
    monkeypatch, case, expected
):
    from backend.jobs import investment_research as job

    subject, original, calendar, _ = fixture()
    rows = deepcopy(original)
    closed = [
        str(row["calendar_date"]) for row in calendar if not row["is_trading_day"]
    ]
    anomaly = closed[-1] if case == "recent_weekend" else closed[0]
    rows.append({**rows[0], "date": anomaly})
    if case in {"tail_200", "tail_199"}:
        rows.remove(
            next(
                row
                for row in rows
                if row["date"] == original[-201 if case == "tail_200" else -200]["date"]
            )
        )
    if case == "future":
        rows.append({**rows[-1], "date": (NOW.date() + timedelta(days=1)).isoformat()})
    rows.sort(key=lambda row: row["date"])
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetch.return_value = calendar
    conn.fetchval.return_value = NOW.date() - timedelta(days=1)

    @asynccontextmanager
    async def connection(pool):
        yield conn

    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(job, "load_subject", AsyncMock(return_value=subject))
    monkeypatch.setattr(
        job,
        "fetch_index_history",
        AsyncMock(
            return_value={
                "points": rows,
                "pe": {"state": "UNAVAILABLE", "points": []},
                "retrievedAt": NOW.isoformat(),
            }
        ),
    )
    result = await job.refresh_subject(
        object(), None, "index", "hs300", NOW.date(), now=NOW, _claimed=True
    )
    assert result["dataQuality"] == result["historyQuality"] == "CONFLICT"
    assert result["score"] is None
    assert result["trendQuality"] == expected
    if expected == "READY":
        assert result["trendWindow"]["start"] > anomaly
        assert (
            result["indicators"]["ma200"]
            == sum(point["close"] for point in original[-200:]) / 200
        )
        if case == "tail_200":
            assert result["trendWindow"]["sampleCount"] == 200
    elif expected == "INSUFFICIENT":
        assert result["trendWindow"]["sampleCount"] < 200
        assert result["indicators"]["ma200"] is None


@pytest.mark.asyncio
async def test_missing_calendar_still_blocks_before_any_provider_request(monkeypatch):
    from backend.jobs import investment_research as job
    from backend.errors import DataUnavailableError

    subject, _, _, _ = fixture()
    conn = AsyncMock()
    conn.fetchval.return_value = None

    @asynccontextmanager
    async def connection(pool):
        yield conn

    fetch = AsyncMock()
    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(job, "load_subject", AsyncMock(return_value=subject))
    monkeypatch.setattr(job, "fetch_index_history", fetch)
    with pytest.raises(DataUnavailableError):
        await job.refresh_subject(
            object(), None, "index", "hs300", NOW.date(), now=NOW, _claimed=True
        )
    fetch.assert_not_awaited()
