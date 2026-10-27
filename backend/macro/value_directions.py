"""Multi-evidence direction reasoning; prices always belong to server anchors."""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.macro.alpha_research import _price, _program_plan
from backend.macro.price_revision import (
    PriceRevisionStatus,
    index_previous_plans,
    normalize_price_revision,
    previous_plan,
    same_coordinate,
)

MAX_VALUE_DIRECTIONS = 8


class DirectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(min_length=1, max_length=240)
    sourceIds: list[str] = Field(min_length=1, max_length=8)


class ValuePlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subjectKind: Literal["index", "fund", "nav"]
    subjectId: str = Field(min_length=1, max_length=100)
    track: Literal["LEFT_VALUE", "RIGHT_TREND"]
    priceRevisionStatus: PriceRevisionStatus
    priceRevisionBasis: Literal["PRICE", "FUNDAMENTAL"] = "PRICE"
    priceRevisionSourceIds: list[str] = Field(default_factory=list, max_length=8)
    priceRevisionNote: str = Field(min_length=1, max_length=1000)
    calibrationRuleKeys: list[str] = Field(max_length=16)
    calibrationNote: str = Field(max_length=1000)


class ValueDirectionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=100)
    thesis: str = Field(min_length=1, max_length=600)
    signal: Literal["LEFT_VALUE", "RIGHT_TREND", "TAKE_PROFIT", "WATCH"]
    sourceIds: list[str] = Field(max_length=8)
    macroCodes: list[str] = Field(max_length=8)
    quantitativeFundCodes: list[str] = Field(default_factory=list, max_length=2)
    watchFor: list[str] = Field(max_length=4)
    catalysts: list[DirectionEvidence] = Field(default_factory=list, max_length=2)
    risks: list[DirectionEvidence] = Field(default_factory=list, max_length=2)
    plans: list[ValuePlanProposal] = Field(max_length=2)


def plan_identity(plan):
    return plan.get("subjectKind"), plan.get("subjectId")


def direction_quantitative_evidence(rows, codes, now, *, plans=None):
    """Copy original server NAV facts with a closed projection, never model values."""
    from backend.macro.daily_research import (
        _visible,
        _public_actionable_plan,
        _pick,
        _finite,
    )

    import re

    if (
        not isinstance(codes, list)
        or len(codes) > 2
        or len(set(codes)) != len(codes)
        or any(
            not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code)
            for code in codes
        )
        or not isinstance(rows, list)
    ):
        raise ValueError("invalid quantitative fund references")
    output = []
    for code in codes:
        matches = [
            row for row in rows if isinstance(row, dict) and row.get("fundCode") == code
        ]
        if len(matches) != 1:
            raise ValueError("missing or ambiguous quantitative fund")
        row = matches[0]
        if row.get("historyQuality") != "READY" or not _visible(row, now):
            raise ValueError("unavailable quantitative history")
        if plans is not None:
            matches = [
                plan
                for plan in plans
                if plan.get("fundCode") == code
                and plan_identity(plan) == plan_identity(row)
            ]
            if not matches:
                raise ValueError("quantitative fund must belong to this direction plan")
            base = matches[0]
            # Report ranking adds derived scores to plan tracks after evidence
            # is projected. Compare the same public price/technical facts so a
            # valid report remains followable without weakening price checks.
            evidence_facts = _public_actionable_plan(
                row, row.get("subjectId"), now, subject_kind=row.get("subjectKind")
            )
            plan_facts = _public_actionable_plan(
                base, base.get("subjectId"), now, subject_kind=base.get("subjectKind")
            )
            if (
                evidence_facts is None
                or plan_facts is None
                or any(
                    evidence_facts.get(key) != plan_facts.get(key)
                    for key in ("asOf", "currentPrice", "tracks")
                )
            ):
                raise ValueError("quantitative facts differ from selected server plan")
        else:
            base = row
        kind, subject = plan_identity(base)
        if kind not in {"nav", "fund"} or base.get("priceBasis") != "UNIT_NAV":
            raise ValueError("quantitative evidence requires the same fund NAV")
        selected = base.get("selectedTrack", base.get("track"))
        valid_tracks = [
            track
            for track in base.get("tracks", [])
            if track.get("state") in {"CANDIDATE", "WATCH"}
        ]
        if selected is None and valid_tracks:
            selected = valid_tracks[0]["track"]
        day, _, _ = _program_plan(base, selected, now, require_theme=False)
        if (now.date() - day).days > 10:
            raise ValueError("quantitative evidence predates the current study")
        clean = _public_actionable_plan(base, subject, now, subject_kind=kind)
        if clean is None:
            raise ValueError("invalid quantitative coordinate")
        clean["historyQuality"] = "READY"
        clean.update(
            _pick(
                row,
                (
                    "fundCode",
                    "discoverySector",
                    "sourceUri",
                    "retrievedAt",
                    "availableAt",
                    "priceTimeBasis",
                    "historySampleCount",
                    "historyTargetSamples",
                    "historyCoverage",
                ),
            )
        )
        clean["signals"] = []
        for signal in row.get("signals", [])[:2]:
            if (
                not isinstance(signal, dict)
                or signal.get("track") not in {"LEFT_VALUE", "RIGHT_TREND"}
                or signal.get("triggerState")
                not in {"CANDIDATE", "WATCH", "INSUFFICIENT"}
                or type(signal.get("inEntryZone")) is not bool
            ):
                raise ValueError("invalid quantitative signal")
            distance = signal.get("distanceToEntryPct")
            if distance is not None and _finite(distance) is None:
                raise ValueError("invalid quantitative distance")
            matching = [
                item
                for item in base.get("tracks", [])
                if item.get("track") == signal["track"]
            ]
            if len(matching) != 1:
                raise ValueError("ambiguous quantitative technical track")
            technical = matching[0]
            candidate = technical.get("state") == "CANDIDATE"
            in_zone = (
                _price(technical["entryZoneMin"])
                <= _price(base["currentPrice"])
                <= _price(technical["entryZoneMax"])
            )
            clean["signals"].append(
                {
                    **_pick(
                        signal,
                        ("track", "triggerState", "inEntryZone", "distanceToEntryPct"),
                    ),
                    "candidate": candidate,
                    "entryReady": candidate
                    and in_zone
                    and technical.get("priceState") == "READY",
                }
            )
        output.append(clean)
    return output


