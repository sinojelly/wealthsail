"""Current public entry protections apply to frozen coordinates without moving prices."""

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.investment import study_price_watch as watch
from backend.jobs import alpha_opportunities as job

NOW = datetime(2026, 10, 21, 14, 40, tzinfo=ZoneInfo("Asia/Shanghai"))


def plan(code="161129"):
    return {
        "fundCode": code,
        "subjectKind": "nav",
        "subjectId": code,
        "market": "FUND_CN",
        "label": "易方达原油(QDII)" if code == "161129" else "沪深300联接A",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY/份",
        "asOf": "2026-10-20",
        "currentPrice": "1.5",
        "selectedTrack": "RIGHT_TREND",
        "tracks": [
            {
                "track": "RIGHT_TREND",
                "state": "CANDIDATE",
                "priceState": "READY",
                "entryZoneMin": "0.9",
                "entryZoneMax": "1.1",
                "stopLossPrice": "0.8",
                "takeProfitT1": "1.2",
                "takeProfitT2": "1.4",
            }
        ],
    }


def quote(value="1"):
    return {
        "fundCode": "161129",
        "subjectKind": "nav",
        "subjectId": "161129",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY/份",
        "value": value,
        "quality": "ESTIMATED",
        "sourceUri": "https://fund.eastmoney.com/161129.html",
        "asOf": NOW.replace(minute=30).isoformat(),
        "checkedAt": NOW.isoformat(),
        "retrievedAt": NOW.isoformat(),
        "usable": True,
    }


@pytest.mark.parametrize("surface", ["1430", "tracking"])
@pytest.mark.parametrize(
    "level,key,value",
    [
        ("plan", "entryBlocked", True),
        ("track", "entryBlocked", True),
        ("plan", "subscriptionRestricted", True),
        ("plan", "preferredEntryEligible", False),
    ],
)
def test_hard_entry_protections_block_buy_hits_on_both_surfaces(
    surface, level, key, value
):
    frozen = plan()
    (frozen if level == "plan" else frozen["tracks"][0])[key] = value
    before = deepcopy(frozen)
    if surface == "1430":
        result = job.compare_frozen_price(frozen, quote(), NOW)
        assert result["state"] == "WATCH"
        assert result["reason"] == "ENTRY_PROTECTED"
    else:
        result = watch.price_watch(frozen, estimate=quote(), now=NOW)
        assert result["state"] == "OBSERVING"
        assert "暂缓入场" in result["reason"]
    assert frozen == before


@pytest.mark.parametrize("surface", ["1430", "tracking"])
def test_old_geometric_outside_flags_do_not_prevent_future_actual_entry(surface):
    frozen = plan()
    frozen.update(entryReady=False, inBuyZone=False)
    frozen["tracks"][0].update(entryReady=False, inBuyZone=False)
    result = (
        job.compare_frozen_price(frozen, quote(), NOW)
        if surface == "1430"
        else watch.price_watch(frozen, estimate=quote(), now=NOW)
    )
    assert result["state"] == ("IN_ENTRY_ZONE" if surface == "1430" else "ENTRY_ZONE")


@pytest.mark.parametrize("surface", ["1430", "tracking"])
@pytest.mark.parametrize("value,state", [("0.7", "STOP_LOSS"), ("1.3", "TAKE_PROFIT")])
def test_entry_protection_does_not_suppress_personal_sell_reference_hits(
    surface, value, state
):
    frozen = plan()
    frozen.update(entryBlocked=True, subscriptionRestricted=True)
    frozen["tracks"][0]["entryBlocked"] = True
    if surface == "1430":
        result = job.compare_frozen_price(frozen, quote(value), NOW)
        assert result["state"] == (
            "TAKE_PROFIT_T1" if state == "TAKE_PROFIT" else state
        )
    else:
        result = watch.price_watch(frozen, estimate=quote(value), now=NOW)
        assert result["state"] == state


