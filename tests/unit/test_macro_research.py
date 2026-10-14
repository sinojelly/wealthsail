"""Research is a reproducible observation, never a buy/sell score."""

from datetime import datetime, timedelta
from decimal import Decimal
from importlib import import_module
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def engine():
    return import_module("backend.macro.research")


def history(code="PMI", values=(48, 49, 49.5, 50.1), start=6, **changes):
    return [
        {
            "asOf": f"2026-{start + i:02d}-01",
            "value": Decimal(str(value)),
            "unit": "" if code == "PMI" else "%",
            "source": "AKSHARE_RESEARCH_MACRO",
            "sourceUri": f"https://data.eastmoney.com/cjsj/{code}.html",
            "revision": 1,
            **changes,
        }
        for i, value in enumerate(values)
    ]


def item(code="PMI", value="50.1", period="2026-09", **changes):
    return {
        "code": code,
        "label": code,
        "value": Decimal(value) if value is not None else None,
        "asOf": period + "-01T00:00:00+08:00",
        "unit": "" if code == "PMI" else "%",
        "source": "AKSHARE_RESEARCH_MACRO",
        "state": "UNVERIFIED",
        "publishedAt": None,
        "retrievedAt": NOW,
        "revision": 1,
        **changes,
    }


def official(code="PMI", value="50.1", **changes):
    return {
        "field_code": code,
        "reference_period": "2026-09",
        "value": Decimal(value),
        "unit": "" if code == "PMI" else "%",
        "publisher": "国家统计局",
        "source_uri": "https://www.stats.gov.cn/sj/zxfb/202610/t20261001_1.html",
        "published_at": None,
        "retrieved_at": NOW - timedelta(minutes=1),
        "revision": 1,
        **changes,
    }


def enrich(base=None, points=None, releases=None, schedules=None):
    return engine().enrich_item(
        base or item(),
        history() if points is None else points,
        {"releases": releases or [], "schedules": schedules or [], "events": []},
        NOW,
    )


def test_reference_month_converts_utc_midnight_to_shanghai():
    result = enrich(item(asOf="2026-08-31T16:00:00+00:00"))
    assert result["referencePeriod"] == "2026-09"


def test_official_value_match_does_not_invent_publication_time():
    result = enrich(releases=[official()])
    assert result["valueVerification"] == "VERIFIED"
    assert result["publicationStatus"] == "UNKNOWN"
    assert result["publishedAt"] is None
    assert result["officialEvidence"]["sourceUri"].startswith(
        "https://www.stats.gov.cn/"
    )


def test_conflicting_value_can_have_confirmed_publication_but_no_level():
    result = enrich(
        releases=[official(value="49.9", published_at=NOW - timedelta(days=1))]
    )
    assert result["valueVerification"] == "CONFLICT"
    assert result["publicationStatus"] == "CONFIRMED"
    assert result["interpretation"]["level"] == "UNAVAILABLE"
    assert result["statistics"]["change3Months"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"published_at": NOW + timedelta(seconds=1)},
        {"retrieved_at": NOW + timedelta(seconds=1)},
        {"reference_period": "2026-08"},
        {"unit": "%"},
        {"source_uri": "https://stats.gov.cn.evil.example/fake"},
    ],
)
def test_inapplicable_or_future_official_evidence_cannot_verify(change):
    assert enrich(releases=[official(**change)])["valueVerification"] == "PENDING"


def test_latest_official_revision_governs_and_conflicting_publishers_abstain():
    result = enrich(releases=[official(), official(value="49.9", revision=2)])
    assert result["valueVerification"] == "CONFLICT"
    result = enrich(
        releases=[
            official(),
            official(value="49.9", source_uri="https://www.stats.gov.cn/other"),
        ]
    )
    assert result["valueVerification"] == "CONFLICT"


def test_database_code_contract_and_ready_require_both_independent_proofs():
    release = official(published_at=NOW - timedelta(days=1))
    release["code"] = release.pop("field_code")
    result = enrich(releases=[release])
    assert result["valueVerification"] == "VERIFIED"
    assert result["state"] == "READY"
    assert enrich()["state"] == "UNVERIFIED"


def test_latest_revision_and_old_period_cannot_claim_current_research():
    result = enrich(item(revision=2))
    assert result["statistics"]["previousValue"] is None
    result = enrich(
        item(period="2026-01", retrievedAt=NOW), history(start=1, values=(50.1,))
    )
    assert result["state"] == "STALE"
    assert result["interpretation"]["level"] == "UNAVAILABLE"


def test_summary_marks_pending_value_as_conditional_observation():
    result = engine().research_context([enrich()])
    assert "待核验" in result["summary"]


def test_known_historical_conflict_breaks_multi_month_statistics():
    result = enrich(releases=[official(reference_period="2026-07", value="48.5")])
    assert result["statistics"]["change3Months"] is None
    assert "2026-07" in result["statistics"]["missingPeriods"]


