"""Live plans replace stale report prices from the same local NAV chart window."""

from copy import deepcopy
from datetime import datetime, timedelta
from math import sin
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.funds.nav_history import select_history_view
from backend.integrations.nav_history_proof import nav_points_digest

NOW = datetime(2026, 10, 28, 18, tzinfo=ZoneInfo("Asia/Shanghai"))


def history(code="008298"):
    day, points = NOW.date() - timedelta(days=1), []
    while len(points) < 550:
        if day.weekday() < 5:
            points.append(
                {
                    "date": day.isoformat(),
                    "close": str(round(1.5 + sin(len(points) / 35) * 0.25, 4)),
                }
            )
        day -= timedelta(days=1)
    points.reverse()
    result = {
        "fundCode": code,
        "priceBasis": "UNIT_NAV",
        "sourceUri": f"https://fund.eastmoney.com/{code}.html",
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "asOf": points[-1]["date"],
        "points": points,
    }
    result["providerHistoryProof"] = {
        "methodVersion": "eastmoney-nav-history-v1",
        "sourceUri": "https://api.fund.eastmoney.com/f10/lsjz",
        "fundCode": code,
        "complete": True,
        "retrievedAt": NOW.isoformat(),
        "queryStart": points[0]["date"],
        "queryEnd": NOW.date().isoformat(),
        "totalCount": len(points),
        "receivedRowCount": len(points),
        "uniquePointCount": len(points),
        "pointCount": len(points),
        "pageCount": 11,
        "requiredPages": 11,
        "firstDate": points[0]["date"],
        "lastDate": points[-1]["date"],
        "pointsDigest": nav_points_digest(points),
    }
    return result


def old_plan(code="008298", **updates):
    return {
        "methodVersion": "actionable-zones-v4",
        "subjectKind": "nav",
        "subjectId": code,
        "fundCode": code,
        "market": "FUND_CN",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "label": "银行联接A",
        "asOf": "2026-10-15",
        "currentPrice": "99.99",
        "sourceUri": f"https://fund.eastmoney.com/{code}.html",
        "selectedTrack": "RIGHT_TREND",
        "priceRevisionStatus": "MAINTAINED",
        "opportunityScore": 99,
        "entryStatus": "旧高位提示",
        "entryStatusTone": "amber",
        "tracks": [
            {
                "track": "RIGHT_TREND",
                "entryZoneMin": "98",
                "takeProfitT1": "101",
                "opportunityScore": 99,
            }
        ],
        **updates,
    }


class LocalHistory:
    def __init__(self, rows=()):
        self.rows, self.calls = rows, []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return [{"fund_code": row["fundCode"], "payload": row} for row in self.rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [True, False])
