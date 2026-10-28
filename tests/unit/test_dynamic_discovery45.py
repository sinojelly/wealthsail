"""Event-driven candidates keep core coverage and actual NAV authority."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.investment import discovery, theme_graph
from test_theme_graph import NOW

CORE_CODES = (
    "320007",
    "008281",
    "005609",
    "161129",
    "008975",
    "008298",
    "000051",
    "012733",
    "011035",
    "004432",
    "004069",
    "013477",
    "018344",
    "003017",
    "008086",
    "012737",
    "009179",
    "000216",
    "162411",
    "270042",
    "021607",
    "004854",
    "007466",
    "000962",
    "110020",
    "202015",
    "001632",
    "011103",
    "161725",
    "161726",
    "006756",
    "001361",
    "001629",
    "016185",
    "018027",
    "024194",
    "007300",
    "014880",
    "000218",
    "023530",
)


def helper():
    assert util.find_spec("backend.investment.dynamic_discovery"), (
        "event-driven market-wide fund selection is missing"
    )
    return import_module("backend.investment.dynamic_discovery")


def asset(code, label=None):
    return {
        "subjectKind": "nav",
        "subjectId": code,
        "fundCode": code,
        "market": "FUND_CN",
        "label": label or f"公开基金{code}",
        "overseasCalendarRequired": False,
    }


def event(text, **changes):
    return {
        "title": text,
        "text": text,
        "publishedAt": NOW.isoformat(),
        "retrievedAt": NOW.isoformat(),
        "sourceUri": "https://www.cls.cn/detail/123",
        **changes,
    }


def catalog(code, name, keyword="卫星", **changes):
    return {
        "fund_code": code,
        "fund_name": name,
        "fund_type": "股票型-指数",
        "instrument_id": None,
        "market": "FUND_CN",
        "mapping_count": 1,
        "matched_keyword": keyword,
        "identity_verified_at": NOW,
        "overseas_calendar_required": False,
        **changes,
    }


def complete_history(code="008281", count=120):
    first = date(2023, 1, 1)
    days = [first + timedelta(days=i) for i in range((NOW.date() - first).days + 1)]
    points = [day for day in days if day.weekday() < 5][-count:]
    proof = {
        "market": "SSE",
        "checkedThrough": NOW.date().isoformat(),
        "days": [
            {"date": day.isoformat(), "isTradingDay": day in points}
            for day in days
            if day >= points[0]
        ],
    }
    return {
        "fundCode": code,
        "asOf": points[-1].isoformat(),
        "sourceUri": f"https://fund.eastmoney.com/{code}.html",
        "retrievedAt": "2026-10-16T22:00:00+08:00",
        "availableAt": "2026-10-16T22:00:00+08:00",
        "priceBasis": "UNIT_NAV",
        "calendarProof": proof,
        "points": [
            {
                "date": day.isoformat(),
                "close": str(Decimal("1.2") + Decimal(i % 17) / 1000),
            }
            for i, day in enumerate(points)
        ],
    }


def test_core_configuration_preserves_the_forty_accepted_real_codes():
    assert tuple(helper().CORE_FUND_CODES) == CORE_CODES
    assert len(set(helper().CORE_FUND_CODES)) == 40


def test_dynamic_themes_read_full_same_batch_text_and_reject_future_news():
    source = event("前文" * 700 + "卫星互联网、固态电池和PCB产业催化")
    themes = helper().event_themes([source], NOW)
    assert {row["code"] for row in themes} >= {"SATELLITE", "SOLID_BATTERY", "PCB"}
    later = (NOW + timedelta(minutes=1)).isoformat()
    assert helper().event_themes([event("卫星", publishedAt=later)], NOW) == []
    assert helper().event_themes([event("卫星", retrievedAt=later)], NOW) == []
    assert (
        helper().event_themes(
            [event("卫星", publishedAt=None, publishedDate="2099-01-01")], NOW
        )
        == []
    )
    assert helper().event_themes([{"title": "卫星"}], NOW) == []


def test_dynamic_themes_do_not_turn_social_aviation_and_daily_roundups_into_industries():
    assert (
        helper().event_themes(
            [
                event("《新闻联播》要闻25条：卫星访问"),
                event("奥地利航空发现可疑物体改航线"),
                event("海外居民楼火灾"),
            ],
            NOW,
        )
        == []
    )
    assert helper().event_themes([event("商业航天卫星制造企业新增订单")], NOW)


@pytest.mark.asyncio
async def test_forty_core_funds_are_not_displaced_by_six_event_candidates(monkeypatch):
    assert callable(getattr(discovery, "select_research_assets", None)), (
        "one shared collect/build selection function is required"
    )
    base = [asset(code) for code in reversed(CORE_CODES)]
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=base)
    )
    conn = AsyncMock()
    conn.fetch.return_value = [
        catalog(f"980{i:03}", f"卫星ETF联接A{i}A") for i in range(20)
    ]
    selected = await discovery.select_research_assets(conn, NOW, [], [event("卫星")])
    assert [row["fundCode"] for row in selected[:40]] == list(CORE_CODES)
    assert len(selected) == 46
    assert len({row["fundCode"] for row in selected}) == 46
    assert all(row["fundCode"] not in CORE_CODES for row in selected[40:])


@pytest.mark.asyncio
async def test_market_wide_lookup_prioritizes_a_classes_and_excludes_known_bad_identity():
    conn = AsyncMock()
    conn.fetch.return_value = [
        catalog("980001", "卫星指数C"),
        catalog("980002", "卫星ETF联接A"),
        catalog("980003", "卫星混合A"),
        catalog("980004", "卫星指数后端"),
        catalog("980005", "卫星清算基金A"),
        catalog("980006", "卫星指数B"),
        catalog("980007", "卫星ETF联接A", mapping_count=2),
        catalog("980008", "卫星ETF联接A", lifecycle_status="INACTIVE"),
        catalog("980009", "卫星ETF联接A", market="HK"),
        catalog("980010", "卫星ETF联接A", identity_verified_at=NOW + timedelta(days=1)),
        catalog("980011", "卫星指数A"),
    ]
    selected = await helper().read_dynamic_assets(conn, NOW, [event("卫星")], [])
    assert [row["fundCode"] for row in selected] == ["980002", "980011", "980003"]
    sql = conn.fetch.await_args.args[0]
    assert "reference.fund_master" in sql and "reference.research_fund_catalog" in sql
    assert "LIMIT 96" not in sql
    assert "row_number()" in sql.lower(), (
        "limit each matched keyword after market lookup"
    )
    assert "ledger." not in sql and "tenant_id" not in sql


@pytest.mark.parametrize(
    "name",
    [
        "卫星ETF联接C（美元现汇）",
        "卫星指数C（人民币份额）",
        "卫星指数B类（人民币）",
        "卫星主题基金后收费",
        "卫星ETF联接C 类",
        "卫星指数C人民币",
    ],
)
@pytest.mark.asyncio
async def test_dynamic_share_exclusion_handles_real_currency_and_fee_suffixes(name):
    conn = AsyncMock()
    conn.fetch.return_value = [catalog("980001", name)]
    assert await helper().read_dynamic_assets(conn, NOW, [event("卫星")], []) == []


@pytest.mark.asyncio
async def test_dynamic_selection_rotates_themes_instead_of_filling_one_theme():
    conn = AsyncMock()
    conn.fetch.return_value = [
        catalog(f"980{i:03}", f"卫星指数{i}A") for i in range(16)
    ] + [
        catalog("981001", "中药ETF联接A", "中药"),
        catalog("981002", "煤炭ETF联接A", "煤炭"),
    ]
    selected = await helper().read_dynamic_assets(
        conn, NOW, [event("卫星、中药、煤炭")], []
    )
    assert len(selected) <= 6
    assert {row["fundCode"] for row in selected[:3]} >= {"981001", "981002"}


@pytest.mark.parametrize(
    "name,currency",
    [
        ("卫星主题ETF联接A（美元现汇）", "CNY"),
        ("卫星主题指数港币A", "CNY"),
        ("卫星主题ETF联接A", "USD"),
        ("卫星主题ETF联接A（外币）", "CNY"),
    ],
)
@pytest.mark.asyncio
async def test_dynamic_foreign_currency_share_cannot_be_labelled_as_cny_nav(
    name, currency
):
    conn = AsyncMock()
    conn.fetch.return_value = [catalog("980001", name, currency=currency)]
    assert await helper().read_dynamic_assets(conn, NOW, [event("卫星")], []) == []


@pytest.mark.asyncio
async def test_dynamic_identity_alias_shared_by_two_codes_is_not_collected():
    conn = AsyncMock()
    subject = uuid4()
    conn.fetch.return_value = [
        catalog("980001", "卫星ETF联接A", instrument_id=subject),
        catalog("980002", "卫星指数A", instrument_id=subject),
    ]
    assert await helper().read_dynamic_assets(conn, NOW, [event("卫星")], []) == []


@pytest.mark.asyncio
async def test_core_and_dynamic_collection_and_build_use_the_same_event_selection(
    monkeypatch,
):
    assert callable(getattr(discovery, "select_research_assets", None))
    selected = [asset("008281"), asset("028777", "真实卫星候选")]
    selection = AsyncMock(return_value=selected)
    monkeypatch.setattr(discovery, "select_research_assets", selection)
    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    collector = AsyncMock(
        return_value={"fundHistories": [], "themeNavSourceResults": {}}
    )
    monkeypatch.setattr(theme_graph, "collect_theme_nav_histories", collector)
    events = [event("卫星")]
    await discovery.collect_discovery_nav_histories(object(), NOW, catalysts=events)
    packet = await discovery.build_discovery_packet(
        conn, {}, {}, [], {}, NOW, catalysts=events
    )
    assert selection.await_args_list[0].args[3] == events
    assert selection.await_args_list[1].args[3] == events
    assert [
        row["asset_code"] for row in collector.await_args.kwargs["asset_rows"]
    ] == packet["discoveryCoverage"]["selectedFundCodes"]


@pytest.mark.asyncio
async def test_missing_core_identity_is_reported_without_synthesizing_a_mapping(
    monkeypatch,
):
    assert callable(getattr(discovery, "select_research_assets", None))
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=[asset("008281")])
    )
    conn = AsyncMock()
    result = await discovery.build_discovery_packet(
        conn, {}, {}, [], {}, NOW, catalysts=[]
    )
    coverage = result["discoveryCoverage"]
    assert coverage["coreFundCodes"] == ["008281"]
    assert set(coverage["coreMissingFundCodes"]) == set(CORE_CODES) - {"008281"}
    assert coverage["dynamicFundCodes"] == []
    assert coverage["selectionLimit"] == 46


@pytest.mark.asyncio
async def test_latest_completed_nav_with_550_samples_is_reused_across_calendar_days(
    monkeypatch,
):
    history = complete_history(count=550)
    before = deepcopy(history)
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "008281", "payload": history}]

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=[asset("008281")])
    )
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=history["calendarProof"])
    )
    collector = AsyncMock(
        return_value={"fundHistories": [], "themeNavSourceResults": {}}
    )
    monkeypatch.setattr(theme_graph, "collect_theme_nav_histories", collector)
    result = await discovery.collect_discovery_nav_histories(object(), NOW)
    assert collector.await_args.kwargs["asset_rows"] == []
    assert result["themeNavSourceResults"]["008281"]["cache"] == "REUSED_CONFIRMED"
    assert result["fundHistories"][0]["points"] == before["points"]
    assert history == before
    assert not conn.execute.await_args_list, "cache reuse must not mutate observations"


@pytest.mark.asyncio
async def test_current_220_sample_cache_is_deepened_before_reuse(monkeypatch):
    history = complete_history(count=220)
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "008281", "payload": history}]

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=[asset("008281")])
    )
    collector = AsyncMock(
        return_value={"fundHistories": [], "themeNavSourceResults": {}}
    )
    monkeypatch.setattr(theme_graph, "collect_theme_nav_histories", collector)
    await discovery.collect_discovery_nav_histories(object(), NOW)
    assert collector.await_args.kwargs["minimum_points"] == 550
    assert collector.await_args.kwargs["asset_rows"] == [
        {"asset_code": "008281", "overseas_calendar_required": False}
    ]


@pytest.mark.parametrize(
    "change",
    [
        "119_SAMPLES",
        "STALE_NAV",
        "MISSING_CALENDAR",
        "QDII_WITHOUT_PROOF",
        "QDII_ASSET_WITHOUT_PROOF",
    ],
)
@pytest.mark.asyncio
async def test_watermark_cannot_reuse_incomplete_stale_or_wrong_calendar_history(
    monkeypatch, change
):
    history = complete_history(count=119 if change == "119_SAMPLES" else 120)
    if change == "STALE_NAV":
        history["points"] = history["points"][:-1]
        history["asOf"] = history["points"][-1]["date"]
    elif change == "MISSING_CALENDAR":
        history["calendarProof"]["days"].pop(-2)
    elif change == "QDII_WITHOUT_PROOF":
        history["overseasCalendarRequired"] = True
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "008281", "payload": history}]

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    candidate = asset("008281")
    candidate["overseasCalendarRequired"] = change == "QDII_ASSET_WITHOUT_PROOF"
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=[candidate])
    )
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=history["calendarProof"])
    )
    collector = AsyncMock(
        return_value={"fundHistories": [], "themeNavSourceResults": {}}
    )
    monkeypatch.setattr(theme_graph, "collect_theme_nav_histories", collector)
    await discovery.collect_discovery_nav_histories(object(), NOW)
    assert collector.await_args.kwargs["asset_rows"] == [
        {
            "asset_code": "008281",
            "overseas_calendar_required": change == "QDII_ASSET_WITHOUT_PROOF",
        }
    ]


@pytest.mark.asyncio
async def test_conflicted_or_short_cache_is_replaced_by_appending_a_new_cohort(
    monkeypatch,
):
    old = complete_history(count=119)
    old["points"].append({**old["points"][-1], "close": "99"})
    original = deepcopy(old)
    fresh = complete_history(count=220)
    fresh.update(retrievedAt=NOW.isoformat(), availableAt=NOW.isoformat())
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "008281", "payload": old}]

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=[asset("008281")])
    )
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=old["calendarProof"])
    )
    monkeypatch.setattr(
        theme_graph,
        "collect_theme_nav_histories",
        AsyncMock(
            return_value={
                "fundHistories": [fresh],
                "themeNavSourceResults": {
                    "008281": {"status": "SUCCESS", "records": 220}
                },
            }
        ),
    )
    result = await discovery.collect_discovery_nav_histories(object(), NOW)
    assert result["fundHistories"][0]["points"] == fresh["points"]
    assert old == original
    writes = [call.args[0] for call in conn.execute.await_args_list]
    assert (
        len(writes) == 1
        and "INSERT INTO market.fund_research_nav_histories" in writes[0]
    )
    assert not any(
        word in writes[0].upper() for word in ("DELETE", "TRUNCATE", "UPDATE")
    )


@pytest.mark.asyncio
async def test_invalid_later_asof_cohort_cannot_hide_a_valid_replacement(monkeypatch):
    fresh = complete_history(count=220)
    fresh.update(retrievedAt=NOW.isoformat(), availableAt=NOW.isoformat())
    bad = deepcopy(fresh)
    bad["asOf"] = NOW.date().isoformat()
    bad["retrievedAt"] = bad["availableAt"] = (NOW - timedelta(minutes=1)).isoformat()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"fund_code": "008281", "payload": fresh},
        {"fund_code": "008281", "payload": bad},
    ]
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=fresh["calendarProof"])
    )
    result = await discovery._cached_histories(conn, ["008281"], NOW)
    assert result["008281"]["asOf"] == "2026-10-16"
    assert result["008281"]["points"] == fresh["points"]


@pytest.mark.asyncio
async def test_later_retrieval_of_older_series_does_not_lower_valid_nav_watermark(
    monkeypatch,
):
    latest = complete_history(count=220)
    older = deepcopy(latest)
    older["points"] = older["points"][:-1]
    older["asOf"] = older["points"][-1]["date"]
    older.update(retrievedAt=NOW.isoformat(), availableAt=NOW.isoformat())
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"fund_code": "008281", "payload": latest},
        {"fund_code": "008281", "payload": older},
    ]
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=latest["calendarProof"])
    )
    result = await discovery._cached_histories(conn, ["008281"], NOW)
    assert result["008281"]["asOf"] == latest["asOf"]


@pytest.mark.asyncio
async def test_history_cohorts_do_not_recompute_the_same_nav_plan_for_ranking(
    monkeypatch,
):
    history = complete_history(count=220)
    originals = [deepcopy(history) for _ in range(4)]
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"fund_code": "008281", "payload": row} for row in originals
    ]
    monkeypatch.setattr(
        discovery,
        "_cohort_calendar_proofs",
        AsyncMock(return_value={"008281": history["calendarProof"]}),
    )
    original_plan = theme_graph.nav_plan
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(theme_graph, "nav_plan", counted)
    result = await discovery._cached_histories(conn, ["008281"], NOW)
    assert result["008281"]["points"] == history["points"]
    assert len(calls) <= 2 * len(originals)
    result["008281"]["points"][0]["close"] = "999"
    assert all(row == history for row in originals)


@pytest.mark.asyncio
async def test_bad_bundle_cohort_cannot_replace_valid_cached_nav_in_packet(monkeypatch):
    fresh = complete_history(count=220)
    bad = deepcopy(fresh)
    bad["asOf"] = NOW.date().isoformat()
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "008281", "payload": fresh}]
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=[asset("008281")])
    )
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=fresh["calendarProof"])
    )
    result = await discovery.build_discovery_packet(
        conn, {}, {"fundHistories": [bad]}, [], {}, NOW
    )
    assert result["discoveryActionableZones"][0]["asOf"] == "2026-10-16"


@pytest.mark.asyncio
async def test_newer_gapped_bundle_cannot_replace_complete_cached_same_nav_day(
    monkeypatch,
):
    fresh = complete_history(count=220)
    bad = deepcopy(fresh)
    bad["points"].pop(20)
    bad.update(retrievedAt=NOW.isoformat(), availableAt=NOW.isoformat())
    bad["calendarProof"] = {
        "market": "SSE",
        "checkedThrough": NOW.date().isoformat(),
        "days": [],
    }
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "008281", "payload": fresh}]
    monkeypatch.setattr(
        discovery, "read_discovery_assets", AsyncMock(return_value=[asset("008281")])
    )
    monkeypatch.setattr(
        theme_graph, "_calendar_proof", AsyncMock(return_value=fresh["calendarProof"])
    )
    result = await discovery.build_discovery_packet(
        conn, {}, {"fundHistories": [bad]}, [], {}, NOW
    )
    assert len(result["quantitativeScan"]["items"]) == 1
    assert result["quantitativeScan"]["items"][0]["historySampleCount"] == 220


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["cache", "bundle"])
async def test_unproven_new_qdii_response_does_not_erase_complete_disclosed_history(
    monkeypatch, surface
):
    from test_disclosed_nav_opportunities import disclosed_history

    valid = disclosed_history()
    partial = deepcopy(valid)
    partial.pop("providerHistoryProof")
    partial["asOf"] = NOW.date().isoformat()
    partial["points"].append({"date": partial["asOf"], "close": "1.33"})
    conn = AsyncMock()
    conn.fetch.return_value = [{"fund_code": "162411", "payload": valid}]
    if surface == "cache":
        conn.fetch.return_value.append({"fund_code": "162411", "payload": partial})
        result = await discovery._cached_histories(conn, ["162411"], NOW)
        assert result["162411"]["asOf"] == valid["asOf"]
    else:
        monkeypatch.setattr(
            discovery,
            "read_discovery_assets",
            AsyncMock(return_value=[asset("162411")]),
        )
        result = await discovery.build_discovery_packet(
            conn, {}, {"fundHistories": [partial]}, [], {}, NOW
        )
        assert len(result["quantitativeScan"]["items"]) == 1
        assert result["quantitativeScan"]["items"][0]["asOf"] == valid["asOf"]


@pytest.mark.asyncio
async def test_nav_collection_is_concurrent_but_bounded_and_one_failure_is_independent(
    monkeypatch,
):
    from backend.integrations.market_data import Observation

    active = peak = 0
    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(theme_graph, "system_connection", acquire)

    async def fetcher(code, start, end, client, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            if code == "980002":
                raise RuntimeError("one public source unavailable")
            return [
                Observation(
                    symbol=code,
                    subject_type="FUND",
                    field_code="NAV",
                    value=Decimal("1.25"),
                    as_of=NOW.replace(day=16),
                    quality_status="FRESH",
                )
            ]
        finally:
            active -= 1

    codes = [f"980{i:03}" for i in range(8)]
    result = await theme_graph.collect_theme_nav_histories(
        object(),
        NOW,
        fetcher=fetcher,
        now_provider=lambda: NOW,
        asset_rows=[{"asset_code": code} for code in codes],
    )
    assert peak == 4, (
        "funds must run in parallel without exceeding four provider requests"
    )
    assert result["themeNavSourceResults"]["980002"]["status"] == "FAILED"
    assert [row["fundCode"] for row in result["fundHistories"]] == [
        code for code in codes if code != "980002"
    ]


@pytest.mark.asyncio
async def test_duplicate_date_conflict_is_not_silently_overwritten_in_a_new_cohort(
    monkeypatch,
):
    from backend.integrations.market_data import Observation

    async def fetcher(code, start, end, client, **kwargs):
        return [
            Observation(
                symbol=code,
                subject_type="FUND",
                field_code="NAV",
                value=Decimal(value),
                as_of=NOW.replace(day=16),
                quality_status="FRESH",
            )
            for value in ("1.2", "1.3")
        ]

    result = await theme_graph.collect_theme_nav_histories(
        object(),
        NOW,
        fetcher=fetcher,
        now_provider=lambda: NOW,
        asset_rows=[{"asset_code": "008281"}],
    )
    assert result["fundHistories"] == []
    assert result["themeNavSourceResults"]["008281"]["status"] == "FAILED"


def test_scanner_v2_does_not_call_watch_right_track_an_entered_buy_signal():
    history = complete_history(count=220)
    plan = theme_graph.nav_plan(history, now=NOW)
    plan["historyQuality"] = "READY"
    right = next(row for row in plan["tracks"] if row["track"] == "RIGHT_TREND")
    # Isolate scanner technical readiness from historical cap/space guards.
    right.update(
        state="WATCH",
        entryZoneMin="1.1900",
        entryZoneMax="1.2500",
        inSellZone=False,
        entryBlocked=False,
        priceState="READY",
    )
    result = discovery._quantitative_scan(
        {
            "discoveryUniverse": [asset("008281")],
            "discoveryActionableZones": [plan],
        },
        NOW,
    )
    assert result["methodVersion"] == "fund-quant-scan-v2"
    signal = next(
        row for row in result["items"][0]["signals"] if row["track"] == "RIGHT_TREND"
    )
    assert signal["inEntryZone"] is True
    assert signal["candidate"] is False and signal["entryReady"] is False
    right.update(state="CANDIDATE", entryReady=True)
    signal = next(
        row
        for row in discovery._quantitative_scan(
            {
                "discoveryUniverse": [asset("008281")],
                "discoveryActionableZones": [plan],
            },
            NOW,
        )["items"][0]["signals"]
        if row["track"] == "RIGHT_TREND"
    )
    assert signal["entryReady"] is True


def test_v6_scanner_coverage_uses_120_sample_history_floor():
    plan = theme_graph.nav_plan(complete_history(count=150), now=NOW)
    scan = discovery._quantitative_scan(
        {"discoveryUniverse": [asset("008281")], "discoveryActionableZones": [plan]},
        NOW,
    )
    assert scan["minimumHistorySamples"] == 120
    assert scan["coverage"] == {"pricedFundCount": 1, "targetHistoryFundCount": 1}
