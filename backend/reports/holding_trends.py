"""Deterministic, shareable holding-price trend calculations for pre-market."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class PricePoint:
    """One verified historical close or official fund NAV."""

    as_of: date
    price: Decimal
    field_code: str
    quality: str


@dataclass(frozen=True)
class HoldingTrend:
    """A reusable trend fact with enough detail to persist safely."""

    display: str
    previous_direction: str | None
    streak_direction: str | None
    streak_days: int | None
    streak_change_pct: Decimal | None
    history_start_date: date | None
    history_end_date: date | None
    history_field_code: str | None
    quality: str


def _direction(change_pct: Decimal | None) -> str | None:
    if change_pct is None or not change_pct.is_finite() or change_pct == 0:
        return None
    return "UP" if change_pct > 0 else "DOWN"


def _display_streak(direction: str, days: int, change_pct: Decimal) -> str:
    emoji = "📈" if direction == "UP" else "📉"
    label = "连涨" if direction == "UP" else "连跌"
    signed = f"{change_pct:+.2f}%"
    return f"{emoji} {label}{days}天 ({signed})"


def unavailable_trend() -> HoldingTrend:
    """Return the explicit no-fact state; never infer a historical move."""

    return HoldingTrend(
        display="--",
        previous_direction=None,
        streak_direction=None,
        streak_days=None,
        streak_change_pct=None,
        history_start_date=None,
        history_end_date=None,
        history_field_code=None,
        quality="UNAVAILABLE",
    )


def calculate_holding_trend(
    today_change_pct: Decimal | None, history: Iterable[PricePoint]
) -> HoldingTrend:
    """Calculate a prior-day streak without treating today's move as history.

    Fresh closes participate for stocks and exchange funds.  Official unit NAV
    disclosures also participate when marked STALE: their disclosure date is
    historical by definition, while the source fact itself remains trusted.
    The caller supplies at most 30 earlier trading dates, while this function
    remains pure so it is straightforward to verify and cache.
    """

    today_direction = _direction(today_change_pct)
    if today_direction is None:
        return unavailable_trend()

    clean_by_date: dict[date, PricePoint] = {}
    for point in history:
        trusted_quality = point.quality == "FRESH" or (
            point.field_code == "NAV" and point.quality == "STALE"
        )
        if (
            not trusted_quality
            or point.field_code not in {"CLOSE", "NAV"}
            or not point.price.is_finite()
            or point.price <= 0
        ):
            continue
        clean_by_date[point.as_of] = point
    points = sorted(clean_by_date.values(), key=lambda item: item.as_of)[-30:]
    if len(points) < 2:
        return unavailable_trend()

    previous_change = (points[-1].price / points[-2].price - Decimal("1")) * 100
    previous_direction = _direction(previous_change)
    if previous_direction is None:
        return unavailable_trend()

    if previous_direction != today_direction:
        return HoldingTrend(
            display="转涨" if today_direction == "UP" else "转跌",
            previous_direction=previous_direction,
            streak_direction=None,
            streak_days=None,
            streak_change_pct=None,
            history_start_date=points[-2].as_of,
            history_end_date=points[-1].as_of,
            history_field_code=points[-1].field_code,
            quality="FRESH",
        )

    streak_days = 0
    start_index = len(points) - 1
    for index in range(len(points) - 1, 0, -1):
        change = (points[index].price / points[index - 1].price - Decimal("1")) * 100
        if _direction(change) != previous_direction:
            break
        streak_days += 1
        start_index = index - 1

    if streak_days == 0:
        return unavailable_trend()
    change_pct = (points[-1].price / points[start_index].price - Decimal("1")) * 100
    return HoldingTrend(
        display=_display_streak(previous_direction, streak_days, change_pct),
        previous_direction=previous_direction,
        streak_direction=previous_direction,
        streak_days=streak_days,
        streak_change_pct=change_pct,
        history_start_date=points[start_index].as_of,
        history_end_date=points[-1].as_of,
        history_field_code=points[-1].field_code,
        quality="FRESH",
    )


def trend_from_cached_row(
    row: object, today_change_pct: Decimal | None
) -> HoldingTrend:
    """Rebuild a display value from a trusted same-day cache record."""

    quality = str(row["quality_status"] or "UNAVAILABLE")  # type: ignore[index]
    if quality != "FRESH":
        return unavailable_trend()
    today_direction = _direction(today_change_pct)
    previous_direction = row["previous_direction"]  # type: ignore[index]
    if today_direction is None or previous_direction not in {"UP", "DOWN"}:
        return unavailable_trend()
    if today_direction != previous_direction:
        return HoldingTrend(
            display="转涨" if today_direction == "UP" else "转跌",
            previous_direction=previous_direction,
            streak_direction=None,
            streak_days=None,
            streak_change_pct=None,
            history_start_date=None,
            history_end_date=None,
            history_field_code=None,
            quality="FRESH",
        )
    streak_direction = row["streak_direction"]  # type: ignore[index]
    streak_days = row["streak_days"]  # type: ignore[index]
    raw_change = row["streak_change_pct"]  # type: ignore[index]
    try:
        change_pct = Decimal(str(raw_change))
    except (ArithmeticError, TypeError, ValueError):
        return unavailable_trend()
    if (
        streak_direction != previous_direction
        or not isinstance(streak_days, int)
        or not 1 <= streak_days <= 30
        or not change_pct.is_finite()
    ):
        return unavailable_trend()
    return HoldingTrend(
        display=_display_streak(streak_direction, streak_days, change_pct),
        previous_direction=previous_direction,
        streak_direction=streak_direction,
        streak_days=streak_days,
        streak_change_pct=change_pct,
        history_start_date=None,
        history_end_date=None,
        history_field_code=None,
        quality="FRESH",
    )