async def test_live_scan_coverage_uses_refreshed_prices_instead_of_saved_counts(
    available,
):
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    report = {
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v2",
            "asOf": "2026-10-15T08:30:00+08:00",
            "minimumHistorySamples": 220,
            "items": [old_plan()],
            "coverage": {"pricedFundCount": 99, "targetHistoryFundCount": 99},
        }
    }
    result = await refresh_report_fund_plans(
        LocalHistory([history()] if available else []), report, NOW
    )
    scan = result["quantitativeScan"]
    assert scan["minimumHistorySamples"] == 120
    assert scan["coverage"] == {
        "pricedFundCount": int(available),
        "targetHistoryFundCount": int(available),
    }
    assert scan["items"][0]["historyTargetSamples"] == 500


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nav_age,checked_age,ready",
    [
        (10, timedelta(0), True),
        (11, timedelta(0), False),
        (20, timedelta(0), False),
        (2, timedelta(hours=36), True),
        (2, timedelta(hours=36, seconds=1), False),
    ],
)
async def test_full_history_cannot_bypass_nav_and_source_freshness(
    nav_age, checked_age, ready
):
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    full = history()
    for index, point in enumerate(full["points"]):
        point["date"] = (
            datetime.fromisoformat(point["date"]).date() - timedelta(days=nav_age - 1)
        ).isoformat()
        point["close"] = str(2 - index / 549)
    full["asOf"] = full["points"][-1]["date"]
    full["retrievedAt"] = (NOW - checked_age).isoformat()
    full["availableAt"] = full["retrievedAt"]
    full.pop("providerHistoryProof")
    full["fullHistory"] = True
    full["historyProof"] = {
        "methodVersion": "akshare-unit-nav-full-v1",
        "complete": True,
        "fundCode": full["fundCode"],
        "sourceUri": full["sourceUri"],
        "queryStart": "1900-01-01",
        "queryEnd": (NOW - checked_age).date().isoformat(),
        "pointCount": 550,
        "receivedRowCount": 550,
        "firstDate": full["points"][0]["date"],
        "lastDate": full["asOf"],
        "pointsDigest": nav_points_digest(full["points"]),
    }
    chart = select_history_view("008298", "银行联接A", "2y", [full], NOW)
    assert chart["coverage"]["fullHistory"] is True

    result = await refresh_report_fund_plans(
        LocalHistory([full]), {"actionablePlans": [old_plan()]}, NOW
    )
    plan = result["actionablePlans"][0]
    assert plan["asOf"] == full["asOf"]
    assert plan["currentPrice"] == "1.0000"
    assert plan["historicalPriceWindow"]["sampleCount"] == len(chart["points"])
    assert all(track["entryZoneMin"] is not None for track in plan["tracks"])
    assert all(track["sellZoneMax"] is not None for track in plan["tracks"])
    assert plan["historyQuality"] == ("READY" if ready else "PARTIAL")
    assert plan["priceRefresh"]["state"] == ("READY" if ready else "PARTIAL")
    assert plan["tracks"][0]["entryReady"] is ready
    if not ready:
        assert all(track["entryReady"] is False for track in plan["tracks"])
        assert all(signal["entryReady"] is False for signal in plan["signals"])


@pytest.mark.asyncio
async def test_complete_history_with_no_feasible_capped_trade_band_is_not_ready():
    from backend.integrations.nav_history_proof import nav_points_digest
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    flat = history()
    for point in flat["points"]:
        point["close"] = "1.0000"
    flat["providerHistoryProof"]["pointsDigest"] = nav_points_digest(flat["points"])
    result = await refresh_report_fund_plans(
        LocalHistory([flat]), {"actionablePlans": [old_plan()]}, NOW
    )
    assert result["actionablePlans"][0]["priceRefresh"]["state"] == "PARTIAL"
    assert (
        result["actionablePlans"][0]["priceRefresh"]["reason"]
        == "LOCAL_PRICE_INTERVAL_UNAVAILABLE"
    )


