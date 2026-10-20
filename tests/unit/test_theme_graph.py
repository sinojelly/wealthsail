"""Theme candidates retain their own NAV and never borrow an index quote."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

from backend.investment import theme_graph as graph

NOW = datetime(2026, 10, 18, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def calendar_proof():
    # Explicit stored reference facts for this fixture, not a runtime fallback.
    start = date(2026, 9, 1)
    traded = {
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
        "2026-09-04",
        "2026-09-07",
        "2026-09-08",
        "2026-09-09",
        "2026-09-10",
        "2026-09-11",
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
        "2026-09-25",
        "2026-09-28",
        "2026-09-29",
        "2026-09-30",
        "2026-10-09",
        "2026-10-12",
        "2026-10-13",
        "2026-10-14",
        "2026-10-15",
        "2026-10-16",
    }
    return {
        "market": "SSE",
        "checkedThrough": "2026-10-18",
        "days": [
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "isTradingDay": (start + timedelta(days=i)).isoformat() in traded,
            }
            for i in range(48)
        ],
    }


def nav_history(**changes):
    proof = calendar_proof()
    return {
        "fundCode": "008281",
        "asOf": "2026-10-16",
        "sourceUri": "https://fund.eastmoney.com/008281.html",
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "priceBasis": "UNIT_NAV",
        "calendarProof": proof,
        "points": [
            {"date": row["date"], "close": str(1 + i / 100)}
            for i, row in enumerate(proof["days"])
            if row["isTradingDay"]
        ],
        **changes,
    }


def test_nav_history_produces_same_fund_risk_plan_without_fabricated_atr():
    history = nav_history()
    history["points"][-1]["close"] = "1.25"
    plan = graph.nav_plan(history, now=NOW)
    assert plan["subjectId"] == "008281"
    assert plan["subjectKind"] == "nav"
    assert plan["priceBasis"] == "UNIT_NAV"
    assert plan["indicators"]["atr14"] is None
    assert plan["volatilityMethod"] == "NAV_ABS_CHANGE_STD_V1"
    assert plan["indicators"]["navVolatility14"] > 0
    assert plan["tracks"][1]["priceState"] == "READY"
    assert plan["currentPrice"] == "1.2500"
    assert plan["historyQuality"] == "READY"


@pytest.mark.asyncio
async def test_injected_discovery_assets_keep_own_codes_and_overseas_requirements(
    monkeypatch,
):
    from backend.integrations.market_data import Observation

    class Conn:
        async def fetch(self, sql, *args):
            assert "reference.trading_calendars" in sql
            return []

    @asynccontextmanager
    async def acquire(pool):
        yield Conn()

    monkeypatch.setattr(graph, "system_connection", acquire)

    async def fetcher(code, start, end, client, **kwargs):
        return [
            Observation(
                symbol=code,
                subject_type="FUND",
                field_code="NAV",
                value=Decimal("1.2500"),
                as_of=NOW.replace(day=16, hour=15),
                quality_status="FRESH",
            )
        ]

    result = await graph.collect_theme_nav_histories(
        object(),
        NOW,
        fetcher=fetcher,
        now_provider=lambda: NOW,
        asset_rows=[
            {"asset_code": "000001", "overseas_calendar_required": False},
            {"asset_code": "000001", "overseas_calendar_required": True},
            {"asset_code": "008281", "overseas_calendar_required": False},
            {"asset_code": "008281/other"},
            {"asset_code": 8281},
            {"asset_code": "12345"},
        ],
    )
    assert [row["fundCode"] for row in result["fundHistories"]] == ["000001", "008281"]
    assert result["fundHistories"][0]["overseasCalendarRequired"] is True
    assert result["fundHistories"][1]["overseasCalendarRequired"] is False


@pytest.mark.asyncio
async def test_injected_nav_discovery_is_capped_at_forty_six_distinct_assets():
    async def fetcher(code, start, end, client, **kwargs):
        return []

    result = await graph.collect_theme_nav_histories(
        object(),
        NOW,
        fetcher=fetcher,
        now_provider=lambda: NOW,
        asset_rows=[{"asset_code": str(100000 + i)} for i in range(50)],
    )
    assert list(result["themeNavSourceResults"]) == [str(100000 + i) for i in range(46)]
    assert result["fundHistories"] == []


@pytest.mark.asyncio
async def test_discovery_collection_budget_keeps_history_without_fetching_remaining(
    monkeypatch,
):
    from backend.integrations.market_data import Observation

    elapsed = [0]

    class Conn:
        async def fetch(self, sql, *args):
            return []

    @asynccontextmanager
    async def acquire(pool):
        yield Conn()

    monkeypatch.setattr(graph, "system_connection", acquire)

    async def fetcher(code, start, end, client, **kwargs):
        assert code == "008281", "exhausted budget must not fetch another code"
        elapsed[0] = 241
        return [
            Observation(
                symbol=code,
                subject_type="FUND",
                field_code="NAV",
                value=Decimal("1.2500"),
                as_of=NOW.replace(day=16, hour=15),
                quality_status="FRESH",
            )
        ]

    result = await graph.collect_theme_nav_histories(
        object(),
        NOW,
        fetcher=fetcher,
        now_provider=lambda: NOW,
        asset_rows=[{"asset_code": code} for code in ("008281", "000001", "320007")],
        clock=lambda: elapsed[0],
    )
    assert [row["fundCode"] for row in result["fundHistories"]] == ["008281"]
    assert result["themeNavSourceResults"]["008281"]["status"] == "SUCCESS"
    for code in ("000001", "320007"):
        assert result["themeNavSourceResults"][code] == {
            "status": "UNAVAILABLE",
            "records": 0,
            "reason": "BUDGET_EXHAUSTED",
        }


def test_future_or_wrong_nav_history_is_not_available():
    assert (
        graph.nav_plan(
            {"fundCode": "008281", "asOf": "2026-10-19", "points": []}, now=NOW
        )
        is None
    )
    assert (
        graph.nav_plan(
            {"fundCode": "not-a-code", "asOf": "2026-10-16", "points": []}, now=NOW
        )
        is None
    )


@pytest.mark.asyncio
async def test_public_themes_do_not_include_private_metadata_or_fake_high_positions():
    class Conn:
        async def fetch(self, sql, *args):
            return [
                {
                    "theme_code": "AI_MEMORY_HBM",
                    "theme_label": "AI内存",
                    "asset_kind": "ACTIVE_FUND",
                    "asset_code": "320007",
                    "asset_name": "诺安成长",
                    "market": "FUND_CN",
                    "relationship_type": "RESEARCH_CANDIDATE",
                    "source_uri": "https://mall.lionfund.com.cn/",
                    "metadata": {
                        "chainRole": "候选",
                        "private": "PRIVATE",
                        "highPositionPct": "99",
                    },
                    "instrument_id": None,
                }
            ]

    result = await graph.read_themes(Conn(), NOW)
    assert result[0]["metadata"]["holdingsVerified"] is False
    assert result[0]["metadata"]["highPositionPct"] is None
    assert "PRIVATE" not in str(result)


@pytest.mark.asyncio
async def test_theme_collection_uses_real_observation_contract_and_rejects_wrong_symbol(
    monkeypatch,
):
    from backend.integrations.market_data import Observation

    class Conn:
        async def fetch(self, *args):
            if "reference.trading_calendars" in args[0]:
                return [
                    {
                        "calendar_date": row["date"],
                        "is_trading_day": row["isTradingDay"],
                    }
                    for row in calendar_proof()["days"]
                ]
            return [{"asset_code": "008281"}]

    @asynccontextmanager
    async def acquire(pool):
        yield Conn()

    monkeypatch.setattr(graph, "system_connection", acquire)

    async def fetcher(code, start, end, client, **kwargs):
        return [
            Observation(
                symbol="008281",
                subject_type="FUND",
                field_code="NAV",
                value=Decimal("1.2500"),
                as_of=NOW.replace(day=16, hour=15),
                quality_status="FRESH",
            ),
            Observation(
                symbol="another",
                subject_type="FUND",
                field_code="NAV",
                value=Decimal("99"),
                as_of=NOW.replace(day=17),
                quality_status="FRESH",
            ),
        ]

    result = await graph.collect_theme_nav_histories(
        object(), NOW, fetcher=fetcher, now_provider=lambda: NOW
    )
    assert result["fundHistories"][0]["points"] == [
        {"date": "2026-10-16", "close": "1.2500"}
    ]


@pytest.mark.asyncio
async def test_confirmed_historical_stale_nav_is_retained_but_estimates_are_not(
    monkeypatch,
):
    from backend.integrations.market_data import Observation

    fixture = nav_history()
    extra = [
        {"date": f"2026-08-{day}", "isTradingDay": day in (27, 28, 31)}
        for day in range(27, 32)
    ]
    fixture["calendarProof"]["days"] = extra + fixture["calendarProof"]["days"]
    fixture["points"] = [
        {"date": row["date"], "close": "1.00"} for row in extra if row["isTradingDay"]
    ] + fixture["points"]

    class Conn:
        async def fetch(self, sql, *args):
            if "trading_calendars" in sql:
                return [
                    {
                        "calendar_date": row["date"],
                        "is_trading_day": row["isTradingDay"],
                    }
                    for row in fixture["calendarProof"]["days"]
                ]
            return [{"asset_code": "008281"}]

    @asynccontextmanager
    async def acquire(pool):
        yield Conn()

    monkeypatch.setattr(graph, "system_connection", acquire)

    async def fetcher(*args, **kwargs):
        observed = [
            Observation(
                symbol="008281",
                subject_type="FUND",
                field_code="NAV",
                value=Decimal(point["close"]),
                as_of=datetime.fromisoformat(point["date"]).replace(
                    hour=15, tzinfo=NOW.tzinfo
                ),
                quality_status="STALE"
                if index < len(fixture["points"]) - 1
                else "FRESH",
            )
            for index, point in enumerate(fixture["points"])
        ]
        observed.append(
            Observation(
                symbol="008281",
                subject_type="FUND",
                field_code="NAV",
                value=Decimal("99"),
                as_of=NOW.replace(day=17, hour=15),
                quality_status="ESTIMATED",
            )
        )
        return observed

    result = await graph.collect_theme_nav_histories(
        object(), NOW, fetcher=fetcher, now_provider=lambda: NOW
    )
    history = result["fundHistories"][0]
    assert len(history["points"]) == 31
    plan = graph.nav_plan(history, now=NOW)
    assert plan["historyQuality"] == "READY"
    assert (
        plan["indicators"]["ma20"] is not None
        and plan["indicators"]["bollLower"] is not None
    )
    assert plan["currentPrice"] != "99"


@pytest.mark.asyncio
async def test_cached_nav_analysis_uses_its_code_and_preserves_frozen_theme(
    monkeypatch,
):
    from backend.macro import alpha_repository

    history = nav_history()

    async def read(conn, now):
        return {"fundHistories": [history]}

    monkeypatch.setattr(alpha_repository, "read_alpha_public", read)

    class Conn:
        async def fetch(self, *args):
            return [
                {"calendar_date": row["date"], "is_trading_day": row["isTradingDay"]}
                for row in calendar_proof()["days"]
            ]

    actual = await graph.cached_nav_analysis(Conn(), "008281", NOW)
    assert actual["actionableZones"]["priceBasis"] == "UNIT_NAV"
    assert actual["seriesKind"] == "UNIT_NAV"
    assert await graph.cached_nav_analysis(Conn(), "other", NOW) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"availableAt": "2026-10-20T08:30:00+08:00"},
        {"priceBasis": "ACCUMULATED_NAV"},
        {"sourceUri": "https://fund.eastmoney.com/999999.html"},
        {"retrievedAt": "2026-10-19T08:30:00+08:00"},
    ],
)
def test_nav_plan_rejects_future_different_basis_or_other_fund_source(changes):
    assert graph.nav_plan(nav_history(**changes), now=NOW) is None


@pytest.mark.parametrize(
    "changes,quality",
    [
        ({"calendarProof": None}, "UNVERIFIED"),
        ({"overseasCalendarRequired": True}, "UNVERIFIED"),
    ],
)
def test_nav_without_real_calendar_retains_only_historical_observation(
    changes, quality
):
    plan = graph.nav_plan(nav_history(**changes), now=NOW)
    assert plan["currentPrice"] is not None and plan["indicators"]["ma20"] is not None
    assert plan["historyQuality"] == quality
    assert all(
        track["entryZoneMin"] is None and track["takeProfitT1"] is None
        for track in plan["tracks"]
    )


def test_missing_confirmed_session_or_weekend_nav_is_not_current():
    history = nav_history()
    history["points"].pop(-2)
    assert graph.nav_plan(history, now=NOW)["historyQuality"] == "CONFLICT"
    history = nav_history()
    history["asOf"] = "2026-10-17"
    history["points"].append({"date": "2026-10-17", "close": "1.4"})
    assert graph.nav_plan(history, now=NOW)["historyQuality"] == "CONFLICT"


@pytest.mark.asyncio
async def test_cached_nav_rechecks_calendar_and_does_not_reuse_an_old_ready_proof(
    monkeypatch,
):
    from backend.macro import alpha_repository

    async def read(conn, now):
        return {"fundHistories": [nav_history()]}

    monkeypatch.setattr(alpha_repository, "read_alpha_public", read)

    class Conn:
        async def fetch(self, *args):
            return []

    actual = await graph.cached_nav_analysis(Conn(), "008281", NOW)
    assert actual["state"] == "UNVERIFIED"
    assert actual["actionableZones"]["tracks"][0]["entryZoneMin"] is None


def test_current_nav_waits_for_an_explicit_new_session_after_eighteen():
    before = NOW.replace(day=19, hour=17)
    history = nav_history()
    history["calendarProof"]["checkedThrough"] = "2026-10-19"
    history["calendarProof"]["days"].append(
        {"date": "2026-10-19", "isTradingDay": True}
    )
    assert graph.nav_plan(history, now=before)["historyQuality"] == "READY"
    after = before.replace(hour=18)
    plan = graph.nav_plan(history, now=after)
    assert plan["historyQuality"] == "STALE" and plan["currentPrice"] is not None
    assert all(track["entryZoneMin"] is None for track in plan["tracks"])


@pytest.mark.asyncio
async def test_calendar_read_failure_does_not_erase_confirmed_provider_nav(monkeypatch):
    from backend.integrations.market_data import Observation

    class Conn:
        async def fetch(self, *args):
            return [{"asset_code": "008281"}]

    @asynccontextmanager
    async def acquire(pool):
        yield Conn()

    monkeypatch.setattr(graph, "system_connection", acquire)
    monkeypatch.setattr(
        graph,
        "_calendar_proof",
        AsyncMock(side_effect=RuntimeError("private db detail")),
    )

    async def fetcher(*args, **kwargs):
        return [
            Observation(
                symbol="008281",
                subject_type="FUND",
                field_code="NAV",
                value=Decimal("1.25"),
                as_of=NOW.replace(day=16),
                quality_status="STALE",
            )
        ]

    result = await graph.collect_theme_nav_histories(
        object(), NOW, fetcher=fetcher, now_provider=lambda: NOW
    )
    assert len(result["fundHistories"]) == 1
    assert (
        graph.nav_plan(result["fundHistories"][0], now=NOW)["historyQuality"]
        == "UNVERIFIED"
    )
    assert "private db detail" not in str(result)


@pytest.mark.asyncio
async def test_cached_old_history_cannot_lose_its_graph_overseas_requirement(
    monkeypatch,
):
    from backend.macro import alpha_repository

    async def read(conn, now):
        return {"fundHistories": [nav_history()]}

    monkeypatch.setattr(alpha_repository, "read_alpha_public", read)

    class Conn:
        async def fetch(self, *args):
            return [
                {
                    "calendar_date": row["date"],
                    "is_trading_day": row["isTradingDay"],
                    "overseas_calendar_required": True,
                }
                for row in calendar_proof()["days"]
            ]

    actual = await graph.cached_nav_analysis(Conn(), "008281", NOW)
    assert actual["state"] == "UNVERIFIED"
    assert "境外" in str(actual["actionableZones"]["tracks"])


def test_nav_kind_and_uuid_kind_cannot_share_a_code_identity():
    assert (
        graph.nav_plan(nav_history(), now=NOW, subject_kind="fund", subject_id="008281")
        is None
    )
    assert (
        graph.nav_plan(nav_history(), now=NOW, subject_kind="nav", subject_id="999999")
        is None
    )


def test_malformed_calendar_fact_is_unknown_and_keeps_history():
    proof = calendar_proof()
    proof["days"][0] = "not a calendar fact"
    actual = graph.nav_plan(nav_history(calendarProof=proof), now=NOW)
    assert actual["historyQuality"] == "UNVERIFIED"
    assert actual["currentPrice"] is not None


@pytest.mark.asyncio
async def test_real_provider_clamped_twenty_row_pages_still_cover_history(monkeypatch):
    import httpx
    from datetime import timedelta

    class Conn:
        async def fetch(self, sql, *args):
            if "reference.theme_asset_graph" in sql:
                return [{"asset_code": "008281", "overseas_calendar_required": False}]
            return []

    @asynccontextmanager
    async def acquire(pool):
        yield Conn()

    monkeypatch.setattr(graph, "system_connection", acquire)
    requested = []

    def request(req):
        page = int(req.url.params["pageIndex"])
        requested.append(page)
        rows = [
            {"FSRQ": (NOW.date() - timedelta(days=day + 1)).isoformat(), "DWJZ": "1.25"}
            for day in range((page - 1) * 20, min(page * 20, 121))
        ]
        return httpx.Response(
            200,
            json={
                "Data": {"LSJZList": rows},
                "PageIndex": page,
                "PageSize": 20,
                "TotalCount": 121,
            },
        )

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        graph.httpx,
        "AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(request), **kwargs),
    )
    result = await graph.collect_theme_nav_histories(
        object(), NOW, now_provider=lambda: NOW
    )
    assert result["themeNavSourceResults"]["008281"]["status"] == "SUCCESS"
    assert result["themeNavSourceResults"]["008281"]["records"] == 121
    assert requested == [1, 2, 3, 4, 5, 6, 7]
