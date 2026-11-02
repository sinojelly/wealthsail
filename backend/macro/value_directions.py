"""Multi-evidence direction reasoning; prices always belong to server anchors."""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.macro.alpha_research import _price, _program_plan
from backend.macro.industry_cycle import (
    IndustryCycleValidationError,
    IndustryCycleProposal,
    merge_industry_cycles,
    normalize_industry_cycle,
)
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
    # Default supports stored reports; new requests require this in REPORT_SCHEMA.
    industryCycle: IndustryCycleProposal | None = None
    reviewIndustryIds: list[str] = Field(default_factory=list, max_length=8)
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
        ResearchValidationError,
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
        raise ResearchValidationError(
            "FUND_PLAN_INVALID", "invalid quantitative fund references"
        )
    output = []
    for code in codes:
        matches = [
            row for row in rows if isinstance(row, dict) and row.get("fundCode") == code
        ]
        if len(matches) != 1:
            raise ResearchValidationError(
                "FUND_PLAN_INVALID", "missing or ambiguous quantitative fund"
            )
        row = matches[0]
        if row.get("historyQuality") != "READY" or not _visible(row, now):
            raise ResearchValidationError(
                "FUND_PLAN_INVALID", "unavailable quantitative history"
            )
        if plans is not None:
            matches = [
                plan
                for plan in plans
                if plan.get("fundCode") == code
                and plan_identity(plan) == plan_identity(row)
            ]
            if not matches:
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID",
                    "quantitative fund must belong to this direction plan",
                )
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
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID",
                    "quantitative facts differ from selected server plan",
                )
        else:
            base = row
        kind, subject = plan_identity(base)
        if kind not in {"nav", "fund"} or base.get("priceBasis") != "UNIT_NAV":
            raise ResearchValidationError(
                "FUND_PLAN_INVALID", "quantitative evidence requires the same fund NAV"
            )
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
            raise ResearchValidationError(
                "FUND_PLAN_INVALID",
                "quantitative evidence predates the current study",
            )
        clean = _public_actionable_plan(base, subject, now, subject_kind=kind)
        if clean is None:
            raise ResearchValidationError(
                "FUND_PLAN_INVALID", "invalid quantitative coordinate"
            )
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
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID", "invalid quantitative signal"
                )
            distance = signal.get("distanceToEntryPct")
            if distance is not None and _finite(distance) is None:
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID", "invalid quantitative distance"
                )
            matching = [
                item
                for item in base.get("tracks", [])
                if item.get("track") == signal["track"]
            ]
            if len(matching) != 1:
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID", "ambiguous quantitative technical track"
                )
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


def validate_value_directions(
    proposals,
    packet,
    now,
    source_aliases,
    *,
    sources=None,
    industry_reviews=None,
    historical_cycles=None,
):
    """Keep a bounded direction index across nested validation boundaries."""
    from backend.macro.daily_research import ResearchValidationError

    active_index = [None]
    try:
        return _validate_value_directions(
            proposals,
            packet,
            now,
            source_aliases,
            sources=sources,
            industry_reviews=industry_reviews,
            historical_cycles=historical_cycles,
            _diagnostic_index=active_index,
        )
    except ResearchValidationError as exc:
        if exc.item_index is not None or active_index[0] is None:
            raise
        raise ResearchValidationError(
            exc.reason_code,
            str(exc),
            rule_id=exc.rule_id,
            item_index=active_index[0],
        ) from exc
    except ValidationError as exc:
        raise ResearchValidationError(
            "SCHEMA_VALIDATION_FAILED", str(exc), item_index=active_index[0]
        ) from exc
    except ValueError as exc:
        raise ResearchValidationError(
            "PAYLOAD_VALIDATION_FAILED", str(exc), item_index=active_index[0]
        ) from exc


