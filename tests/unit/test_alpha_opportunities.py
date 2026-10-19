"""Isolated actor-scoped, permanently consumed opportunity reminder slots."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from importlib import import_module, util
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 19, 14, 30, tzinfo=TZ)
TENANT, USER, INSTRUMENT, TRACK, VERSION = [uuid4() for _ in range(5)]


def module():
    assert util.find_spec("backend.jobs.alpha_opportunities"), (
        "alpha opportunity job missing"
    )
    return import_module("backend.jobs.alpha_opportunities")


def settings(**changes):
    return SimpleNamespace(
        **{
            "alpha_opportunity_enabled": True,
            "wecom_opportunity_webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fixture",
            "opportunity_tenant_id": str(TENANT),
            "opportunity_user_id": str(USER),
            **changes,
        }
    )


def plan(**changes):
    return {
        "subjectKind": "fund",
        "subjectId": str(INSTRUMENT),
        "label": "公共基金名",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "asOf": "2026-10-16",
        "currentPrice": "1.00",
        "selectedTrack": "LEFT_VALUE",
        "methodVersion": "zones-v2",
        "tracks": [
            {
                "track": "LEFT_VALUE",
                "state": "CANDIDATE",
                "entryZoneMin": "0.98",
                "entryZoneMax": "1.02",
                "stopLossPrice": "0.9",
                "takeProfitT1": "1.1",
                "takeProfitT2": "1.2",
            }
        ],
        **changes,
    }


def study(**changes):
    return {
        "track_id": TRACK,
        "version_id": VERSION,
        "generation": 2,
        "scope": {
            "instrumentId": str(INSTRUMENT),
            "fundCode": "110022",
            "fundMarket": "FUND_CN",
        },
        "snapshot": {
            "actionablePlan": plan(),
            "thesis": "PRIVATE_THESIS_NEVER_PUSH",
            "expiresOn": "2026-11-30",
        },
        "instrument_id": INSTRUMENT,
        "asset_type": "OFF_EXCHANGE_FUND",
        "trade_form": "FUND",
        "fund_code": "110022",
        "market": "FUND_CN",
        "fund_name": "公共基金名",
        "mapping_count": 1,
        **changes,
    }


def quote(**changes):
    return {
        "subjectKind": "fund",
        "subjectId": str(INSTRUMENT),
        "priceBasis": "UNIT_NAV",
        "unit": "元/份",
        "value": "1.01",
        "asOf": NOW.isoformat(),
        "quality": "ESTIMATED",
        "sourceUri": "https://example.org/nav",
        **changes,
    }


class FakeDatabase:
    def __init__(self):
        self.claims = {}
        self.active = True
        self.trading = True
        self.ready_nav = False
        self.report = None
        self.studies = [study()]
        self.actor_scopes = []
        self.events = []
        self.excursions = []
        self.public_batch = None

    async def fetchval(self, sql, *args):
        if "identity.tenant_members" in sql:
            assert args[:2] == (TENANT, USER)
            return self.active
        if "market.market_observations" in sql:
            return self.ready_nav
        if "max(calendar_date)" in sql:
            return date(2026, 10, 16)
        if "reference.trading_calendars" in sql:
            return self.trading
        return None

    async def fetchrow(self, sql, *args):
        if "INSERT INTO research.alpha_opportunity_runs" in sql:
            tenant, user, day, slot = args[:4]
            key = tenant, user, day, slot
            if key in self.claims:
                return None
            row = {"id": uuid4(), "status": "RUNNING", "result": {}}
            self.claims[key] = row
            self.events.append(("claim", slot))
            return row
        if "daily_research" in sql:
            return self.report
        if "market.alpha_public_batches" in sql:
            return self.public_batch
        return None

    async def fetch(self, sql, *args):
        if "research.study_tracks" in sql:
            assert args[:2] == (TENANT, USER)
            return self.studies
        return []

    async def execute(self, sql, *args):
        if "UPDATE research.alpha_opportunity_runs" in sql:
            run_id, tenant, user, status, finished_at, result = args
            assert (tenant, user) == (TENANT, USER)
            row = next(value for value in self.claims.values() if value["id"] == run_id)
            row.update(status=status, result=json.loads(result))
            self.events.append(("finish", status))
        elif "INSERT INTO research.study_excursions" in sql:
            self.excursions.append(args)
        return "UPDATE 1"


def install(monkeypatch):
    job = module()
    db = FakeDatabase()

    @asynccontextmanager
    async def system(pool):
        yield db

    @asynccontextmanager
    async def actor(pool, user, tenant):
        db.actor_scopes.append((tenant, user))
        yield db

    monkeypatch.setattr(job, "system_connection", system)
    monkeypatch.setattr(job, "tenant_transaction", actor)
    send = AsyncMock(return_value="accepted")
    monkeypatch.setattr(job, "send_opportunity_webhook", send)
    return job, db, send


@pytest.mark.asyncio
async def test_claim_commits_before_price_loading_and_second_tick_never_replays(
    monkeypatch,
):
    job, db, send = install(monkeypatch)

    async def load(pool, studies, now):
        assert db.events == [("claim", "FUND_1430")]
        assert studies[0]["track_id"] == TRACK
        return {str(TRACK): quote()}

    first = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=load
    )
    second = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=AsyncMock()
    )
    assert first["fund1430"]["status"] == "SENT"
    assert second["fund1430"]["status"] == "SKIPPED"
    assert send.await_count == 1
    text = send.call_args.args[1]
    assert "110022" in text and "入场区间" in text
    assert "PRIVATE_THESIS" not in text
    assert db.actor_scopes and set(db.actor_scopes) == {(TENANT, USER)}


@pytest.mark.parametrize("stage", ["loader", "sender"])
@pytest.mark.asyncio
async def test_a_failed_attempt_consumes_the_slot_permanently(monkeypatch, stage):
    job, db, send = install(monkeypatch)
    loader = AsyncMock(return_value={str(TRACK): quote()})
    if stage == "loader":
        loader.side_effect = RuntimeError("private failure")
    else:
        send.side_effect = TimeoutError("private webhook key")
    first = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=loader
    )
    second = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=loader
    )
    assert first["fund1430"]["status"] == "FAILED"
    assert second["fund1430"]["status"] == "SKIPPED"
    assert loader.await_count == 1
    assert next(iter(db.claims.values()))["status"] == "FAILED"
    assert "private" not in json.dumps(next(iter(db.claims.values()))["result"])


@pytest.mark.parametrize(
    "moment,trading",
    [
        (NOW.replace(minute=29), True),
        (NOW.replace(hour=15, minute=0), True),
        (NOW, False),
        (NOW, None),
    ],
)
@pytest.mark.asyncio
async def test_closed_or_unknown_market_and_outside_cutoff_never_fetch_or_send(
    monkeypatch, moment, trading
):
    job, db, send = install(monkeypatch)
    db.trading = trading
    loader = AsyncMock()
    await job.sync_alpha_opportunities(
        object(), settings(), moment, now_provider=lambda: moment, price_loader=loader
    )
    assert not db.claims
    loader.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_price_loading_crossing_1500_expires_without_sending(monkeypatch):
    job, db, send = install(monkeypatch)
    start = NOW.replace(minute=59)
    result = await job.sync_alpha_opportunities(
        object(),
        settings(),
        start,
        now_provider=lambda: start.replace(hour=15, minute=0),
        price_loader=AsyncMock(
            return_value={str(TRACK): quote(asOf=start.isoformat())}
        ),
    )
    assert result["fund1430"]["status"] == "EXPIRED"
    assert next(iter(db.claims.values()))["status"] == "EXPIRED"


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("rolling_base", "UNKNOWN"),
        ("missing_frozen_day", "UNKNOWN"),
        ("missing_frozen_nav", "UNKNOWN"),
        ("missing_latest_nav", "UNKNOWN"),
        ("duplicate_frozen_day", "UNKNOWN"),
        ("none", "WATCH"),
    ],
)
@pytest.mark.asyncio
async def test_real_return_history_must_preserve_frozen_coordinate_and_nav_pair(
    monkeypatch, fault, expected
):
    from backend.market.research_history import fund_history

    job, db, send = install(monkeypatch)
    raw = [
        {"净值日期": "2026-10-14", "单位净值": "1.00", "日增长率": 0},
        {"净值日期": "2026-10-15", "单位净值": "1.01", "日增长率": 1},
        {"净值日期": "2026-10-16", "单位净值": "0.9999", "日增长率": -1},
    ]
    old, _ = fund_history(
        raw, return_verified=True, start=date(2026, 10, 14), end=date(2026, 10, 15)
    )
    current, _ = fund_history(
        raw,
        return_verified=True,
        start=date(2026, 10, 15 if fault == "rolling_base" else 14),
        end=date(2026, 10, 16),
    )
    if fault == "missing_frozen_day":
        current = [point for point in current if point["date"] != "2026-10-15"]
    elif fault == "missing_frozen_nav":
        current[1]["unitNav"] = None
    elif fault == "missing_latest_nav":
        current[-1]["unitNav"] = None
    elif fault == "duplicate_frozen_day":
        current.append({**current[1], "close": 999})
    frozen = plan(
        priceBasis="PROVIDER_RETURN_INDEX",
        unit="return_index",
        asOf="2026-10-15",
        currentPrice=str(old[-1]["close"]),
        tracks=[
            {
                "track": "LEFT_VALUE",
                "state": "WATCH",
                "entryZoneMin": "100",
                "entryZoneMax": "100.5",
            }
        ],
    )
    source = "https://example.org/nav"
    row = study(
        snapshot={"actionablePlan": frozen},
        instrument_metadata={
            "research": {
                "sourceUri": source,
                "sourceVerified": True,
                "returnSeriesVerified": True,
            }
        },
    )
    db.fetchrow = AsyncMock(
        return_value={
            "points": current,
            "series_kind": "PROVIDER_RETURN_INDEX",
            "source_uri": source,
            "result": {"returnSeriesVerified": True, "dataQuality": "READY"},
        }
    )
    pair = await job._conversion_pair(object(), row, NOW)
    checked = job.compare_frozen_price(
        frozen, quote(value="1.005", conversion=pair), NOW
    )
    assert checked["state"] == expected
    if fault == "none":
        assert float(checked["price"]) == pytest.approx(100.5)
    else:
        assert not pair
    send.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.parametrize(
    "config,active",
    [
        (settings(opportunity_user_id=""), True),
        (settings(opportunity_tenant_id="bad"), True),
        (settings(alpha_opportunity_enabled=False), True),
        (settings(), False),
    ],
)
@pytest.mark.asyncio
async def test_scope_must_be_enabled_valid_and_an_active_membership(
    monkeypatch, config, active
):
    job, db, send = install(monkeypatch)
    db.active = active
    result = await job.sync_alpha_opportunities(
        object(), config, NOW, now_provider=lambda: NOW, price_loader=AsyncMock()
    )
    assert result["fund1430"]["status"] == "DISABLED"
    assert not db.claims
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_today_ready_morning_report_is_forwarded_once_with_its_actual_id(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    stamp = NOW.replace(hour=8, minute=40)
    report_id = uuid4()
    db.report = {
        "run_id": report_id,
        "slot_date": stamp.date(),
        "recorded_at": stamp,
        "payload": {
            "summary": "公共日报摘要",
            "events": [{"title": "公共事件"}],
            "privateNote": "PRIVATE_NOTE",
        },
    }
    await job.sync_alpha_opportunities(
        object(), settings(), stamp, now_provider=lambda: stamp
    )
    await job.sync_alpha_opportunities(
        object(), settings(), stamp, now_provider=lambda: stamp
    )
    assert send.await_count == 1
    assert "公共日报摘要" in send.call_args.args[1]
    assert "PRIVATE_NOTE" not in send.call_args.args[1]
    row = db.claims[(TENANT, USER, stamp.date(), "MORNING")]
    assert row["result"]["reportRunId"] == str(report_id)


@pytest.mark.asyncio
async def test_an_old_ready_report_cannot_be_forwarded_as_todays_report(monkeypatch):
    job, db, send = install(monkeypatch)
    stamp = NOW.replace(hour=8, minute=40)
    db.report = {
        "run_id": uuid4(),
        "slot_date": stamp.date() - timedelta(days=1),
        "recorded_at": stamp - timedelta(days=1),
        "payload": {"summary": "旧日报"},
    }
    await job.sync_alpha_opportunities(
        object(), settings(), stamp, now_provider=lambda: stamp
    )
    assert not db.claims
    send.assert_not_awaited()


@pytest.mark.parametrize(
    "value,state",
    [
        ("0.89", "STOP_LOSS"),
        ("1.10", "TAKE_PROFIT_T1"),
        ("1.01", "IN_ENTRY_ZONE"),
        ("1.05", "WATCH"),
    ],
)
def test_price_checks_keep_predeclared_nav_coordinates(value, state):
    result = module().compare_frozen_price(plan(), quote(value=value), NOW)
    assert result["state"] == state
    assert result["priceBasis"] == "UNIT_NAV"
    assert result["price"] == value


@pytest.mark.parametrize(
    "changes",
    [
        {"subjectId": str(uuid4())},
        {"subjectKind": "index"},
        {"priceBasis": "ACCUMULATED_NAV"},
        {"unit": "USD"},
        {"value": "NaN"},
        {"value": "0"},
        {"quality": "STALE"},
        {"asOf": "2026-10-16T14:30:00+08:00"},
        {"asOf": "2026-10-19T14:31:00+08:00"},
    ],
)
def test_incomparable_or_unknown_observations_never_trigger_a_price_signal(changes):
    assert (
        module().compare_frozen_price(plan(), quote(**changes), NOW)["state"]
        == "UNKNOWN"
    )


def test_return_index_conversion_requires_a_real_same_subject_pair_without_dividend_conflict():
    frozen = plan(
        priceBasis="PROVIDER_RETURN_INDEX",
        currentPrice="100",
        tracks=[
            {
                "track": "LEFT_VALUE",
                "entryZoneMin": "98",
                "entryZoneMax": "102",
                "stopLossPrice": "90",
                "takeProfitT1": "110",
            }
        ],
    )
    observed = quote(
        conversion={
            "subjectId": str(INSTRUMENT),
            "date": "2026-10-16",
            "close": "100",
            "unitNav": "1",
            "sourceVerified": True,
            "returnSeriesVerified": True,
            "dividendConflict": False,
        }
    )
    result = module().compare_frozen_price(frozen, observed, NOW)
    assert result["state"] == "WATCH" and result["price"] == "101.00"
    assert result["inEntryZone"] is True
    assert result["reason"] == "TECHNICAL_STATE_UNKNOWN"
    assert result["conversionAsOf"] == "2026-10-16"
    assert module().compare_frozen_price(frozen, quote(), NOW)["state"] == "UNKNOWN"
    observed["conversion"]["dividendConflict"] = True
    assert module().compare_frozen_price(frozen, observed, NOW)["state"] == "UNKNOWN"


def test_return_index_declared_units_are_not_compared_directly_to_currency():
    frozen = plan(
        priceBasis="PROVIDER_RETURN_INDEX",
        unit="return_index",
        currentPrice="100",
        tracks=[{"track": "LEFT_VALUE", "entryZoneMin": "98", "entryZoneMax": "102"}],
    )
    pair = {
        "subjectId": str(INSTRUMENT),
        "date": "2026-10-16",
        "close": "100",
        "unitNav": "1",
        "sourceVerified": True,
        "returnSeriesVerified": True,
        "dividendConflict": False,
    }
    assert (
        module().compare_frozen_price(frozen, quote(conversion=pair), NOW)["state"]
        == "WATCH"
    )
    assert (
        module().compare_frozen_price(frozen, quote(conversion=pair, unit="USD"), NOW)[
            "state"
        ]
        == "UNKNOWN"
    )


@pytest.mark.asyncio
async def test_resolved_scope_cannot_check_a_different_frozen_subject(monkeypatch):
    job, db, send = install(monkeypatch)
    db.studies = [study(snapshot={"actionablePlan": plan(subjectId=str(uuid4()))})]
    load = AsyncMock()
    result = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=load
    )
    assert result["fund1430"]["status"] == "NO_ALERT"
    load.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_nav_only_catalog_fund_keeps_code_and_market_identity_without_instrument(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    db.studies = [
        study(
            instrument_id=None,
            asset_type=None,
            scope={"fundCode": "110022", "fundMarket": "FUND_CN"},
            snapshot={"actionablePlan": plan(subjectKind="nav", subjectId="110022")},
        )
    ]
    load = AsyncMock(
        return_value={str(TRACK): quote(subjectKind="nav", subjectId="110022")}
    )
    result = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=load
    )
    assert result["fund1430"]["status"] == "SENT"
    assert result["fund1430"]["checks"][0]["state"] == "IN_ENTRY_ZONE"
    assert load.await_count == 1 and send.await_count == 1


@pytest.mark.parametrize("verified,expected", [(True, "SENT"), (False, "NO_ALERT")])
@pytest.mark.asyncio
async def test_known_theme_graph_allows_only_verified_nav_only_otc_scope(
    monkeypatch, verified, expected
):
    job, db, send = install(monkeypatch)
    db.studies = [
        study(
            mapping_count=0,
            instrument_id=None,
            asset_type=None,
            trade_form=None,
            graph_scope_verified=verified,
            graph_asset_kind="ETF_LINK_FUND",
            scope={"fundCode": "110022", "fundMarket": "FUND_CN"},
            snapshot={"actionablePlan": plan(subjectKind="nav", subjectId="110022")},
        )
    ]
    load = AsyncMock(
        return_value={str(TRACK): quote(subjectKind="nav", subjectId="110022")}
    )
    result = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=load
    )
    assert result["fund1430"]["status"] == expected
    assert load.await_count == int(verified) and send.await_count == int(verified)


def test_confirmed_excursions_use_original_baseline_and_exclude_estimates():
    facts = [
        quote(value="1", asOf="2026-10-16", quality="CONFIRMED"),
        quote(value="1.10", asOf="2026-10-19", quality="CONFIRMED"),
        quote(value="0.95", asOf="2026-10-20", quality="CONFIRMED"),
        quote(value="50", asOf="2026-10-20", quality="ESTIMATED"),
    ]
    result = module().compute_confirmed_excursions(plan(), facts, date(2026, 10, 20))
    assert result["baseAsOf"] == "2026-10-16" and result["basePrice"] == "1.00"
    assert result["mfePct"] == "10.00" and result["maePct"] == "-5.00"
    assert result["sampleCount"] == 2
    assert result["lastAsOf"] == "2026-10-20"
    assert (
        module().compute_confirmed_excursions(
            plan(currentPrice="2"), facts, date(2026, 10, 20)
        )["state"]
        == "UNKNOWN"
    )


@pytest.mark.asyncio
async def test_confirmed_slot_waits_for_actual_nav_then_updates_once_without_delivery(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    assert hasattr(job, "sync_confirmed_excursions"), "confirmed NAV job missing"
    stamp = NOW.replace(hour=18, minute=0)
    loader = AsyncMock(
        return_value={
            str(TRACK): [
                quote(value="1", asOf="2026-10-16", quality="CONFIRMED"),
                quote(value="1.10", asOf="2026-10-19", quality="CONFIRMED"),
            ]
        }
    )
    initial = await job.sync_confirmed_excursions(
        object(), settings(), stamp, now_provider=lambda: stamp, history_loader=loader
    )
    assert initial["status"] == "NOT_READY" and not db.claims
    loader.assert_not_awaited()
    db.ready_nav = True
    first = await job.sync_confirmed_excursions(
        object(), settings(), stamp, now_provider=lambda: stamp, history_loader=loader
    )
    second = await job.sync_confirmed_excursions(
        object(), settings(), stamp, now_provider=lambda: stamp, history_loader=loader
    )
    assert first["status"] == "COMPLETED" and first["updated"] == 1
    assert second["status"] == "SKIPPED"
    assert loader.await_count == 1 and len(db.excursions) == 1
    assert db.excursions[0][:4] == (TENANT, USER, TRACK, VERSION)
    assert db.excursions[0][4] == date(2026, 10, 16)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_morning_can_first_consume_the_previous_trading_day_confirmation(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    assert hasattr(job, "sync_confirmed_excursions"), "confirmed NAV job missing"
    db.ready_nav = True
    earlier = NOW.replace(hour=8, minute=0)
    db.studies = [
        study(
            snapshot={
                "actionablePlan": plan(asOf="2026-10-15"),
                "expiresOn": "2026-11-30",
            }
        )
    ]
    loader = AsyncMock(
        return_value={
            str(TRACK): [
                quote(value="1", asOf="2026-10-15", quality="CONFIRMED"),
                quote(value="0.95", asOf="2026-10-16", quality="CONFIRMED"),
            ]
        }
    )
    result = await job.sync_confirmed_excursions(
        object(),
        settings(),
        earlier,
        now_provider=lambda: earlier,
        history_loader=loader,
    )
    assert result["status"] == "COMPLETED"
    assert (TENANT, USER, date(2026, 10, 16), "CONFIRMED_NAV") in db.claims
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_public_history_confirms_nonheld_fund_and_retains_partial_gaps(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    stamp = NOW.replace(hour=19, minute=0)
    missing_id = uuid4()
    db.studies.append(
        study(
            track_id=missing_id,
            fund_code="000001",
            instrument_id=uuid4(),
            scope={"fundCode": "000001", "fundMarket": "FUND_CN"},
            snapshot={"actionablePlan": plan(subjectKind="nav", subjectId="000001")},
        )
    )
    db.public_batch = {
        "completed_at": stamp,
        "payload": {
            "fundHistories": [
                {
                    "fundCode": "110022",
                    "asOf": "2026-10-19",
                    "sourceUri": "https://fund.example.org/110022",
                    "retrievedAt": stamp.isoformat(),
                    "availableAt": stamp.isoformat(),
                    "priceBasis": "UNIT_NAV",
                    "points": [
                        {"date": "2026-10-16", "close": "1.00"},
                        {"date": "2026-10-19", "close": "1.05"},
                    ],
                }
            ]
        },
    }
    result = await job.sync_confirmed_excursions(
        object(), settings(), stamp, now_provider=lambda: stamp
    )
    assert result["status"] == "COMPLETED" and result["updated"] == 1
    assert result["missing"] == 1 and result["gaps"][0]["state"] != "CONFIRMED"
    assert len(db.excursions) == 1 and db.excursions[0][9] == 5
    send.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        {"availableAt": "2026-10-20T08:30:00+08:00"},
        {"priceBasis": "ESTIMATED_NAV"},
        {"fundCode": "000001"},
    ],
)
@pytest.mark.asyncio
async def test_future_estimated_or_wrong_fund_public_history_cannot_consume_confirmation(
    monkeypatch, change
):
    job, db, send = install(monkeypatch)
    stamp = NOW.replace(hour=19)
    db.public_batch = {
        "completed_at": stamp,
        "payload": {
            "fundHistories": [
                {
                    "fundCode": "110022",
                    "asOf": "2026-10-19",
                    "sourceUri": "https://fund.example.org/110022",
                    "retrievedAt": stamp.isoformat(),
                    "availableAt": stamp.isoformat(),
                    "priceBasis": "UNIT_NAV",
                    "points": [
                        {"date": "2026-10-16", "close": "1.00"},
                        {"date": "2026-10-19", "close": "1.05"},
                    ],
                    **change,
                }
            ]
        },
    }
    result = await job.sync_confirmed_excursions(
        object(), settings(), stamp, now_provider=lambda: stamp
    )
    assert result["status"] == "NOT_READY" and not db.claims
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_estimate_loader_fetches_only_governed_scoped_otc_codes(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    from backend.integrations import market_data

    observed = SimpleNamespace(
        symbol="110022",
        value="1.01",
        as_of=NOW,
        quality_status="ESTIMATED",
        unit="元/份",
        origin_uri="https://example.org/estimate",
    )
    fetch = AsyncMock(return_value=[observed])
    monkeypatch.setattr(market_data, "fetch_fund_intraday_estimates", fetch)
    result = await job.load_opportunity_prices(
        object(), [study(), study(fund_code="SHOULD_NOT_FETCH", mapping_count=0)], NOW
    )
    fetch.assert_awaited_once_with(["110022"])
    assert result[str(TRACK)]["subjectId"] == str(INSTRUMENT)
    assert result[str(TRACK)]["priceBasis"] == "UNIT_NAV"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_stops_cooperatively_and_keeps_confirmation_independent(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    assert hasattr(job, "run_alpha_opportunity_service"), "alpha service missing"
    import asyncio

    stop = asyncio.Event()
    tick = AsyncMock(side_effect=RuntimeError("one tick failed"))
    confirmed = AsyncMock()
    monkeypatch.setattr(job, "sync_alpha_opportunities", tick)
    monkeypatch.setattr(job, "sync_confirmed_excursions", confirmed)

    async def wait(event, seconds):
        assert seconds == 60
        event.set()

    await job.run_alpha_opportunity_service(
        object(), settings(), now_provider=lambda: NOW, stop_event=stop, wait=wait
    )
    assert tick.await_count == 1 and confirmed.await_count == 1


@pytest.mark.asyncio
async def test_fifteen_minute_checks_skip_only_otc_price_plans(monkeypatch):
    from backend.jobs import study_tracking

    conn = AsyncMock()
    base = {"id": TRACK, "tenant_id": TENANT, "user_id": USER, "generation": 2}
    conn.fetch.return_value = [
        {
            **base,
            "asset_type": "OFF_EXCHANGE_FUND",
            "latest_snapshot": {"actionablePlan": plan()},
        },
        {
            **base,
            "id": uuid4(),
            "asset_type": "OFF_EXCHANGE_FUND",
            "latest_snapshot": {"thesis": "ordinary macro study"},
        },
        {
            **base,
            "id": uuid4(),
            "asset_type": "EXCHANGE_FUND",
            "trade_form": "ETF",
            "latest_snapshot": {"actionablePlan": plan()},
        },
        {
            **base,
            "id": uuid4(),
            "asset_type": "EXCHANGE_FUND",
            "trade_form": "LOF",
            "latest_snapshot": {"actionablePlan": plan()},
        },
        {
            **base,
            "id": uuid4(),
            "graph_scope_verified": True,
            "latest_snapshot": {
                "actionablePlan": plan(subjectKind="nav", subjectId="008281")
            },
        },
        {
            **base,
            "id": uuid4(),
            "graph_scope_verified": False,
            "latest_snapshot": {
                "actionablePlan": plan(subjectKind="nav", subjectId="999999")
            },
        },
    ]

    @asynccontextmanager
    async def connection(*args):
        yield conn

    checker = AsyncMock(return_value={"stored": True})
    monkeypatch.setattr(study_tracking, "system_connection", connection)
    monkeypatch.setattr(study_tracking, "tenant_transaction", connection)
    monkeypatch.setattr(study_tracking, "check_study", checker)
    result = await study_tracking.sync_study_tracking(object(), NOW)
    assert result == {"checked": 3, "stored": 3, "failed": 0}
    assert [call.args[3] for call in checker.await_args_list] == [
        conn.fetch.return_value[1]["id"],
        conn.fetch.return_value[2]["id"],
        conn.fetch.return_value[5]["id"],
    ]


@pytest.mark.parametrize(
    "observed_at",
    [
        "2026-10-19T09:31:00+08:00",
        "2026-10-19T13:59:59+08:00",
        "2026-10-19T14:00:00+08:00",
    ],
)
def test_intraday_estimate_requires_afternoon_time_and_at_most_thirty_minutes(
    observed_at,
):
    checked = module().compare_frozen_price(
        plan(), quote(asOf=observed_at), NOW.replace(minute=31)
    )
    assert checked["state"] == "UNKNOWN"
    assert checked["reason"] == "ESTIMATE_STALE"


def test_estimate_at_thirty_minute_boundary_retains_disclosed_interval_and_coverage():
    checked = module().compare_frozen_price(
        plan(),
        quote(
            asOf=NOW.replace(hour=14, minute=0).isoformat(),
            lowerValue="0.99",
            upperValue="1.03",
            coveragePct="85.5",
        ),
        NOW,
    )
    assert checked["state"] == "IN_ENTRY_ZONE"
    assert (checked["lowerValue"], checked["upperValue"], checked["coveragePct"]) == (
        "0.99",
        "1.03",
        "85.5",
    )
    rows = [{**study(), "check": checked}]
    text = module()._fund_message(rows, NOW.date())
    assert "估算范围 0.9900–1.0300 元/份" in text and "覆盖率 85.50%" in text
    assert "可能误差" in text


def test_provider_unknown_interval_and_coverage_are_explicit_in_the_message():
    checked = module().compare_frozen_price(plan(), quote(), NOW)
    text = module()._fund_message([{**study(), "check": checked}], NOW.date())
    assert "估算范围 未提供" in text and "覆盖率 未提供" in text
    assert "可能误差 未知" in text


@pytest.mark.parametrize("coverage", ["0", "59.99", "-1", "101", "NaN"])
def test_known_inadequate_or_invalid_coverage_cannot_emit_a_hit(coverage):
    checked = module().compare_frozen_price(plan(), quote(coveragePct=coverage), NOW)
    assert checked["state"] == "UNKNOWN"
    assert checked["reason"] == "ESTIMATE_COVERAGE_INSUFFICIENT"


@pytest.mark.asyncio
async def test_stale_database_estimate_does_not_block_the_once_per_slot_fresh_fetch(
    monkeypatch,
):
    job, db, _ = install(monkeypatch)
    original = db.fetchrow

    async def fetchrow(sql, *args):
        if "ESTIMATED_NAV" in sql:
            return {
                "value": "0.89",
                "as_of": NOW.replace(hour=9, minute=31),
                "quality_status": "ESTIMATED",
                "unit": "元/份",
                "source_uri": "https://example.org/estimate",
                "metadata": {},
            }
        return await original(sql, *args)

    db.fetchrow = fetchrow
    fresh = SimpleNamespace(
        symbol="110022",
        value="1.01",
        as_of=NOW,
        quality_status="ESTIMATED",
        unit="元/份",
        origin_uri="https://example.org/fresh",
        lower_value="0.99",
        upper_value="1.03",
        metadata={"coveragePct": "85"},
    )
    fetch = AsyncMock(return_value=[fresh])
    monkeypatch.setattr(
        "backend.integrations.market_data.fetch_fund_intraday_estimates", fetch
    )
    prices = await job.load_opportunity_prices(object(), [study()], NOW)
    fetch.assert_awaited_once_with(["110022"])
    checked = job.compare_frozen_price(plan(), prices[str(TRACK)], NOW)
    assert checked["state"] == "IN_ENTRY_ZONE" and checked["coveragePct"] == "85"
    assert checked["lowerValue"] == "0.99" and checked["upperValue"] == "1.03"


def test_morning_message_includes_cycle_theme_and_server_coordinates_only():
    public_plan = plan(
        subjectKind="nav",
        subjectId="110022",
        fundCode="110022",
        themeCode="AI_MEMORY_HBM",
        priceRevisionStatus="INITIAL",
        notes="PRIVATE_PLAN_NOTES",
        holdings="PRIVATE_HOLDINGS",
        priceRevisionNote="PRIVATE_REVISION_TEXT",
    )
    payload = {
        "summary": "公共摘要",
        "macroCycle": "信用扩张尚待核实",
        "actionablePlans": [public_plan],
        "privateNote": "PRIVATE_NOTE",
    }
    text = module()._morning_message(payload, NOW.date())
    assert "信用扩张尚待核实" in text and "AI算力与HBM/DRAM" in text
    assert "110022" in text and "左侧价值" in text and "单位净值（元/份）" in text
    assert "0.9800–1.0200" in text and "止损 0.9000" in text and "目标一 1.1000" in text
    assert "2026-10-16" in text and "zones-v2" in text
    assert "PRIVATE_" not in text


def test_dynamic_direction_morning_coordinates_are_visible_and_rounded_by_basis():
    nav = plan(
        subjectKind="nav",
        subjectId="000051",
        fundCode="000051",
        label="华夏沪深300ETF联接A",
        valueDirectionKey="core-value-recovery",
        valueDirectionTitle="核心宽基低吸",
        currentPrice="1.2345678901234567",
        tracks=[
            {
                **plan()["tracks"][0],
                "entryZoneMin": "1.2000123456789",
                "entryZoneMax": "1.2400678912345",
                "stopLossPrice": "1.100023456789",
                "takeProfitT1": "1.400078912345",
                "takeProfitT2": "1.500067891234",
            }
        ],
    )
    index = plan(
        subjectKind="index",
        subjectId="hs300",
        label="沪深300",
        priceBasis="INDEX_POINTS",
        unit="points",
        currentPrice="4170.617899",
        valueDirectionKey="core-value-recovery",
        valueDirectionTitle="核心宽基低吸",
        tracks=[
            {
                **plan()["tracks"][0],
                "entryZoneMin": "4116.1234567",
                "entryZoneMax": "4191.7876543",
                "stopLossPrice": "3981.123456",
                "takeProfitT1": "4543.456789",
                "takeProfitT2": "4679.987654",
            }
        ],
    )
    text = module()._morning_message({"actionablePlans": [nav, index]}, NOW.date())
    assert "核心宽基低吸" in text and "000051" in text and "hs300" in text
    assert "1.2000–1.2401" in text and "1.1000" in text
    assert "4116.12–4191.79" in text and "3981.12" in text
    assert "123456" not in text and "core-value-recovery" not in text


def test_fund_markdown_rounds_prices_ranges_and_coverage_without_changing_comparison():
    frozen = plan(currentPrice="1.00000012345678")
    checked = module().compare_frozen_price(
        frozen,
        quote(
            value="1.0100123456789",
            lowerValue="0.990023456789",
            upperValue="1.030078912345",
            coveragePct="83.123456789",
        ),
        NOW,
    )
    assert checked["price"] == "1.0100123456789"
    text = module()._fund_message([{**study(), "check": checked}], NOW.date())
    assert "0.9900–1.0301" in text and "83.12%" in text
    assert "单位净值（元/份） 1.0100" in text and "0.9800–1.0200" in text
    assert "123456" not in text


@pytest.mark.parametrize(
    "changes",
    [
        {
            "mapping_count": 2,
            "graph_scope_verified": True,
            "graph_asset_kind": "ETF_LINK_FUND",
            "instrument_id": None,
            "asset_type": None,
            "trade_form": None,
            "scope": {"fundCode": "110022", "fundMarket": "FUND_CN"},
        },
        {"scope": {"fundCode": "110022", "fundMarket": "SSE"}},
    ],
)
def test_catalog_ambiguity_or_conflicting_scope_cannot_be_overridden_by_graph(changes):
    assert module()._mapped_otc(study(**changes)) is False


@pytest.mark.asyncio
async def test_dynamic_direction_daily_plan_resolves_unique_catalog_fund_without_theme(
    monkeypatch,
):
    job, db, _ = install(monkeypatch)
    candidate = plan(
        subjectKind="nav",
        subjectId="000051",
        fundCode="000051",
        valueDirectionKey="core-value-recovery",
        valueDirectionTitle="核心宽基低吸",
    )
    db.report = {
        "run_id": uuid4(),
        "slot_date": NOW.date(),
        "recorded_at": NOW,
        "payload": {"actionablePlans": [candidate]},
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "research_fund_catalog" in sql and "research.study_tracks" not in sql:
            return [
                {
                    "asset_code": "000051",
                    "asset_name": "华夏沪深300ETF联接A",
                    "market": "FUND_CN",
                    "instrument_id": None,
                    "mapping_count": 1,
                    "trade_form": "FUND",
                    "asset_type": None,
                    "catalog_present": True,
                    "graph_scope_verified": False,
                }
            ]
        return await original(sql, *args)

    db.fetch = fetch
    rows = await job._daily_plan_rows(object(), NOW)
    assert len(rows) == 1
    assert rows[0]["fund_code"] == "000051" and rows[0]["market"] == "FUND_CN"
    assert (
        rows[0]["snapshot"]["actionablePlan"]["valueDirectionKey"]
        == "core-value-recovery"
    )
    assert rows[0]["graph_scope_verified"] is False


@pytest.mark.asyncio
async def test_dynamic_direction_fund_reaches_single_1430_estimate_and_reminder(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    db.studies = []
    candidate = plan(
        subjectKind="nav",
        subjectId="000051",
        fundCode="000051",
        valueDirectionKey="core-value-recovery",
        valueDirectionTitle="核心宽基低吸",
    )
    db.report = {
        "run_id": uuid4(),
        "slot_date": NOW.date(),
        "recorded_at": NOW,
        "payload": {"actionablePlans": [candidate]},
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "research_fund_catalog" in sql and "research.study_tracks" not in sql:
            return [
                {
                    "asset_code": "000051",
                    "asset_name": "华夏沪深300ETF联接A",
                    "market": "FUND_CN",
                    "instrument_id": None,
                    "mapping_count": 1,
                    "trade_form": "FUND",
                    "asset_type": None,
                    "catalog_present": True,
                    "graph_scope_verified": False,
                }
            ]
        return await original(sql, *args)

    db.fetch = fetch
    prices_for = []

    async def load(pool, rows, stamp):
        prices_for.extend(rows)
        return {
            str(row["track_id"]): quote(subjectKind="nav", subjectId="000051")
            for row in rows
        }

    first = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=load
    )
    second = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=load
    )
    assert (
        first["fund1430"]["checked"] == 1
        and first["fund1430"]["checks"][0]["state"] == "IN_ENTRY_ZONE"
    )
    assert second["fund1430"]["status"] == "SKIPPED"
    assert len(prices_for) == 1 and prices_for[0]["fund_code"] == "000051"
    text = send.call_args.args[1]
    assert "核心宽基低吸" in text and "000051" in text and "1.0100" in text
    assert send.await_count == 2  # daily morning plus one consolidated 14:30 reminder


@pytest.mark.parametrize(
    "plan_changes,row_changes",
    [
        ({"subjectId": "000052", "fundCode": "000052"}, {}),
        ({"market": "SSE"}, {}),
        ({"fundCode": "999999"}, {}),
        ({"valueDirectionKey": "INVALID KEY"}, {}),
        ({}, {"mapping_count": 2, "graph_scope_verified": True}),
        ({}, {"market": "SSE"}),
        ({}, {"trade_form": "ETF", "asset_type": "EXCHANGE_FUND"}),
    ],
)
@pytest.mark.asyncio
async def test_dynamic_direction_title_cannot_certify_unknown_ambiguous_or_exchange_identity(
    monkeypatch, plan_changes, row_changes
):
    job, db, _ = install(monkeypatch)
    candidate = plan(
        subjectKind="nav",
        subjectId="000051",
        fundCode="000051",
        valueDirectionKey="core-value-recovery",
        valueDirectionTitle="核心宽基低吸",
    )
    db.report = {
        "run_id": uuid4(),
        "slot_date": NOW.date(),
        "recorded_at": NOW,
        "payload": {"actionablePlans": [{**candidate, **plan_changes}]},
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "research_fund_catalog" in sql and "research.study_tracks" not in sql:
            return [
                {
                    "asset_code": "000051",
                    "asset_name": "华夏沪深300ETF联接A",
                    "market": "FUND_CN",
                    "instrument_id": None,
                    "mapping_count": 1,
                    "trade_form": "FUND",
                    "asset_type": None,
                    "catalog_present": True,
                    "graph_scope_verified": False,
                    **row_changes,
                }
            ]
        return await original(sql, *args)

    db.fetch = fetch
    assert await job._daily_plan_rows(object(), NOW) == []


@pytest.mark.asyncio
async def test_dynamic_morning_checks_catalog_identity_and_known_index_before_sending(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    stamp = NOW.replace(hour=8, minute=40)
    dynamic = {
        "valueDirectionKey": "new-medical-cycle",
        "valueDirectionTitle": "医疗产业新周期",
    }
    good = plan(subjectKind="nav", subjectId="000051", fundCode="000051", **dynamic)
    unknown = plan(
        subjectKind="nav",
        subjectId="999999",
        fundCode="999999",
        label="伪造的基金名称",
        **dynamic,
    )
    broad = plan(
        subjectKind="index",
        subjectId="hs300",
        priceBasis="INDEX_POINTS",
        unit="points",
        **dynamic,
    )
    db.report = {
        "run_id": uuid4(),
        "slot_date": stamp.date(),
        "recorded_at": stamp,
        "payload": {"actionablePlans": [good, unknown, broad]},
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "research_fund_catalog" in sql and "research.study_tracks" not in sql:
            return [
                {
                    "asset_code": "000051",
                    "asset_name": "华夏沪深300ETF联接A",
                    "market": "FUND_CN",
                    "instrument_id": None,
                    "mapping_count": 1,
                    "trade_form": "FUND",
                    "asset_type": None,
                    "catalog_present": True,
                    "graph_scope_verified": False,
                }
            ]
        if "reference.research_indices" in sql:
            return [{"id": "hs300", "name": "沪深300", "market": "SSE"}]
        return await original(sql, *args)

    db.fetch = fetch
    outcome = await job.sync_alpha_opportunities(
        object(), settings(), stamp, now_provider=lambda: stamp
    )
    assert outcome["morning"]["status"] == "SENT"
    text = send.call_args.args[1]
    assert "医疗产业新周期" in text and "000051" in text and "沪深300(hs300)" in text
    assert "999999" not in text and "伪造" not in text


@pytest.mark.parametrize("change", ["date", "membership"])
@pytest.mark.asyncio
async def test_morning_rechecks_clock_and_membership_after_catalog_lookup(
    monkeypatch, change
):
    job, db, send = install(monkeypatch)
    stamp = NOW.replace(hour=8, minute=40)
    clock = [stamp]
    candidate = plan(
        subjectKind="nav",
        subjectId="000051",
        fundCode="000051",
        valueDirectionKey="core-value-recovery",
        valueDirectionTitle="核心宽基低吸",
    )
    db.report = {
        "run_id": uuid4(),
        "slot_date": stamp.date(),
        "recorded_at": stamp,
        "payload": {"actionablePlans": [candidate]},
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "research_fund_catalog" in sql and "research.study_tracks" not in sql:
            if change == "date":
                clock[0] += timedelta(days=1)
            else:
                db.active = False
            return []
        return await original(sql, *args)

    db.fetch = fetch
    result = await job.sync_alpha_opportunities(
        object(), settings(), stamp, now_provider=lambda: clock[0]
    )
    assert result["morning"]["status"] == "EXPIRED"
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("INVESTMENT_TEST_DATABASE_URL"), reason="isolated DB required"
)
async def test_real_catalog_ambiguity_excludes_public_and_frozen_fund_checks(
    monkeypatch,
):
    import asyncpg
    from backend.common.database import apply_tenant_context

    job = module()
    conn = await asyncpg.connect(os.environ["INVESTMENT_TEST_DATABASE_URL"])
    tx = conn.transaction()
    await tx.start()
    tenant, user, instrument, duplicate, track, version = [uuid4() for _ in range(6)]
    frozen = plan(
        subjectId=str(instrument),
        valueDirectionKey="medical-recovery",
        valueDirectionTitle="医疗服务复苏",
    )
    scope = {
        "instrumentId": str(instrument),
        "fundCode": "989981",
        "fundMarket": "FUND_CN",
    }

    @asynccontextmanager
    async def connection(*args):
        yield conn

    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(job, "tenant_transaction", connection)
    try:
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'isolated alpha identity fixture')",
            tenant,
        )
        await conn.execute(
            "INSERT INTO identity.profiles(id,nickname) VALUES($1,'isolated actor')",
            user,
        )
        await conn.execute(
            "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
            tenant,
            user,
        )
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','真实目录基金')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER','989981','FUND_CN','2026-01-01')",
            instrument,
        )
        await conn.execute(
            "INSERT INTO research.study_tracks(id,tenant_id,user_id,origin_key,title,scope,created_at,updated_at) VALUES($1,$2,$3,'isolated-alpha','隔离跟踪',$4::jsonb,$5,$5)",
            track,
            tenant,
            user,
            json.dumps(scope),
            NOW,
        )
        await conn.execute(
            "INSERT INTO research.study_versions(id,tenant_id,user_id,track_id,version,generation,snapshot,reason,command_key,command_hash,created_at) VALUES($1,$2,$3,$4,1,1,$5::jsonb,'isolated','fixture','hash',$6)",
            version,
            tenant,
            user,
            track,
            json.dumps({"actionablePlan": frozen}),
            NOW,
        )
        await apply_tenant_context(conn, user, tenant)
        known = await job._known_public_plans(
            object(), {"actionablePlans": [frozen]}, NOW
        )
        assert len(known) == 1 and known[0][0]["label"] == "真实目录基金"
        broad = plan(
            subjectKind="index",
            subjectId="hs300",
            priceBasis="INDEX_POINTS",
            unit="points",
            valueDirectionKey="core-value-recovery",
            valueDirectionTitle="核心宽基低吸",
        )
        indices = await job._known_public_plans(
            object(), {"actionablePlans": [broad]}, NOW, funds_only=False
        )
        assert len(indices) == 1 and indices[0][0]["label"] == "沪深300"
        assert (
            await job._known_public_plans(object(), {"actionablePlans": [broad]}, NOW)
            == []
        )
        rows = await job._read_studies(object(), (tenant, user), NOW)
        assert len(rows) == 1 and job._mapped_otc(rows[0])
        await conn.execute("RESET ROLE")
        await conn.execute(
            "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','重复目录身份')",
            duplicate,
        )
        await conn.execute(
            "INSERT INTO reference.instrument_aliases(instrument_id,alias_type,alias_value,market,effective_from) VALUES($1,'TICKER','989981','FUND_CN','2025-01-01')",
            duplicate,
        )
        await apply_tenant_context(conn, user, tenant)
        assert (
            await job._known_public_plans(object(), {"actionablePlans": [frozen]}, NOW)
            == []
        )
        rows = await job._read_studies(object(), (tenant, user), NOW)
        assert len(rows) == 1 and not job._mapped_otc(rows[0])
    finally:
        await tx.rollback()
        await conn.close()


def test_morning_message_does_not_relabel_index_coordinates_as_fund_nav_or_print_bad_plans():
    public_index = plan(
        subjectKind="index",
        subjectId="hs300",
        themeCode="AI_MEMORY_HBM",
        priceBasis="INDEX_POINTS",
        unit="points",
    )
    invalid = [
        plan(themeCode="PRIVATE_THEME"),
        plan(themeCode="AI_MEMORY_HBM", priceBasis="INDEX_POINTS", unit="points"),
        plan(themeCode="AI_MEMORY_HBM", asOf="2026-10-20"),
    ]
    text = module()._morning_message(
        {"summary": "摘要", "actionablePlans": [public_index, *invalid]}, NOW.date()
    )
    assert "指数点（仅研究坐标）" in text and "hs300" in text
    assert "单位净值" not in text and "PRIVATE_THEME" not in text


@pytest.mark.asyncio
async def test_untracked_today_report_fund_is_checked_without_creating_a_private_study(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    db.studies = []
    public_plan = plan(
        subjectKind="nav",
        subjectId="008281",
        fundCode="008281",
        themeCode="AI_MEMORY_HBM",
    )
    db.report = {
        "run_id": uuid4(),
        "slot_date": NOW.date(),
        "recorded_at": NOW,
        "payload": {"summary": "摘要", "actionablePlans": [public_plan]},
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "reference.theme_asset_graph" in sql and "research.study_tracks" not in sql:
            return [
                {
                    "theme_code": "AI_MEMORY_HBM",
                    "asset_code": "008281",
                    "asset_name": "公共芯片基金",
                    "asset_kind": "ETF_LINK_FUND",
                    "market": "FUND_CN",
                    "instrument_id": None,
                    "mapping_count": 0,
                    "trade_form": None,
                    "asset_type": None,
                }
            ]
        return await original(sql, *args)

    db.fetch = fetch
    checked_items = []

    async def load(pool, items, stamp):
        checked_items.extend(items)
        return {
            str(row["track_id"]): quote(subjectKind="nav", subjectId="008281")
            for row in items
        }

    result = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=load
    )
    assert result["fund1430"]["checked"] == 1
    assert checked_items[0]["planOrigin"] == "DAILY_REPORT"
    assert result["fund1430"]["checks"][0]["state"] == "IN_ENTRY_ZONE"
    assert result["fund1430"]["checks"][0]["planOrigin"] == "DAILY_REPORT"
    text = send.call_args_list[-1].args[1]
    assert "今日日报计划" in text and "008281" in text
    assert "PRIVATE_" not in text and not db.studies


@pytest.mark.asyncio
async def test_frozen_subject_track_wins_over_today_plan_and_ungoverned_plans_are_rejected(
    monkeypatch,
):
    job, db, _ = install(monkeypatch)
    db.report = {
        "run_id": uuid4(),
        "slot_date": NOW.date(),
        "recorded_at": NOW,
        "payload": {
            "actionablePlans": [
                plan(
                    subjectKind="nav",
                    subjectId="110022",
                    fundCode="110022",
                    themeCode="AI_MEMORY_HBM",
                ),
                plan(
                    subjectKind="nav",
                    subjectId="999999",
                    fundCode="999999",
                    themeCode="AI_MEMORY_HBM",
                ),
                plan(
                    themeCode="AI_MEMORY_HBM", priceBasis="INDEX_POINTS", unit="points"
                ),
            ]
        },
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "reference.theme_asset_graph" in sql and "research.study_tracks" not in sql:
            return [
                {
                    "theme_code": "AI_MEMORY_HBM",
                    "asset_code": "110022",
                    "asset_name": "公共基金",
                    "asset_kind": "ETF_LINK_FUND",
                    "market": "FUND_CN",
                    "instrument_id": INSTRUMENT,
                    "mapping_count": 1,
                    "trade_form": "FUND",
                    "asset_type": "OFF_EXCHANGE_FUND",
                }
            ]
        return await original(sql, *args)

    db.fetch = fetch
    load = AsyncMock(return_value={str(TRACK): quote()})
    result = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW, price_loader=load
    )
    assert result["fund1430"]["checked"] == 1
    assert load.call_args.args[1][0]["track_id"] == TRACK
    assert result["fund1430"]["checks"][0]["planOrigin"] == "FROZEN_STUDY"


@pytest.mark.parametrize(
    "changes",
    [
        {"subjectKind": "fund", "subjectId": str(uuid4())},
        {"themeCode": "HAINAN_FTP"},
        {"fundCode": "999999"},
        {"priceBasis": "PROVIDER_RETURN_INDEX"},
        {"unit": "USD"},
        {
            "tracks": [
                {
                    "track": "LEFT_VALUE",
                    "state": "CANDIDATE",
                    "entryZoneMin": None,
                    "entryZoneMax": "1.02",
                }
            ]
        },
    ],
)
@pytest.mark.asyncio
async def test_report_plan_rejects_wrong_identity_theme_basis_unit_or_missing_price(
    monkeypatch, changes
):
    job, db, _ = install(monkeypatch)
    candidate = plan(
        subjectKind="nav",
        subjectId="008281",
        fundCode="008281",
        themeCode="AI_MEMORY_HBM",
    )
    db.report = {
        "run_id": uuid4(),
        "slot_date": NOW.date(),
        "recorded_at": NOW,
        "payload": {"actionablePlans": [{**candidate, **changes}]},
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "reference.theme_asset_graph" in sql and "research.study_tracks" not in sql:
            return [
                {
                    "theme_code": "AI_MEMORY_HBM",
                    "asset_code": "008281",
                    "asset_name": "公共基金",
                    "asset_kind": "ETF_LINK_FUND",
                    "market": "FUND_CN",
                    "instrument_id": INSTRUMENT,
                    "mapping_count": 1,
                    "trade_form": "FUND",
                    "asset_type": "OFF_EXCHANGE_FUND",
                }
            ]
        return await original(sql, *args)

    db.fetch = fetch
    assert await job._daily_plan_rows(object(), NOW) == []


@pytest.mark.asyncio
async def test_both_today_tracks_share_one_estimate_request_and_no_slot_replay(
    monkeypatch,
):
    job, db, send = install(monkeypatch)
    db.studies = []
    first = plan(
        subjectKind="nav",
        subjectId="008281",
        fundCode="008281",
        themeCode="AI_MEMORY_HBM",
    )
    second = {
        **first,
        "selectedTrack": "RIGHT_TREND",
        "tracks": [{**first["tracks"][0], "track": "RIGHT_TREND"}],
    }
    db.report = {
        "run_id": uuid4(),
        "slot_date": NOW.date(),
        "recorded_at": NOW,
        "payload": {"actionablePlans": [first, second]},
    }
    original = db.fetch

    async def fetch(sql, *args):
        if "reference.theme_asset_graph" in sql and "research.study_tracks" not in sql:
            return [
                {
                    "theme_code": "AI_MEMORY_HBM",
                    "asset_code": "008281",
                    "asset_name": "公共基金",
                    "asset_kind": "ETF_LINK_FUND",
                    "market": "FUND_CN",
                    "instrument_id": None,
                    "mapping_count": 0,
                    "trade_form": None,
                    "asset_type": None,
                }
            ]
        return await original(sql, *args)

    db.fetch = fetch
    fetch_estimate = AsyncMock(
        return_value=[
            SimpleNamespace(
                symbol="008281",
                value="1.01",
                as_of=NOW,
                quality_status="ESTIMATED",
                unit="元/份",
                origin_uri="https://example.org/estimate",
            )
        ]
    )
    monkeypatch.setattr(
        "backend.integrations.market_data.fetch_fund_intraday_estimates", fetch_estimate
    )
    first_result = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW
    )
    second_result = await job.sync_alpha_opportunities(
        object(), settings(), NOW, now_provider=lambda: NOW
    )
    assert first_result["fund1430"]["checked"] == 2
    assert second_result["fund1430"]["status"] == "SKIPPED"
    fetch_estimate.assert_awaited_once_with(["008281"])
    assert send.await_count == 2  # one morning message and one fund summary


@pytest.mark.asyncio
async def test_real_confirmed_nav_adapter_unit_matches_frozen_cny_coordinates(
    monkeypatch,
):
    import httpx
    from backend.integrations.market_data import fetch_fund_nav_history

    job, db, _ = install(monkeypatch)

    async def provider(request):
        return httpx.Response(
            200,
            json={
                "Data": {
                    "LSJZList": [
                        {"FSRQ": "2026-10-19", "DWJZ": "1.10"},
                        {"FSRQ": "2026-10-16", "DWJZ": "1.00"},
                    ]
                },
                "PageIndex": 1,
                "PageSize": 100,
                "TotalCount": 2,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        actual = await fetch_fund_nav_history(
            "110022", date(2026, 10, 16), date(2026, 10, 19), client, max_pages=1
        )
    assert {item.unit for item in actual} == {"CNY/share"}
    original = db.fetch

    async def fetch(sql, *args):
        if "market.market_observations" in sql:
            return [
                {
                    "value": item.value,
                    "as_of": item.as_of,
                    "unit": item.unit,
                    "currency": item.currency,
                    "quality_status": item.quality_status,
                }
                for item in actual
            ]
        return await original(sql, *args)

    db.fetch = fetch
    histories = await job.load_confirmed_history(
        object(), [study()], date(2026, 10, 19), NOW.replace(hour=19)
    )
    checked = job.compute_confirmed_excursions(
        plan(), histories[str(TRACK)], date(2026, 10, 19)
    )
    assert checked["state"] == "CONFIRMED"
    assert checked["mfePct"] == "10.00" and checked["maePct"] == "0.00"


@pytest.mark.parametrize("slot", ["morning", "fund1430"])
@pytest.mark.asyncio
async def test_final_membership_read_cannot_carry_delivery_past_its_time_window(
    monkeypatch, slot
):
    job, db, send = install(monkeypatch)
    start = NOW.replace(hour=23 if slot == "morning" else 14, minute=59, second=58)
    current = {"now": start, "activeCalls": 0}
    if slot == "morning":
        db.report = {
            "run_id": uuid4(),
            "slot_date": start.date(),
            "recorded_at": start,
            "payload": {"summary": "当日公共摘要"},
        }

    async def active(pool, scope):
        current["activeCalls"] += 1
        if current["activeCalls"] == 2:
            current["now"] = start + timedelta(seconds=3)
        return True

    monkeypatch.setattr(job, "_active", active)
    result = await job.sync_alpha_opportunities(
        object(),
        settings(),
        start,
        now_provider=lambda: current["now"],
        price_loader=AsyncMock(
            return_value={str(TRACK): quote(asOf=start.isoformat())}
        ),
    )
    assert result[slot]["status"] == "EXPIRED"
    send.assert_not_awaited()
    assert next(iter(db.claims.values()))["status"] == "EXPIRED"
