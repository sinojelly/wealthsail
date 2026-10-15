"""Official index facts keep observed dates distinct from actual retrieval."""

from datetime import date, datetime, timedelta
from importlib import import_module
from zoneinfo import ZoneInfo

import httpx
import pytest

NOW = datetime(2026, 10, 15, 8, tzinfo=ZoneInfo("Asia/Shanghai"))


def row(**changes):
    result = dict(
        tradeDate="20261014",
        indexCode="000300",
        open="100",
        high="105",
        low="99",
        close="103",
        peg="12.5",
        tradingVol="999",
    )
    result.update(changes)
    return result


async def fetch(rows, *, status=200):
    adapter = import_module("backend.integrations.index_official")
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(status, json={"data": rows})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await adapter.fetch_index_history(
            "000300",
            date(2026, 10, 1),
            date(2026, 10, 14),
            client=client,
            clock=lambda: NOW,
        )
    return result, seen


@pytest.mark.asyncio
async def test_official_ohlc_and_separate_pe_capture_completed_http_time():
    result, requests = await fetch([row()])
    assert result["retrievedAt"] == NOW.isoformat()
    assert result["publishedAt"] is None and result["timeBasis"] == "RETRIEVAL_ONLY"
    assert result["points"][0] == {
        "date": "2026-10-14",
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 103.0,
        "volume": None,
    }
    assert result["pe"]["metric"] == "PE_TTM"
    assert result["pe"]["points"] == [{"date": "2026-10-14", "value": 12.5}]
    assert "pb" not in result
    assert requests[0].url.params["indexCode"] == "000300"
    assert requests[0].extensions["timeout"]["read"] <= 20


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"indexCode": "000905"},
        {"tradeDate": "20261016"},
        {"close": "NaN"},
        {"close": True},
        {"low": "104"},
        {"tradeDate": "garbage"},
        {"high": "NaN"},
    ],
)
async def test_wrong_identity_future_invalid_price_rejected(changes):
    with pytest.raises(ValueError):
        await fetch([row(**changes)])


@pytest.mark.asyncio
async def test_real_close_only_records_keep_optional_ohlc_null():
    result, _ = await fetch([row(open=None, high=None, low=None)])
    assert result["points"][0]["close"] == 103
    assert all(result["points"][0][key] is None for key in ("open", "high", "low"))


@pytest.mark.asyncio
async def test_duplicate_conflict_rejected_same_duplicates_deduplicated():
    with pytest.raises(ValueError):
        await fetch([row(), row(close="104")])
    result, _ = await fetch([row(), row()])
    assert len(result["points"]) == 1


@pytest.mark.asyncio
async def test_pe_missing_or_invalid_does_not_suppress_valid_price():
    for value, state in [
        (None, "UNAVAILABLE"),
        ("NaN", "CONFLICT"),
        (-3, "CONFLICT"),
        (0, "CONFLICT"),
    ]:
        result, _ = await fetch([row(peg=value)])
        assert result["points"] and result["pe"]["state"] == state


@pytest.mark.asyncio
async def test_http_failure_not_successful_empty_dataset():
    with pytest.raises(httpx.HTTPStatusError):
        await fetch([], status=403)


@pytest.mark.asyncio
async def test_cutoff_previous_calendar_day_uses_complete_exchange_calendar():
    from unittest.mock import AsyncMock

    policy = import_module("backend.market.index_policy")
    conn = AsyncMock()
    conn.fetchval.return_value = date(2026, 10, 16)
    monday = datetime(2026, 10, 19, 7, tzinfo=NOW.tzinfo)
    assert await policy.expected_session(conn, monday, date(2021, 10, 19)) == date(
        2026, 10, 16
    )
    assert conn.fetchval.await_args.args[-2] == date(2026, 10, 18)
    conn.fetchval.return_value = None
    assert await policy.expected_session(conn, monday, date(2021, 10, 19)) is None


def test_default_policy_and_config_hash_do_not_claim_official_release_hour():
    policy = import_module("backend.market.index_policy")
    subject = dict(
        code="000300",
        provider_symbol="000300",
        source_uri=policy.source_uri("000300"),
        verified_at=NOW - timedelta(days=1),
        collection_policy=policy.POLICY,
    )
    assert policy.trusted(subject, NOW)
    assert not policy.trusted({**subject, "verified_at": None}, NOW)
    assert not policy.trusted({**subject, "source_uri": "https://example.test"}, NOW)
    assert not policy.trusted({**subject, "provider_symbol": "000905"}, NOW)
    assert policy.config_hash(subject) != policy.config_hash(
        {**subject, "collection_policy": {}}
    )


@pytest.mark.asyncio
async def test_collection_comes_from_actual_attempt_and_keeps_original_next_check():
    from unittest.mock import AsyncMock

    policy = import_module("backend.market.index_policy")
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "phase": "FINISHED",
        "recorded_at": NOW,
        "lease_until": None,
        "result": {"status": "FAILED", "errorClass": "ReadTimeout"},
    }
    result = await policy.read_collection(conn, "hs300", NOW + timedelta(hours=2))
    assert result["collectionState"] == "FAILED"
    assert result["collectedAt"] == NOW.isoformat()
    assert result["nextCheckAt"] == (NOW + timedelta(hours=1)).isoformat()
    conn.fetchrow.return_value = None
    assert await policy.read_collection(conn, "hs300", NOW) is None
