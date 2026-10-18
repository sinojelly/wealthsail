"""Deterministic dual tracks preserve usable facts and quote identity."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from importlib import import_module, util

import pytest


def engine():
    assert util.find_spec("backend.indicators.actionable_zones"), (
        "dual-track calculator missing"
    )
    return import_module("backend.indicators.actionable_zones")


def points(closes, *, ohlc=True):
    start = date(2025, 1, 1)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "close": close,
            "high": close + 1 if ohlc else None,
            "low": close - 1 if ohlc else None,
        }
        for i, close in enumerate(closes)
    ]


def calculate(rows, **kwargs):
    return engine().compute_actionable_price_plan(
        rows, subject_kind="index", subject_id="hs300", **kwargs
    )


def track(plan, kind):
    return next(row for row in plan["tracks"] if row["track"] == kind)


def test_indicators_match_hand_calculated_prices_and_population_bollinger():
    plan = calculate(points(range(100, 300)))
    values = plan["indicators"]
    assert values["ma20"] == 289.5
    assert values["ma60"] == 269.5
    assert values["ma200"] == 199.5
    assert values["rsi14"] == 100
    assert values["atr14"] == 2
    assert values["momentum20"] == pytest.approx(299 / 279 - 1)
    assert values["bollLower"] == pytest.approx(277.9674374053292)
    assert values["bollUpper"] == pytest.approx(301.0325625946708)


def test_wilder_smoothing_uses_seed_then_recursive_update():
    plan = calculate(points([100 + i for i in range(15)] + [112]))
    assert plan["indicators"]["rsi14"] == pytest.approx(86.66666666666667)
    assert plan["indicators"]["atr14"] == pytest.approx(29 / 14)


def test_missing_ma200_does_not_erase_shorter_indicators_or_left_candidate():
    plan = calculate(points(range(100, 130)), pe_percentile=40)
    assert plan["indicators"]["ma20"] == 119.5
    assert plan["indicators"]["ma200"] is None
    assert plan["indicatorStates"]["ma200"]["state"] == "INSUFFICIENT"
    assert track(plan, "LEFT_VALUE")["state"] == "CANDIDATE"


def test_left_value_can_be_candidate_below_ma200_without_valuation_or_pit():
    plan = calculate(points([100] * 199 + [90]))
    left = track(plan, "LEFT_VALUE")
    assert left["state"] == "CANDIDATE"
    assert plan["indicators"]["ma200"] == 99.95
    assert left["priceState"] == "READY"
    assert (
        Decimal(left["stopLossPrice"])
        < Decimal(left["entryZoneMin"])
        <= Decimal(left["entryZoneMax"])
    )
    assert (
        Decimal(left["entryZoneMax"])
        < Decimal(left["takeProfitT1"])
        < Decimal(left["takeProfitT2"])
    )


def test_right_track_needs_positive_momentum_but_no_invented_catalyst_gate():
    plan = calculate(points([100 + 0.05 * i + (3 if i % 2 else 0) for i in range(200)]))
    assert plan["indicators"]["rsi14"] < 75
    assert track(plan, "RIGHT_TREND")["state"] == "CANDIDATE"
    assert track(plan, "LEFT_VALUE")["state"] == "INSUFFICIENT"


def test_both_tracks_remain_independent_when_valuation_is_cheap():
    plan = calculate(
        points([100 + 0.05 * i + (3 if i % 2 else 0) for i in range(200)]),
        pe_percentile=35,
    )
    assert [row["state"] for row in plan["tracks"]] == ["CANDIDATE", "CANDIDATE"]


def test_close_only_candidate_keeps_reference_zone_without_inventing_atr():
    plan = calculate(points(range(300, 100, -1), ohlc=False))
    left = track(plan, "LEFT_VALUE")
    assert left["state"] == "CANDIDATE"
    assert left["priceState"] == "PARTIAL"
    assert left["entryZoneMin"] is not None and left["entryZoneMax"] is not None
    assert plan["indicators"]["atr14"] is None
    assert left["stopLossPrice"] is left["takeProfitT1"] is left["takeProfitT2"] is None


def test_old_missing_ohlc_does_not_poison_real_contiguous_tail_atr():
    rows = points([100] * 200)
    rows[0]["high"] = rows[0]["low"] = None
    plan = calculate(rows)
    assert plan["indicators"]["atr14"] == 2
    rows[-1]["high"] = None
    assert calculate(rows)["indicators"]["atr14"] is None


@pytest.mark.parametrize("count", [0, 1, 14, 19])
def test_short_history_reports_missing_indicators_without_fabrication(count):
    plan = calculate(points([100] * count))
    assert plan["indicators"]["ma20"] is None
    assert plan["indicators"]["ma200"] is None
    assert track(plan, "RIGHT_TREND")["state"] == "INSUFFICIENT"


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -1, 0])
def test_bad_latest_close_cannot_form_candidate_or_price(bad):
    rows = points([100] * 200)
    rows[-1]["close"] = bad
    plan = calculate(rows)
    assert plan["currentPrice"] is None
    assert all(row["state"] == "INSUFFICIENT" for row in plan["tracks"])
    assert all(row["entryZoneMin"] is None for row in plan["tracks"])


@pytest.mark.parametrize(
    "basis", ["INDEX_POINTS", "ETF_HFQ", "PROVIDER_RETURN_INDEX", "UNIT_NAV"]
)
def test_research_coordinates_are_never_presented_as_orderable_quotes(basis):
    plan = engine().compute_actionable_price_plan(
        points([100] * 200), subject_kind="fund", subject_id="etf", price_basis=basis
    )
    assert plan["orderPriceAvailable"] is False
    assert plan["subjectId"] == "etf" and plan["priceBasis"] == basis


def test_index_coordinates_cannot_become_orderable_by_relabelling_price_basis():
    assert (
        calculate(points([100] * 200), price_basis="ETF_RAW")["orderPriceAvailable"]
        is False
    )


def test_raw_etf_plan_retains_own_identity_unit_and_source():
    plan = engine().compute_actionable_price_plan(
        points([3] * 199 + [2.7]),
        subject_kind="fund",
        subject_id="etf-uuid",
        price_basis="ETF_RAW",
        unit="CNY",
        source_uri="https://quotes.test/etf",
        market="SSE",
    )
    assert plan["orderPriceAvailable"] is True
    assert plan["currentPrice"] == "2.7000"
    assert plan["sourceUri"] == "https://quotes.test/etf"
    assert plan["unit"] == "CNY" and plan["market"] == "SSE"


def test_raw_label_without_quote_provenance_is_not_orderable():
    plan = engine().compute_actionable_price_plan(
        points([100] * 200),
        subject_kind="fund",
        subject_id="etf",
        price_basis="ETF_RAW",
    )
    assert plan["orderPriceAvailable"] is False


def test_future_tail_is_excluded_at_explicit_observation_date_without_mutating_input():
    rows = points([100] * 199 + [90, 10000])
    before = deepcopy(rows)
    plan = calculate(rows, as_of=rows[-2]["date"])
    assert plan["currentPrice"] == "90.00"
    assert plan["indicators"]["ma200"] == 99.95
    assert rows == before


def test_reference_and_full_price_formulas_keep_original_anchors():
    plan = calculate(points([100] * 200))
    right = track(plan, "RIGHT_TREND")
    assert Decimal(right["entryZoneMin"]) == Decimal("99.6")
    assert Decimal(right["entryZoneMax"]) == Decimal("101.2")
    assert Decimal(right["stopLossPrice"]) == Decimal("95.6")
    assert Decimal(right["takeProfitT1"]) == Decimal("105.2")
    assert Decimal(right["takeProfitT2"]) == Decimal("109.2")
    no_atr = track(calculate(points([100] * 200, ohlc=False)), "RIGHT_TREND")
    assert Decimal(no_atr["entryZoneMin"]) == Decimal(no_atr["entryZoneMax"]) == 100
    assert no_atr["priceState"] == "PARTIAL"


def test_inverted_reference_zone_does_not_move_anchor_or_hide_candidate():
    left = track(calculate(points([100] * 199 + [90], ohlc=False)), "LEFT_VALUE")
    assert left["state"] == "CANDIDATE"
    assert left["priceState"] == "INVALID"
    assert left["entryZoneMin"] is left["entryZoneMax"] is None


def test_invalid_observation_breaks_tail_instead_of_being_removed():
    rows = points([100] * 200)
    rows[-30]["close"] = None
    plan = calculate(rows)
    assert plan["inputWindow"]["sampleCount"] == 29
    assert plan["indicators"]["ma20"] == 100
    assert plan["indicators"]["ma60"] is plan["indicators"]["ma200"] is None


def test_extreme_finite_input_never_leaks_infinite_indicators_or_crashes():
    plan = calculate(points([1e308] * 200, ohlc=False))
    import math

    assert all(
        value is None or math.isfinite(value) for value in plan["indicators"].values()
    )


def test_duplicate_or_unordered_dates_are_conflicts_not_reordered_history():
    rows = points([100] * 200)
    rows[-1]["date"] = rows[-2]["date"]
    plan = calculate(rows)
    assert plan["currentPrice"] is None
    assert plan["indicatorStates"]["ma20"]["state"] == "CONFLICT"
    assert all(row["state"] == "INSUFFICIENT" for row in plan["tracks"])


def test_invalid_recent_ohlc_does_not_poison_valid_close_indicators():
    rows = points(range(300, 100, -1))
    rows[-1]["high"] = 1
    plan = calculate(rows)
    assert plan["indicators"]["ma20"] == 110.5
    assert plan["indicators"]["atr14"] is None
    assert plan["indicatorStates"]["atr14"]["state"] == "CONFLICT"
    assert track(plan, "LEFT_VALUE")["state"] == "CANDIDATE"


def test_left_or_stays_unknown_when_only_known_rsi_branch_is_false():
    left = track(calculate(points([100] * 15)), "LEFT_VALUE")
    assert left["state"] == "INSUFFICIENT"
    assert len(left["missingEvidence"]) == 3


def test_left_or_is_watch_only_when_all_branches_are_known_false():
    plan = calculate(
        points([100 + 0.05 * i + (3 if i % 2 else 0) for i in range(200)]),
        pe_percentile=80,
    )
    assert track(plan, "LEFT_VALUE")["state"] == "WATCH"


def nav_calculate(rows):
    return engine().compute_actionable_price_plan(
        rows,
        subject_kind="nav",
        subject_id="008281",
        price_basis="UNIT_NAV",
        unit="CNY",
    )


def test_twenty_confirmed_navs_get_risk_prices_without_inventing_atr():
    plan = nav_calculate(points([1] * 20, ohlc=False))
    assert plan["indicators"]["atr14"] is None
    assert plan["indicatorStates"]["atr14"]["state"] == "MISSING_OHLC"
    assert plan["indicators"]["navVolatility14"] == pytest.approx(0.012)
    assert plan["volatilityMethod"] == "NAV_ABS_CHANGE_STD_V1"
    left = track(plan, "LEFT_VALUE")
    assert left["priceState"] == "READY"
    assert [
        left[key]
        for key in (
            "entryZoneMin",
            "entryZoneMax",
            "stopLossPrice",
            "takeProfitT1",
            "takeProfitT2",
        )
    ] == ["0.9904", "1.0000", "0.9664", "1.0300", "1.0540"]
    right = track(plan, "RIGHT_TREND")
    assert [
        right[key]
        for key in (
            "entryZoneMin",
            "entryZoneMax",
            "stopLossPrice",
            "takeProfitT1",
            "takeProfitT2",
        )
    ] == ["0.9976", "1.0072", "0.9760", "1.0312", "1.0540"]


@pytest.mark.parametrize(
    ("closes", "expected"),
    [([1, 1.1] * 10, 0.14), ([1] * 10 + [2] * 10, 0.325)],
)
def test_nav_scale_respects_real_absolute_changes_and_population_deviation(
    closes, expected
):
    plan = nav_calculate(points(closes, ohlc=False))
    assert plan["indicators"]["navVolatility14"] == pytest.approx(expected)
    assert plan["indicators"]["atr14"] is None


def test_nav_targets_stay_ordered_after_a_drop_with_a_high_ma60():
    plan = nav_calculate(points([5] * 40 + [1] * 20, ohlc=False))
    left = track(plan, "LEFT_VALUE")
    assert left["takeProfitT1"] == "3.6667"
    assert left["takeProfitT2"] == "3.6787"
    assert left["priceState"] == "READY"


@pytest.mark.parametrize("count", [0, 1, 14, 15, 19])
def test_short_nav_history_does_not_create_risk_prices(count):
    plan = nav_calculate(points([1] * count, ohlc=False))
    assert plan["indicators"]["navVolatility14"] is None
    assert all(
        row["stopLossPrice"] is row["takeProfitT1"] is None for row in plan["tracks"]
    )


def test_invalid_nav_observation_cannot_be_removed_to_fill_twenty_samples():
    rows = points([1] * 40, ohlc=False)
    rows[-20]["close"] = None
    plan = nav_calculate(rows)
    assert plan["inputWindow"]["sampleCount"] == 19
    assert plan["indicators"]["navVolatility14"] is None
    assert all(
        row["stopLossPrice"] is row["takeProfitT1"] is None for row in plan["tracks"]
    )


@pytest.mark.parametrize("basis", ["INDEX_POINTS", "ETF_HFQ", "PROVIDER_RETURN_INDEX"])
def test_close_only_other_price_bases_never_borrow_nav_risk_prices(basis):
    plan = calculate(points([100] * 20, ohlc=False), price_basis=basis)
    assert plan["indicators"].get("navVolatility14") is None
    assert plan.get("volatilityMethod") != "NAV_ABS_CHANGE_STD_V1"
    assert all(
        row["stopLossPrice"] is row["takeProfitT1"] is None for row in plan["tracks"]
    )


def test_real_nav_ohlc_keeps_atr_and_never_substitutes_nav_scale():
    plan = nav_calculate(points([3] * 20))
    assert plan["indicators"]["atr14"] == 2
    assert plan["indicators"].get("navVolatility14") is None
    assert plan.get("volatilityMethod") != "NAV_ABS_CHANGE_STD_V1"


def test_nav_conflicting_ohlc_is_not_relabelled_as_missing_ohlc():
    rows = points([3] * 20)
    rows[-1]["high"] = 1
    plan = nav_calculate(rows)
    assert plan["indicatorStates"]["atr14"]["state"] == "CONFLICT"
    assert plan["indicators"].get("navVolatility14") is None
    assert all(row["stopLossPrice"] is None for row in plan["tracks"])


def test_small_nav_rounding_does_not_emit_zero_or_collapsed_risk_prices():
    plan = nav_calculate(points([0.0001] * 20, ohlc=False))
    assert plan["indicators"]["navVolatility14"] is None
    assert all(row["priceState"] != "READY" for row in plan["tracks"])
    assert all(
        row["stopLossPrice"] is row["takeProfitT1"] is None for row in plan["tracks"]
    )


def test_nav_future_or_unordered_points_cannot_feed_current_risk_scale():
    rows = points([1] * 20 + [10000], ohlc=False)
    plan = engine().compute_actionable_price_plan(
        rows,
        subject_kind="nav",
        subject_id="008281",
        price_basis="UNIT_NAV",
        as_of=rows[-2]["date"],
    )
    assert plan["currentPrice"] == "1.0000"
    assert plan["indicators"]["navVolatility14"] == pytest.approx(0.012)
    rows[-1]["date"] = rows[-2]["date"]
    conflict = nav_calculate(rows)
    assert conflict["indicators"]["navVolatility14"] is None
    assert all(row["stopLossPrice"] is None for row in conflict["tracks"])


def test_price_coordinates_are_quantized_by_basis_without_long_float_artifacts():
    index = calculate(points([100.123456789] * 20))
    assert index["currentPrice"] == "100.12"
    assert track(index, "LEFT_VALUE")["entryZoneMax"] == "100.12"
    nav = nav_calculate(points([1.23456789] * 20, ohlc=False))
    assert nav["currentPrice"] == "1.2346"
    assert all(
        value is None or len(value.split(".")[-1]) == 4
        for row in nav["tracks"]
        for key, value in row.items()
        if key
        in {
            "entryZoneMin",
            "entryZoneMax",
            "stopLossPrice",
            "takeProfitT1",
            "takeProfitT2",
        }
    )


def test_rounding_cannot_hide_an_inverted_original_reference_range():
    plan = calculate(points([100] * 199 + [99.9999], ohlc=False))
    left = track(plan, "LEFT_VALUE")
    assert left["priceState"] == "INVALID"
    assert left["entryZoneMin"] is left["entryZoneMax"] is None
