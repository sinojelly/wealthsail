"""Report entry protection uses the same fund identity and current public facts."""

from copy import deepcopy
from datetime import timedelta

import pytest

from backend.macro import daily_research
from test_quantitative_scan import NOW, scan_plan


def fixtures(code="161129", label="易方达原油(QDII)"):
    plan = scan_plan(code, label=label, overseasCalendarRequired=True)
    plan["tracks"][0]["state"] = "CANDIDATE"
    plan["signals"][0].update(triggerState="CANDIDATE", entryReady=True)
    plan["selectedTrack"] = "RIGHT_TREND"
    packet = {
        "asOf": NOW.isoformat(),
        "discoveryUniverse": [{"fundCode": code, "overseasCalendarRequired": True}],
        "documents": [
            {"title": "WTI原油期货下跌2.5%", "publishedDate": NOW.date().isoformat()}
        ],
    }
    report = {
        "quantitativeScan": {"items": [deepcopy(plan)]},
        "valueDirections": [
            {
                "signal": "RIGHT_TREND",
                "plans": [deepcopy(plan)],
                "quantitativeEvidence": [deepcopy(plan)],
            }
        ],
        "actionablePlans": [deepcopy(plan)],
    }
    return report, packet


def test_delayed_oil_nav_drop_overrides_every_report_entry_surface():
    report, packet = fixtures()
    before = deepcopy(report)
    result = daily_research.apply_entry_guards(report, packet, NOW)
    rows = [
        result["quantitativeScan"]["items"][0],
        result["valueDirections"][0]["plans"][0],
        result["valueDirections"][0]["quantitativeEvidence"][0],
        result["actionablePlans"][0],
    ]
    for row in rows:
        assert row["entryStatus"] == "⚠️ 净值滞后1天·当晚外盘急跌暂缓入场"
        assert row["entryStatusTone"] == "amber"
        assert row["entryReady"] is row["inBuyZone"] is False
        assert row["signals"][0]["entryReady"] is False
        assert row["signals"][0]["inEntryZone"] is True
    assert result["valueDirections"][0]["signal"] == "WATCH"
    assert report == before


@pytest.mark.parametrize(
    "headline",
    [
        "WTI原油期货下跌1.99%",
        "WTI原油期货上涨2.5%",
        "标普500下跌3%，WTI原油上涨2%",
        "WTI原油可能下跌3%",
        "WTI原油未下跌3%",
        "预计WTI原油下跌3%",
        "WTI原油上涨2%，黄金下跌3%",
        "WTI原油上涨2%而黄金下跌3%",
        "WTI rose 2% while gold fell 3%",
        "假如WTI原油下跌3%则减仓",
        "WTI原油跌幅不足2%",
        "如果WTI原油下跌3%则减仓",
        "一旦WTI原油下跌3%则减仓",
        "WTI原油跌幅不到2%",
        "WTI原油跌幅未达2%",
        "昨日WTI原油下跌3%",
        "WTI原油将下跌3%",
        "假设WTI原油下跌3%",
    ],
)
def test_unrelated_small_positive_or_conditional_news_cannot_trip_oil_guard(headline):
    report, packet = fixtures()
    packet["documents"][0]["title"] = headline
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert not result["quantitativeScan"]["items"][0].get("entryBlocked")


def test_yesterday_retrieved_again_news_does_not_trip_guard():
    report, packet = fixtures()
    packet["documents"][0].update(
        publishedDate=(NOW - timedelta(days=1)).date().isoformat(),
        retrievedAt=NOW.isoformat(),
    )
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert not result["quantitativeScan"]["items"][0].get("entryBlocked")


def test_corresponding_structured_nasdaq_two_percent_drop_trips_guard():
    report, packet = fixtures("270042", "广发纳斯达克100(QDII)")
    packet["documents"] = []
    packet["series"] = [
        {"code": "US_NASDAQ_CHANGE_PCT", "value": -2, "asOf": NOW.date().isoformat()}
    ]
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert result["quantitativeScan"]["items"][0]["entryBlocked"] is True


def test_fresh_nav_does_not_trip_delayed_nav_guard():
    report, packet = fixtures()
    report["quantitativeScan"]["items"][0]["asOf"] = NOW.date().isoformat()
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert not result["quantitativeScan"]["items"][0].get("entryBlocked")


@pytest.mark.parametrize(
    "status,label", [("SUSPENDED", "⛔ 暂停申购"), ("LIMITED", "⛔ 单日限购10元")]
)
def test_subscription_restrictions_annotate_and_preserve_reference_plans(
    status, label
):
    report, packet = fixtures()
    packet["documents"] = []
    packet["fundSubscriptionStatus"] = {
        "161129": {"purchaseStatus": status, "dailyLimitCny": "10"}
    }
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert result["quantitativeScan"]["items"][0]["subscriptionLabel"] == label
    assert result["quantitativeScan"]["items"][0]["preferredEntryEligible"] is False
    assert result["valueDirections"][0]["plans"][0]["entryBlocked"] is True
    assert result["actionablePlans"][0]["entryBlocked"] is True
    assert result["valueDirections"][0]["plans"][0]["tracks"]
    assert result["valueDirections"][0]["signal"] == "WATCH"