def _macro_codes(packet, now):
    from backend.macro.daily_research import _finite, _visible, _day, _pick, _TZ

    rows = [
        *packet.get("macros", []),
        *packet.get("series", []),
        *(packet.get("alphaPublic") or {}).get("observations", []),
    ]
    facts = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not row.get("code")
            or _finite(row.get("value")) is None
            or not _visible(row, now)
        ):
            continue
        day = _day(
            row.get("referenceDate")
            or row.get("asOf")
            or str(row.get("referencePeriod") or "") + "-01"
        )
        monthly = (
            row.get("frequency") == "MONTHLY"
            or row["code"]
            in {
                "PMI",
                "CPI_YOY",
                "PPI_YOY",
                "M1_YOY",
                "M2_YOY",
                "M1_M2_GAP",
                "TSF_STOCK_YOY",
            }
            or row["code"].startswith("HOUSE_")
        )
        if (
            day is None
            or (now.astimezone(_TZ).date() - day).days > (120 if monthly else 10)
            or row.get("state", "READY") not in {"READY", "VERIFIED", "OBSERVED"}
        ):
            continue
        code = row["code"]
        if code not in facts or day > facts[code][0]:
            facts[code] = (
                day,
                _pick(
                    row,
                    (
                        "code",
                        "label",
                        "value",
                        "unit",
                        "asOf",
                        "referenceDate",
                        "referencePeriod",
                        "sourceUri",
                        "retrievedAt",
                        "availableAt",
                        "publishedAt",
                        "timeBasis",
                        "state",
                    ),
                ),
            )
    return {code: item[1] for code, item in facts.items()}


