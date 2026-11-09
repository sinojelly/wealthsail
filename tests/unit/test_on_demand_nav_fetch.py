"""The public history fetch boundary shares calls and rejects fabricated prices."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.integrations.market_data import Observation
from backend.jobs import fund_nav_sync as sync
from tests.unit.test_on_demand_nav_sync import NOW, history


def observations():
    h = history(count=550, lag=0)
    return [
        Observation(
            symbol="011035",
            subject_type="FUND",
            field_code="NAV",
            value=Decimal(p["close"]),
            as_of=NOW.replace(hour=0) - timedelta(days=550 - index - 1),
            quality_status="FRESH",
            unit="CNY/share",
            metadata={
                "officialUnitNav": True,
                "providerHistoryProof": h["providerHistoryProof"],
                "returnPct": "1.23",
            },
        )
        for index, p in enumerate(h["points"])
    ]


@pytest.mark.asyncio
async def test_concurrent_reads_share_one_provider_call_and_retain_disclosed_return():
    cache = sync.OnDemandNavHistoryCache(timeout=1)
    calls = []

    async def fetcher(*args, **kwargs):
        calls.append(args[0])
        await asyncio.sleep(0)
        return observations()

    first, second = await asyncio.gather(
        *[
            cache.get("011035", NOW, fetcher=fetcher, now_provider=lambda: NOW)
            for _ in range(2)
        ]
    )
    assert calls == ["011035"] and first == second
    assert len(first["points"]) == 550
    assert first["points"][-1]["returnPct"] == "1.23"
    assert (
        first["providerHistoryProof"]["pointsDigest"]
        == history(count=550, lag=0)["providerHistoryProof"]["pointsDigest"]
    )


@pytest.mark.asyncio
async def test_failed_fetch_has_short_cooldown_then_recovers_without_fake_history():
    cache = sync.OnDemandNavHistoryCache(timeout=1, cooldown_seconds=5)
    fetch = AsyncMock(side_effect=RuntimeError("offline provider"))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cache.get("011035", NOW, fetcher=fetch, now_provider=lambda: NOW)
    assert fetch.await_count == 1
    fetch.side_effect = None
    fetch.return_value = observations()
    result = await cache.get(
        "011035",
        NOW + timedelta(seconds=6),
        fetcher=fetch,
        now_provider=lambda: NOW + timedelta(seconds=6),
    )
    assert result["points"] and fetch.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["estimate", "currency", "future-clock", "conflict"])
async def test_estimates_or_conflicting_provider_coordinates_are_never_saved(bad):
    from dataclasses import replace

    rows = observations()
    if bad == "estimate":
        rows[-1].metadata["officialUnitNav"] = False
    elif bad == "currency":
        rows[-1] = replace(rows[-1], currency="USD")
    elif bad == "future-clock":
        rows[-1] = replace(rows[-1], as_of=NOW + timedelta(minutes=1))
    else:
        rows.append(replace(rows[-1], value=Decimal("99.99")))
    with pytest.raises(ValueError):
        await sync.OnDemandNavHistoryCache(timeout=1).get(
            "011035",
            NOW,
            fetcher=AsyncMock(return_value=rows),
            now_provider=lambda: NOW,
        )


@pytest.mark.asyncio
async def test_batch_deadline_includes_queue_and_provider_concurrency_is_at_most_four():
    cache = sync.OnDemandNavHistoryCache(timeout=0.05)
    active, peak = 0, 0

    async def stalled(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    results = await asyncio.wait_for(
        asyncio.gather(
            *[
                cache.get(f"{index:06}", NOW, fetcher=stalled, now_provider=lambda: NOW)
                for index in range(12)
            ],
            return_exceptions=True,
        ),
        timeout=1,
    )
    assert peak == 4 and active == 0
    assert all(isinstance(result, TimeoutError) for result in results)