class SafetyFacts:
    def __init__(self, *, subscription=None, packet=None, payload=None):
        self.subscription = subscription
        self.packet = packet or {}
        self.payload = payload or {}
        self.queries = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        if "reference.fund_subscription_status" in sql:
            return (
                [
                    {
                        "fund_code": "161129",
                        "purchase_status": self.subscription,
                        "daily_purchase_limit": "10"
                        if self.subscription == "LIMITED"
                        else None,
                        "retrieved_at": NOW,
                    }
                ]
                if self.subscription
                else []
            )
        if "research.daily_research_runs" in sql:
            assert "run_mode='SCHEDULED'" not in sql
            return [{"public_packet": self.packet, "payload": self.payload}]
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["SUSPENDED", "LIMITED"])
async def test_current_subscription_overlay_protects_old_frozen_plans_without_changing_coordinates(
    subscription,
):
    frozen = plan()
    before = deepcopy(frozen)
    guarded = (
        await watch.current_entry_safety(
            SafetyFacts(subscription=subscription), [frozen], NOW
        )
    )[0]
    assert guarded["subscriptionRestricted"] is True
    assert not watch.entry_permitted(frozen, frozen["tracks"][0], guarded)
    assert [
        {key: track[key] for key in original}
        for track, original in zip(guarded["tracks"], before["tracks"], strict=True)
    ] == before["tracks"]
    assert guarded["tracks"][0]["entryBlocked"] is True
    assert guarded["tracks"][0]["entryBlockReason"] == "SUBSCRIPTION_RESTRICTED"
    assert frozen == before
    assert guarded["currentPrice"] == "1.5"


@pytest.mark.asyncio
async def test_latest_same_day_debug_public_news_protects_an_old_frozen_qdii_plan():
    frozen = plan()
    packet = {
        "asOf": NOW.isoformat(),
        "documents": [
            {"title": "WTI原油期货下跌3%", "publishedDate": NOW.date().isoformat()}
        ],
    }
    guarded = (
        await watch.current_entry_safety(SafetyFacts(packet=packet), [frozen], NOW)
    )[0]
    assert guarded["entryBlockReason"] == "DELAYED_NAV_MARKET_DROP"
    assert guarded["entryBlocked"] is True
    assert guarded["tracks"][0]["entryZoneMin"] == "0.9"
    assert guarded["asOf"] == "2026-10-20"


@pytest.mark.asyncio
async def test_current_public_high_nav_guard_is_only_metadata_and_cannot_reprice_a_frozen_plan():
    frozen = plan()
    current = plan()
    current.update(currentPrice="4", indicators={"navPercentile1y": 95, "ma20": 3})
    current["tracks"][0].update(
        entryZoneMin="3", entryZoneMax="3.5", stopLossPrice="2.8", takeProfitT1="5"
    )
    guarded = (
        await watch.current_entry_safety(
            SafetyFacts(packet={"quantitativeScan": {"items": [current]}}),
            [frozen],
            NOW,
        )
    )[0]
    assert guarded["entryBlocked"] is True
    assert guarded["entryBlockReason"] == "HIGH_NAV_PERCENTILE"
    for key in ("asOf", "currentPrice", "subjectId", "selectedTrack"):
        assert guarded[key] == frozen[key]
    for key in (
        "entryZoneMin",
        "entryZoneMax",
        "stopLossPrice",
        "takeProfitT1",
        "takeProfitT2",
    ):
        assert guarded["tracks"][0][key] == frozen["tracks"][0][key]


@pytest.mark.asyncio
async def test_1430_rechecks_subscription_changed_during_the_estimate_without_creating_an_extra_message(
    monkeypatch,
):
    from test_alpha_opportunities import (
        install,
        settings,
        NOW as clock,
        TRACK,
        quote as observed,
    )

    task, db, send = install(monkeypatch)
    fetch = db.fetch
    status = None

    async def facts(sql, *args):
        if "reference.fund_subscription_status" in sql:
            return (
                [
                    {
                        "fund_code": "110022",
                        "purchase_status": status,
                        "daily_purchase_limit": None,
                        "retrieved_at": clock,
                    }
                ]
                if status
                else []
            )
        return await fetch(sql, *args)

    db.fetch = facts

    async def prices(pool, studies, stamp):
        nonlocal status
        status = "SUSPENDED"
        return {str(TRACK): observed()}

    result = await task.sync_alpha_opportunities(
        object(), settings(), clock, now_provider=lambda: clock, price_loader=prices
    )
    assert result["fund1430"]["checks"][0]["state"] == "WATCH"
    assert result["fund1430"]["checks"][0]["reason"] == "ENTRY_PROTECTED"
    assert send.await_count == 1
    assert "进入冻结入场区间" not in send.await_args.args[1]
    assert "暂停申购" in send.await_args.args[1]


