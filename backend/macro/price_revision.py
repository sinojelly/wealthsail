"""Normalize model revision words from comparable server price coordinates."""

from decimal import Decimal, localcontext
import re
from typing import Literal

PriceRevisionStatus = Literal[
    "INITIAL", "MAINTAINED", "UPGRADED", "DOWNGRADED", "TAKE_PROFIT_ALERT"
]

_COORDINATES = ("entryZoneMin", "entryZoneMax", "takeProfitT1", "takeProfitT2")
_REASONS = {
    "INITIAL": "前一有效研究日没有同基金同口径同轨道的可比计划，首次建立区间。",
    "MAINTAINED": "与前一有效研究日相比，买入区间上下沿未同时同向变动至少0.5%，维持关注区间。",
    "UPGRADED": "与前一有效研究日相比，买入区间上下沿均上移至少0.5%。",
    "DOWNGRADED": "与前一有效研究日相比，买入区间上下沿均下移至少0.5%。",
}


def _fund_code(plan, identity_codes=None):
    if plan.get("subjectKind") not in {"nav", "fund"}:
        return None
    code = plan.get("fundCode")
    subject = plan.get("subjectId")
    if code is not None:
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            return None
        if plan.get("subjectKind") == "nav" and subject != code:
            return None
        return code
    if plan.get("subjectKind") == "nav" and re.fullmatch(r"[0-9]{6}", str(subject)):
        return subject
    codes = (identity_codes or {}).get((plan.get("subjectKind"), subject), set())
    return next(iter(codes)) if len(codes) == 1 else None


def _revision_key(plan, track, identity_codes=None):
    code = _fund_code(plan, identity_codes)
    coordinate = (plan.get("market"), plan.get("priceBasis"), plan.get("unit"), track)
    if code:
        return ("FUND_CODE", code, *coordinate)
    if plan.get("subjectKind") in {"nav", "fund"}:
        return None  # Never guess a fund identity from its display label.
    return ("SUBJECT", plan.get("subjectKind"), plan.get("subjectId"), *coordinate)


def _frozen_signature(plan, track_name):
    tracks = [row for row in plan.get("tracks", []) if row.get("track") == track_name]
    if len(tracks) != 1:
        return None
    values = []
    for field in (*_COORDINATES, "stopLossPrice"):
        value = tracks[0].get(field)
        values.append(Decimal(str(value)) if value is not None else None)
    return (*values, plan.get("asOf"), plan.get("sourceUri"))


def index_previous_plans(rows, anchors):
    """Index one prior report by public fund code, independent of topic names."""
    identity_codes = {}
    for anchor in anchors:
        if code := _fund_code(anchor):
            identity_codes.setdefault(
                (anchor.get("subjectKind"), anchor.get("subjectId")), set()
            ).add(code)
    index, signatures = {}, {}
    for row in rows:
        track = row.get("selectedTrack", row.get("track"))
        key = _revision_key(row, track, identity_codes)
        if key is None:
            continue
        signature = _frozen_signature(row, track)
        if signature is None:
            continue
        if key in signatures and signatures[key] != signature:
            index[key] = None  # Conflicting duplicates are not a comparable plan.
            continue
        signatures[key] = signature
        if key not in index:
            index[key] = {**row}
            if key[0] == "FUND_CODE":
                index[key]["fundCode"] = key[1]
    return index


def previous_plan(index, anchor, track):
    return index.get(_revision_key(anchor, track))


def same_coordinate(anchor, old):
    if old is None:
        return False
    current_code, old_code = _fund_code(anchor), _fund_code(old)
    if current_code or old_code:
        return (
            current_code is not None
            and current_code == old_code
            and all(
                anchor.get(field) == old.get(field)
                for field in ("market", "priceBasis", "unit")
            )
        )
    return all(
        anchor.get(field) == old.get(field)
        for field in ("subjectKind", "subjectId", "market", "priceBasis", "unit")
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
    elif _fund_code(anchor):
        bounds = [
            (Decimal(str(track[key])), Decimal(str(old_track[key])))
            for key in ("entryZoneMin", "entryZoneMax")
        ]
        with localcontext() as context:
            context.prec = max(
                28,
                max(len(value.as_tuple().digits) for pair in bounds for value in pair)
                + 8,
            )
            up = all(
                current >= previous * Decimal("1.005") for current, previous in bounds
            )
            down = all(
                current <= previous * Decimal("0.995") for current, previous in bounds
            )
        actual = "UPGRADED" if up else "DOWNGRADED" if down else "MAINTAINED"
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
