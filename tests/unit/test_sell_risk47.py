"""Personal risk alerts require a real actor relationship and comparable prices."""

from copy import deepcopy
from datetime import datetime, timedelta
from importlib import import_module, util
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 20, 14, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
TENANT, ACTOR, TRACK, VERSION = [uuid4() for _ in range(4)]


def module():
    assert util.find_spec("backend.investment.sell_risk"), (
        "personal sell-risk service missing"
    )
    return import_module("backend.investment.sell_risk")


def target(**updates):
    return {
        "fundCode": "000051",
        "label": "沪深300联接A",
        "subjectKind": "nav",
        "subjectId": "000051",
        "relationship": ["TRACKED"],
        "planOrigin": "FROZEN_STUDY",
        "trackId": str(TRACK),
        "versionId": str(VERSION),
        "plan": {
            "subjectKind": "nav",
            "subjectId": "000051",
            "fundCode": "000051",
            "market": "FUND_CN",
            "priceBasis": "UNIT_NAV",
            "unit": "CNY/份",
            "asOf": "2026-10-19",
            "selectedTrack": "LEFT_VALUE",
            "tracks": [
                {
                    "track": "LEFT_VALUE",
                    "state": "WATCH",
                    "priceState": "READY",
                    "entryZoneMin": "0.9",
                    "entryZoneMax": "1.1",
                    "stopLossPrice": "0.8",
                    "takeProfitT1": "1.2",
                    "takeProfitT2": "1.4",
                }
            ],
        },
        **updates,
    }


def confirmed(value="1.1", rsi=None, **updates):
    return {
        "subjectKind": "nav",
        "subjectId": "000051",
        "fundCode": "000051",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY/份",
        "quality": "READY",
        "usable": True,
        "value": value,
        "asOf": "2026-10-19",
        "retrievedAt": NOW.isoformat(),
        "sourceUri": "https://fund.eastmoney.com/000051.html",
        "rsi14": rsi,
        "rsiAsOf": "2026-10-19",
        **updates,
    }


@pytest.mark.parametrize(
    "value,rsi,state",
    [
        ("0.8", 95, "STOP_LOSS"),
        ("1.4", 95, "TAKE_PROFIT_T2"),
        ("1.2", 95, "TAKE_PROFIT_T1"),
        ("1.1", 72, "RSI_OVERBOUGHT"),
        ("1.1", 71.99, None),
    ],
)
def test_stop_t2_t1_and_latest_confirmed_rsi_have_independent_boundaries(
    value, rsi, state
):
    result = module().evaluate_sell_risk(
        target(), confirmed=confirmed(value, rsi), now=NOW
    )
    assert (result or {}).get("state") == state
    if result:
        assert result["basis"] == "CONFIRMED_NAV"
        assert result["rsiAsOf"] == "2026-10-19"
        assert result["relationship"] == ["TRACKED"]


@pytest.mark.parametrize(
    "relationship", [[], ["PUBLIC_CANDIDATE"], ["HELD", "UNVERIFIED"]]
)
def test_a_public_plan_or_unknown_relationship_cannot_trigger_a_personal_sell_alert(
    relationship,
):
    assert (
        module().evaluate_sell_risk(
            target(relationship=relationship), confirmed=confirmed("1.5", 99), now=NOW
        )
        is None
    )


@pytest.mark.parametrize(
    "change",
    [
        {"fundCode": "000216"},
        {"subjectId": "000216"},
        {"priceBasis": "PROVIDER_RETURN_INDEX"},
        {"unit": "points"},
        {"quality": "STALE"},
        {"usable": False},
        {"value": "NaN"},
        {"asOf": "2026-10-21"},
        {"asOf": "2026-10-18"},
        {"retrievedAt": (NOW + timedelta(seconds=1)).isoformat()},
        {"sourceUri": "http://fund.eastmoney.com/000051.html"},
    ],
)
def test_mismatched_future_stale_or_unsourced_quotes_do_not_create_personal_alerts(
    change,
):
    assert (
        module().evaluate_sell_risk(
            target(), confirmed=confirmed(**{"value": "1.5", **change}), now=NOW
        )
        is None
    )


