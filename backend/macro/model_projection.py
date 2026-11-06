"""Compact model-only inputs; immutable server packets remain authoritative."""

from copy import deepcopy
import json
import re


def _pick(row, fields):
    return {key: deepcopy(row[key]) for key in fields if key in row}


def _plan(row):
    result = _pick(
        row,
        (
            "methodVersion",
            "subjectKind",
            "subjectId",
            "fundCode",
            "label",
            "market",
            "priceBasis",
            "priceTimeBasis",
            "unit",
            "asOf",
            "sourceUri",
            "currentPrice",
            "historyQuality",
            "historySampleCount",
            "historicalPriceWindow",
            "researchAsOf",
            "researchMethodVersion",
            "researchSourceUri",
            "priceRefresh",
            "selectedTrack",
            "track",
            "subscriptionStatus",
            "subscriptionRestricted",
            "subscriptionLabel",
            "preferredEntryEligible",
            "isDelayedNav",
            "entryBlocked",
            "entryBlockReason",
            "entryStatus",
            "entryStatusTone",
            "entryReady",
            "inBuyZone",
            "inSellZone",
            "discoverySector",
            "requiredReviewIndustryIds",
            "industryReviewEligible",
            "industryCycle",
            "industryRiskWarning",
            "industryRiskAdjustment",
            "relativeStrength",
        ),
    )
    result["indicators"] = _pick(
        row.get("indicators") or {},
        (
            "rsi14",
            "ma20",
            "ma60",
            "ma200",
            "bollUpper",
            "bollLower",
            "bollMiddle",
            "momentum20",
            "atr14",
            "navVolatility14",
            "navPercentile1y",
            "p30",
            "p45",
            "p75",
            "p92",
            "troughLow",
            "priceSpan",
            "p35",
            "p40",
            "p50",
            "p55",
            "p60",
            "p65",
            "p85",
            "p95",
            "peakHigh",
            "excessReturn20d",
            "excessReturn60d",
            "drawdown60d",
        ),
    )
    result["tracks"] = [
        _pick(
            track,
            (
                "track",
                "state",
                "candidate",
                "priceState",
                "entryZoneMin",
                "entryZoneMax",
                "stopLossPrice",
                "takeProfitT1",
                "takeProfitT2",
                "sellZoneMin",
                "sellZoneMax",
                "buyZoneAnchor",
                "estimatedProfitMinPct",
                "estimatedProfitMaxPct",
                "currentToSellMaxPct",
                "missingEvidence",
                "technicalTriggered",
                "entryReady",
                "inBuyZone",
                "inSellZone",
                "industryRiskWarning",
                "industryRiskAdjustment",
                "entryBlocked",
                "entryBlockReason",
                "entryStatus",
                "entryStatusTone",
            ),
        )
        for track in row.get("tracks", [])[:2]
    ]
    if "signals" in row:
        result["signals"] = deepcopy(row["signals"])
    return result


def _observation_groups(rows):
    groups = {}
    provenance = ("publishedAt", "retrievedAt", "availableAt")
    for row in rows:
        base = _pick(
            row,
            (
                "code",
                "unit",
                "frequency",
                "sourceKey",
                "sourceName",
                "sourceUri",
                "timeBasis",
                "methodVersion",
            ),
        )
        metadata = row.get("metadata") or {}
        base["metadata"] = {
            key: deepcopy(value)
            for key, value in metadata.items()
            if key != "components"
        }
        key = json.dumps(base, ensure_ascii=False, sort_keys=True)
        group = groups.setdefault(key, {**base, **_pick(row, provenance), "points": []})
        point = _pick(row, ("referenceDate", "value"))
        point.update(
            {
                field: deepcopy(row.get(field))
                for field in provenance
                if row.get(field) != group.get(field)
            }
        )
        if "components" in metadata:
            point["components"] = deepcopy(metadata["components"])
        group["points"].append(point)
    return list(groups.values())


