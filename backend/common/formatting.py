"""Shared formatting helpers: decimal-string money, quantity and quality merge."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

# Quality severity order: higher wins when merging rows.
QUALITY_SEVERITY = {
    "FRESH": 0,
    "ESTIMATED": 1,
    "STALE": 2,
    "UNAVAILABLE": 3,
    "MISSING": 3,
}

# DB 'UNAVAILABLE' is exposed as 'MISSING' to match the frontend QualityCode.
QUALITY_EXPOSED: dict[str, str] = {
    "FRESH": "FRESH",
    "ESTIMATED": "ESTIMATED",
    "STALE": "STALE",
    "UNAVAILABLE": "MISSING",
}


def fmt_money(value: Decimal | int | float | None, places: int = 2) -> str:
    """Decimal → fixed-scale money string, e.g. Decimal('426000.00') → '426000.00'."""
    if value is None:
        return "0.00"
    d = Decimal(value)
    return format(d.quantize(Decimal(1).scaleb(-places)), "f")


def fmt_qty(value: Decimal | int | float | None) -> str:
    """Decimal → quantity string with trailing zeros stripped ('300.0000' → '300')."""
    if value is None:
        return "0"
    d = Decimal(value)
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "0") else s


def merge_quality(qualities: Iterable[str | None]) -> str:
    """Merge row quality statuses: worst severity wins (fail closed)."""
    worst = "FRESH"
    worst_sev = 0
    for q in qualities:
        key = (q or "UNAVAILABLE").upper()
        sev = QUALITY_SEVERITY.get(key, 3)
        if sev > worst_sev:
            worst_sev = sev
            worst = key
    return QUALITY_EXPOSED.get(worst, "MISSING")


def pct(part: Decimal, whole: Decimal) -> Decimal:
    """Percentage with 2-decimal rounding; safe against zero whole."""
    if not whole:
        return Decimal("0.00")
    return (part / whole * Decimal(100)).quantize(Decimal("0.01"))


__all__ = ["fmt_money", "fmt_qty", "merge_quality", "pct"]