def test_estimate_precedes_confirmed_quote_only_before_cutoff_and_rsi_stays_confirmed():
    quote = confirmed(
        "1.4",
        99,
        quality="ESTIMATED",
        asOf=NOW.replace(minute=30).isoformat(),
        rsiAsOf=NOW.date().isoformat(),
    )
    result = module().evaluate_sell_risk(
        target(), confirmed=confirmed("1.1", 25), estimate=quote, now=NOW
    )
    assert result["state"] == "TAKE_PROFIT_T2" and result["basis"] == "ESTIMATE_1430"
    assert result["rsi14"] == 25 and result["rsiAsOf"] == "2026-10-19"
    assert (
        module().evaluate_sell_risk(
            target(),
            confirmed=confirmed("1.1", 25),
            estimate=quote,
            now=NOW.replace(hour=15),
        )
        is None
    )


@pytest.mark.parametrize(
    "change",
    [
        {"coveragePct": 59},
        {"lowerValue": "1.51", "upperValue": "1.6"},
        {"asOf": NOW.replace(hour=13).isoformat()},
        {"asOf": (NOW + timedelta(minutes=1)).isoformat()},
    ],
)
def test_stale_or_inadequate_estimates_do_not_hide_a_valid_confirmed_quote(change):
    quote = confirmed(
        "1.5", **{"quality": "ESTIMATED", "asOf": NOW.isoformat(), **change}
    )
    result = module().evaluate_sell_risk(
        target(), confirmed=confirmed("1.2"), estimate=quote, now=NOW
    )
    assert result["state"] == "TAKE_PROFIT_T1" and result["basis"] == "CONFIRMED_NAV"


def test_held_fund_without_a_price_plan_can_alert_on_real_current_rsi_only():
    owned = target(relationship=["HELD"], plan=None, planOrigin="NONE")
    before = deepcopy(owned)
    result = module().evaluate_sell_risk(owned, confirmed=confirmed("9", 72), now=NOW)
    assert result["state"] == "RSI_OVERBOUGHT" and result["planOrigin"] == "NONE"
    assert (
        result["stopLossPrice"] is None
        and result["takeProfitT1"] is None
        and result["takeProfitT2"] is None
    )
    assert owned == before
    assert (
        module().evaluate_sell_risk(owned, confirmed=confirmed("9", 20), now=NOW)
        is None
    )


def test_rsi_cannot_come_from_the_frozen_plan_or_another_observation_date():
    owned = target()
    owned["plan"]["indicators"] = {"rsi14": 99}
    assert (
        module().evaluate_sell_risk(owned, confirmed=confirmed("1.1", 20), now=NOW)
        is None
    )
    assert (
        module().evaluate_sell_risk(
            owned, confirmed=confirmed("1.1", 99, rsiAsOf="2026-10-18"), now=NOW
        )
        is None
    )


def study_row(**updates):
    return {
        "track_id": TRACK,
        "version_id": VERSION,
        "enabled": True,
        "created_at": NOW - timedelta(days=1),
        "generation": 1,
        "scope": {"fundCode": "000051", "fundMarket": "FUND_CN"},
        "snapshot": {
            "actionablePlan": target()["plan"],
            "expiresOn": "2026-12-01",
            "thesis": "PRIVATE_NOTE",
        },
        "fund_code": "000051",
        "fund_name": "沪深300联接A",
        "market": "FUND_CN",
        "mapping_count": 1,
        "instrument_id": None,
        "trade_form": "FUND",
        **updates,
    }


def holding_row(code="000051", **updates):
    return {
        "fund_code": code,
        "fund_name": "持有基金",
        "market": "FUND_CN",
        "mapping_count": 1,
        "instrument_id": None,
        "trade_form": "FUND",
        "quantity": "3",
        "data_quality_status": "FRESH",
        "as_of": NOW.date() - timedelta(days=1),
        **updates,
    }


class ActorFacts:
    def __init__(self, studies=(), holdings=(), checks=()):
        self.studies, self.holdings, self.checks = (
            list(studies),
            list(holdings),
            list(checks),
        )
        self.queries = []

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        if "research.study_tracks" in sql:
            assert args[:2] == (TENANT, ACTOR)
            return self.studies
        if "ledger.position_snapshots" in sql:
            assert args[:2] == (TENANT, ACTOR)
            return self.holdings
        if "market.market_observations" in sql:
            return []
        if "reference.trading_calendars" in sql:
            start, end = args[-2:]
            return [
                {
                    "calendar_date": start + timedelta(days=i),
                    "is_trading_day": (start + timedelta(days=i)).weekday() < 5,
                }
                for i in range((end - start).days + 1)
            ]
        if "research.alpha_opportunity_runs" in sql:
            assert args[:2] == (TENANT, ACTOR)
            return self.checks
        return []


