"""Latest display quotes refresh independently of immutable research plans."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.market_data import Observation

NOW = datetime(2026, 10, 28, 16, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def observation(field="ESTIMATED_NAV", **changes):
    values = dict(
        symbol="004069",
        subject_type="FUND",
        field_code=field,
        value=Decimal("1.1234"),
        as_of=NOW.replace(hour=16, minute=4),
        quality_status="ESTIMATED",
        unit="元/份",
        origin_uri="https://finance.sina.com.cn/fund/",
        metadata={"confirmedNav": "1.0876"},
    )
    return Observation(**{**values, **changes})


@pytest.mark.asyncio
async def test_latest_quote_separates_after_close_estimate_from_official_disclosed_nav():
    from backend.funds.latest_quotes import LatestQuoteCache

    cache = LatestQuoteCache()
    estimate = observation()
    confirmed = observation(
        "NAV",
        value=Decimal("1.0876"),
        as_of=NOW.replace(day=27, hour=0, minute=0),
        quality_status="STALE",
        metadata={"officialUnitNav": True, "returnPct": "-2.04"},
        origin_uri="https://fundf10.eastmoney.com",
    )
    result = await cache.get(
        ["004069"],
        NOW,
        trading_day=True,
        estimate_loader=AsyncMock(return_value=[estimate]),
        confirmed_loader=AsyncMock(return_value=[confirmed]),
        now_provider=lambda: NOW,
    )
    row = result["items"][0]
    assert row["confirmedNav"]["value"] == "1.0876"
    assert row["confirmedNav"]["asOf"] == "2026-10-27"
    assert row["confirmedNav"]["changePct"] == "-2.04"
    assert row["currentEstimate"]["value"] == "1.1234"
    assert row["currentEstimate"]["asOf"] == "2026-10-28T16:04:00+08:00"
    assert row["currentEstimate"]["usable"] is False


@pytest.mark.asyncio
async def test_estimate_base_without_official_date_is_never_returned_as_confirmed_nav():
    from backend.funds.latest_quotes import LatestQuoteCache

    result = await LatestQuoteCache().get(
        ["004069"],
        NOW,
        trading_day=True,
        estimate_loader=AsyncMock(return_value=[observation()]),
        confirmed_loader=AsyncMock(return_value=[observation("NAV")]),
        now_provider=lambda: NOW,
    )
    assert result["items"][0]["confirmedNav"] is None
    assert result["items"][0]["currentEstimate"] is not None


@pytest.mark.asyncio
async def test_quote_cache_reuses_recent_public_observations_and_refreshes_after_ttl():
    from backend.funds.latest_quotes import LatestQuoteCache

    estimate_loader = AsyncMock(return_value=[observation()])
    confirmed_loader = AsyncMock(
        return_value=[
            observation(
                "NAV",
                metadata={"officialUnitNav": True},
                origin_uri="https://fundf10.eastmoney.com",
            )
        ]
    )
    cache = LatestQuoteCache()
    for now in [NOW, NOW + timedelta(seconds=20), NOW + timedelta(seconds=61)]:
        await cache.get(
            ["004069", "004069"],
            now,
            trading_day=True,
            estimate_loader=estimate_loader,
            confirmed_loader=confirmed_loader,
            now_provider=lambda: now,
        )
    assert estimate_loader.await_count == 2
    assert confirmed_loader.await_count == 1


@pytest.mark.asyncio
async def test_partial_32_fund_batch_retries_only_missing_quotes_without_starvation():
    from backend.funds.latest_quotes import LatestQuoteCache

    codes = [f"{index:06d}" for index in range(32)]
    requested = []

    async def limited_provider(batch):
        requested.append(list(batch))
        # A healthy but bounded first request can finish only part of a batch;
        # unstarted/cancelled work must not receive a five-minute success TTL.
        completed = batch[:20] if len(requested) == 1 else batch
        return [
            observation(
                "NAV",
                symbol=code,
                metadata={"officialUnitNav": True},
                origin_uri="https://fundf10.eastmoney.com",
            )
            for code in completed
        ]

    cache = LatestQuoteCache()
    first = await cache.get(
        codes,
        NOW,
        trading_day=False,
        confirmed_loader=limited_provider,
        now_provider=lambda: NOW,
    )
    assert sum(row["confirmedNav"] is not None for row in first["items"]) == 20
    # Brief negative caching avoids sending a failed batch again immediately.
    await cache.get(
        codes,
        NOW + timedelta(seconds=1),
        trading_day=False,
        confirmed_loader=limited_provider,
        now_provider=lambda: NOW + timedelta(seconds=1),
    )
    assert len(requested) == 1

    later = NOW + timedelta(seconds=61)
    retried = await cache.get(
        codes,
        later,
        trading_day=False,
        confirmed_loader=limited_provider,
        now_provider=lambda: later,
    )
    assert requested == [codes, codes[20:]]
    assert all(row["confirmedNav"] is not None for row in retried["items"])
    assert retried["items"][0]["confirmedNav"]["retrievedAt"] == NOW.isoformat()
    assert retried["items"][-1]["confirmedNav"]["retrievedAt"] == later.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [[], TimeoutError()])
async def test_failed_refresh_preserves_last_good_nav_and_retries_after_short_cooldown(
    failure,
):
    from backend.funds.latest_quotes import LatestQuoteCache

    good = observation(
        "NAV",
        value=Decimal("1.1234"),
        metadata={"officialUnitNav": True},
        origin_uri="https://fundf10.eastmoney.com",
    )
    newer = observation(
        "NAV",
        value=Decimal("1.1250"),
        metadata={"officialUnitNav": True},
        origin_uri="https://fundf10.eastmoney.com",
    )
    loader = AsyncMock(side_effect=[[good], failure, [newer]])
    cache = LatestQuoteCache()
    await cache.get(
        ["004069"],
        NOW,
        trading_day=False,
        confirmed_loader=loader,
        now_provider=lambda: NOW,
    )

    expired = NOW + timedelta(minutes=5, seconds=1)
    result = await cache.get(
        ["004069"],
        expired,
        trading_day=False,
        confirmed_loader=loader,
        now_provider=lambda: expired,
    )
    retained = result["items"][0]["confirmedNav"]
    assert retained is not None
    assert retained["value"] == "1.1234"
    assert retained["asOf"] == "2026-10-28"
    assert retained["retrievedAt"] == NOW.isoformat()

    cooldown = expired + timedelta(seconds=4)
    await cache.get(
        ["004069"],
        cooldown,
        trading_day=False,
        confirmed_loader=loader,
        now_provider=lambda: cooldown,
    )
    assert loader.await_count == 2

    retry = expired + timedelta(seconds=6)
    recovered = await cache.get(
        ["004069"],
        retry,
        trading_day=False,
        confirmed_loader=loader,
        now_provider=lambda: retry,
    )
    assert loader.await_count == 3
    assert recovered["items"][0]["confirmedNav"]["value"] == "1.1250"
    assert recovered["items"][0]["confirmedNav"]["retrievedAt"] == retry.isoformat()


@pytest.mark.asyncio
async def test_reading_cache_keeps_the_real_retrieval_time():
    from backend.funds.latest_quotes import LatestQuoteCache

    cache = LatestQuoteCache()
    options = dict(
        trading_day=True,
        estimate_loader=AsyncMock(return_value=[observation()]),
        confirmed_loader=AsyncMock(return_value=[]),
    )
    await cache.get(["004069"], NOW, now_provider=lambda: NOW, **options)
    later = NOW + timedelta(seconds=20)
    result = await cache.get(["004069"], later, now_provider=lambda: later, **options)
    assert result["items"][0]["currentEstimate"]["retrievedAt"] == NOW.isoformat()


def test_quote_http_contract_is_bounded_and_releases_transaction_before_provider(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from fastapi.testclient import TestClient
    from backend.common.tenancy import TenantContext
    from backend.dependencies import get_pool, get_request_id, get_tenant_context
    from backend.funds import api, latest_quotes
    from backend.main import create_app

    active = False
    conn = AsyncMock()
    conn.fetchval.return_value = True

    @asynccontextmanager
    async def transaction(*_args):
        nonlocal active
        active = True
        yield conn
        active = False

    async def lookup(codes, now, **kwargs):
        assert active is False
        assert codes == ["004069"]
        assert kwargs["trading_day"] is True
        return {
            "items": [
                {"fundCode": "004069", "confirmedNav": None, "currentEstimate": None}
            ]
        }

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    monkeypatch.setattr(latest_quotes.latest_quote_cache, "get", lookup)
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_request_id] = lambda: "quote-test"
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        tenant_role="VIEWER",
    )
    client = TestClient(app, raise_server_exceptions=False)
    result = client.get("/api/v1/funds/quotes", params={"codes": "004069"})
    assert result.status_code == 200
    assert result.json()["data"]["items"][0]["fundCode"] == "004069"
    for codes in ["bad", ",004069", ",".join(["004069"] * 33)]:
        assert (
            client.get("/api/v1/funds/quotes", params={"codes": codes}).status_code
            == 400
        )


@pytest.mark.asyncio
async def test_quote_timeout_preserves_fast_partial_results():
    from backend.funds.latest_quotes import LatestQuoteCache

    async def blocked(_codes):
        await asyncio.Event().wait()

    result = await LatestQuoteCache(timeout=0.01).get(
        ["004069"],
        NOW,
        trading_day=True,
        estimate_loader=AsyncMock(return_value=[observation()]),
        confirmed_loader=blocked,
        now_provider=lambda: NOW,
    )
    assert result["items"][0]["currentEstimate"]["value"] == "1.1234"
    assert result["items"][0]["confirmedNav"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"as_of": NOW + timedelta(minutes=1)},
        {"as_of": NOW - timedelta(days=1)},
        {"symbol": "000051"},
        {"unit": "points"},
        {"subject_type": "ETF"},
    ],
)
async def test_latest_estimate_rejects_future_stale_or_wrong_identity(changes):
    from backend.funds.latest_quotes import LatestQuoteCache

    result = await LatestQuoteCache().get(
        ["004069"],
        NOW,
        trading_day=True,
        estimate_loader=AsyncMock(return_value=[observation(**changes)]),
        confirmed_loader=AsyncMock(return_value=[]),
        now_provider=lambda: NOW,
    )
    assert result["items"][0]["currentEstimate"] is None


@pytest.mark.asyncio
async def test_latest_disclosed_provider_keeps_official_date_and_daily_change():
    from backend.integrations import market_data as md

    response = AsyncMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {
        "Data": {
            "LSJZList": [
                {
                    "FSRQ": "2026-10-28",
                    "DWJZ": "1.1234",
                    "JZZZL": "3.29",
                }
            ]
        }
    }
    client = AsyncMock()
    client.get.return_value = response
    row = await md._fetch_fund_nav(client, "004069")
    assert row.metadata["officialUnitNav"] is True
    assert row.metadata["returnPct"] == "3.29"
    assert row.as_of.date().isoformat() == "2026-10-28"


@pytest.mark.asyncio
async def test_latest_disclosed_provider_does_not_fabricate_missing_date():
    from backend.integrations import market_data as md

    response = AsyncMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {"Data": {"LSJZList": [{"FSRQ": "", "DWJZ": "1.1234"}]}}
    client = AsyncMock()
    client.get.return_value = response
    assert await md._fetch_fund_nav(client, "004069") is None


@pytest.mark.asyncio
async def test_study_initial_read_uses_only_cached_quotes_and_never_waits_for_provider(
    monkeypatch,
):
    from backend.funds import latest_quotes
    from backend.integrations import market_data
    from backend.investment.study_intraday import refresh_tracks

    cache = latest_quotes.LatestQuoteCache()
    monkeypatch.setattr(latest_quotes, "latest_quote_cache", cache)
    provider = AsyncMock(
        side_effect=AssertionError("initial study read must be cache-only")
    )
    monkeypatch.setattr(market_data, "fetch_fund_intraday_estimates", provider)
    item = {
        "scope": {"fundCode": "004069", "fundMarket": "FUND_CN"},
        "priceWatch": None,
    }
    assert await refresh_tracks(
        [item], NOW, trading_day=True, now_provider=lambda: NOW
    ) == [item]
    provider.assert_not_awaited()

    await cache.get(
        ["004069"],
        NOW,
        trading_day=True,
        estimate_loader=AsyncMock(return_value=[observation()]),
        confirmed_loader=AsyncMock(return_value=[]),
        now_provider=lambda: NOW,
    )
    result = await refresh_tracks(
        [item], NOW, trading_day=True, now_provider=lambda: NOW
    )
    assert result[0]["currentEstimate"]["value"] == "1.1234"
    provider.assert_not_awaited()