def _validate_value_directions(
    proposals,
    packet,
    now,
    source_aliases,
    *,
    sources=None,
    industry_reviews=None,
    historical_cycles=None,
    _diagnostic_index=None,
):
    from backend.macro.daily_research import ResearchValidationError, _TZ

    anchors = {}
    for plan in packet.get("discoveryActionableZones", []):
        identity = plan_identity(plan)
        if identity in anchors and anchors[identity] != plan:
            raise ResearchValidationError(
                "FUND_PLAN_INVALID", "ambiguous discovery anchor"
            )
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
    reviews = {row["industry"]: row for row in industry_reviews or []}
    sector_rows = [
        *anchors.values(),
        *(packet.get("quantitativeScan") or {}).get("items", []),
        *packet.get("discoveryUniverse", []),
    ]
    rules = {rule["key"]: rule for rule in packet.get("strategyMemoryRules", [])}
    macro_codes, seen, output = _macro_codes(packet, now), set(), []
    if len(proposals) > MAX_VALUE_DIRECTIONS:
        raise ResearchValidationError(
            "DIRECTION_VALIDATION_FAILED", "too many value directions"
        )
    for item_index, raw in enumerate(proposals):
        if _diagnostic_index is not None:
            _diagnostic_index[0] = item_index
        try:
            direction = ValueDirectionProposal.model_validate(raw).model_dump()
        except Exception as exc:
            raise ResearchValidationError(
                "SCHEMA_VALIDATION_FAILED", f"invalid direction proposal: {exc}"
            ) from exc
        key = direction["key"]
        if key in seen:
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED", "duplicate value direction"
            )
        seen.add(key)
        if any(ref not in source_aliases for ref in direction["sourceIds"]):
            raise ResearchValidationError(
                "SOURCE_REFERENCE_INVALID", "unknown direction source"
            )
        refs = sorted({source_aliases[ref] for ref in direction["sourceIds"]})
        review_ids = set(direction["reviewIndustryIds"])
        plan_sectors = {
            row["discoverySector"]
            for proposal in direction["plans"]
            for row in sector_rows
            if isinstance(row, dict)
            and row.get("discoverySector")
            and (
                plan_identity(row) == plan_identity(proposal)
                or row.get("fundCode") == proposal.get("subjectId")
            )
        }
        try:
            cycle = normalize_industry_cycle(
                direction.get("industryCycle"),
                source_aliases=source_aliases,
                sources=sources,
                now=now,
                # Prefer structured review links and server product sectors.
                # A model title may describe the scope only when neither exists.
                expected_industries=sorted(review_ids | plan_sectors)
                or [direction["title"]],
                require_checks=bool(
                    (packet.get("industryCycleResearch") or {}).get("required")
                ),
            )
        except ValidationError as exc:
            raise ResearchValidationError("SCHEMA_VALIDATION_FAILED", str(exc)) from exc
        except ValueError as exc:
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                str(exc),
                rule_id=exc.rule_id
                if isinstance(exc, IndustryCycleValidationError)
                else None,
            ) from exc
        if any(ref not in refs for ref in cycle["sourceIds"]):
            raise ResearchValidationError(
                "SOURCE_REFERENCE_INVALID",
                "industry-cycle sources must belong to the direction",
                rule_id="INDUSTRY_CYCLE_SOURCE_INVALID",
            )
        if (packet.get("industryCycleResearch") or {}).get("required") and not reviews:
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                "direction requires an explicit industry review link",
                rule_id="DIRECTION_INDUSTRY_REVIEW_UNKNOWN",
            )
        if reviews:
            review_ids.update(plan_sectors)
            if any(identity not in reviews for identity in review_ids):
                raise ResearchValidationError(
                    "DIRECTION_VALIDATION_FAILED",
                    "unknown direction industry review",
                    rule_id="DIRECTION_INDUSTRY_REVIEW_UNKNOWN",
                )
            if (packet.get("industryCycleResearch") or {}).get(
                "required"
            ) and not review_ids:
                raise ResearchValidationError(
                    "DIRECTION_VALIDATION_FAILED",
                    "direction requires an explicit industry review link",
                    rule_id="DIRECTION_INDUSTRY_REVIEW_UNKNOWN",
                )
            for identity in sorted(review_ids):
                cycle = merge_industry_cycles(cycle, reviews[identity])
            refs = sorted(set(refs) | set(cycle["sourceIds"]))
        elif review_ids:
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                "unknown direction industry review",
                rule_id="DIRECTION_INDUSTRY_REVIEW_UNKNOWN",
            )
        if historical_cycles:
            from backend.macro.daily_research import _persist_review_cycle

            for identity in [
                ("key", key),
                *(("industry", item) for item in sorted(review_ids)),
            ]:
                cycle = _persist_review_cycle(
                    cycle, historical_cycles.get(identity), now
                )
        direction["reviewIndustryIds"] = sorted(review_ids)
        direction["industryCycle"] = cycle
        macros = sorted(set(direction["macroCodes"]))
        if any(code not in macro_codes for code in macros):
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                "unknown direction macro fact",
                rule_id="DIRECTION_MACRO_REFERENCE_UNKNOWN",
            )
        for field in ("catalysts", "risks"):
            for note in direction[field]:
                if any(
                    ref not in source_aliases or source_aliases[ref] not in refs
                    for ref in note["sourceIds"]
                ):
                    raise ResearchValidationError(
                        "SOURCE_REFERENCE_INVALID",
                        "direction note needs related current sources",
                    )
                note["sourceIds"] = sorted(
                    {source_aliases[ref] for ref in note["sourceIds"]}
                )
        plans, used, revisions = [], set(), []
        for proposal in direction["plans"]:
            identity = plan_identity(proposal)
            pair = (*identity, proposal["track"])
            if pair in used:
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID", "duplicate direction product track"
                )
            used.add(pair)
            anchor = anchors.get(identity)
            if not anchor:
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID", "unknown discovery product or price"
                )
            day, current, track = _program_plan(
                anchor, proposal["track"], now, require_theme=False
            )
            if (
                anchor.get("historyQuality")
                in {"STALE", "CONFLICT", "UNVERIFIED", "UNAVAILABLE", "INSUFFICIENT"}
                or (now.astimezone(_TZ).date() - day).days > 10
            ):
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID",
                    "current direction price is stale or unverified",
                )
            old = previous_plan(previous, anchor, proposal["track"])
            revision = proposal["priceRevisionStatus"]
            old_track = None
            if same_coordinate(anchor, old):
                try:
                    old_day, _, old_track = _program_plan(
                        old, proposal["track"], now, require_theme=False
                    )
                except ValueError:
                    if (old.get("industryRiskAdjustment") or {}).get(
                        "state"
                    ) != "INSUFFICIENT":
                        raise
                    old, old_track, old_day = None, None, None
                if old_day is not None and old_day > day:
                    raise ResearchValidationError(
                        "FUND_PLAN_INVALID",
                        "direction previous plan is later than current",
                    )
            if revision == "TAKE_PROFIT_ALERT":
                if not (
                    old is not None
                    and old_track is not None
                    and old.get("priceBasis") == anchor.get("priceBasis")
                    and old.get("unit") == anchor.get("unit")
                    and old.get("sourceUri") == anchor.get("sourceUri")
                    and current >= _price(old_track.get("takeProfitT1"))
                ):
                    raise ResearchValidationError(
                        "FUND_PLAN_INVALID",
                        "direction profit alert lacks observed target",
                    )
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
            revisions.append((old, old_track, fundamental_refs))
            allowed_rules = {f"DISCOVERY:{key}", anchor.get("themeCode")}
            if any(
                rule not in rules or rules[rule].get("themeCode") not in allowed_rules
                for rule in proposal["calibrationRuleKeys"]
            ):
                raise ResearchValidationError(
                    "DIRECTION_VALIDATION_FAILED",
                    "unknown direction calibration rule",
                    rule_id="DIRECTION_CALIBRATION_RULE_UNKNOWN",
                )
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
            from backend.investment.quantitative_scan import (
                scan_minimum_history_samples,
            )
            from backend.macro.daily_research import _visible

            scan = packet.get("quantitativeScan") or {}
            if (
                scan.get("methodVersion")
                not in {"fund-quant-scan-v1", "fund-quant-scan-v2"}
                or scan.get("minimumHistorySamples") not in {120, 220}
                or scan.get("minimumHistorySamples")
                != scan_minimum_history_samples(scan)
                or not _visible(scan, now)
            ):
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID", "missing current quantitative scan"
                )
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
                raise ResearchValidationError(
                    "FUND_PLAN_INVALID",
                    "pure quantitative direction needs a triggered fund plan",
                    rule_id="DIRECTION_EVIDENCE_INSUFFICIENT",
                )
        elif len(refs) + len(macros) + len(quantitative) < 2:
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                "direction needs multiple available evidence",
                rule_id="DIRECTION_EVIDENCE_INSUFFICIENT",
            )
        if direction["signal"] in {"LEFT_VALUE", "RIGHT_TREND"} and not any(
            plan["selectedTrack"] == direction["signal"]
            and next(
                row for row in plan["tracks"] if row["track"] == plan["selectedTrack"]
            )["state"]
            == "CANDIDATE"
            for plan in plans
        ):
            raise ResearchValidationError(
                "FUND_PLAN_INVALID", "buy signal lacks a qualifying program track"
            )
        if direction["signal"] == "TAKE_PROFIT" and not any(
            plan["priceRevisionStatus"] == "TAKE_PROFIT_ALERT" for plan in plans
        ):
            raise ResearchValidationError(
                "FUND_PLAN_INVALID", "profit signal lacks observed target"
            )
        # First validate exact same-fund quantitative coordinates above; the
        # industry overlay is then applied equally to plans and their evidence.
        from backend.indicators.actionable_zones import apply_industry_cycle

        plans = [apply_industry_cycle(plan, cycle) for plan in plans]
        quantitative = [apply_industry_cycle(plan, cycle) for plan in quantitative]
        for plan, (old, old_track, fundamental_refs) in zip(plans, revisions):
            final_track = next(
                row for row in plan["tracks"] if row["track"] == plan["selectedTrack"]
            )
            if plan["priceRevisionStatus"] != "TAKE_PROFIT_ALERT" and (
                any(
                    final_track.get(key) is None
                    for key in ("entryZoneMin", "entryZoneMax")
                )
                or old_track is not None
                and any(
                    old_track.get(key) is None
                    for key in ("entryZoneMin", "entryZoneMax")
                )
            ):
                plan.update(
                    priceRevisionStatus="MAINTAINED" if old is not None else "INITIAL",
                    priceRevisionBasis="PRICE",
                    priceRevisionSourceIds=[],
                    priceRevisionNote="缺少可验证的行业折价价格锚点，等待完整价格后再比较。",
                )
            else:
                normalize_price_revision(
                    plan,
                    plan,
                    final_track,
                    old,
                    old_track,
                    fundamental_refs=fundamental_refs,
                )
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