@pytest.mark.asyncio
async def test_frozen_tracking_wins_over_today_and_holdings_do_not_require_a_complete_account_valuation():
    other = target()["plan"]
    other["tracks"][0]["takeProfitT1"] = "1.3"
    second = deepcopy(other)
    second.update(subjectId="000216", fundCode="000216")
    conn = ActorFacts([study_row()], [holding_row(), holding_row("000216")])
    daily = {
        "state": "READY",
        "reportRunId": str(uuid4()),
        "report": {"asOf": NOW.isoformat(), "actionablePlans": [other, second]},
    }
    rows = await module().load_risk_targets(conn, TENANT, ACTOR, NOW, daily=daily)
    assert len(rows) == 2
    first = next(row for row in rows if row["fundCode"] == "000051")
    assert first["relationship"] == ["HELD", "TRACKED"]
    assert (
        first["planOrigin"] == "FROZEN_STUDY"
        and first["plan"]["tracks"][0]["takeProfitT1"] == "1.2"
    )
    assert (
        next(row for row in rows if row["fundCode"] == "000216")["planOrigin"]
        == "DAILY_REPORT"
    )
    assert "PRIVATE_NOTE" not in str(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"enabled": False},
        {"market": "HK"},
        {"mapping_count": 2},
        {"currency": "USD"},
        {"snapshot": {"actionablePlan": target()["plan"], "expiresOn": "not-a-date"}},
        {"snapshot": {"actionablePlan": target()["plan"], "expiresOn": "2026-10-19"}},
    ],
)
async def test_disabled_expired_cross_market_or_ambiguous_tracking_is_not_a_personal_relationship(
    change,
):
    conn = ActorFacts(
        [study_row(**change)],
        [
            holding_row(quantity="0"),
            holding_row("000216", as_of=NOW.date() - timedelta(days=30)),
        ],
    )
    assert await module().load_risk_targets(conn, TENANT, ACTOR, NOW) == []


@pytest.mark.asyncio
async def test_personal_read_uses_real_cached_nav_rsi_and_never_returns_private_details(
    monkeypatch,
):
    from backend.investment import discovery

    days = [
        NOW.date() - timedelta(days=i)
        for i in range(400, 0, -1)
        if (NOW.date() - timedelta(days=i)).weekday() < 5
    ][-250:]
    history = {
        "fundCode": "000051",
        "asOf": str(days[-1]),
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "priceBasis": "UNIT_NAV",
        "sourceUri": "https://fund.eastmoney.com/000051.html",
        "points": [
            {"date": str(day), "close": str(1 + i / 1000)} for i, day in enumerate(days)
        ],
    }

    async def cached(conn, codes, now):
        assert codes == ["000051"]
        return {"000051": history}

    monkeypatch.setattr(discovery, "_cached_histories", cached)
    conn = ActorFacts(holdings=[holding_row()])
    result = await module().sell_risk_alerts(conn, TENANT, ACTOR, NOW)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["state"] == "RSI_OVERBOUGHT" and item["rsi14"] == 100
    assert item["rsiAsOf"] == str(days[-1]) and item["planOrigin"] == "NONE"
    assert (
        "quantity" not in str(result)
        and "account" not in str(result)
        and "PRIVATE" not in str(result)
    )


@pytest.mark.asyncio
async def test_opportunity_context_exposes_private_risk_without_modifying_public_daily(
    monkeypatch,
):
    from test_opportunity_context import dependencies
    from backend.investment import discovery

    service = dependencies(monkeypatch)
    days = [
        NOW.date() - timedelta(days=i)
        for i in range(400, 0, -1)
        if (NOW.date() - timedelta(days=i)).weekday() < 5
    ][-250:]
    history = {
        "fundCode": "000051",
        "asOf": str(days[-1]),
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "priceBasis": "UNIT_NAV",
        "sourceUri": "https://fund.eastmoney.com/000051.html",
        "points": [
            {"date": str(day), "close": str(1 + i / 1000)} for i, day in enumerate(days)
        ],
    }

    async def cached(conn, codes, now):
        return {"000051": history}

    monkeypatch.setattr(discovery, "_cached_histories", cached)
    conn = ActorFacts(holdings=[holding_row()])
    result = await service.get_context(conn, TENANT, ACTOR, now=NOW)
    assert result["sellRiskAlerts"]["items"][0]["state"] == "RSI_OVERBOUGHT"
    assert result["portfolio"]["state"] == "UNKNOWN"
    assert result["dailyResearch"] == {
        "methodVersion": "daily-research-v1",
        "state": "NOT_RUN",
        "report": None,
    }
    assert "sellRiskAlerts" not in result["dailyResearch"]