def _industry_research_view(packet):
    from backend.macro.research_topics import frozen_manifest

    manifest = frozen_manifest(packet)
    if manifest is None:
        return deepcopy(packet.get("industryCycleResearch"))
    contract = manifest["candidateContract"]
    result = _pick(
        contract,
        (
            "required",
            "execution",
            "asOf",
            "lookbackDays",
            "maximumCandidates",
            "omittedCandidateCount",
            "omittedRiskCandidateCount",
            "omittedIndustries",
            "omittedPlanIndustryIds",
            "standardIndustryCodes",
            "planReviewEligibility",
            "newDirectionRequiresSameChecks",
            "scopeSearchTerms",
        ),
    )
    result.update(
        discoveryVersion=packet["researchDiscovery"]["version"],
        registryVersion=manifest["registryVersion"],
        budget=deepcopy(manifest["budget"]),
        window=deepcopy(manifest["window"]),
        maximumQueries=manifest["budget"]["maxQueries"],
        candidates=[
            {
                "industry": scope,
                "priority": index + 1,
                "queries": [
                    _pick(task, ("taskId", "kind", "query"))
                    for task in manifest["tasks"]
                    if task["industryId"] == scope
                ],
            }
            for index, scope in enumerate(manifest["selectedScopes"])
        ],
    )
    return result


def model_packet(packet, *, version="daily-model-input-v1"):
    """One price table, current evidence and previous fund coordinates, no history dumps."""
    if version not in {"daily-model-input-v1", "daily-model-input-v2"}:
        raise ValueError("unsupported model input projection version")
    result = _pick(
        packet,
        (
            "methodVersion",
            "asOf",
            "macros",
            "series",
            "directions",
            "schedules",
            "documents",
            "energy",
            "previousQuestions",
            "previousResearch",
            "previousIndustryRiskStates",
            "previousUpcoming",
            "sourceResults",
            "strategyMemoryRules",
            "institutionalSignals",
            "discoveryCoverage",
            "dynamicNavSourceResults",
        ),
    )
    research_view = _industry_research_view(packet)
    if research_view is not None:
        result["industryCycleResearch"] = research_view
    public = packet.get("alphaPublic") or {}
    referenced_media = {
        row.get("mediaEvidenceId")
        for rows in (packet.get("institutionalSignals") or {}).values()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict)
    }
    result["alphaPublic"] = _pick(
        public, ("asOf", "sourceResults", "observations", "odds", "launches")
    )
    result["alphaPublic"]["observations"] = _observation_groups(
        public.get("observations", [])
    )
    result["alphaPublic"]["catalysts"] = [
        deepcopy(row)
        for row in public.get("catalysts", [])
        if row.get("researchPriority") == "CORE_CATALYST"
        or row.get("id") in referenced_media
        or any(bucket != "OTHER" for bucket in row.get("sectorBuckets", []))
    ]
    kept_ids = {row.get("id") for row in result["alphaPublic"]["catalysts"]}
    result["alphaPublic"]["sectorCatalysts"] = {
        key: [identity for identity in ids if identity in kept_ids]
        for key, ids in public.get("sectorCatalysts", {}).items()
        if any(identity in kept_ids for identity in ids)
    }
    result["candidateIndex"] = [
        _pick(row, ("id", "title", "url", "publishedDate", "retrievedAt", "sourceKey"))
        for row in packet.get("candidateIndex", [])[:16]
    ]
    scan = packet.get("quantitativeScan")
    if scan is not None:
        from backend.macro.industry_contract import plan_industry_ids

        contract = packet.get("industryCycleResearch") or {}
        selected = {row["industry"] for row in contract.get("candidates", [])}
        eligibility = {
            (row["subjectKind"], row["subjectId"]): row
            for row in contract.get("planReviewEligibility", [])
        }

        def research_plan(row):
            item = _plan(row)
            required = sorted(plan_industry_ids(packet, row))
            obligation = eligibility.get((row.get("subjectKind"), row.get("subjectId")))
            item.update(
                requiredReviewIndustryIds=required,
                industryReviewEligible=bool(required) and set(required) <= selected,
            )
            if obligation is not None:
                item.update(
                    _pick(
                        obligation,
                        ("requiredReviewIndustryIds", "industryReviewEligible"),
                    )
                )
            return item

        result["quantitativeScan"] = {
            **_pick(
                scan, ("methodVersion", "asOf", "coverage", "minimumHistorySamples")
            ),
            "items": [research_plan(row) for row in scan.get("items", [])[:46]],
        }
    else:
        # Old contract packets do not have the unified scan yet.
        result.update(
            _pick(
                packet,
                (
                    "actionableZones",
                    "themeActionableZones",
                    "themes",
                    "discoveryUniverse",
                    "discoveryActionableZones",
                ),
            )
        )
    result["previousValueDirections"] = [
        _pick(
            row,
            (
                "key",
                "title",
                "thesis",
                "signal",
                "sourceIds",
                "macroCodes",
                "watchFor",
                "catalysts",
                "risks",
                "industryCycle",
            ),
        )
        for row in packet.get("previousValueDirections", [])[:8]
    ]
    prior, seen = [], set()
    for row in [
        *(
            plan
            for direction in packet.get("previousValueDirections", [])
            for plan in direction.get("plans", [])
        ),
        *packet.get("previousActionablePlans", []),
    ]:
        key = (
            row.get("fundCode"),
            row.get("selectedTrack", row.get("track")),
            row.get("priceBasis"),
            row.get("unit"),
        )
        if key not in seen:
            seen.add(key)
            prior.append(_plan(row))
    result["previousFundPlans"] = prior[:46]
    if version == "daily-model-input-v2":
        from backend.macro.model_input_v2 import compact_model_input

        return compact_model_input(result, packet)
    return result