def test_newer_official_period_prevents_stale_provider_current_interpretation():
    newer = official(
        reference_period="2026-10", published_at=NOW - timedelta(minutes=5)
    )
    schedule = {
        "code": "PMI",
        "reference_period": "2026-10",
        "scheduled_at": NOW - timedelta(hours=1),
        "retrieved_at": NOW - timedelta(days=1),
        "source_uri": "https://www.stats.gov.cn/calendar",
    }
    result = enrich(releases=[newer], schedules=[schedule])
    assert result["state"] == "STALE"
    assert result["interpretation"]["level"] == "UNAVAILABLE"
    assert result["nextRelease"] is None
    assert any("更晚" in text for text in result["interpretation"]["limitations"])


def test_three_month_change_requires_all_four_contiguous_same_basis_points():
    result = enrich()
    assert result["statistics"]["change3Months"] == Decimal("2.1")
    assert result["statistics"]["change6Months"] is None
    assert result["benchmark"]["value"] == 50
    assert result["interpretation"]["level"] == "EXPANSION"
    assert result["interpretation"]["momentum"] == "RISING"
    assert any(
        "跨越" in change["summary"]
        for change in engine().research_context([result])["changes"]
    )
    assert (
        enrich(points=history()[::2] + history()[-1:])["statistics"]["change3Months"]
        is None
    )
    shifted = history()
    shifted[1]["sourceUri"] = "https://different.example/history"
    assert enrich(points=shifted)["statistics"]["change3Months"] is None


def test_percentile_uses_ties_and_real_sample_count_not_requested_months():
    points = [
        {
            **history(code="M2_YOY", values=(7,), start=1)[0],
            "asOf": f"2025-{i + 1:02d}-01",
        }
        for i in range(12)
    ]
    result = enrich(item("M2_YOY", "7", "2025-12"), points)
    assert result["statistics"]["percentile"] == 50
    assert result["statistics"]["median"] == 7
    assert result["benchmark"]["label"] == "历史样本中位数"
    assert "紧缩" not in result["interpretation"]["summary"]
    assert enrich(item("M2_YOY", "7"), points[:11])["statistics"]["percentile"] is None


def test_missing_months_and_changed_units_disable_comparable_statistics():
    points = history()
    points[1]["unit"] = "%"
    result = enrich(points=points)
    assert result["statistics"]["change3Months"] is None
    assert result["statistics"]["median"] is None
    result = enrich(points=history()[::2] + history()[-1:])
    assert result["statistics"]["missingPeriods"] == ["2026-07"]


def test_unusable_latest_fact_does_not_recover_old_history_level():
    result = enrich(item(asOf=None, value=None, state="CONFLICT"))
    assert result["valueVerification"] == "UNAVAILABLE"
    assert result["interpretation"]["level"] == "UNAVAILABLE"
    assert result["statistics"]["previousValue"] is None


def test_schedule_is_never_actual_publication_and_past_plan_is_overdue():
    schedule = {
        "field_code": "PMI",
        "reference_period": "2026-10",
        "scheduled_at": NOW - timedelta(hours=1),
        "retrieved_at": NOW - timedelta(days=2),
        "source_uri": "https://www.stats.gov.cn/calendar",
        "revision": 1,
    }
    result = enrich(schedules=[schedule])
    assert result["publicationStatus"] == "UNKNOWN"
    assert result["nextRelease"]["status"] == "OVERDUE"
    assert result["nextRelease"]["referencePeriod"] == "2026-10"
    assert result["nextRelease"]["sourceUri"] == schedule["source_uri"]


def test_four_dimensions_report_manufacturing_credit_and_market_gaps():
    result = engine().research_context([enrich()])
    assert result["methodVersion"] == "macro-research-v1"
    dimensions = {d["id"]: d for d in result["dimensions"]}
    assert dimensions["growth"]["state"] == "PARTIAL"
    assert dimensions["risk"]["state"] == "INSUFFICIENT"
    assert "社融/信贷" in dimensions["liquidity"]["missingEvidence"]
    assert "score" not in result and "buy" not in str(result).lower()


def test_direction_channel_always_includes_counter_case_and_checks():
    context = {"items": [enrich()], "research": engine().research_context([enrich()])}
    broad = engine().direction_research(
        {"id": "a-share:broad", "label": "宽基"}, context
    )
    assert broad["channels"][0]["counterCase"]
    assert "盈利" in str(broad["channels"][0]["watchFor"])
    gold = engine().direction_research(
        {"id": "gold:physical", "label": "黄金"}, context
    )
    assert "实际利率" in str(gold["missingEvidence"])
    assert not engine().direction_research({"id": "unknown", "label": "未知"}, context)[
        "channels"
    ]
