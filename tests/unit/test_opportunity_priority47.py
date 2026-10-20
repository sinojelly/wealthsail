"""Public priorities rank executable research, never invent a win probability."""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module

import pytest

from test_daily_research import NOW
from test_quantitative_scan import scan_plan, scan


def plan(code="008281", *, candidate=True, current="1.0", rsi=25):
    result = scan_plan(code, label="半导体芯片ETF联接A", currentPrice=current)
    result["indicators"].update(rsi14=rsi, momentum20=0.1)
    result["selectedTrack"] = "LEFT_VALUE"
    result["tracks"] = [
        {
            "track": "LEFT_VALUE",
            "state": "CANDIDATE" if candidate else "WATCH",
            "priceState": "READY",
            "entryZoneMin": "0.9",
            "entryZoneMax": "1.1",
            "stopLossPrice": "0.8",
            "takeProfitT1": "1.4",
            "takeProfitT2": "1.6",
            "reasons": [],
        }
    ]
    return result


def evidence():
    def media(identity, parent):
        return {
            "id": identity,
            "parentSourceId": parent,
            "title": "云南锗业半导体订单增长与回购",
            "publishedAt": NOW.isoformat(),
            "retrievedAt": NOW.isoformat(),
            "sourceUri": f"https://www.cls.cn/detail/{parent}",
        }

    return {
        "asOf": NOW.isoformat(),
        "alphaPublic": {
            "catalysts": [media("a", "1"), media("b", "1"), media("c", "2")]
        },
        "institutionalSignals": {
            "etfFlows": [
                {
                    "fundCode": "588000",
                    "fundName": "芯片ETF",
                    "metric": "ETF_SHARE_CHANGE",
                    "value": "100000",
                    "unit": "份",
                    "referenceDate": (NOW - timedelta(days=1)).date().isoformat(),
                    "comparisonDate": (NOW - timedelta(days=2)).date().isoformat(),
                    "retrievedAt": NOW.isoformat(),
                    "sourceUri": "https://www.sse.com.cn/market/funddata/",
                    "verification": "EXCHANGE_REPORTED",
                    "previousShares": "1000000",
                    "currentShares": "1100000",
                    "comparisonSourceUri": "https://www.sse.com.cn/market/funddata/",
                }
            ]
        },
    }


def test_priority_uses_independent_parent_sources_and_actual_primary_share_growth():
    score_plan = import_module("backend.investment.opportunity_scoring").score_plan
    original = plan()
    scored = score_plan(original, evidence(), NOW)
    # 40 ready + 15 two independent sources + 15 primary shares + 7.5 at RR=2 + 10 RSI.
    assert scored["opportunityScore"] == 87.5
    assert scored["rewardRiskRatio"] == 2
    assert scored["scoreBreakdown"]["catalyst"] == 15
    assert scored["scoreBreakdown"]["etfShares"] == 15
    assert "opportunityScore" not in original
    assert scored["tracks"][0]["opportunityScore"] == 87.5


@pytest.mark.parametrize(
    "change", ["secondary", "old", "future", "negative", "other-sector", "bad-source"]
)
def test_secondary_money_or_stale_unrelated_or_unproved_shares_never_earn_subscription_score(
    change,
):
    mod = import_module("backend.investment.opportunity_scoring")
    packet = evidence()
    row = packet["institutionalSignals"]["etfFlows"][0]
    if change == "secondary":
        row["metric"] = "SECONDARY_MAIN_NET_FLOW_CNY"
    if change == "old":
        row["referenceDate"] = (NOW - timedelta(days=30)).date().isoformat()
    if change == "future":
        row["referenceDate"] = (NOW + timedelta(days=1)).date().isoformat()
    if change == "negative":
        row["value"] = "-100000"
    if change == "other-sector":
        row["fundName"] = "黄金ETF"
    if change == "bad-source":
        row["sourceUri"] = "https://example.org/fake"
    assert mod.score_plan(plan(), packet, NOW)["scoreBreakdown"]["etfShares"] == 0


def test_not_yet_in_zone_with_good_news_ranks_after_ready_without_news():
    mod = import_module("backend.investment.opportunity_scoring")
    first = plan("008281", current="1.2")
    second = plan("000051")
    report = {
        "asOf": NOW.isoformat(),
        "valueDirections": [
            {"key": "waiting", "plans": [first]},
            {"key": "ready", "plans": [second]},
        ],
        "quantitativeScan": {**scan(0), "items": [first, second]},
    }
    before = deepcopy(report)
    result = mod.rank_report(report, evidence())
    assert [d["key"] for d in result["valueDirections"]] == ["ready", "waiting"]
    assert report == before
    assert all(
        d["opportunityScore"] == max(p["opportunityScore"] for p in d["plans"])
        for d in result["valueDirections"]
    )


