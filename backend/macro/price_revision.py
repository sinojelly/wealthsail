"""Normalize model revision words from comparable server price coordinates."""

from decimal import Decimal
from typing import Literal

PriceRevisionStatus = Literal[
    "INITIAL", "MAINTAINED", "UPGRADED", "DOWNGRADED", "TAKE_PROFIT_ALERT"
]

_COORDINATES = ("entryZoneMin", "entryZoneMax", "takeProfitT1", "takeProfitT2")
_REASONS = {
    "INITIAL": "前一有效研究日没有同方向同标的同轨道的可比计划，首次建立区间。",
    "MAINTAINED": "与前一有效研究日相比，买入区间和目标未一致上移或下移，维持关注区间。",
    "UPGRADED": "与前一有效研究日相比，程序买入区间或目标一致上移。",
    "DOWNGRADED": "与前一有效研究日相比，程序买入区间或目标一致下移。",
}


def same_coordinate(anchor, old):
    return old is not None and all(
        anchor.get(field) == old.get(field)
        for field in ("subjectKind", "subjectId", "priceBasis", "unit")
    )


def normalize_price_revision(
    proposal, anchor, track, old=None, old_track=None, *, fundamental_refs=None
):
    """Correct mechanical revision errors without changing any server price."""
    requested = proposal["priceRevisionStatus"]
    if requested == "TAKE_PROFIT_ALERT":
        if "priceRevisionBasis" in proposal:
            proposal["priceRevisionBasis"] = "PRICE"
            proposal["priceRevisionSourceIds"] = []
        return  # Its observed-price proof remains the caller's hard gate.
    refs = fundamental_refs or []
    comparable = same_coordinate(anchor, old) and old_track is not None
    changes = []
    complete_comparison = True
    if comparable:
        for key in _COORDINATES:
            current, previous = track.get(key), old_track.get(key)
            if current is None or previous is None:
                if current != previous:
                    complete_comparison = False
                continue
            current, previous = Decimal(str(current)), Decimal(str(previous))
            changes.append((current > previous) - (current < previous))
    same_prices = comparable and complete_comparison and not any(changes)
    fundamental = (
        same_prices
        and proposal.get("priceRevisionBasis") == "FUNDAMENTAL"
        and requested in {"UPGRADED", "DOWNGRADED"}
        and bool(refs)
    )
    if not comparable:
        actual = "MAINTAINED" if requested == "MAINTAINED" else "INITIAL"
    elif fundamental:
        actual = requested
    elif (
        complete_comparison
        and any(change > 0 for change in changes)
        and all(change >= 0 for change in changes)
    ):
        actual = "UPGRADED"
    elif (
        complete_comparison
        and any(change < 0 for change in changes)
        and all(change <= 0 for change in changes)
    ):
        actual = "DOWNGRADED"
    else:
        actual = "MAINTAINED"
    proposal["priceRevisionStatus"] = actual
    if "priceRevisionBasis" in proposal:
        proposal["priceRevisionBasis"] = "FUNDAMENTAL" if fundamental else "PRICE"
        proposal["priceRevisionSourceIds"] = refs if fundamental else []
    if actual != requested:
        proposal["priceRevisionNote"] = _REASONS[actual]