@pytest.mark.asyncio
async def test_old_report_plan_reprices_from_chart_window_once_per_code(monkeypatch):
    from backend.investment import live_fund_plans as module

    # This test measures cold-cache work within one response. Cross-request
    # cache reuse is covered separately and must not hide these calls.
    module._PLAN_CACHE.clear()
    bank, oil = old_plan(), old_plan("162411", methodVersion="actionable-zones-v5")
    report = {
        "asOf": "2026-10-15T08:30:00+08:00",
        "valueDirections": [{"title": "保留研究标题", "plans": [bank, oil]}],
        "quantitativeScan": {"items": [deepcopy(bank)]},
    }
    before = deepcopy(report)
    calls, original = [], module.compute_actionable_price_plan

    def compute(*args, **kwargs):
        calls.append(kwargs["subject_id"])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "compute_actionable_price_plan", compute)
    conn = LocalHistory([history(), history("162411")])
    result = await module.refresh_report_fund_plans(conn, report, NOW)
    plan = result["valueDirections"][0]["plans"][0]
    assert set(calls) == {"008298", "162411"} and len(calls) == 2
    assert len(conn.calls) == 1
    assert (
        "fund_nav_chart_histories" in conn.calls[0][0]
        and "fund_research_nav_histories" in conn.calls[0][0]
    )
    assert report == before and result["asOf"] == report["asOf"]
    assert plan["methodVersion"] == "actionable-zones-v6"
    assert (
        plan["researchAsOf"] == "2026-10-15"
        and plan["researchMethodVersion"] == "actionable-zones-v4"
    )
    assert plan["currentPrice"] != "99.99" and plan["asOf"] == "2026-10-27"
    assert plan["selectedTrack"] == "RIGHT_TREND"
    assert "priceRevisionStatus" not in plan and "opportunityScore" not in plan
    chart = select_history_view("008298", "银行联接A", "2y", [history()], NOW)
    assert plan["historicalPriceWindow"]["sampleCount"] == len(chart["points"])
    assert plan["historicalPriceWindow"]["start"] == chart["points"][0]["date"]
    assert plan["priceRefresh"]["state"] == "READY"
    assert result["quantitativeScan"]["items"][0]["signals"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing", "future", "source", "wrong_code"])
async def test_missing_or_invalid_local_history_clears_old_prices(invalid):
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    rows = [] if invalid == "missing" else [history()]
    if invalid == "future":
        rows[0]["availableAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif invalid == "source":
        rows[0]["sourceUri"] = "https://unverified.example/008298"
    elif invalid == "wrong_code":
        rows[0]["fundCode"] = "000001"
    result = await refresh_report_fund_plans(
        LocalHistory(rows), {"actionablePlans": [old_plan()]}, NOW
    )
    plan = result["actionablePlans"][0]
    assert plan["methodVersion"] == "actionable-zones-v6"
    assert plan["currentPrice"] is plan["sourceUri"] is None
    assert plan["priceRefresh"]["state"] == "UNAVAILABLE"
    assert plan["priceRefresh"]["reason"]
    assert all(
        track["entryZoneMin"] is None
        and track["takeProfitT1"] is None
        and track["entryReady"] is False
        for track in plan["tracks"]
    )
    assert plan.get("entryStatus") != "旧高位提示"


@pytest.mark.asyncio
async def test_market_missing_nav_is_upgraded_and_uuid_fund_identity_is_preserved():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    nav = old_plan()
    nav.pop("market")
    subject = str(uuid4())
    fund = old_plan(subjectKind="fund", subjectId=subject)
    result = await refresh_report_fund_plans(
        LocalHistory(), {"actionablePlans": [nav, fund]}, NOW, histories=[history()]
    )
    assert result["actionablePlans"][0]["methodVersion"] == "actionable-zones-v6"
    assert result["actionablePlans"][0]["market"] == "FUND_CN"
    assert result["actionablePlans"][1]["subjectId"] == subject


@pytest.mark.asyncio
async def test_foreign_currency_and_mismatched_nav_identity_are_never_repriced():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    plans = [
        old_plan(unit="USD"),
        old_plan(subjectId="000001"),
        old_plan(market="FUND_HK"),
    ]
    result = await refresh_report_fund_plans(
        LocalHistory(), {"actionablePlans": plans}, NOW, histories=[history()]
    )
    assert result["actionablePlans"] == plans


@pytest.mark.asyncio
async def test_newer_local_nav_wins_over_already_loaded_complete_alpha_history():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    prior, latest = history(), history()
    prior["points"] = prior["points"][:-1]
    prior["asOf"] = prior["points"][-1]["date"]
    latest["points"][-1]["returnPct"] = "1.25"
    result = await refresh_report_fund_plans(
        LocalHistory([latest]),
        {"actionablePlans": [old_plan()]},
        NOW,
        histories=[prior],
    )
    plan = result["actionablePlans"][0]
    assert plan["asOf"] == latest["asOf"]
    assert plan["currentChangePct"] == "1.25"


@pytest.mark.asyncio
async def test_invalid_alpha_cohort_cannot_supply_return_for_selected_valid_history():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    invalid, valid = history(), history()
    invalid["points"][-1]["returnPct"] = "99"
    invalid["points"].append(
        {"date": (NOW.date() + timedelta(days=1)).isoformat(), "close": "1"}
    )
    valid["points"][-1]["returnPct"] = "1.25"
    result = await refresh_report_fund_plans(
        LocalHistory([valid]),
        {"actionablePlans": [old_plan()]},
        NOW,
        histories=[invalid],
    )
    assert result["actionablePlans"][0]["currentChangePct"] == "1.25"


@pytest.mark.asyncio
async def test_partial_chart_source_keeps_actual_new_nav_but_never_claims_buy_ready():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    partial = history()
    partial["points"] = partial["points"][-140:]
    result = await refresh_report_fund_plans(
        LocalHistory([partial]), {"actionablePlans": [old_plan()]}, NOW
    )
    plan = result["actionablePlans"][0]
    assert plan["currentPrice"] != "99.99"
    assert plan["priceRefresh"]["state"] == "PARTIAL"
    assert all(track["entryReady"] is False for track in plan["tracks"])
    assert all(signal["entryReady"] is False for signal in plan["signals"])


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_count", [1, 19, 20, 60, 119])
async def test_short_history_retains_real_nav_and_every_calculable_reference(sample_count):
    from backend.indicators.actionable_zones import compute_actionable_price_plan
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    short = history()
    short["points"] = short["points"][-sample_count:]
    short.pop("providerHistoryProof")
    report = {"valueDirections": [{
        "auditStatus": "UNAUDITED_SCOPE", "qualificationStatus": "OBSERVATION_ONLY",
        "plans": [old_plan()],
    }]}
    before = deepcopy(report)
    expected = compute_actionable_price_plan(
        short["points"], subject_kind="nav", subject_id="008298",
        price_basis="UNIT_NAV", unit="CNY/份", market="FUND_CN",
        source_uri=short["sourceUri"],
    )

    result = await refresh_report_fund_plans(
        LocalHistory([short]), report, NOW, include_series=True,
    )

    direction = result["valueDirections"][0]
    plan = direction["plans"][0]
    assert direction["auditStatus"] == "UNAUDITED_SCOPE"
    assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
    assert plan["currentPrice"] == expected["currentPrice"]
    assert plan["asOf"] == short["asOf"]
    assert plan["sourceUri"] == short["sourceUri"]
    assert plan["retrievedAt"] == short["retrievedAt"]
    assert plan["availableAt"] == short["availableAt"]
    assert plan["confirmedNav"]["value"] == expected["currentPrice"]
    assert plan["historyQuality"] == plan["priceRefresh"]["state"] == "PARTIAL"
    assert plan["historyCoverage"] == "SHORT_HISTORY"
    assert plan["historySampleCount"] == sample_count
    assert plan["inputWindow"]["sampleCount"] == sample_count
    assert plan["priceRefresh"]["reason"] == "LOCAL_NAV_HISTORY_INSUFFICIENT"
    assert plan["indicators"]["navPercentile2y"] is None
    assert plan["historicalPriceWindow"]["state"] == "INSUFFICIENT"
    assert plan["indicatorStates"]["navPercentile2y"]["requiredSamples"] == 120
    assert plan["indicatorStates"]["navPercentile2y"]["sampleCount"] == sample_count
    assert plan["series"]["state"] == "PARTIAL"
    assert plan["series"]["points"] == [
        {"date": row["date"], "value": row["close"]} for row in short["points"]
    ]
    for track, calculated in zip(plan["tracks"], expected["tracks"]):
        for field in (
            "entryZoneMin", "entryZoneMax", "stopLossPrice", "takeProfitT1",
            "takeProfitT2", "sellZoneMin", "sellZoneMax",
        ):
            assert track[field] == calculated[field]
            assert (track[field] is not None) is (sample_count >= 20)
        assert track["entryReady"] is False
        assert track["priceState"] == ("PARTIAL" if sample_count >= 20 else "INSUFFICIENT")
    assert all(signal["entryReady"] is False for signal in plan["signals"])
    assert plan["actionableZones"]["currentPrice"] == expected["currentPrice"]
    assert plan["actionableZones"]["buyZone"]["min"] == expected["tracks"][1]["entryZoneMin"]
    assert report == before


@pytest.mark.asyncio
async def test_current_guards_reapply_without_treating_old_packet_news_as_current():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    plan = old_plan("162411", label="原油QDII", overseasCalendarRequired=True)
    report = {"actionablePlans": [plan]}
    old_packet = {
        "documents": [{"title": "WTI原油下跌3%", "publishedDate": "2026-10-15"}]
    }
    result = await refresh_report_fund_plans(
        LocalHistory([history("162411")]), report, NOW, packet=old_packet
    )
    assert (
        result["actionablePlans"][0].get("entryBlockReason")
        != "DELAYED_NAV_MARKET_DROP"
    )
    current_packet = {
        "series": [
            {"code": "WTI_CHANGE_PCT", "value": -3, "asOf": NOW.date().isoformat()}
        ]
    }
    result = await refresh_report_fund_plans(
        LocalHistory([history("162411")]), report, NOW, packet=current_packet
    )
    assert result["actionablePlans"][0]["entryBlockReason"] == "DELAYED_NAV_MARKET_DROP"
    assert result["actionablePlans"][0]["entryReady"] is False


@pytest.mark.asyncio
async def test_sparse_chart_coverage_without_source_proof_is_not_executable():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    sparse = history()
    sparse["points"] = sparse["points"][::4]
    sparse["asOf"] = sparse["points"][-1]["date"]
    sparse.pop("providerHistoryProof")
    chart = select_history_view("008298", "银行联接A", "2y", [sparse], NOW)
    assert chart["state"] == "READY"
    result = await refresh_report_fund_plans(
        LocalHistory([sparse]), {"actionablePlans": [old_plan()]}, NOW
    )
    plan = result["actionablePlans"][0]
    assert plan["priceRefresh"]["state"] == "PARTIAL"
    assert plan["priceRefresh"]["reason"] == "LOCAL_NAV_CONTINUITY_UNVERIFIED"
    assert all(track["entryReady"] is False for track in plan["tracks"])


@pytest.mark.asyncio
async def test_more_than_46_codes_are_batched_without_retaining_old_prices():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    conn = LocalHistory()
    result = await refresh_report_fund_plans(
        conn,
        {"actionablePlans": [old_plan(f"{100000 + i:06d}") for i in range(47)]},
        NOW,
    )
    assert len(conn.calls) == 2
    assert [len(args[0]) for _, args in conn.calls] == [46, 1]
    assert all(
        plan["methodVersion"] == "actionable-zones-v6" and plan["currentPrice"] is None
        for plan in result["actionablePlans"]
    )


@pytest.mark.asyncio
async def test_same_eight_source_cohorts_as_chart_can_select_third_full_history():
    from backend.investment.live_fund_plans import refresh_report_fund_plans

    full = history()
    full["retrievedAt"] = (NOW - timedelta(hours=1)).isoformat()
    full["providerHistoryProof"]["retrievedAt"] = full["retrievedAt"]
    short = history()
    short["points"] = short["points"][-100:]

    class Cohorts(LocalHistory):
        async def fetch(self, sql, *args):
            self.calls.append((sql, args))
            rows = [short, deepcopy(short), full]
            return [
                {"fund_code": "008298", "payload": row}
                for row in rows[: 8 if "recent_rank<=8" in sql else 2]
            ]

    result = await refresh_report_fund_plans(
        Cohorts(), {"actionablePlans": [old_plan()]}, NOW
    )
    chart = select_history_view(
        "008298", "银行联接A", "2y", [short, deepcopy(short), full], NOW
    )
    assert chart["state"] == "READY"
    assert result["actionablePlans"][0]["priceRefresh"]["state"] == "READY"
    assert result["actionablePlans"][0]["historicalPriceWindow"]["sampleCount"] == len(
        chart["points"]
    )
