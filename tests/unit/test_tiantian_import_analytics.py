"""Literal behavior tests for holding-period and official-NAV range analytics."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from backend.portfolio.imports.analytics import (
    NavObservation,
    ShareTransaction,
    calculate_range_metrics,
    derive_holding_start,
)


def _event(
    occurred_at: date | datetime | str,
    business_type: str,
    shares: str | None,
    source_id: str,
) -> ShareTransaction:
    if isinstance(occurred_at, str):
        occurred_at = date.fromisoformat(occurred_at)
    return ShareTransaction(
        occurred_at=occurred_at,
        source_id=source_id,
        business_type=business_type,
        share_quantity=Decimal(shares) if shares is not None else None,
    )


def test_first_purchase_derives_a_reconciled_holding_start() -> None:
    result = derive_holding_start(
        [_event("2025-01-01", "PURCHASE", "100", "001")],
        Decimal("100"),
        as_of=date(2026, 8, 28),
    )

    assert result.started_on == date(2025, 1, 1)
    assert result.holding_days == 605
    assert result.status == "RECONCILED"
    assert result.reason_codes == ()
    assert result.algorithm_version == "holding-start/v1"


def test_multiple_buys_and_partial_redemption_keep_the_first_purchase() -> None:
    result = derive_holding_start(
        [
            _event("2025-01-01", "PURCHASE", "100", "001"),
            _event("2025-02-01", "PURCHASE", "30", "002"),
            _event("2025-03-01", "REDEEM", "20", "003"),
        ],
        Decimal("110"),
        as_of=date(2025, 3, 2),
    )

    assert result.started_on == date(2025, 1, 1)
    assert result.status == "RECONCILED"


def test_full_exit_then_repurchase_starts_a_new_holding_episode() -> None:
    result = derive_holding_start(
        [
            _event("2025-01-01", "PURCHASE", "100", "001"),
            _event("2025-03-01", "REDEEM", "100", "002"),
            _event("2025-06-10", "PURCHASE", "40", "003"),
        ],
        Decimal("40"),
        as_of=date(2026, 8, 28),
    )

    assert result.started_on == date(2025, 6, 10)
    assert result.status == "RECONCILED"


def test_cash_dividend_does_not_change_shares_but_reinvestment_does() -> None:
    result = derive_holding_start(
        [
            _event("2025-01-01", "PURCHASE", "100", "001"),
            _event("2025-02-01", "DIVIDEND_CASH", None, "002"),
            _event("2025-03-01", "DIVIDEND_REINVEST", "2.5", "003"),
        ],
        Decimal("102.5"),
        as_of=date(2025, 3, 1),
    )

    assert result.started_on == date(2025, 1, 1)
    assert result.status == "RECONCILED"


def test_history_that_starts_with_an_open_position_is_marked_partial() -> None:
    result = derive_holding_start(
        [_event("2025-03-01", "REDEEM", "20", "001")],
        Decimal("80"),
        as_of=date(2025, 3, 2),
    )

    assert result.started_on == date(2025, 3, 1)
    assert result.status == "PARTIAL_HISTORY"
    assert result.reason_codes == ("HISTORY_STARTS_WITH_OPEN_POSITION",)


def test_impossible_replay_is_a_mismatch_but_keeps_the_candidate_date() -> None:
    result = derive_holding_start(
        [_event("2025-01-01", "PURCHASE", "100", "001")],
        Decimal("80"),
        as_of=date(2025, 1, 2),
    )

    assert result.started_on == date(2025, 1, 1)
    assert result.status == "MISMATCH"
    assert result.reason_codes == ("SIMULATED_SHARES_MISMATCH",)


@pytest.mark.parametrize("business_type", ["UNKNOWN", "FORCED_ADJUSTMENT"])
def test_unknown_or_forced_share_change_is_insufficient(business_type: str) -> None:
    result = derive_holding_start(
        [_event("2025-01-01", business_type, "10", "001")],
        Decimal("10"),
        as_of=date(2025, 1, 1),
    )

    assert result.started_on is None
    assert result.holding_days is None
    assert result.status == "INSUFFICIENT_DATA"
    assert result.reason_codes == ("UNSUPPORTED_SHARE_TRANSACTION",)


def test_same_day_exit_and_repurchase_without_times_is_downgraded() -> None:
    result = derive_holding_start(
        [
            _event("2025-06-10", "PURCHASE", "100", "001"),
            _event("2025-06-10", "REDEEM", "100", "002"),
            _event("2025-06-10", "PURCHASE", "40", "003"),
        ],
        Decimal("40"),
        as_of=date(2025, 6, 10),
    )

    assert result.started_on == date(2025, 6, 10)
    assert result.status == "PARTIAL_HISTORY"
    assert result.reason_codes == ("SAME_DAY_ORDER_AMBIGUOUS",)


def test_tolerance_is_the_larger_of_one_hundredth_and_one_part_per_million() -> None:
    large = derive_holding_start(
        [_event("2025-01-01", "PURCHASE", "1000000", "001")],
        Decimal("999999.5"),
        as_of=date(2025, 1, 1),
    )
    small = derive_holding_start(
        [_event("2025-01-01", "PURCHASE", "1", "001")],
        Decimal("0.995"),
        as_of=date(2025, 1, 1),
    )

    assert large.tolerance == Decimal("0.9999995")
    assert large.status == "RECONCILED"
    assert small.tolerance == Decimal("0.01")
    assert small.status == "RECONCILED"


def _nav(observed_on: str, value: str) -> NavObservation:
    return NavObservation(date.fromisoformat(observed_on), Decimal(value))


def test_nav_range_uses_latest_value_and_most_recent_repeated_extremes() -> None:
    result = calculate_range_metrics(
        [
            _nav("2025-01-01", "1.0000"),
            _nav("2025-01-02", "1.5000"),
            _nav("2025-01-03", "1.0000"),
            _nav("2025-01-04", "1.5000"),
            _nav("2025-01-05", "1.2000"),
        ],
        holding_started_on=date(2025, 1, 1),
    )

    assert result.current_nav == Decimal("1.2000")
    assert result.as_of == date(2025, 1, 5)
    assert (result.high_nav, result.high_on) == (Decimal("1.5000"), date(2025, 1, 4))
    assert (result.low_nav, result.low_on) == (Decimal("1.0000"), date(2025, 1, 3))
    assert result.from_high_pct == Decimal("-20.00")
    assert result.from_low_pct == Decimal("20.00")
    assert result.range_position_pct == Decimal("40.00")
    assert result.status == "READY"
    assert result.algorithm_version == "nav-range/v1"


@pytest.mark.parametrize(
    ("latest", "from_high", "from_low", "position"),
    [
        ("1.5000", "0.00", "50.00", "100.00"),
        ("1.0000", "-33.33", "0.00", "0.00"),
    ],
)
def test_current_at_high_or_low_has_bounded_literal_percentages(
    latest: str,
    from_high: str,
    from_low: str,
    position: str,
) -> None:
    result = calculate_range_metrics(
        [
            _nav("2025-01-01", "1.0000"),
            _nav("2025-01-02", "1.5000"),
            _nav("2025-01-03", latest),
        ],
        holding_started_on=date(2025, 1, 1),
    )

    assert result.from_high_pct == Decimal(from_high)
    assert result.from_low_pct == Decimal(from_low)
    assert result.range_position_pct == Decimal(position)


def test_flat_nav_range_returns_no_position_percentage() -> None:
    result = calculate_range_metrics(
        [_nav("2025-01-01", "1.0000"), _nav("2025-01-02", "1.0000")],
        holding_started_on=date(2025, 1, 1),
    )

    assert result.high_nav == result.low_nav == Decimal("1.0000")
    assert result.range_position_pct is None
    assert result.status == "READY"
    assert result.quality_reasons == ("FLAT_NAV_RANGE",)


def test_nonpositive_nav_is_excluded_and_marks_result_partial() -> None:
    result = calculate_range_metrics(
        [
            _nav("2025-01-01", "1.0000"),
            _nav("2025-01-02", "0"),
            _nav("2025-01-03", "-1"),
            _nav("2025-01-04", "1.2000"),
        ],
        holding_started_on=date(2025, 1, 1),
    )

    assert result.current_nav == Decimal("1.2000")
    assert result.low_nav == Decimal("1.0000")
    assert result.status == "PARTIAL"
    assert result.quality_reasons == ("INVALID_NAV_EXCLUDED",)


def test_expected_market_date_gap_is_explicit_but_weekends_need_not_be_expected() -> None:
    result = calculate_range_metrics(
        [_nav("2025-01-03", "1.0000"), _nav("2025-01-06", "1.1000")],
        holding_started_on=date(2025, 1, 3),
        expected_observation_dates=(
            date(2025, 1, 3),
            date(2025, 1, 6),
            date(2025, 1, 7),
        ),
    )

    assert result.status == "PARTIAL"
    assert result.quality_reasons == ("NAV_DATE_GAP",)


def test_conflicting_values_for_one_date_are_not_silently_selected() -> None:
    result = calculate_range_metrics(
        [_nav("2025-01-01", "1.0000"), _nav("2025-01-01", "1.1000")],
        holding_started_on=date(2025, 1, 1),
    )

    assert result.status == "CONFLICT"
    assert result.current_nav is None
    assert result.quality_reasons == ("NAV_SOURCE_CONFLICT",)


def test_no_nav_after_holding_start_remains_pending() -> None:
    result = calculate_range_metrics(
        [_nav("2024-12-31", "1.0000")],
        holding_started_on=date(2025, 1, 1),
    )

    assert result.status == "PENDING"
    assert result.current_nav is None
    assert result.quality_reasons == ("NAV_NOT_AVAILABLE",)
