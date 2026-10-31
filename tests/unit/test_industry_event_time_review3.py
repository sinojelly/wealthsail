"""Article publication dates do not establish when an industry event happened."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest


NOW = datetime(2026, 10, 31, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def verify(quote, published="2026-10-31", now=NOW):
    from backend.macro.industry_evidence_time import event_time_verification

    return event_time_verification(quote, published, now)


def test_recent_article_cannot_make_last_years_financing_event_recent():
    result = verify("2025年1月，机器人行业融资金额大幅下降。")
    assert result["eventTimeVerification"] == "HISTORICAL"
    assert result["eventPeriodStart"] == "2025-01-01"
    assert result["eventPeriodEnd"] == "2025-01-31"
    assert result["eventPeriod"]


@pytest.mark.parametrize(
    "quote",
    ["2025年行业融资减少。", "2026年企业出现裁员。", "企业出现欠薪。", "", None],
)
def test_coarse_year_or_missing_event_period_remains_unverified(quote):
    result = verify(quote)
    assert result["eventTimeVerification"] == "UNVERIFIED"
    assert result["eventPeriod"] is None


@pytest.mark.parametrize(
    "quote,published,now",
    [
        ("近期行业融资降温。", None, NOW),
        ("近期行业融资降温。", "not-a-date", NOW),
        ("近期行业融资降温。", "2026-02-30", NOW),
        ("近期行业融资降温。", "2026-11-01", NOW),
        ("近期行业融资降温。", "2026-10-31", None),
        ("近期行业融资降温。", "2026-10-31", datetime(2026, 10, 31)),
    ],
)
def test_invalid_reference_clocks_do_not_certify_event_time(quote, published, now):
    assert verify(quote, published, now)["eventTimeVerification"] == "UNVERIFIED"


@pytest.mark.parametrize("days,expected", [(90, "RECENT"), (91, "HISTORICAL")])
def test_ninety_day_boundary_uses_actual_event_date_not_article_date(days, expected):
    happened = NOW.date() - timedelta(days=days)
    result = verify(f"{happened.isoformat()}，行业融资金额下降。")
    assert result["eventTimeVerification"] == expected
    assert result["eventPeriodStart"] == happened.isoformat()
    assert result["eventPeriodEnd"] == happened.isoformat()


@pytest.mark.parametrize(
    "quote,start,end,expected",
    [
        ("2026年9月行业订单减少。", "2026-09-01", "2026-09-30", "RECENT"),
        ("2026年第三季度行业融资下降。", "2026-07-01", "2026-09-30", "RECENT"),
        ("2026年二季度出现倒闭。", "2026-04-01", "2026-06-30", "HISTORICAL"),
        ("2026Q3行业融资减少。", "2026-07-01", "2026-09-30", "RECENT"),
        ("2026年11月行业将裁员。", "2026-11-01", "2026-11-30", "FUTURE"),
    ],
)
def test_month_and_quarter_use_their_actual_calendar_intervals(
    quote, start, end, expected
):
    result = verify(quote)
    assert result["eventTimeVerification"] == expected
    assert result["eventPeriodStart"] == start
    assert result["eventPeriodEnd"] == end


def test_explicit_range_uses_its_end_without_turning_a_whole_year_into_recent_data():
    result = verify("2025年1月至2025年3月，行业融资金额下降。")
    assert result["eventTimeVerification"] == "HISTORICAL"
    assert result["eventPeriodStart"] == "2025-01-01"
    assert result["eventPeriodEnd"] == "2025-03-31"


@pytest.mark.parametrize(
    "quote,start,end",
    [
        ("本月行业融资减少。", "2026-09-01", "2026-09-15"),
        ("本季度行业订单减少。", "2026-07-01", "2026-09-15"),
        ("上月行业融资减少。", "2026-08-01", "2026-08-31"),
    ],
)
def test_relative_periods_are_anchored_to_publication_not_the_reading_clock(
    quote, start, end
):
    result = verify(quote, published=date(2026, 9, 15))
    assert result["eventTimeVerification"] == "RECENT"
    assert result["eventPeriodStart"] == start
    assert result["eventPeriodEnd"] == end


def test_recent_wording_in_old_article_is_historical_when_read_today():
    assert (
        verify("近期该行业融资下降。", "2026-01-15")["eventTimeVerification"]
        == "HISTORICAL"
    )


def test_relative_recent_announcement_is_not_replaced_by_future_delivery_deadline():
    result = verify("近期公司宣布量产延期至2028年1月。")
    assert result["eventTimeVerification"] == "RECENT"
    assert result["eventPeriodEnd"] == "2026-10-31"


def test_future_deadline_without_event_time_does_not_certify_a_recent_announcement():
    assert verify("公司量产延期至2028年1月。")["eventTimeVerification"] == "UNVERIFIED"


@pytest.mark.parametrize(
    "quote",
    [
        "2026年2月30日出现裁员。",
        "2026年第四季度行业融资下降。",
        "2025年1月融资下降，2026年10月企业发布新产品。",
    ],
)
def test_invalid_partial_or_ambiguous_event_intervals_are_not_certified(quote):
    assert verify(quote)["eventTimeVerification"] == "UNVERIFIED"


def test_recent_reporting_phrase_cannot_override_an_older_coarse_event_year():
    assert (
        verify("近日披露，2025年融资总量下降。")["eventTimeVerification"]
        == "UNVERIFIED"
    )


@pytest.mark.parametrize("published", [NOW + timedelta(hours=1), date(1, 1, 1)])
def test_future_timestamp_and_calendar_underflow_remain_unverified(published):
    assert (
        verify("上月行业融资下降。", published)["eventTimeVerification"] == "UNVERIFIED"
    )
