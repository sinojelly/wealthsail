"""Pure Decimal algorithms for imported fund holding periods and NAV ranges."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

HOLDING_ALGORITHM_VERSION = "holding-start/v1"
NAV_RANGE_ALGORITHM_VERSION = "nav-range/v1"
PERCENT_QUANTUM = Decimal("0.01")

_INCREASE_TYPES = frozenset({"PURCHASE", "CONVERSION_IN", "DIVIDEND_REINVEST"})
_DECREASE_TYPES = frozenset({"REDEEM", "CONVERSION_OUT"})
_NO_SHARE_CHANGE_TYPES = frozenset({"DIVIDEND_CASH", "NON_POSITION"})
_UNSUPPORTED_TYPES = frozenset({"FORCED_ADJUSTMENT", "UNKNOWN"})


@dataclass(frozen=True, slots=True)
class ShareTransaction:
    """The share-changing subset of a source transaction."""

    occurred_at: date | datetime
    source_id: str
    business_type: str
    share_quantity: Decimal | None


@dataclass(frozen=True, slots=True)
class HoldingStartResult:
    started_on: date | None
    holding_days: int | None
    status: str
    reason_codes: tuple[str, ...]
    algorithm_version: str
    tolerance: Decimal
    simulated_opening_quantity: Decimal | None


@dataclass(frozen=True, slots=True)
class NavObservation:
    observed_on: date
    nav: Decimal


@dataclass(frozen=True, slots=True)
class RangeMetricsResult:
    current_nav: Decimal | None
    as_of: date | None
    high_nav: Decimal | None
    high_on: date | None
    low_nav: Decimal | None
    low_on: date | None
    from_high_pct: Decimal | None
    from_low_pct: Decimal | None
    range_position_pct: Decimal | None
    status: str
    quality_reasons: tuple[str, ...]
    algorithm_version: str


def _event_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


def _event_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _share_delta(transaction: ShareTransaction) -> Decimal | None:
    business_type = transaction.business_type
    if business_type in _NO_SHARE_CHANGE_TYPES:
        return Decimal("0")
    if business_type in _UNSUPPORTED_TYPES:
        return None
    shares = transaction.share_quantity
    if business_type not in _INCREASE_TYPES | _DECREASE_TYPES:
        return None
    if shares is None or not shares.is_finite() or shares < 0:
        return None
    return shares if business_type in _INCREASE_TYPES else -shares


def _insufficient_result(tolerance: Decimal, reason: str) -> HoldingStartResult:
    return HoldingStartResult(
        started_on=None,
        holding_days=None,
        status="INSUFFICIENT_DATA",
        reason_codes=(reason,),
        algorithm_version=HOLDING_ALGORITHM_VERSION,
        tolerance=tolerance,
        simulated_opening_quantity=None,
    )


def derive_holding_start(
    transactions: Iterable[ShareTransaction],
    current_quantity: Decimal,
    *,
    as_of: date,
    tolerance_ratio: Decimal = Decimal("0.000001"),
) -> HoldingStartResult:
    """Derive the latest open holding episode by replaying shares backwards.

    Backward replay starts from the authoritative current snapshot. A zero crossing
    isolates the latest holding episode even when older source history is partial.
    """
    if (
        not current_quantity.is_finite()
        or current_quantity <= 0
        or not tolerance_ratio.is_finite()
        or tolerance_ratio < 0
    ):
        return _insufficient_result(Decimal("0.01"), "INVALID_CURRENT_QUANTITY")

    tolerance = max(Decimal("0.01"), current_quantity * tolerance_ratio)
    ordered = sorted(
        transactions,
        key=lambda item: (_event_datetime(item.occurred_at), item.source_id),
    )
    if not ordered:
        return _insufficient_result(tolerance, "NO_SHARE_TRANSACTIONS")

    deltas: list[tuple[ShareTransaction, Decimal]] = []
    for transaction in ordered:
        delta = _share_delta(transaction)
        if delta is None:
            reason = (
                "UNSUPPORTED_SHARE_TRANSACTION"
                if transaction.business_type in _UNSUPPORTED_TYPES
                or transaction.business_type
                not in _INCREASE_TYPES | _DECREASE_TYPES | _NO_SHARE_CHANGE_TYPES
                else "MISSING_SHARE_QUANTITY"
            )
            return _insufficient_result(tolerance, reason)
        deltas.append((transaction, delta))

    balance_after = current_quantity
    candidate: date | None = None
    mismatch = False
    for transaction, delta in reversed(deltas):
        balance_before = balance_after - delta
        if candidate is None and balance_after > tolerance and balance_before <= tolerance:
            candidate = _event_date(transaction.occurred_at)
        if balance_before < -tolerance:
            mismatch = True
        balance_after = balance_before

    opening_quantity = balance_after
    reason_codes: list[str] = []
    if mismatch or opening_quantity < -tolerance:
        status = "MISMATCH"
        reason_codes.append("SIMULATED_SHARES_MISMATCH")
    elif candidate is None and opening_quantity > tolerance:
        status = "PARTIAL_HISTORY"
        candidate = _event_date(ordered[0].occurred_at)
        reason_codes.append("HISTORY_STARTS_WITH_OPEN_POSITION")
    elif candidate is None:
        return _insufficient_result(tolerance, "HOLDING_START_NOT_FOUND")
    else:
        status = "RECONCILED"

    candidate_day_deltas = [
        delta
        for transaction, delta in deltas
        if _event_date(transaction.occurred_at) == candidate
        and not isinstance(transaction.occurred_at, datetime)
        and delta != 0
    ]
    if (
        candidate_day_deltas
        and any(delta > 0 for delta in candidate_day_deltas)
        and any(delta < 0 for delta in candidate_day_deltas)
    ):
        status = "PARTIAL_HISTORY" if status == "RECONCILED" else status
        reason_codes.append("SAME_DAY_ORDER_AMBIGUOUS")

    holding_days = (as_of - candidate).days + 1 if candidate <= as_of else None
    if holding_days is None:
        status = "MISMATCH"
        reason_codes.append("START_DATE_AFTER_AS_OF")

    return HoldingStartResult(
        started_on=candidate,
        holding_days=holding_days,
        status=status,
        reason_codes=tuple(reason_codes),
        algorithm_version=HOLDING_ALGORITHM_VERSION,
        tolerance=tolerance,
        simulated_opening_quantity=opening_quantity,
    )


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return ((numerator / denominator) * Decimal("100")).quantize(
        PERCENT_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _pending_range(reason: str) -> RangeMetricsResult:
    return RangeMetricsResult(
        current_nav=None,
        as_of=None,
        high_nav=None,
        high_on=None,
        low_nav=None,
        low_on=None,
        from_high_pct=None,
        from_low_pct=None,
        range_position_pct=None,
        status="PENDING",
        quality_reasons=(reason,),
        algorithm_version=NAV_RANGE_ALGORITHM_VERSION,
    )


def calculate_range_metrics(
    observations: Iterable[NavObservation],
    *,
    holding_started_on: date | None,
    expected_observation_dates: Iterable[date] | None = None,
    history_complete: bool = True,
) -> RangeMetricsResult:
    """Calculate the current position within an official unit-NAV range."""
    if holding_started_on is None:
        return _pending_range("HOLDING_START_NOT_AVAILABLE")

    values_by_date: dict[date, set[Decimal]] = defaultdict(set)
    invalid_found = False
    for observation in observations:
        if observation.observed_on < holding_started_on:
            continue
        if not observation.nav.is_finite() or observation.nav <= 0:
            invalid_found = True
            continue
        values_by_date[observation.observed_on].add(observation.nav)

    if any(len(values) > 1 for values in values_by_date.values()):
        return replace(_pending_range("NAV_SOURCE_CONFLICT"), status="CONFLICT")

    normalized = sorted((day, next(iter(values))) for day, values in values_by_date.items())
    if not normalized:
        return _pending_range("NAV_NOT_AVAILABLE")

    current_on, current_nav = normalized[-1]
    high_nav = max(value for _, value in normalized)
    low_nav = min(value for _, value in normalized)
    high_on = max(day for day, value in normalized if value == high_nav)
    low_on = max(day for day, value in normalized if value == low_nav)

    quality_reasons: list[str] = []
    if invalid_found:
        quality_reasons.append("INVALID_NAV_EXCLUDED")
    observed_dates = set(values_by_date)
    if expected_observation_dates is not None and any(
        day >= holding_started_on and day not in observed_dates
        for day in expected_observation_dates
    ):
        quality_reasons.append("NAV_DATE_GAP")
    if not history_complete:
        quality_reasons.append("PARTIAL_HOLDING_HISTORY")

    range_position: Decimal | None
    if high_nav == low_nav:
        range_position = None
        quality_reasons.append("FLAT_NAV_RANGE")
    else:
        range_position = _pct(current_nav - low_nav, high_nav - low_nav)

    status = "PARTIAL" if any(
        reason != "FLAT_NAV_RANGE" for reason in quality_reasons
    ) else "READY"
    return RangeMetricsResult(
        current_nav=current_nav,
        as_of=current_on,
        high_nav=high_nav,
        high_on=high_on,
        low_nav=low_nav,
        low_on=low_on,
        from_high_pct=_pct(current_nav - high_nav, high_nav),
        from_low_pct=_pct(current_nav - low_nav, low_nav),
        range_position_pct=range_position,
        status=status,
        quality_reasons=tuple(quality_reasons),
        algorithm_version=NAV_RANGE_ALGORITHM_VERSION,
    )
