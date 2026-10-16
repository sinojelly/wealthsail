"""CSI's request-boundary weekend row cannot enter a trading-day PE percentile."""

from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from test_index_official import NOW
from test_index_research_pipeline import fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "short_pe", "unknown_calendar"])
async def test_pe_retains_latest_but_only_verified_sessions_enter_percentile(
    monkeypatch, mode
):
    from backend.jobs import investment_research as job

    subject = fixture()[0]
    start = NOW.date() - timedelta(days=1461)
    end = NOW.date() - timedelta(days=1)
    assert start.weekday() == 5
    calendar, rows = [], []
    day = start
    while day <= end:
        trading = day.weekday() < 5
        calendar.append({"calendar_date": day, "is_trading_day": trading})
        if trading:
            rows.append(
                {
                    "date": day.isoformat(),
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": 100 + len(rows),
                    "volume": None,
                }
            )
        day += timedelta(days=1)
    legal = [
        {"date": row["date"], "value": 10 + index / 1000}
        for index, row in enumerate(rows)
    ]
    if mode == "short_pe":
        legal = legal[-500:]
    anomalous = {"date": start.isoformat(), "value": 999}
    raw_pe = [anomalous, *legal]
    raw_prices = [{**rows[0], "date": start.isoformat()}, *rows]
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetch.return_value = calendar
    conn.fetchval.side_effect = [end, None if mode == "unknown_calendar" else end]

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
                "points": raw_prices,
                "pe": {"state": "READY", "metric": "PE_TTM", "points": raw_pe},
                "retrievedAt": NOW.isoformat(),
            }
        ),
    )
    result = await job.refresh_subject(
        object(), None, "index", "hs300", NOW.date(), now=NOW, _claimed=True
    )
    pe = result["officialPE"]
    assert result["historyQuality"] == result["dataQuality"] == "CONFLICT"
    assert result["score"] is None and result["valuation"]["state"] == "PARTIAL"
    assert pe["latest"] == legal[-1]["value"]
    assert pe["sampleCount"] == len(legal)
    assert pe["points"] == legal
    assert pe["excludedObservations"] == [{**anomalous, "reason": "NON_TRADING_DAY"}]
    assert pe["sampling"] == "verified_trading_sessions"
    assert (
        len(raw_pe) == len(legal) + 1
    )  # no mutation or quiet deletion of source facts
    if mode == "complete":
        assert pe["state"] == "READY"
        assert pe["percentile"] == pytest.approx((len(legal) - 0.5) / len(legal) * 100)
    else:
        assert pe["state"] == ("INSUFFICIENT" if mode == "short_pe" else "UNVERIFIED")
        assert pe["percentile"] is None
    # Immutable inputs include both the legal series and excluded original point.
    persisted = next(
        args
        for args in conn.execute.await_args_list
        if "investment_analysis_snapshots" in args.args[0]
    )
    assert '"excludedObservations"' in persisted.args[-2]
