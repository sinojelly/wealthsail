"""Shared disclosed-NAV depth and the chart/plan two-year research window."""

from calendar import monthrange
from datetime import date

NAV_SYNC_TARGET_SAMPLES = 550
# 550 mainland exchange sessions need more than two calendar years. Keep a
# holiday margin when an authoritative 550-session calendar is unavailable.
NAV_HISTORY_FETCH_CALENDAR_DAYS = 900
NAV_RESEARCH_MAX_SAMPLES = 500
NAV_RESEARCH_MIN_SAMPLES = 120


def two_year_nav_start(as_of: date) -> date:
    """Anchor both views to the latest disclosed NAV, including delayed funds."""
    year = as_of.year - 2
    return date(year, as_of.month, min(as_of.day, monthrange(year, as_of.month)[1]))


def two_year_nav_points(points: list[dict]) -> list[dict]:
    """Keep actual dated observations in the latest two years, capped at 500."""
    if not points:
        return []
    start = two_year_nav_start(date.fromisoformat(points[-1]["date"])).isoformat()
    return [point for point in points if point["date"] >= start][
        -NAV_RESEARCH_MAX_SAMPLES:
    ]