def validate_value_directions(proposals, packet, now, source_aliases):
    anchors = {}
    for plan in packet.get("discoveryActionableZones", []):
        identity = plan_identity(plan)
        if identity in anchors and anchors[identity] != plan:
            raise ValueError("ambiguous discovery anchor")
        anchors[identity] = plan
    previous = index_previous_plans(
        [
            *packet.get("previousActionablePlans", []),
            *(
                plan
                for direction in packet.get("previousValueDirections", [])
                for plan in direction.get("plans", [])
            ),
        ],
        list(anchors.values()),
    )
    rules = {rule["key"]: rule for rule in packet.get("strategyMemoryRules", [])}
    macro_codes, seen, output = _macro_codes(packet, now), set(), []
    if len(proposals) > MAX_VALUE_DIRECTIONS:
        raise ValueError("too many value directions")
    for raw in proposals:
        direction = ValueDirectionProposal.model_validate(raw).model_dump()
        key = direction["key"]
        if key in seen:
            raise ValueError("duplicate value direction")
        seen.add(key)
        if any(ref not in source_aliases for ref in direction["sourceIds"]):
            raise ValueError("unknown direction source")
        refs = sorted({source_aliases[ref] for ref in direction["sourceIds"]})
        macros = sorted(set(direction["macroCodes"]))
        if any(code not in macro_codes for code in macros):
            raise ValueError("unknown direction macro fact")
        for field in ("catalysts", "risks"):
            for note in direction[field]:
                if any(
                    ref not in source_aliases or source_aliases[ref] not in refs
                    for ref in note["sourceIds"]
                ):
                    raise ValueError("direction note needs related current sources")
                note["sourceIds"] = sorted(
                    {source_aliases[ref] for ref in note["sourceIds"]}
                )
        plans, used = [], set()
        for proposal in direction["plans"]:
            identity = plan_identity(proposal)
            pair = (*identity, proposal["track"])
            if pair in used:
                raise ValueError("duplicate direction product track")
            used.add(pair)
            anchor = anchors.get(identity)
            if not anchor:
                raise ValueError("unknown discovery product or price")
            day, current, track = _program_plan(
                anchor, proposal["track"], now, require_theme=False
            )
            from backend.macro.daily_research import _TZ

            if (
                anchor.get("historyQuality")
                in {"STALE", "CONFLICT", "UNVERIFIED", "UNAVAILABLE", "INSUFFICIENT"}
                or (now.astimezone(_TZ).date() - day).days > 10
            ):
                raise ValueError("current direction price is stale or unverified")
            old = previous_plan(previous, anchor, proposal["track"])
            revision = proposal["priceRevisionStatus"]
            old_track = None
            if same_coordinate(anchor, old):
                old_day, _, old_track = _program_plan(
                    old, proposal["track"], now, require_theme=False
                )
                if old_day > day:
                    raise ValueError("direction previous plan is later than current")
            if revision == "TAKE_PROFIT_ALERT":
                if not (
                    old is not None
                    and old_track is not None
                    and old.get("priceBasis") == anchor.get("priceBasis")
                    and old.get("unit") == anchor.get("unit")
                    and old.get("sourceUri") == anchor.get("sourceUri")
                    and current >= _price(old_track.get("takeProfitT1"))
                ):
                    raise ValueError("direction profit alert lacks observed target")
            note_field = "catalysts" if revision == "UPGRADED" else "risks"
            note_refs = {
                ref for note in direction[note_field] for ref in note["sourceIds"]
            }
            fundamental_refs = []
            requested_refs = proposal["priceRevisionSourceIds"]
            if requested_refs and all(
                ref in source_aliases
                and source_aliases[ref] in refs
                and source_aliases[ref] in note_refs
                for ref in requested_refs
            ):
                fundamental_refs = sorted(
                    {source_aliases[ref] for ref in requested_refs}
                )
            normalize_price_revision(
                proposal,
                anchor,
                track,
                old,
                old_track,
                fundamental_refs=fundamental_refs,
            )
            allowed_rules = {f"DISCOVERY:{key}", anchor.get("themeCode")}
            if any(
                rule not in rules or rules[rule].get("themeCode") not in allowed_rules
                for rule in proposal["calibrationRuleKeys"]
            ):
                raise ValueError("unknown direction calibration rule")
            plans.append(
                {
                    **deepcopy(anchor),
                    **proposal,
                    "selectedTrack": proposal["track"],
                    "valueDirectionKey": key,
                    "valueDirectionTitle": direction["title"],
                    "orderPriceAvailable": False,
                }
            )
        quantitative = []
        if direction["quantitativeFundCodes"]:
            from backend.macro.daily_research import _visible

            scan = packet.get("quantitativeScan") or {}
            if (
                scan.get("methodVersion")
                not in {"fund-quant-scan-v1", "fund-quant-scan-v2"}
                or scan.get("minimumHistorySamples") != 220
                or not _visible(scan, now)
            ):
                raise ValueError("missing current quantitative scan")
            quantitative = direction_quantitative_evidence(
                scan.get("items"), direction["quantitativeFundCodes"], now, plans=plans
            )
        qualifying_track = any(
            track.get("state") == "CANDIDATE"
            for plan in plans
            if plan.get("fundCode") in direction["quantitativeFundCodes"]
            for track in plan.get("tracks", [])
            if track.get("track") == plan.get("selectedTrack")
        )
        if not refs:
            if not quantitative or not qualifying_track:
                raise ValueError(
                    "pure quantitative direction needs a triggered fund plan"
                )
        elif len(refs) + len(macros) + len(quantitative) < 2:
            raise ValueError("direction needs multiple available evidence")
        if direction["signal"] in {"LEFT_VALUE", "RIGHT_TREND"} and not any(
            plan["selectedTrack"] == direction["signal"]
            and next(
                row for row in plan["tracks"] if row["track"] == plan["selectedTrack"]
            )["state"]
            == "CANDIDATE"
            for plan in plans
        ):
            raise ValueError("buy signal lacks a qualifying program track")
        if direction["signal"] == "TAKE_PROFIT" and not any(
            plan["priceRevisionStatus"] == "TAKE_PROFIT_ALERT" for plan in plans
        ):
            raise ValueError("profit signal lacks observed target")
        output.append(
            {
                **direction,
                "sourceIds": refs,
                "macroCodes": macros,
                "macroEvidence": [deepcopy(macro_codes[code]) for code in macros],
                "quantitativeEvidence": quantitative,
                "plans": plans,
            }
        )
    return output
