"""Page-open estimates use the actual trading session and the same fund NAV."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.market_data import Observation
from backend.investment.study_price_watch import price_watch


NOW = datetime(2026, 10, 28, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def tracked():
    frozen = {
        "subjectKind": "nav",
        "subjectId": "004069",
        "fundCode": "004069",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "asOf": "2026-10-27",
        "selectedTrack": "LEFT_VALUE",
        "tracks": [
            {
                "track": "LEFT_VALUE",
                "state": "CANDIDATE",
                "stopLossPrice": "0.95",
                "entryZoneMin": "1.05",
                "entryZoneMax": "1.10",
                "takeProfitT1": "1.40",
                "takeProfitT2": "1.60",
            }
        ],
    }
    confirmed = {
        "subjectKind": "nav",
        "subjectId": "004069",
        "fundCode": "004069",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "value": "1.20",
        "asOf": "2026-10-27",
        "retrievedAt": NOW.isoformat(),
        "quality": "READY",
        "sourceUri": "https://fund.eastmoney.com/004069.html",
        "usable": True,
    }
    return {
        "id": "track",
        "priceWatch": price_watch(frozen, confirmed=confirmed, now=NOW),
    }


def estimate(**changes):
    values = {
        "symbol": "004069",
        "subject_type": "FUND",
        "field_code": "ESTIMATED_NAV",
        "value": Decimal("1.08"),
        "as_of": NOW - timedelta(minutes=1),
        "quality_status": "ESTIMATED",
        "unit": "元/份",
        "method_version": "sina/fund-estimate-v1",
        "origin_uri": "https://finance.sina.com.cn/fund/quotes/004069/bc.shtml",
        "metadata": {"confirmedNav": "1.20"},
    }
    return Observation(**{**values, **changes})


@pytest.mark.asyncio
async def test_page_open_refreshes_morning_estimate_and_keeps_1430_history_separate():
    from backend.investment.study_intraday import refresh_tracks

    original = tracked()
    before = deepcopy(original)
    loader = AsyncMock(return_value=[estimate()])
    result = await refresh_tracks(
        [original], NOW, trading_day=True, loader=loader, now_provider=lambda: NOW
    )
    watch = result[0]["priceWatch"]
    loader.assert_awaited_once_with(["004069"])
    assert watch["currentEstimate"]["value"] == "1.08"
    assert watch["currentEstimate"]["changePct"] == "-10.00"
    assert watch["currentEstimate"]["asOf"] == (NOW - timedelta(minutes=1)).isoformat()
    assert watch["basis"] == "ESTIMATE_INTRADAY" and watch["state"] == "ENTRY_ZONE"
    assert watch["estimate1430"] is None
    assert watch["frozenPlan"] == before["priceWatch"]["frozenPlan"]
    assert original == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hour,minute,trading_day",
    [
        (9, 29, True),
        (11, 31, True),
        (12, 30, True),
        (15, 0, True),
        (10, 30, False),
        (10, 30, None),
    ],
)
async def test_closed_or_unverified_sessions_never_call_estimate_provider(
    hour, minute, trading_day
):
    from backend.investment.study_intraday import refresh_tracks

    loader = AsyncMock()
    item = tracked()
    result = await refresh_tracks(
        [item],
        NOW.replace(hour=hour, minute=minute),
        trading_day=trading_day,
        loader=loader,
    )
    loader.assert_not_awaited()
    assert result == [item]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "000051"},
        {"as_of": NOW - timedelta(days=1)},
        {"as_of": NOW + timedelta(minutes=1)},
        {"quality_status": "STALE"},
        {"unit": "points"},
        {"field_code": "CLOSE"},
        {"subject_type": "ETF"},
        {"origin_uri": None},
    ],
)
async def test_wrong_identity_stale_or_non_nav_quote_cannot_override_confirmed(changes):
    from backend.investment.study_intraday import refresh_tracks

    item = tracked()
    result = await refresh_tracks(
        [item],
        NOW,
        trading_day=True,
        loader=AsyncMock(return_value=[estimate(**changes)]),
        now_provider=lambda: NOW,
    )
    assert result[0]["priceWatch"]["basis"] == "CONFIRMED_NAV"
    assert result[0]["priceWatch"].get("currentEstimate") is None


@pytest.mark.asyncio
async def test_failed_refresh_retains_confirmed_fact_and_entry_protection():
    from backend.investment.study_intraday import refresh_tracks

    item = tracked()
    item["priceWatch"]["entrySafety"] = {
        "entryBlocked": True,
        "entryStatus": "暂停申购",
    }
    failed = await refresh_tracks(
        [item],
        NOW,
        trading_day=True,
        loader=AsyncMock(side_effect=TimeoutError()),
        now_provider=lambda: NOW,
    )
    assert failed == [item]
    result = await refresh_tracks(
        [item],
        NOW,
        trading_day=True,
        loader=AsyncMock(return_value=[estimate()]),
        now_provider=lambda: NOW,
    )
    assert result[0]["priceWatch"]["state"] == "OBSERVING"
    assert result[0]["priceWatch"]["entrySafety"]["entryBlocked"] is True


@pytest.mark.asyncio
async def test_intraday_refresh_preserves_current_clearance_of_historical_entry_block():
    from backend.investment.study_intraday import refresh_tracks

    item = tracked()
    plan = item["priceWatch"]["frozenPlan"]
    plan["entryBlocked"] = True
    plan["tracks"][0]["entryBlocked"] = True
    current = deepcopy(plan)
    current.pop("entryBlocked")
    current["tracks"][0].pop("entryBlocked")
    item["priceWatch"] = price_watch(
        plan,
        confirmed=item["priceWatch"]["confirmedNav"],
        now=NOW,
        entry_safety=current,
    )
    result = await refresh_tracks(
        [item],
        NOW,
        trading_day=True,
        loader=AsyncMock(return_value=[estimate()]),
        now_provider=lambda: NOW,
    )
    assert result[0]["priceWatch"]["state"] == "ENTRY_ZONE"


@pytest.mark.asyncio
async def test_manual_tracks_and_imported_holdings_refresh_together_without_duplicate_requests():
    from backend.investment.study_intraday import refresh_watch_context

    loader = AsyncMock(return_value=[estimate()])
    data = {
        "items": [tracked()],
        "total": 1,
        "heldFunds": [
            {
                "fundCode": "004069",
                "fundMarket": "FUND_CN",
                "label": "证券联接",
                "confirmedNav": {"value": "1.20", "asOf": "2026-10-27"},
            }
        ],
        "suppressedFundKeys": ["FUND_CN:000051"],
    }
    result = await refresh_watch_context(
        data, NOW, trading_day=True, loader=loader, now_provider=lambda: NOW
    )
    loader.assert_awaited_once_with(["004069"])
    assert result["items"][0]["priceWatch"]["currentEstimate"]["value"] == "1.08"
    assert result["heldFunds"][0]["currentEstimate"]["changePct"] == "-10.00"
    assert result["suppressedFundKeys"] == data["suppressedFundKeys"]


@pytest.mark.asyncio
async def test_cancelled_tracking_never_requests_that_fund_again():
    from backend.investment.study_intraday import refresh_watch_context

    loader = AsyncMock()
    data = {
        "items": [tracked()],
        "heldFunds": [
            {
                "fundCode": "004069",
                "fundMarket": "FUND_CN",
                "confirmedNav": None,
            }
        ],
        "suppressedFundKeys": ["FUND_CN:004069"],
    }
    result = await refresh_watch_context(
        data, NOW, trading_day=True, loader=loader, now_provider=lambda: NOW
    )
    loader.assert_not_awaited()
    assert result["items"] == data["items"]
    assert result["heldFunds"][0]["currentEstimate"] is None


@pytest.mark.asyncio
async def test_manual_fund_without_frozen_plan_gets_quote_without_inventing_a_plan():
    from backend.investment.study_intraday import refresh_watch_context

    item = {
        "id": "selection-track",
        "scope": {"fundCode": "004069", "fundMarket": "FUND_CN"},
        "priceWatch": None,
    }
    before = deepcopy(item)
    loader = AsyncMock(return_value=[estimate()])
    result = await refresh_watch_context(
        {"items": [item], "heldFunds": []},
        NOW,
        trading_day=True,
        loader=loader,
        now_provider=lambda: NOW,
    )
    loader.assert_awaited_once_with(["004069"])
    assert result["items"][0]["currentEstimate"]["value"] == "1.08"
    assert result["items"][0]["currentEstimate"]["changePct"] == "-10.00"
    assert result["items"][0]["priceWatch"] is None
    assert "frozenPlan" not in result["items"][0]
    assert item == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "market,suppressed", [("SSE", []), (None, []), ("FUND_CN", ["FUND_CN:004069"])]
)
async def test_planless_fund_needs_exact_otc_identity_and_respects_cancellation(
    market, suppressed
):
    from backend.investment.study_intraday import refresh_watch_context

    loader = AsyncMock()
    item = {"scope": {"fundCode": "004069", "fundMarket": market}, "priceWatch": None}
    result = await refresh_watch_context(
        {"items": [item], "suppressedFundKeys": suppressed},
        NOW,
        trading_day=True,
        loader=loader,
        now_provider=lambda: NOW,
    )
    loader.assert_not_awaited()
    assert result["items"] == [item]


@pytest.mark.asyncio
async def test_instrument_cancellation_suppresses_planned_and_held_quote_requests():
    from backend.investment.study_intraday import refresh_watch_context

    loader = AsyncMock()
    item = tracked()
    item["scope"] = {"instrumentId": "instrument", "fundMarket": "FUND_CN"}
    data = {
        "items": [item],
        "heldFunds": [
            {
                "instrumentId": "instrument",
                "fundCode": "004069",
                "fundMarket": "FUND_CN",
            }
        ],
        "suppressedFundKeys": ["FUND_CN:instrument:instrument"],
    }
    result = await refresh_watch_context(
        data, NOW, trading_day=True, loader=loader, now_provider=lambda: NOW
    )
    loader.assert_not_awaited()
    assert result["items"] == [item]
    assert result["heldFunds"][0]["currentEstimate"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        {"fundCode": "004069"},
        {"instrumentId": "c2d1d269-9430-4c69-8eae-9a2a89afcda4"},
    ],
)
async def test_legacy_scope_resolves_unique_current_public_fund_identity_without_writes(
    scope,
):
    from backend.investment.study_intraday import resolve_fund_scopes, refresh_tracks

    item = {"scope": scope, "priceWatch": None}
    before = deepcopy(item)
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "instrument_id": "c2d1d269-9430-4c69-8eae-9a2a89afcda4",
            "fund_code": "004069",
            "market": "FUND_CN",
        }
    ]
    resolved = await resolve_fund_scopes(conn, [item], NOW)
    assert resolved[0]["scope"]["fundCode"] == "004069"
    assert resolved[0]["scope"]["fundMarket"] == "FUND_CN"
    assert item == before
    conn.execute.assert_not_awaited()
    query, *args = conn.fetch.await_args.args
    assert "effective_from" in query and "effective_to" in query
    assert NOW.date() in args
    loader = AsyncMock(return_value=[estimate()])
    refreshed = await refresh_tracks(
        resolved, NOW, trading_day=True, loader=loader, now_provider=lambda: NOW
    )
    loader.assert_awaited_once_with(["004069"])
    assert refreshed[0]["currentEstimate"]["value"] == "1.08"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,rows",
    [
        (
            {"fundCode": "004069"},
            [
                {"instrument_id": "otc", "fund_code": "004069", "market": "FUND_CN"},
                {"instrument_id": "etf", "fund_code": "004069", "market": "SSE"},
            ],
        ),
        (
            {
                "fundCode": "004069",
                "instrumentId": "c2d1d269-9430-4c69-8eae-9a2a89afcda4",
            },
            [
                {
                    "instrument_id": "different",
                    "fund_code": "004069",
                    "market": "FUND_CN",
                },
            ],
        ),
    ],
)
async def test_legacy_scope_never_guesses_ambiguous_or_conflicting_fund_identity(
    scope, rows
):
    from backend.investment.study_intraday import resolve_fund_scopes

    conn = AsyncMock()
    conn.fetch.return_value = rows
    item = {"scope": scope, "priceWatch": None}
    assert await resolve_fund_scopes(conn, [item], NOW) == [item]


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["list", "detail"])
async def test_api_releases_actor_transaction_before_requesting_public_quotes(
    monkeypatch, surface
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from backend.investment import fund_watch, study_api

    active = False
    conn = AsyncMock()
    conn.fetchval.return_value = True

    @asynccontextmanager
    async def transaction(*args):
        nonlocal active
        active = True
        yield conn
        active = False

    async def refresh(data, now, **kwargs):
        assert active is False
        assert kwargs["trading_day"] is True
        return data

    monkeypatch.setattr(study_api, "tenant_transaction", transaction)
    monkeypatch.setattr(study_api, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(study_api, "refresh_watch_context", refresh)
    monkeypatch.setattr(study_api, "refresh_tracks", refresh)
    monkeypatch.setattr(
        study_api.service,
        "list_studies",
        AsyncMock(return_value={"items": [tracked()], "total": 1}),
    )
    monkeypatch.setattr(
        study_api.service, "get_detail", AsyncMock(return_value={"track": tracked()})
    )
    monkeypatch.setattr(
        fund_watch,
        "list_watch_context",
        AsyncMock(return_value={"heldFunds": [], "suppressedFundKeys": []}),
    )
    options = {
        "pool": object(),
        "ctx": SimpleNamespace(user_id="user", tenant_id="tenant"),
        "request_id": "request",
        "limit": 50,
        "offset": 0,
    }
    if surface == "list":
        result = await study_api.list_tracks(**options)
        assert result["data"]["total"] == 1
    else:
        result = await study_api.get_track("track", **options)
        assert result["data"]["priceWatch"] == result["data"]["track"]["priceWatch"]