def test_one_year_high_warning_applies_even_when_left_track_would_be_selected():
    report, packet = fixtures()
    packet["documents"] = []
    item = report["quantitativeScan"]["items"][0]
    item["indicators"].update(navPercentile1y=96, ma20=0.97)
    result = daily_research.apply_entry_guards(report, packet, NOW)
    guarded = result["quantitativeScan"]["items"][0]
    assert guarded["entryStatus"] == "⚠️ 处于近1年高位区(96%分位)·勿追高"
    assert guarded["signals"][0]["entryReady"] is False


def test_existing_ready_report_uses_its_own_packet_and_report_clock_for_guard():
    from backend.investment.quantitative_scan import report_with_quantitative_scan

    report, packet = fixtures()
    report["asOf"] = NOW.isoformat()
    result = report_with_quantitative_scan(report, packet, NOW + timedelta(days=3))
    assert (
        result["quantitativeScan"]["items"][0]["entryBlockReason"]
        == "DELAYED_NAV_MARKET_DROP"
    )


def test_build_prompt_gets_guarded_copy_without_mutating_audit_packet():
    import json

    report, packet = fixtures()
    packet.update(quantitativeScan=report["quantitativeScan"])
    before = deepcopy(packet)
    prompt = daily_research.build_prompt(packet)
    public = json.loads(prompt[prompt.index('{"alphaPublic"') :])
    assert public["quantitativeScan"]["items"][0]["entryReady"] is False
    assert packet == before


def test_limited_triggered_plan_cannot_leave_a_watch_only_alternative_as_buy_signal():
    report, packet = fixtures()
    packet["documents"] = []
    packet["fundSubscriptionStatus"] = {"161129": {"purchaseStatus": "SUSPENDED"}}
    alternative = scan_plan("008281", selectedTrack="RIGHT_TREND")
    report["valueDirections"][0]["plans"].append(alternative)
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert len(result["valueDirections"][0]["plans"]) == 2
    assert result["valueDirections"][0]["signal"] == "WATCH"


@pytest.mark.parametrize(
    "variation,blocked",
    [
        ("fresh", True),
        ("stale_quote", False),
        ("nonfresh", False),
        ("already_in_nav", False),
    ],
)
def test_verified_us_session_loss_only_blocks_nav_before_that_session(
    variation, blocked
):
    report, packet = fixtures("270042", "广发纳斯达克100(QDII)")
    packet["documents"] = []
    session = (NOW - timedelta(days=1)).date()
    metadata = {"qualityStatus": "FRESH", "sourceAsOf": f"{session}T16:00:00-04:00"}
    if variation == "stale_quote":
        metadata["sourceAsOf"] = f"{session - timedelta(days=1)}T16:00:00-04:00"
    elif variation == "nonfresh":
        metadata["qualityStatus"] = "STALE"
    packet["series"] = [
        {
            "code": "US_NASDAQ_CHANGE_PCT",
            "value": -3,
            "asOf": session.isoformat(),
            "retrievedAt": NOW.isoformat(),
            "definition": metadata,
        }
    ]
    item = report["quantitativeScan"]["items"][0]
    item["asOf"] = (
        session if variation == "already_in_nav" else session - timedelta(days=1)
    ).isoformat()
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert bool(result["quantitativeScan"]["items"][0].get("entryBlocked")) is blocked


def test_packet_preserves_bounded_overnight_quote_provenance():
    metadata = {
        "sourceAsOf": "2026-10-15T16:00:00-04:00",
        "qualityStatus": "FRESH",
        "secret": "not public",
    }
    packet = daily_research.make_research_packet(
        {},
        [
            {
                "code": "US_NASDAQ_CHANGE_PCT",
                "value": -3,
                "asOf": "2026-10-15",
                "metadata": metadata,
            }
        ],
        [],
        [],
        now=NOW,
    )
    assert packet["series"][0]["definition"]["sourceAsOf"] == metadata["sourceAsOf"]
    assert packet["series"][0]["definition"]["qualityStatus"] == "FRESH"
    assert "secret" not in packet["series"][0]["definition"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "CONFLICT"),
        ("state", "STALE"),
        ("state", "UNAVAILABLE"),
        ("qualityStatus", "STALE"),
    ],
)
def test_invalid_current_structured_market_fact_cannot_trip_guard(field, value):
    report, packet = fixtures("270042", "广发纳斯达克100(QDII)")
    packet["documents"] = []
    observation = {
        "code": "US_NASDAQ_CHANGE_PCT",
        "value": -3,
        "asOf": NOW.date().isoformat(),
    }
    if field == "qualityStatus":
        observation["definition"] = {field: value}
    else:
        observation[field] = value
    packet["series"] = [observation]
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert not result["quantitativeScan"]["items"][0].get("entryBlocked")


def test_sp_oil_fund_does_not_inherit_sp500_index_exposure_from_brand_name():
    report, packet = fixtures("162411", "华宝标普油气(QDII)")
    packet["documents"] = []
    packet["series"] = [
        {"code": "US_SP500_CHANGE_PCT", "value": -3, "asOf": NOW.date().isoformat()}
    ]
    result = daily_research.apply_entry_guards(report, packet, NOW)
    assert not result["quantitativeScan"]["items"][0].get("entryBlocked")
