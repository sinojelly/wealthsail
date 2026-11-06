"""Versioned model reading view. The original packet remains the validator input.

Only server-generated display/calculation details are omitted. Evidence text uses
exact, lossless references; IDs, dates, quantities and frozen queries stay literal.
"""

from collections import Counter
from copy import deepcopy
import json

VERSION = "daily-model-input-v2"


def _pick(row, fields):
    return {key: deepcopy(row[key]) for key in fields if key in row}


def _current_plan(row):
    result = deepcopy(row)
    result["indicators"] = _pick(
        row.get("indicators") or {},
        (
            "rsi14",
            "ma60",
            "ma200",
            "momentum20",
            "navPercentile1y",
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
                "missingEvidence",
                "technicalTriggered",
                "entryReady",
                "inBuyZone",
                "inSellZone",
                "industryRiskWarning",
                "industryRiskAdjustment",
                "entryBlocked",
                "entryBlockReason",
            ),
        )
        for track in row.get("tracks", [])
    ]
    result["signals"] = [
        _pick(
            signal,
            (
                "track",
                "triggerState",
                "inEntryZone",
                "distanceToEntryPct",
                "candidate",
                "entryReady",
                "technicalTriggered",
                "entryBlocked",
                "entryBlockReason",
                "inSellZone",
                "preferredEntryEligible",
            ),
        )
        for signal in row.get("signals", [])
    ]
    # Tone/labels and repeated per-track prose are presentation details. The
    # block reason, restrictions, risk facts and readiness flags remain literal.
    result.pop("entryStatusTone", None)
    strength = result.get("relativeStrength")
    if isinstance(strength, dict):
        result["relativeStrength"] = _pick(
            strength,
            (
                "state",
                "asOf",
                "benchmark",
                "weakening",
                "trendWeakening",
                "lowerHighs",
                "consecutiveWeakDays",
                "breakdownDate",
                "missingEvidence",
            ),
        )
    return result


def _previous_plan(row):
    result = _pick(
        row,
        (
            "methodVersion",
            "subjectKind",
            "subjectId",
            "fundCode",
            "market",
            "priceBasis",
            "priceTimeBasis",
            "unit",
            "asOf",
            "sourceUri",
            "researchAsOf",
            "researchMethodVersion",
            "researchSourceUri",
            "currentPrice",
            "historyQuality",
            "selectedTrack",
            "track",
            "industryCycle",
            "industryRiskWarning",
            "industryRiskAdjustment",
        ),
    )
    selected = row.get("selectedTrack", row.get("track"))
    result["tracks"] = [
        _pick(
            track,
            (
                "track",
                "priceState",
                "entryZoneMin",
                "entryZoneMax",
                "stopLossPrice",
                "takeProfitT1",
                "takeProfitT2",
            ),
        )
        for track in row.get("tracks", [])
        if selected is None or track.get("track") == selected
    ]
    return result


# These are text-bearing properties, never task identities, queries, precise
# prices, dates or source IDs. A shared value always expands to the exact string.
_TEXT_FIELDS = frozenset(
    {
        "content",
        "summary",
        "quote",
        "title",
        "definition",
        "definitionNote",
        "sourceUri",
        "comparisonSourceUri",
        "url",
        "sourceName",
        "entryStatus",
        "indexBasis",
        "transformation",
        "reason",
        "description",
    }
)


def _share_text(result):
    counts = Counter()

    def visit(value, field=None):
        if isinstance(value, dict):
            for key, child in value.items():
                # The complete frozen industry contract stays byte-exact.
                if key not in {"industryCycleResearch", "previousIndustryRiskStates"}:
                    visit(child, key)
        elif isinstance(value, list):
            for child in value:
                visit(child, field)
        elif isinstance(value, str) and field in _TEXT_FIELDS:
            if len(value.encode("utf-8")) >= 64:
                counts[value] += 1

    visit(result)
    repeated = {value for value, count in counts.items() if count > 1}
    identities, texts = {}, {}

    def replace(value, field=None):
        if isinstance(value, dict):
            return {
                key: child
                if key in {"industryCycleResearch", "previousIndustryRiskStates"}
                else replace(child, key)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [replace(child, field) for child in value]
        if isinstance(value, str) and field in _TEXT_FIELDS and value in repeated:
            if value not in identities:
                identity = "t" + str(len(identities) + 1)
                identities[value] = identity
                texts[identity] = value
            return {"textRef": identities[value]}
        return value

    result = replace(result)
    if texts:
        result["sharedText"] = texts
    return result


def _common_defaults(container, rows_key, defaults_key, fields):
    rows = container.get(rows_key) or []
    if len(rows) < 2:
        return
    common = {
        key: deepcopy(rows[0][key])
        for key in fields
        if key in rows[0]
        and all(key in row and row[key] == rows[0][key] for row in rows[1:])
    }
    if common:
        container[defaults_key] = common
        for row in rows:
            for key in common:
                del row[key]


def compact_model_input(projected, original):
    """No truncation, URL-based fact merging, source downgrade or validator change."""
    result = deepcopy(projected)
    result["modelProjectionVersion"] = VERSION
    if "quantitativeScan" in result:
        result["quantitativeScan"]["items"] = [
            _current_plan(row) for row in result["quantitativeScan"]["items"]
        ]
    result["previousFundPlans"] = [
        _previous_plan(row) for row in result["previousFundPlans"]
    ]
    from backend.macro.research_topics import frozen_manifest

    manifest = frozen_manifest(original)
    if manifest is not None:
        tasks = {row["taskId"]: row for row in manifest["tasks"]}
        for candidate in result["industryCycleResearch"]["candidates"]:
            for task in candidate["queries"]:
                task["industryId"] = tasks[task["taskId"]]["industryId"]
        # Per-fund obligations are already attached to each quantitative item.
        if "quantitativeScan" in result:
            result["industryCycleResearch"].pop("planReviewEligibility", None)
        # Exact search terms for deferred scopes are not execution tasks. Their
        # names and all omitted risk/plan lists remain; selected queries are exact.
        selected = set(manifest["selectedScopes"])
        terms = result["industryCycleResearch"].get("scopeSearchTerms")
        if isinstance(terms, dict):
            result["industryCycleResearch"]["scopeSearchTerms"] = {
                key: value for key, value in terms.items() if key in selected
            }
    public = result["alphaPublic"]
    metadata, identities = {}, {}
    for group in public["observations"]:
        value = group.pop("metadata")
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if key not in identities:
            identity = "m" + str(len(identities) + 1)
            identities[key] = identity
            metadata[identity] = value
        group["metadataRef"] = identities[key]
    if metadata:
        public["observationMetadata"] = metadata
    _common_defaults(
        public,
        "catalysts",
        "catalystDefaults",
        (
            "sourceKey",
            "retrievedAt",
            "availableAt",
            "factVerification",
            "researchPriority",
            "phase",
            "asOf",
        ),
    )
    _common_defaults(
        public,
        "observations",
        "observationDefaults",
        (
            "retrievedAt",
            "availableAt",
            "publishedAt",
            "timeBasis",
            "methodVersion",
        ),
    )
    signals = result.get("institutionalSignals") or {}
    _common_defaults(
        signals,
        "etfFlows",
        "etfFlowDefaults",
        (
            "retrievedAt",
            "verification",
            "institutionAttribution",
        ),
    )
    return _share_text(result)