def separate_fund_codes(text, codes):
    """Repair known adjacent fund codes, while preserving non-investment identifiers."""
    valid = {
        code
        for code in codes
        if isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code)
    }
    if not valid:
        return text
    # Thirteen-digit runs may contain one spurious joining digit between known codes.
    pattern = re.compile(r"(?<![0-9.])([0-9]{6})(?:[0-9])?([0-9]{6})(?![0-9.])")

    def repair(match):
        first, second = match.groups()
        before = text[max(0, match.start() - 8) : match.start()]
        after = text[match.end() : match.end() + 3]
        if (
            first not in valid
            or second not in valid
            or re.search(r"金额|订单|编号|流水|合同|账号|电话|日期", before)
            or re.match(r"元|万元|亿元|日|号", after)
        ):
            return match.group(0)
        return first + "与" + second

    return pattern.sub(repair, text)


def known_company_names(packet):
    rows = [
        *(packet.get("alphaPublic") or {}).get("catalysts", []),
        *packet.get("documents", []),
    ]
    text = " ".join(
        str(row.get(key) or "")
        for row in rows
        if isinstance(row, dict)
        for key in ("title", "content", "summary", "quote")
    )
    return tuple(name for name in ("云南锗业", "云南锡业", "云南铜业") if name in text)


def readable_fund_codes(value, codes, company_names=()):
    if isinstance(value, str):
        result = separate_fund_codes(value, codes)
        if len(company_names) == 1:
            result = re.sub(r"云南\s+业", company_names[0], result)
        return result
    if isinstance(value, list):
        return [readable_fund_codes(item, codes, company_names) for item in value]
    if isinstance(value, dict):
        # Identity, URLs and precise prices must never be rewritten.
        narrative = {
            "summary",
            "macroCycle",
            "thesis",
            "title",
            "condition",
            "impact",
            "counter",
            "channels",
            "watchFor",
            "unknowns",
            "priceRevisionNote",
            "calibrationNote",
        }
        return {
            key: readable_fund_codes(item, codes, company_names)
            if key in narrative
            or isinstance(item, dict)
            or isinstance(item, list)
            and any(isinstance(child, (dict, list)) for child in item)
            else item
            for key, item in value.items()
        }
    return value