@pytest.mark.asyncio
async def test_latest_verified_open_subscription_releases_old_purchase_only_restriction():
    frozen = plan()
    frozen.update(
        subscriptionRestricted=True,
        subscriptionStatus={"purchaseStatus": "SUSPENDED"},
        preferredEntryEligible=False,
    )
    guarded = (
        await watch.current_entry_safety(
            SafetyFacts(subscription="OPEN"), [frozen], NOW
        )
    )[0]
    assert guarded["subscriptionRestricted"] is False
    assert watch.entry_permitted(frozen, frozen["tracks"][0], guarded)
    assert frozen["subscriptionRestricted"] is True


@pytest.mark.asyncio
async def test_tracking_read_overlays_current_public_safety_but_keeps_the_original_frozen_plan(
    monkeypatch,
):
    from uuid import uuid4
    from backend.investment import discovery

    frozen = plan()
    before = deepcopy(frozen)
    track, version, tenant, actor = [uuid4() for _ in range(4)]

    async def cached(conn, codes, now):
        return {"161129": {"fundCode": "161129"}}

    monkeypatch.setattr(discovery, "_cached_histories", cached)
    monkeypatch.setattr(
        watch,
        "_confirmed",
        lambda *args: {**quote(), "quality": "READY", "asOf": "2026-10-20"},
    )
    versions = {
        str(track): {
            "id": version,
            "generation": 1,
            "created_at": NOW - timedelta(days=1),
            "snapshot": {"actionablePlan": frozen},
        }
    }
    result = await watch.enrich_tracks(
        SafetyFacts(subscription="SUSPENDED"),
        tenant,
        actor,
        [{"id": str(track), "generation": 1, "scope": {"fundCode": "161129"}}],
        NOW,
        versions=versions,
    )
    assert result[0]["priceWatch"]["state"] == "OBSERVING"
    assert "暂停申购" in result[0]["priceWatch"]["reason"]
    assert result[0]["priceWatch"]["frozenPlan"] == before
    assert frozen == before


def test_current_global_hard_guard_cannot_be_hidden_by_an_old_track_false_flag():
    frozen = plan()
    frozen["tracks"][0]["entryBlocked"] = False
    frozen["tracks"][0]["entryStatus"] = "旧轨状态"
    current = deepcopy(frozen)
    current.update(entryBlocked=True, entryStatus="暂缓入场")
    checked = job.compare_frozen_price(frozen, quote(), NOW, entry_safety=current)
    assert checked["state"] == "WATCH"
    assert checked["entryBlocked"] is True
    assert checked["entryStatus"] == "暂缓入场"


def historical_protection(reason):
    frozen = plan()
    frozen["tracks"][0].update(entryZoneMin="1.05", entryZoneMax="1.10")
    fields = {
        "entryBlocked": True,
        "entryBlockReason": reason,
        "entryReady": False,
        "inBuyZone": False,
        "preferredEntryEligible": False,
        "entryStatus": "历史禁入",
    }
    if reason == "SUBSCRIPTION_RESTRICTED":
        fields.update(
            subscriptionRestricted=True,
            subscriptionStatus={"purchaseStatus": "SUSPENDED"},
            subscriptionLabel="⛔ 暂停申购",
        )
    for row in (frozen, *frozen["tracks"]):
        row.update(deepcopy(fields))
    frozen["signals"] = [{"track": "RIGHT_TREND", **deepcopy(fields)}]
    return frozen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["SUBSCRIPTION_RESTRICTED", "DELAYED_NAV_MARKET_DROP", "HIGH_NAV_PERCENTILE"],
)
async def test_old_top_and_track_transient_guards_are_recomputed_and_real_107_can_enter(
    reason,
):
    frozen = historical_protection(reason)
    frozen["indicators"] = {"navPercentile1y": 99, "ma20": 1}
    current = plan()
    current["indicators"] = {"navPercentile1y": 40, "ma20": 1.5}
    facts = SafetyFacts(
        subscription="OPEN",
        packet={
            "asOf": NOW.isoformat(),
            "quantitativeScan": {"items": [current]},
            "documents": [],
        },
    )
    before = deepcopy(frozen)
    guarded = (await watch.current_entry_safety(facts, [frozen], NOW))[0]
    checked = job.compare_frozen_price(frozen, quote("1.07"), NOW, entry_safety=guarded)
    assert checked["state"] == "IN_ENTRY_ZONE"
    assert (
        watch.price_watch(
            frozen, estimate=quote("1.07"), now=NOW, entry_safety=guarded
        )["state"]
        == "ENTRY_ZONE"
    )
    assert guarded.get("subscriptionRestricted") is False
    for row in (guarded, *guarded["tracks"], *guarded["signals"]):
        assert row.get("entryBlocked") is not True
        assert row.get("preferredEntryEligible") is not False
    assert frozen == before
    for key in ("asOf", "currentPrice", "indicators", "selectedTrack"):
        assert guarded[key] == frozen[key]
    for key in (
        "state",
        "entryZoneMin",
        "entryZoneMax",
        "stopLossPrice",
        "takeProfitT1",
        "takeProfitT2",
    ):
        assert guarded["tracks"][0][key] == frozen["tracks"][0][key]