@pytest.mark.asyncio
async def test_1430_adds_t2_to_one_existing_actor_message(monkeypatch):
    from test_alpha_opportunities import (
        install,
        settings,
        NOW as clock,
        TRACK as existing_track,
        quote,
    )

    job, db, send = install(monkeypatch)

    async def prices(pool, studies, now):
        return {str(existing_track): quote(value="1.3")}

    result = await job.sync_alpha_opportunities(
        object(), settings(), clock, now_provider=lambda: clock, price_loader=prices
    )
    assert result["fund1430"]["sellRiskAlerts"][0]["state"] == "TAKE_PROFIT_T2"
    assert send.await_count == 1
    assert "目标二" in send.await_args.args[1]
    assert "PRIVATE_THESIS" not in send.await_args.args[1]


@pytest.mark.asyncio
async def test_1430_keeps_public_buy_checks_but_suppresses_unowned_public_sell_points(
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from test_alpha_opportunities import (
        install,
        settings,
        NOW as clock,
        quote,
        study,
    )

    job, db, send = install(monkeypatch)
    db.studies = []
    public = {
        **study(),
        "track_id": "daily-public",
        "planOrigin": "DAILY_REPORT",
        "source": "DAILY_RESEARCH",
        "reportRunId": str(uuid4()),
    }
    monkeypatch.setattr(job, "_daily_plan_rows", AsyncMock(return_value=[public]))

    async def prices(pool, studies, now):
        return {"daily-public": quote(value="1.3")}

    result = await job.sync_alpha_opportunities(
        object(), settings(), clock, now_provider=lambda: clock, price_loader=prices
    )
    assert result["fund1430"]["sellRiskAlerts"] == []
    assert result["fund1430"]["checks"][0]["state"] == "WATCH"
    assert "达到冻结目标一" not in send.await_args.args[1]

    async def entry_prices(pool, studies, now):
        return {"daily-public": quote(value="1.01")}

    db.claims.clear()
    entry = await job.sync_alpha_opportunities(
        object(),
        settings(),
        clock,
        now_provider=lambda: clock,
        price_loader=entry_prices,
    )
    assert entry["fund1430"]["checks"][0]["state"] == "IN_ENTRY_ZONE"


@pytest.mark.asyncio
async def test_1430_rechecks_tracking_authority_after_the_estimate_finishes(
    monkeypatch,
):
    from test_alpha_opportunities import (
        install,
        settings,
        NOW as clock,
        TRACK as existing_track,
        quote,
    )

    job, db, send = install(monkeypatch)

    async def prices(pool, studies, now):
        db.studies = []
        return {str(existing_track): quote(value="1.3")}

    result = await job.sync_alpha_opportunities(
        object(), settings(), clock, now_provider=lambda: clock, price_loader=prices
    )
    assert result["fund1430"]["status"] == "NO_ALERT"
    assert result["fund1430"]["sellRiskAlerts"] == []
    assert send.await_count == 0


@pytest.mark.asyncio
async def test_personal_read_reuses_only_the_current_frozen_version_estimate():
    saved = {
        **confirmed(
            "1.4", quality="ESTIMATED", asOf=NOW.replace(minute=30).isoformat()
        ),
        "trackId": str(TRACK),
        "versionId": str(VERSION),
        "reportRunId": None,
        "planOrigin": "FROZEN_STUDY",
        "plan": target()["plan"],
    }
    row = {
        "run_date": NOW.date(),
        "finished_at": NOW,
        "result": {"riskEstimates": [saved]},
    }
    conn = ActorFacts([study_row()], checks=[row])
    result = await module().sell_risk_alerts(conn, TENANT, ACTOR, NOW)
    assert result["items"][0]["state"] == "TAKE_PROFIT_T2"
    assert result["items"][0]["basis"] == "ESTIMATE_1430"
    saved["versionId"] = str(uuid4())
    assert (await module().sell_risk_alerts(conn, TENANT, ACTOR, NOW))["items"] == []


@pytest.mark.asyncio
async def test_1430_held_fund_without_plan_uses_confirmed_rsi_without_requesting_a_fake_estimate(
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from test_alpha_opportunities import install, settings, NOW as clock, INSTRUMENT
    from backend.investment import discovery

    job, db, send = install(monkeypatch)
    db.studies = []
    fetch = db.fetch

    async def facts(sql, *args):
        if "ledger.position_snapshots" in sql:
            return [
                holding_row(
                    "110022",
                    instrument_id=INSTRUMENT,
                    as_of=clock.date() - timedelta(days=3),
                )
            ]
        if "reference.trading_calendars" in sql:
            start, end = args[-2:]
            return [
                {
                    "calendar_date": start + timedelta(days=i),
                    "is_trading_day": (start + timedelta(days=i)).weekday() < 5,
                }
                for i in range((end - start).days + 1)
            ]
        return await fetch(sql, *args)

    db.fetch = facts
    days = [
        clock.date() - timedelta(days=i)
        for i in range(400, 0, -1)
        if (clock.date() - timedelta(days=i)).weekday() < 5
    ][-250:]

    async def cached(conn, codes, now):
        return {
            "110022": {
                "fundCode": "110022",
                "asOf": str(days[-1]),
                "retrievedAt": clock.isoformat(),
                "availableAt": clock.isoformat(),
                "priceBasis": "UNIT_NAV",
                "sourceUri": "https://fund.eastmoney.com/110022.html",
                "points": [
                    {"date": str(day), "close": str(1 + i / 1000)}
                    for i, day in enumerate(days)
                ],
            }
        }

    monkeypatch.setattr(discovery, "_cached_histories", cached)
    prices = AsyncMock(
        side_effect=AssertionError(
            "no plan must not cause an invented price-plan request"
        )
    )
    result = await job.sync_alpha_opportunities(
        object(), settings(), clock, now_provider=lambda: clock, price_loader=prices
    )
    assert result["fund1430"]["sellRiskAlerts"][0]["state"] == "RSI_OVERBOUGHT"
    assert result["fund1430"]["sellRiskAlerts"][0]["planOrigin"] == "NONE"
    assert send.await_count == 1 and "RSI14" in send.await_args.args[1]
    assert prices.await_count == 0


@pytest.mark.asyncio
async def test_qdii_positive_holding_is_not_erased_by_the_sse_nav_watermark(
    monkeypatch,
):
    from backend.investment import discovery
    from backend.integrations.nav_history_proof import nav_points_digest

    clock = datetime(2026, 10, 21, 14, 40, tzinfo=NOW.tzinfo)
    last = clock.date() - timedelta(days=5)
    days = [
        last - timedelta(days=i)
        for i in range(350, -1, -1)
        if (last - timedelta(days=i)).weekday() < 5
    ][-250:]
    points = [
        {"date": str(day), "close": str(1 + i / 1000)} for i, day in enumerate(days)
    ]
    proof = {
        "methodVersion": "eastmoney-nav-history-v1",
        "fundCode": "161129",
        "sourceUri": "https://api.fund.eastmoney.com/f10/lsjz",
        "queryStart": str(days[0]),
        "queryEnd": str(clock.date()),
        "totalCount": len(points),
        "receivedRowCount": len(points),
        "uniquePointCount": len(points),
        "pointCount": len(points),
        "pageCount": 1,
        "requiredPages": 1,
        "complete": True,
        "firstDate": str(days[0]),
        "lastDate": str(days[-1]),
        "pointsDigest": nav_points_digest(points),
        "retrievedAt": clock.isoformat(),
    }
    history = {
        "fundCode": "161129",
        "asOf": str(last),
        "retrievedAt": clock.isoformat(),
        "availableAt": clock.isoformat(),
        "priceBasis": "UNIT_NAV",
        "sourceUri": "https://fund.eastmoney.com/161129.html",
        "overseasCalendarRequired": True,
        "providerHistoryProof": proof,
        "points": points,
    }

    async def cached(conn, codes, now):
        return {"161129": history}

    monkeypatch.setattr(discovery, "_cached_histories", cached)
    conn = ActorFacts(holdings=[holding_row("161129", as_of=last)])
    result = await module().sell_risk_alerts(conn, TENANT, ACTOR, clock)
    assert result["items"][0]["state"] == "RSI_OVERBOUGHT"
    assert result["items"][0]["relationship"] == ["HELD"]
    assert result["items"][0]["holdingsAsOf"] == "2026-10-16"
    conn.holdings[0]["quantity"] = "0"
    assert (await module().sell_risk_alerts(conn, TENANT, ACTOR, clock))["items"] == []
