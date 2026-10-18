"""Bind a completed public unit-NAV response to its actual retained coordinates."""

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json


def nav_points_digest(points):
    """Canonical values make 1.0100 and 1.01 the same disclosed coordinate."""
    coordinates = []
    for point in points:
        try:
            day = date.fromisoformat(point["date"])
            value = point["close"]
            if isinstance(value, bool) or value is None:
                raise ValueError("invalid unit NAV")
            number = Decimal(str(value))
            if not number.is_finite() or number <= 0:
                raise ValueError("invalid unit NAV")
            canonical = format(number, "f")
            if "." in canonical:
                canonical = canonical.rstrip("0").rstrip(".")
            coordinates.append([day.isoformat(), canonical])
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise ValueError("invalid unit NAV coordinate") from exc
    return hashlib.sha256(
        json.dumps(coordinates, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