@pytest.mark.parametrize(
    "level,key,value",
    [
        ("plan", "entryBlocked", True),
        ("track", "entryBlocked", True),
        ("plan", "subscriptionRestricted", True),
        ("plan", "preferredEntryEligible", False),
        ("track", "entryReady", False),
        ("track", "inBuyZone", False),
    ],
)
def test_safety_guards_cannot_be_promoted_back_to_ready_by_priority(level, key, value):
    mod = import_module("backend.investment.opportunity_scoring")
    guarded = plan()
    (guarded if level == "plan" else guarded["tracks"][0])[key] = value
    scored = mod.score_plan(guarded, evidence(), NOW)
    assert mod.priority_key(scored, scored["tracks"][0])[0] != 0
    assert scored["scoreBreakdown"]["technical"] < 40


def test_forged_model_score_is_replaced_and_high_rsi_does_not_receive_buy_priority():
    mod = import_module("backend.investment.opportunity_scoring")
    hot = plan("008281", rsi=74)
    hot["opportunityScore"] = 999
    ready = plan("000051")
    result = mod.rank_report(
        {
            "asOf": NOW.isoformat(),
            "valueDirections": [
                {"key": "hot", "plans": [hot]},
                {"key": "ready", "plans": [ready]},
            ],
        },
        evidence(),
    )
    assert result["valueDirections"][0]["key"] == "ready"
    assert result["valueDirections"][1]["plans"][0]["opportunityScore"] <= 100


def test_same_run_reading_projection_adds_scores_and_fixes_known_code_prose_only():
    from backend.investment.quantitative_scan import report_with_quantitative_scan

    raw = {
        "asOf": NOW.isoformat(),
        "summary": "基金0183447004854及0127375006756、云南 业的半导体材料。",
        "sourceIds": ["0183447004854"],
        "valueDirections": [],
        "quantitativeScan": {
            **scan(0),
            "items": [plan(c) for c in ("018344", "004854", "012737", "006756")],
        },
    }
    before = deepcopy(raw)
    result = report_with_quantitative_scan(
        raw, {**evidence(), "quantitativeScan": raw["quantitativeScan"]}, NOW
    )
    assert (
        "018344与004854" in result["summary"] and "012737与006756" in result["summary"]
    )
    assert "云南锗业" in result["summary"]
    assert result["sourceIds"] == raw["sourceIds"]
    assert result["quantitativeScan"]["items"][0]["opportunityScore"] >= 0
    assert raw == before


def test_46_scan_rows_and_compact_projection_are_supported_together():
    from backend.investment.quantitative_scan import public_quantitative_scan
    from backend.macro.model_projection import model_packet

    result = public_quantitative_scan(scan(46), NOW)
    assert (
        len(result["items"])
        == len(model_packet({"quantitativeScan": result})["quantitativeScan"]["items"])
        == 46
    )


@pytest.mark.parametrize(
    "names",
    [
        [],
        ["云南锗业", "云南锡业"],
        ["云南锗业", "云南铜业"],
        ["云南锡业", "云南铜业"],
    ],
)
def test_missing_company_character_is_not_guessed_from_generic_materials_context(names):
    from backend.investment.quantitative_scan import report_with_quantitative_scan

    packet = {
        "asOf": NOW.isoformat(),
        "alphaPublic": {"catalysts": [{"title": name} for name in names]},
    }
    raw = {
        "asOf": NOW.isoformat(),
        "summary": "云南 业的半导体材料业务。",
        "quantitativeScan": {**scan(0), "items": [plan()]},
    }
    assert report_with_quantitative_scan(raw, packet, NOW)["summary"] == raw["summary"]


@pytest.mark.parametrize("company", ["云南锗业", "云南锡业"])
def test_missing_company_character_uses_the_only_explicit_source_name(company):
    from backend.investment.quantitative_scan import report_with_quantitative_scan

    packet = {
        "asOf": NOW.isoformat(),
        "documents": [{"title": f"{company}订单增长"}],
    }
    raw = {"asOf": NOW.isoformat(), "summary": "云南 业订单增长。"}
    original = deepcopy(raw)
    assert report_with_quantitative_scan(raw, packet, NOW)["summary"] == (
        f"{company}订单增长。"
    )
    assert raw == original


@pytest.mark.asyncio
async def test_daily_pool_keeps_the_40_base_funds_and_six_new_event_candidates(
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from backend.investment import discovery, dynamic_discovery

    base = (
        "320007 008281 005609 161129 008975 008298 000051 012733 011035 004432 004069 013477 018344 003017 008086 012737 009179 000216 162411 270042 021607 004854 007466 000962 110020 202015 001632 011103 161725 161726 006756 001361 001629 016185 018027 024194 007300 014880 000218 023530"
    ).split()
    extra = [f"{i:06}" for i in range(700001, 700007)]

    def asset(code):
        return {
            "fundCode": code,
            "subjectKind": "nav",
            "subjectId": code,
            "market": "FUND_CN",
            "label": code,
        }

    monkeypatch.setattr(
        discovery,
        "read_discovery_assets",
        AsyncMock(return_value=[asset(code) for code in base]),
    )

    async def events(conn, now, catalysts, excluded_codes):
        assert set(excluded_codes) == set(base)
        return [asset(code) for code in extra]

    monkeypatch.setattr(dynamic_discovery, "read_dynamic_assets", events)
    selected = await discovery.select_research_assets(object(), NOW, [], [])
    assert [row["fundCode"] for row in selected] == base + extra