@pytest.mark.asyncio
async def test_cleared_old_flags_cannot_hide_a_current_oil_shock():
    frozen = historical_protection("DELAYED_NAV_MARKET_DROP")
    facts = SafetyFacts(
        subscription="OPEN",
        packet={
            "asOf": NOW.isoformat(),
            "documents": [
                {"title": "WTI原油期货下跌3%", "publishedDate": NOW.date().isoformat()}
            ],
        },
    )
    guarded = (await watch.current_entry_safety(facts, [frozen], NOW))[0]
    assert guarded["entryBlockReason"] == "DELAYED_NAV_MARKET_DROP"
    assert (
        job.compare_frozen_price(frozen, quote("1.07"), NOW, entry_safety=guarded)[
            "state"
        ]
        == "WATCH"
    )
    assert (
        job.compare_frozen_price(frozen, quote("0.7"), NOW, entry_safety=guarded)[
            "state"
        ]
        == "STOP_LOSS"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["SUBSCRIPTION_RESTRICTED", "DELAYED_NAV_MARKET_DROP"]
)
async def test_missing_current_safety_facts_are_unknown_not_historical_risk_reasserted(
    reason,
):
    frozen = historical_protection(reason)
    guarded = (await watch.current_entry_safety(SafetyFacts(), [frozen], NOW))[0]
    assert guarded["entryBlockReason"] == "CURRENT_SAFETY_UNKNOWN"
    assert "待核验" in guarded["entryStatus"]
    assert "subscriptionStatus" not in guarded
    assert (
        job.compare_frozen_price(frozen, quote("1.07"), NOW, entry_safety=guarded)[
            "state"
        ]
        == "WATCH"
    )
    assert (
        job.compare_frozen_price(frozen, quote("1.3"), NOW, entry_safety=guarded)[
            "state"
        ]
        == "TAKE_PROFIT_T1"
    )


@pytest.mark.asyncio
async def test_no_current_facts_do_not_invent_a_new_veto_for_a_plain_frozen_track():
    frozen = plan()
    frozen["tracks"][0].update(entryZoneMin="1.05", entryZoneMax="1.10")
    guarded = (await watch.current_entry_safety(SafetyFacts(), [frozen], NOW))[0]
    assert "subscriptionStatus" not in guarded
    assert guarded.get("entryBlocked") is not True
    assert (
        job.compare_frozen_price(frozen, quote("1.07"), NOW, entry_safety=guarded)[
            "state"
        ]
        == "IN_ENTRY_ZONE"
    )


@pytest.mark.asyncio
async def test_open_subscription_is_not_evidence_that_an_unchecked_old_market_shock_cleared():
    frozen = historical_protection("DELAYED_NAV_MARKET_DROP")
    guarded = (
        await watch.current_entry_safety(
            SafetyFacts(subscription="OPEN"), [frozen], NOW
        )
    )[0]
    assert guarded["subscriptionRestricted"] is False
    assert guarded["entryBlockReason"] == "CURRENT_SAFETY_UNKNOWN"
