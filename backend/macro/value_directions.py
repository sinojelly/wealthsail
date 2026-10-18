"""Multi-evidence direction reasoning; prices always belong to server anchors."""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.macro.alpha_research import _price, _program_plan

MAX_VALUE_DIRECTIONS = 6


class DirectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(min_length=1, max_length=240)
    sourceIds: list[str] = Field(min_length=1, max_length=8)


class ValuePlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subjectKind: Literal["index", "fund", "nav"]
    subjectId: str = Field(min_length=1, max_length=100)
    track: Literal["LEFT_VALUE", "RIGHT_TREND"]
    priceRevisionStatus: Literal[
        "INITIAL", "UPGRADED", "DOWNGRADED", "TAKE_PROFIT_ALERT"
    ]
    priceRevisionNote: str = Field(min_length=1, max_length=1000)
    calibrationRuleKeys: list[str] = Field(max_length=16)
    calibrationNote: str = Field(max_length=1000)


class ValueDirectionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=100)
    thesis: str = Field(min_length=1, max_length=600)
    signal: Literal["LEFT_VALUE", "RIGHT_TREND", "TAKE_PROFIT", "WATCH"]
    sourceIds: list[str] = Field(min_length=1, max_length=8)
    macroCodes: list[str] = Field(max_length=8)
    watchFor: list[str] = Field(max_length=4)
    catalysts: list[DirectionEvidence] = Field(default_factory=list, max_length=2)
    risks: list[DirectionEvidence] = Field(default_factory=list, max_length=2)
    plans: list[ValuePlanProposal] = Field(max_length=2)


def plan_identity(plan):
    return plan.get("subjectKind"), plan.get("subjectId")


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
    previous = {
        (
            direction["key"],
            *plan_identity(plan),
            plan.get("selectedTrack", plan.get("track")),
        ): plan
        for direction in packet.get("previousValueDirections", [])
        for plan in direction.get("plans", [])
    }
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
        if (
            any(code not in macro_codes for code in macros)
            or len(refs) + len(macros) < 2
        ):
            raise ValueError("direction needs multiple available evidence")
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
            old = previous.get((key, *pair))
            revision = proposal["priceRevisionStatus"]
            if (revision == "INITIAL") != (old is None):
                raise ValueError("revision does not match previous direction plan")
            if old:
                old_day, _, old_track = _program_plan(
                    old, proposal["track"], now, require_theme=False
                )
                if old_day > day:
                    raise ValueError("direction previous plan is later than current")
                if revision == "TAKE_PROFIT_ALERT" and not (
                    old.get("priceBasis") == anchor.get("priceBasis")
                    and old.get("unit") == anchor.get("unit")
                    and old.get("sourceUri") == anchor.get("sourceUri")
                    and current >= _price(old_track.get("takeProfitT1"))
                ):
                    raise ValueError("direction profit alert lacks observed target")
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
                "plans": plans,
            }
        )
    return output
