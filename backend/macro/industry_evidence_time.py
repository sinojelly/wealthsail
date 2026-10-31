"""Conservative event-period checks, independent of an article's publication age."""

import calendar
from datetime import date, datetime, timedelta
import re


_FULL_DATE = re.compile(
    r"(?<!\d)(\d{4})(?:年|[-/])(\d{1,2})(?:月|[-/])(\d{1,2})日?(?!\d)"
)
_MONTH = re.compile(r"(?<!\d)(\d{4})(?:年|[-/])(\d{1,2})月?(?!\d)")
_QUARTER = re.compile(r"(?<!\d)(\d{4})年?第?([1-4一二三四])季(?:度)?")
_ISO_QUARTER = re.compile(r"(?<!\d)(\d{4})\s*[- ]?Q([1-4])(?!\d)", re.I)
_SHORT_MONTH_RANGE = re.compile(
    r"(?<!\d)(\d{4})年(\d{1,2})月\s*[至到~～—–-]\s*(\d{1,2})月"
)
_DEADLINE = re.compile(r"(?:延期|延后|推迟|延迟|延)(?:至|到)\s*$")
_RANGE_CONNECTOR = re.compile(r"\s*(?:至|到|[-~～—–])\s*")


def _published_day(value):
    try:
        if isinstance(value, datetime):
            return (
                value.date()
                if value.tzinfo is not None and value.utcoffset() is not None
                else None
            )
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            if "T" in value or " " in value:
                return _published_day(datetime.fromisoformat(value))
            return date.fromisoformat(value)
    except (TypeError, ValueError):
        pass
    return None


def _month_period(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _absolute_periods(quote):
    """Ignore future fulfillment deadlines; they are not announcement clocks."""
    periods, occupied = [], []
    invalid = False
    for kind, pattern in (
        ("range", _SHORT_MONTH_RANGE),
        ("day", _FULL_DATE),
        ("quarter", _QUARTER),
        ("quarter", _ISO_QUARTER),
        ("month", _MONTH),
    ):
        for match in pattern.finditer(quote):
            if any(
                match.start() < end and match.end() > start for start, end in occupied
            ):
                continue
            occupied.append(match.span())
            if _DEADLINE.search(quote[max(0, match.start() - 12) : match.start()]):
                continue
            try:
                year = int(match[1])
                if kind == "day":
                    start = end = date(year, int(match[2]), int(match[3]))
                elif kind == "quarter":
                    quarter = (
                        int(match[2])
                        if match[2].isdigit()
                        else "一二三四".index(match[2]) + 1
                    )
                    start, _ = _month_period(year, (quarter - 1) * 3 + 1)
                    _, end = _month_period(year, quarter * 3)
                elif kind == "range":
                    start, _ = _month_period(year, int(match[2]))
                    _, end = _month_period(year, int(match[3]))
                    if end < start:
                        raise ValueError("ambiguous year-crossing range")
                else:
                    start, end = _month_period(year, int(match[2]))
                periods.append((match.start(), match.end(), start, end))
            except (ValueError, IndexError):
                invalid = True
    return sorted(periods), invalid


def _relative_period(quote, published):
    if "本季度" in quote or "本季" in quote:
        return (
            date(published.year, (published.month - 1) // 3 * 3 + 1, 1),
            published,
            None,
        )
    if "上季度" in quote or "上季" in quote:
        current = date(published.year, (published.month - 1) // 3 * 3 + 1, 1)
        end = current - timedelta(days=1)
        return date(end.year, (end.month - 1) // 3 * 3 + 1, 1), end, None
    if "本月" in quote:
        return published.replace(day=1), published, None
    if "上月" in quote:
        end = published.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, None
    recent = re.search(r"近\s*(\d{1,3})\s*(?:天|日)", quote)
    if recent and 0 < int(recent[1]) <= 366:
        return published - timedelta(days=int(recent[1])), published, None
    if re.search(r"近期|近日|最近|日前|本周|今天|今日", quote):
        return None, published, f"近期（截至{published.isoformat()}）"
    return None


def event_time_verification(quote, published_date, now):
    """Check the event interval in a risk-matching quote, never a model date.

    Absolute date/month/quarter periods must be complete. Relative periods use
    the article's disclosed date and end at that date when still in progress.
    A year alone, ambiguous multiple events, or an absent clock stays unknown.
    """
    unknown = {
        "eventTimeVerification": "UNVERIFIED",
        "eventPeriod": None,
        "eventPeriodStart": None,
        "eventPeriodEnd": None,
    }
    if (
        not isinstance(quote, str)
        or not quote.strip()
        or not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        return unknown
    try:
        source_clock = (
            datetime.fromisoformat(published_date)
            if isinstance(published_date, str)
            and ("T" in published_date or " " in published_date)
            else published_date
        )
        if isinstance(source_clock, datetime) and (
            source_clock.tzinfo is None
            or source_clock.utcoffset() is None
            or source_clock > now
        ):
            return unknown
    except (TypeError, ValueError):
        return unknown
    published = _published_day(published_date)
    if published is None or published > now.date():
        return unknown
    periods, invalid = _absolute_periods(quote)
    if invalid:
        return unknown
    label = None
    if periods:
        if len(periods) > 1:
            if not all(
                _RANGE_CONNECTOR.fullmatch(quote[left[1] : right[0]])
                for left, right in zip(periods, periods[1:])
            ):
                return unknown
            start, end = periods[0][2], periods[-1][3]
            if end < start:
                return unknown
        else:
            start, end = periods[0][2:]
    else:
        if any(
            int(match[1]) < published.year
            and not _DEADLINE.search(quote[max(0, match.start() - 12) : match.start()])
            for match in re.finditer(r"(?<!\d)(\d{4})年", quote)
        ):
            return unknown
        try:
            relative = _relative_period(quote, published)
        except (ValueError, OverflowError):
            return unknown
        if relative is None:
            return unknown
        start, end, label = relative
    today = now.date()
    verification = (
        "FUTURE"
        if start is not None and start > today
        else "UNVERIFIED"
        if end > today or end > published
        else "RECENT"
        if (today - end).days <= 90
        else "HISTORICAL"
    )
    return {
        "eventTimeVerification": verification,
        "eventPeriod": label
        or (
            start.isoformat()
            if start == end
            else f"{start.isoformat()}至{end.isoformat()}"
        ),
        "eventPeriodStart": start.isoformat() if start is not None else None,
        "eventPeriodEnd": end.isoformat(),
    }
