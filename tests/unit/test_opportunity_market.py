"""New series retain frequency, definition and first-known timestamps."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from importlib import import_module, util
from zoneinfo import ZoneInfo
import pytest

NOW = datetime(2026, 10, 14, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def repo():
    assert util.find_spec("backend.macro.market_repository"), (
        "opportunity market facts are not implemented"
    )
    return import_module("backend.macro.market_repository")


def row(code="US_T10_REAL", value="1.5", day=date(2026, 10, 13), **changes):
    return {
        "code": code,
        "reference_date": day,
        "frequency": "DAILY",
        "value": Decimal(value),
        "unit": "%",
        "source_uri": "https://home.treasury.gov/resource-center-data-chart-center/interest-rates/pages/xml?year=2026",
        "source_name": "美国财政部",
        "published_at": None,
        "retrieved_at": NOW - timedelta(hours=1),
        "available_at": NOW - timedelta(hours=1),
        "time_basis": "RETRIEVAL_ONLY",
        "method_version": "treasury-v1",
        "metadata": {"definitionKey": code, "definitionVersion": "UST-v1"},
        "revision": 1,
        **changes,
    }


def context(rows, **kwargs):
    return {i["code"]: i for i in repo().series_context(rows, NOW, **kwargs)}


def test_official_daily_value_has_no_invented_release_time():
    item = context([row(value="-0.2")])["US_T10_REAL"]
    assert item["value"] == Decimal("-0.2")
    assert item["publishedAt"] is None and item["timeBasis"] == "RETRIEVAL_ONLY"
    assert item["state"] == "READY" and item["frequency"] == "DAILY"


def test_permission_required_sources_remain_empty_without_authorization():
    result = context([row("CN_T10")])
    assert result["CN_T10"]["state"] == "PERMISSION_REQUIRED"
    assert result["CN_T10"]["value"] is None and result["DR007"]["value"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"retrieved_at": NOW + timedelta(seconds=1)},
        {"available_at": NOW + timedelta(seconds=1)},
        {"published_at": NOW + timedelta(seconds=1)},
        {"reference_date": date(2026, 10, 15)},
    ],
)
def test_future_fact_never_appears(changes):
    assert context([row(**changes)])["US_T10_REAL"]["value"] is None


def test_latest_conflict_does_not_restore_older_value():
    result = context(
        [
            row(value="1"),
            row(
                value="2",
                revision=2,
                metadata={"qualityStatus": "CONFLICT", "definitionKey": "US_T10_REAL"},
            ),
        ]
    )
    assert result["US_T10_REAL"]["state"] == "CONFLICT"
    assert result["US_T10_REAL"]["value"] is None


def test_yield_change_is_basis_points_and_url_year_does_not_change_definition():
    rows = [
        row(
            value=str(1 + Decimal(i) / 100),
            day=date(2026, 10, 13) - timedelta(days=20 - i),
        )
        for i in range(21)
    ]
    rows[0]["source_uri"] = rows[0]["source_uri"].replace("2026", "2025")
    result = context(rows)["US_T10_REAL"]
    assert result["changes"]["change5Samples"] == Decimal(5)
    assert result["changes"]["change20Samples"] == Decimal(20)
    assert result["changes"]["changeUnit"] == "bp"
    assert result["changes"]["percentile"] is None


def test_monthly_m1_does_not_mix_old_definition():
    rows = [
        row(
            "M1_YOY",
            str(month),
            date(2026, month, 1),
            frequency="MONTHLY",
            source_uri="https://www.pbc.gov.cn/diaochatongjisi/report",
            method_version="m1-v1",
            metadata={"definitionKey": "M1_2025", "definitionVersion": "M1_2025"},
        )
        for month in range(1, 10)
    ]
    rows[-2]["metadata"]["definitionKey"] = "M1_OLD"
    result = context(rows)["M1_YOY"]
    assert result["changes"]["previousValue"] is None
    assert result["changes"]["change5Samples"] is None
    assert result["changes"]["changeUnit"] == "pp"


def test_cumulative_company_loans_never_create_a_monthly_growth_signal():
    rows = [
        row(
            "CORP_MEDLONG_LOANS_YTD",
            str(month),
            date(2026, month, 1),
            frequency="YTD",
            unit="万亿元",
            source_uri="https://www.pbc.gov.cn/report",
            metadata={
                "definitionKey": "CORP_MEDLONG_LOANS_YTD",
                "accumulationStart": "2026-01",
            },
        )
        for month in (8, 9)
    ]
    item = context(rows)["CORP_MEDLONG_LOANS_YTD"]
    assert item["changes"]["change5Samples"] is None
    assert item["changes"]["changeUnit"] is None


def test_currently_old_observation_stays_stale_despite_new_retrieval():
    assert (
        context([row(day=date(2026, 9, 1), retrieved_at=NOW, available_at=NOW)])[
            "US_T10_REAL"
        ]["state"]
        == "STALE"
    )


def test_monthly_changes_have_explicit_pp_and_daily_quote_return_is_not_bp():
    rows = [
        row(
            "M1_YOY",
            str(month),
            date(2026, month, 1),
            frequency="MONTHLY",
            source_uri="https://www.pbc.gov.cn/report",
            metadata={"definitionKey": "M1_2025", "definitionVersion": "M1_2025"},
        )
        for month in range(3, 10)
    ]
    assert context(rows)["M1_YOY"]["changes"]["periodChange"] == Decimal(1)
    assert (
        context([row("A50_CHANGE_PCT")])["A50_CHANGE_PCT"]["changes"]["changeUnit"]
        == "pp"
    )


def test_history_retains_definition_unit_and_first_known_evidence():
    point = context([row()])["US_T10_REAL"]["history"][0]
    assert point["definitionVersion"] == "UST-v1"
    assert point["unit"] == "%" and point["availableAt"] == NOW - timedelta(hours=1)


def provider(value="1.5", **changes):
    return {
        "code": "US_T10_REAL",
        "referenceDate": "2026-10-13",
        "frequency": "DAILY",
        "value": value,
        "unit": "%",
        "sourceUri": "https://home.treasury.gov/resource-center-data-chart-center/interest-rates/pages/xml?year=2026",
        "sourceName": "美国财政部",
        "publishedAt": None,
        "retrievedAt": NOW,
        "availableAt": NOW,
        "timeBasis": "RETRIEVAL_ONLY",
        "methodVersion": "treasury-v1",
        "metadata": {"definitionKey": "US_T10_REAL", "definitionVersion": "UST-v1"},
        **changes,
    }


def test_semantic_hash_ignores_retrieval_only_availability_but_not_value_revision():
    a = repo().normalize_series(provider(), NOW)
    b = repo().normalize_series(
        provider(
            retrievedAt=NOW + timedelta(hours=1), availableAt=NOW + timedelta(hours=1)
        ),
        NOW + timedelta(hours=1),
    )
    assert a["canonical_hash"] == b["canonical_hash"]
    assert (
        a["canonical_hash"]
        != repo().normalize_series(provider("1.6"), NOW)["canonical_hash"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"availableAt": NOW - timedelta(hours=1)},
        {"sourceUri": "https://treasury.gov.evil.example/doc"},
        {"referenceDate": "2026-10-15"},
        {"code": "CN_T10", "sourceUri": "https://yield.chinabond.com.cn/doc"},
    ],
)
def test_unsafe_or_unauthorized_series_never_persists(changes):
    with pytest.raises(ValueError):
        repo().normalize_series(provider(**changes), NOW)


def test_empty_quote_change_unit_is_pp_and_yield_change_unit_is_bp():
    result = context([])
    assert result["A50_CHANGE_PCT"]["changes"]["changeUnit"] == "pp"
    assert result["US_T10_REAL"]["changes"]["changeUnit"] == "bp"


def test_upstream_stale_quality_survives_a_recent_reference_date():
    item = context(
        [
            row(
                metadata={
                    "definitionKey": "US_T10_REAL",
                    "definitionVersion": "UST-v1",
                    "qualityStatus": "STALE",
                }
            )
        ]
    )["US_T10_REAL"]
    assert item["state"] == "STALE"
    assert item["history"][0]["state"] == "STALE"


@pytest.mark.parametrize(
    "code", ["A50_CHANGE_PCT", "US_SP500_CHANGE_PCT", "US_NASDAQ_CHANGE_PCT"]
)
def test_old_premarket_quote_has_a_shorter_lifetime_than_yields(code):
    item = context([row(code, day=date(2026, 10, 12))])[code]
    assert item["state"] == "STALE"


def test_ust_display_and_statistics_are_filtered_to_three_new_york_years():
    # NOW is October 14 in Shanghai but October 13 in New York.
    cutoff = date(2023, 10, 13)
    old = [row(value="1", day=cutoff - timedelta(days=i + 1)) for i in range(252)]
    rows = old + [row(value="2", day=cutoff), row(value="3")]
    item = context(rows)["US_T10_REAL"]
    assert [p["date"] for p in item["history"]] == ["2023-10-13", "2026-10-13"]
    assert item["changes"]["sampleCount"] == 2
    assert item["changes"]["median"] is None
    assert item["changes"]["percentile"] is None


def test_missing_definition_version_never_normalizes_as_trusted_fact():
    with pytest.raises(ValueError):
        repo().normalize_series(
            provider(metadata={"definitionKey": "US_T10_REAL"}), NOW
        )
    item = context([row(metadata={"definitionKey": "US_T10_REAL"})])["US_T10_REAL"]
    assert item["state"] == "CONFLICT"
    assert item["value"] is None


@pytest.mark.parametrize(
    "changes", [{"frequency": "MONTHLY"}, {"unit": None}, {"value": None}]
)
def test_bad_historical_frequency_unit_or_value_is_conflict_and_not_a_comparison(
    changes,
):
    previous = row(day=date(2026, 10, 12))
    previous.update(changes)
    item = context([previous, row()])["US_T10_REAL"]
    assert item["history"][0]["state"] == "CONFLICT"
    assert item["changes"]["previousValue"] is None
    assert item["changes"]["sampleCount"] == 1


def test_finite_input_cannot_overflow_derived_basis_point_fields():
    item = context([row(value="1e308", day=date(2026, 10, 12)), row(value="-1e308")])[
        "US_T10_REAL"
    ]
    assert item["changes"]["periodChange"] is None


def test_same_official_fact_keeps_first_known_time_across_later_fetches():
    original = provider(
        timeBasis="OFFICIAL_PUBLICATION", publishedAt=NOW - timedelta(days=1)
    )
    later = {
        **original,
        "retrievedAt": NOW + timedelta(hours=1),
        "availableAt": NOW + timedelta(hours=1),
    }
    assert (
        repo().normalize_series(original, NOW)["canonical_hash"]
        == repo().normalize_series(later, NOW + timedelta(hours=1))["canonical_hash"]
    )
