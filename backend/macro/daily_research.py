"""Public research contracts and deterministic projections; never trading authority."""

import json
import hashlib
import ipaddress
import math
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from backend.macro.research_diagnostics import (
    REASON_CODES,
    RULE_IDS,
    is_safe_item_index,
    sanitize_missing_industry_codes,
)
from backend.macro.model_projection import (
    model_packet,
    separate_fund_codes as separate_fund_codes,
    readable_fund_codes,
    known_company_names,
)
from backend.macro.alpha_research import ActionableProposal, validate_actionable_plans
from backend.macro.industry_cycle import (
    IndustryCycleValidationError,
    IndustryCycleReview,
    earnings_recovery_verified,
    normalize_industry_cycle,
)
from backend.macro.value_directions import (
    MAX_VALUE_DIRECTIONS,
    ValueDirectionProposal,
    diagnose_value_directions,
)
from backend.macro.research_topics import TopicProposal, direction_limit, is_v2
from backend.macro.event_metadata import event_metadata, source_metadata

VERSION = "daily-research-v1"
PROMPT_VERSION = "daily-research-input-v27"
MAX_REPORT_SOURCES = 24


class ResearchValidationError(ValueError):
    """Validation failure during research report checking.

    Carries a canonical reason_code that is safe for telemetry and logs.
    """

    def __init__(
        self,
        reason_code: str,
        message: str = "",
        *,
        rule_id=None,
        item_index=None,
        missing_industry_codes=None,
    ):
        if not isinstance(reason_code, str) or reason_code not in REASON_CODES:
            raise ValueError("invalid research diagnostic reason code")
        if rule_id is not None and (
            not isinstance(rule_id, str) or rule_id not in RULE_IDS
        ):
            raise ValueError("invalid research diagnostic rule id")
        if item_index is not None and not is_safe_item_index(item_index):
            raise ValueError("invalid research diagnostic item index")
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.rule_id = rule_id
        self.item_index = item_index
        self.missing_industry_codes = sanitize_missing_industry_codes(
            missing_industry_codes
        )


def _schema_item_index(error):
    """Read only a bounded top-level review/direction index from schema paths."""
    if not isinstance(error, ValidationError):
        return None
    for detail in error.errors():
        location = detail.get("loc", ())
        if (
            len(location) >= 2
            and location[0] in {"industryCycleReviews", "valueDirections"}
            and is_safe_item_index(location[1])
        ):
            return location[1]
    return None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Coverage(_Strict):
    topic: Literal[
        "DOMESTIC_GROWTH",
        "US_RATES",
        "ENERGY_SHIPPING",
        "GEOPOLITICS_POLICY",
        "GLOBAL_EQUITIES",
        "CALENDAR",
    ]
    status: Literal["FOUND", "NOT_FOUND", "UNVERIFIED"]
    summary: str = Field(min_length=1, max_length=300)


class _Source(_Strict):
    id: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=300)
    publishedDate: str | None
    publishedAt: str | None


class _Scenario(_Strict):
    condition: str = Field(min_length=1, max_length=300)
    impact: str = Field(min_length=1, max_length=300)
    counter: str = Field(min_length=1, max_length=300)


_LinkLabel = Annotated[str, Field(min_length=1, max_length=120)]


class _EventLinks(_Strict):
    relatedTopicKeys: list[_LinkLabel] = Field(default_factory=list, max_length=16)
    relatedFormalDirectionKeys: list[_LinkLabel] = Field(default_factory=list, max_length=16)
    relatedMacroConcepts: list[_LinkLabel] = Field(default_factory=list, max_length=16)
    candidateConceptLabels: list[_LinkLabel] = Field(default_factory=list, max_length=16)


class _Event(_EventLinks):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=600)
    phase: Literal["PLANNED", "ANNOUNCED", "IMPLEMENTED", "REPORTED", "OPINION"]
    importance: Literal["LOW", "MEDIUM", "HIGH"]
    sourceIds: list[str] = Field(min_length=1, max_length=8)
    channels: list[str] = Field(max_length=6)
    affectedDirectionIds: list[str] = Field(max_length=16)
    scenarios: list[_Scenario] = Field(max_length=3)
    watchFor: list[str] = Field(max_length=8)
    unknowns: list[str] = Field(max_length=8)
    marketReaction: Literal["UNVERIFIED"]


class _Upcoming(_EventLinks):
    title: str = Field(min_length=1, max_length=200)
    phase: Literal["PLANNED"]
    importance: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    scheduledDate: str | None
    scheduledAt: str | None
    sourceIds: list[str] = Field(min_length=1, max_length=8)
    affectedDirectionIds: list[str] = Field(max_length=16)
    watchFor: list[str] = Field(max_length=8)


class _NarrativeDirectionIds(_Strict):
    summary: list[str] = Field(max_length=8)
    macroCycle: list[str] = Field(max_length=8)
    coverage: list[str] = Field(max_length=8)


class _Report(_Strict):
    summary: str = Field(min_length=1, max_length=1000)
    coverage: list[_Coverage] = Field(max_length=6)
    sources: list[_Source] = Field(max_length=MAX_REPORT_SOURCES)
    events: list[_Event] = Field(max_length=12)
    upcoming: list[_Upcoming] = Field(max_length=12)
    # Only select program-supplied series; the model cannot author energy values.
    energy: list[Literal["DCOILBRENTEU", "GASDESW"]] = Field(max_length=2)
    macroCycle: str = Field(min_length=1, max_length=1000)
    # Optional display extension: any JSON shape is repaired independently.
    cycleInsights: object | None = None
    # Stored reports predate this provenance; missing metadata never grants repair.
    narrativeDirectionIds: _NarrativeDirectionIds | None = None
    actionablePlans: list[ActionableProposal] = Field(max_length=24)
    valueDirections: list[ValueDirectionProposal] = Field(
        max_length=MAX_VALUE_DIRECTIONS
    )
    industryCycleReviews: list[IndustryCycleReview] = Field(
        default_factory=list, max_length=64
    )
    topicCatalog: list[dict] = Field(default_factory=list, max_length=64)


REPORT_SCHEMA = _Report.model_json_schema()
REPORT_SCHEMA["required"].append("industryCycleReviews")
REPORT_SCHEMA["required"].append("narrativeDirectionIds")
REPORT_SCHEMA["required"].append("topicCatalog")
_topic_schema = TopicProposal.model_json_schema()
REPORT_SCHEMA["properties"]["topicCatalog"]["items"] = _topic_schema
REPORT_SCHEMA["properties"]["industryCycleReviews"]["maxItems"] = 10
REPORT_SCHEMA["properties"]["actionablePlans"]["maxItems"] = 0
for _event_schema_name in ("_Event", "_Upcoming"):
    REPORT_SCHEMA["$defs"][_event_schema_name]["required"].extend(
        ("relatedTopicKeys", "relatedFormalDirectionKeys", "relatedMacroConcepts", "candidateConceptLabels")
    )
# New model requests must audit every direction. The permissive Python default
# is only for reading pre-upgrade stored reports, never the outbound contract.
_direction_schema = REPORT_SCHEMA["$defs"]["ValueDirectionProposal"]
_direction_schema["required"].append("industryCycle")
_direction_schema["required"].append("reviewIndustryIds")
_direction_schema["properties"]["industryCycle"] = {
    "anyOf": [{"$ref": "#/$defs/IndustryCycleProposal"}, {"type": "null"}]
}


def _request_industry_scopes(packet):
    """Expose global identities; the frozen subset only describes audit work."""
    from backend.macro.research_topics import frozen_manifest, is_targeted_research
    from backend.macro.industry_contract import STANDARD_INDUSTRY_CODES

    manifest = frozen_manifest(packet)
    if manifest is None:
        return None
    observation_scopes = (
        manifest["deferredScopes"] if is_targeted_research(packet) else []
    )
    return {
        "allowedReviewIndustryIds": sorted(STANDARD_INDUSTRY_CODES),
        "freeIndustryLabelsAllowed": True,
        "observationScopes": observation_scopes,
    }


def report_schema_for_packet(packet):
    """Discovery can name every global industry or a new free-form label.

    The frozen search budget describes extra audit work, never permission to
    produce an insight. Local content defects are repaired after generation.
    """
    return deepcopy(REPORT_SCHEMA)


_TZ = ZoneInfo("Asia/Shanghai")
_MACROS = {"PMI", "CPI_YOY", "PPI_YOY", "M2_YOY"}
_INDICES = {"hs300", "a500", "zz500"}
_ENERGY = {"DCOILBRENTEU", "GASDESW"}


def _stamp(value):
    try:
        result = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        return (
            result
            if result.tzinfo is not None and result.utcoffset() is not None
            else None
        )
    except (ValueError, TypeError):
        return None


def _now(value):
    result = _stamp(value)
    if result is None:
        raise ValueError("research requires an aware clock")
    return result


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _url(value):
    try:
        uri = urlsplit(value)
        host = uri.hostname or ""
        if (
            uri.scheme != "https"
            or uri.username
            or uri.password
            or uri.port not in (None, 443)
        ):
            raise ValueError("unsafe research URL")
        if (
            not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", host)
            or host.endswith((".local", ".localhost", ".internal", ".test"))
            or host.replace(".", "").isdigit()
        ):
            raise ValueError("unsafe research host")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError("private research host")
        except ValueError as exc:
            if "research host" in str(exc):
                raise
        return urlunsplit(uri._replace(netloc=host.lower(), fragment=""))
    except (TypeError, AttributeError) as exc:
        raise ValueError("invalid research URL") from exc


def _visible(row, now, *, planned=False):
    for field in ("retrievedAt", "availableAt", "publishedAt"):
        if row.get(field) is not None:
            stamp = _stamp(row[field])
            if stamp is None or stamp > now:
                return False
    for field in ("asOf", "referenceDate", "publishedDate"):
        if row.get(field) is not None:
            day = _day(row[field])
            if day is None or day > now.astimezone(_TZ).date():
                return False
    if row.get("referencePeriod") is not None and not planned:
        period = str(row["referencePeriod"])
        if (
            not re.fullmatch(r"\d{4}-\d{2}", period)
            or _day(period + "-01") is None
            or period > now.astimezone(_TZ).strftime("%Y-%m")
        ):
            return False
    return True


def _public_value(value):
    if isinstance(value, datetime):
        stamp = _stamp(value)
        return stamp.isoformat() if stamp else None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (float, Decimal)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        return value[:2000]
    return value if value is None or type(value) in (int, bool) else None


def _pick(row, fields):
    result = {}
    numeric = {
        "value",
        "previousValue",
        "change",
        "changePct",
        "periodChange",
        "change3Months",
        "change6Months",
        "change5Samples",
        "percentile",
        "median",
        "ma200",
        "ma20",
        "ma60",
        "rsi14",
        "atr14",
        "bollLower",
        "bollMiddle",
        "bollUpper",
        "momentum20",
        "distanceMA200",
        "momentum63",
        "volatility20",
        "maxDrawdown",
        "latest",
    }
    for key in fields:
        if key not in row:
            continue
        if key == "sourceUri":
            try:
                result[key] = _url(row[key])
            except ValueError:
                continue
        else:
            result[key] = (
                _finite(row[key]) if key in numeric else _public_value(row[key])
            )
    return result


def _provenance(row):
    result = _pick(
        row, ("retrievedAt", "availableAt", "publishedAt", "timeBasis", "sourceName")
    )
    if row.get("sourceUri"):
        try:
            result["sourceUri"] = _url(row["sourceUri"])
        except ValueError:
            pass
    return result


def _texts(value, limit=8, width=300):
    return list(
        dict.fromkeys(
            text[:width]
            for text in (value if isinstance(value, list) else [])
            if isinstance(text, str) and text.strip()
        )
    )[:limit]


def _public_industry_cycle(value):
    result = _pick(
        value,
        (
            "stage",
            "label",
            "evidenceSummary",
            "researchStatus",
            "auditStructureStatus",
            "earningsVerified",
            "earningsIndustryScopeVerified",
            "forwardPlansOnly",
            "riskLevel",
            "leftDiscountScale",
            "industryRiskWarning",
            "earningsEvidenceScope",
            "earningsEntity",
            "earningsReportPeriod",
            "confirmedAt",
            "searchExecutionVerified",
        ),
    )
    for key in (
        "riskFlags",
        "confirmedRiskFlags",
        "suspectedRiskFlags",
        "persistedRiskFlags",
        "sourceIds",
        "evidenceGaps",
    ):
        result[key] = _texts(value.get(key), limit=24)
    for key in ("evidence", "riskChecks", "reviewEvidenceSummaries"):
        result[key] = [
            {
                **_pick(
                    row,
                    (
                        "kind",
                        "industry",
                        "summary",
                        "status",
                        "query",
                        "verification",
                        "timeVerification",
                        "currentFactVerified",
                        "companyName",
                        "entityId",
                        "reportPeriod",
                        "factIdentityVerified",
                        "eventTimeVerification",
                        "eventPeriod",
                        "eventPeriodStart",
                        "eventPeriodEnd",
                        "factCheckReason",
                        "historicalEvidence",
                        "strength",
                        "independentSourceCount",
                        "claimSupported",
                        "industryScopeVerified",
                        "queryValidation",
                        "searchExecutionVerified",
                    ),
                ),
                "sourceIds": _texts(row.get("sourceIds"), limit=24),
            }
            for row in (value.get(key) or [])[:64]
            if isinstance(row, dict)
        ]
    result["confirmedRiskAt"] = {
        str(flag)[:80]: stamp.isoformat()
        for flag, raw in (value.get("confirmedRiskAt") or {}).items()
        if (stamp := _stamp(raw)) is not None
    }
    result["persistedEvidence"] = []
    for row in (value.get("persistedEvidence") or [])[:16]:
        if not isinstance(row, dict):
            continue
        proof = {
            **_pick(row, ("flag", "confirmedAt", "summary")),
            "sourceIds": _texts(row.get("sourceIds"), 24),
        }
        proof["sources"] = _public_industry_sources(row.get("sources"))
        result["persistedEvidence"].append(proof)
    return result


def _public_industry_sources(rows):
    output = []
    for row in (rows if isinstance(rows, list) else [])[:24]:
        if not isinstance(row, dict):
            continue
        try:
            url = _url(row.get("url"))
        except ValueError:
            continue
        output.append(
            {
                **_pick(
                    row,
                    (
                        "id",
                        "title",
                        "quote",
                        "publishedDate",
                        "verification",
                        "retrievedAt",
                        "contentHash",
                    ),
                ),
                "url": url,
            }
        )
    return output


def _public_risk_contracts(value, result):
    """Project nested server contracts explicitly; _pick rejects dictionaries."""
    if isinstance(value.get("industryCycle"), dict):
        result["industryCycle"] = _public_industry_cycle(value["industryCycle"])
    if isinstance(value.get("industryRiskAdjustment"), dict):
        result["industryRiskAdjustment"] = _pick(
            value["industryRiskAdjustment"],
            (
                "state",
                "entryShiftScale",
                "scale",
                "riskLevel",
                "baseEntryShiftScale",
                "relativeStrengthUpgrade",
            ),
        )
    strength = value.get("relativeStrength")
    if isinstance(strength, dict):
        result["relativeStrength"] = {
            **_pick(
                strength,
                (
                    "state",
                    "benchmark",
                    "asOf",
                    "excessReturn20d",
                    "excessReturn60d",
                    "excessReturnUnit",
                    "drawdown60d",
                    "drawdownUnit",
                    "breakdownDate",
                    "weakening",
                    "lowerHighs",
                    "trendWeakening",
                    "consecutiveWeakDays",
                    "sampleCount",
                    "benchmarkSampleCount",
                ),
            ),
            "reasons": _texts(strength.get("reasons")),
            "missingEvidence": _texts(strength.get("missingEvidence")),
        }
    base = value.get("industryAdjustmentBase")
    if isinstance(base, dict):
        track = base.get("leftTrack") or {}
        result["industryAdjustmentBase"] = {
            **_pick(
                base,
                (
                    "close",
                    "scale",
                    "navScale",
                    "navBasis",
                    "places",
                    "technicalCandidate",
                ),
            ),
            "conditionReasons": _texts(base.get("conditionReasons")),
            "navHistory": _pick(
                base.get("navHistory") or {},
                (
                    "p30",
                    "p35",
                    "p40",
                    "p45",
                    "p50",
                    "p55",
                    "p60",
                    "p65",
                    "p75",
                    "p85",
                    "p92",
                    "p95",
                    "peakHigh",
                    "troughLow",
                    "priceSpan",
                ),
            ),
            "leftTrack": {
                **_pick(
                    track,
                    (
                        "track",
                        "state",
                        "priceState",
                        "candidate",
                        "technicalTriggered",
                        "inBuyZone",
                        "entryReady",
                        "entryBlocked",
                        "entryBlockReason",
                        "entryStatus",
                        "entryStatusTone",
                        "inSellZone",
                        "buyZoneAnchor",
                        "entryZoneMin",
                        "entryZoneMax",
                        "stopLossPrice",
                        "takeProfitT1",
                        "takeProfitT2",
                        "sellZoneMin",
                        "sellZoneMax",
                        "estimatedProfitMinPct",
                        "estimatedProfitMaxPct",
                        "currentToSellMaxPct",
                        "asOf",
                    ),
                ),
                "reasons": _texts(track.get("reasons")),
                "missingEvidence": _texts(track.get("missingEvidence")),
            },
        }


def _public_actionable_plan(value, index_id, now, *, subject_kind="index"):
    if (
        not isinstance(value, dict)
        or value.get("subjectKind") != subject_kind
        or subject_kind not in {"index", "fund", "nav"}
        or value.get("subjectId") != index_id
        or not value.get("asOf")
        or not _visible(value, now)
    ):
        return None
    result = _pick(
        value,
        (
            "methodVersion",
            "subjectKind",
            "subjectId",
            "label",
            "priceBasis",
            "unit",
            "asOf",
            "sourceUri",
            "volatilityMethod",
            "priceTimeBasis",
            "historyQuality",
            "historySampleCount",
            "historyTargetSamples",
            "historyCoverage",
            "overseasCalendarRequired",
            "isDelayedNav",
            "researchAsOf",
            "researchMethodVersion",
            "researchSourceUri",
            "industryCycle",
            "industryRiskWarning",
            "industryRiskAdjustment",
            "industryAdjustmentBase",
            "relativeStrength",
        ),
    )
    if isinstance(value.get("priceRefresh"), dict):
        result["priceRefresh"] = _pick(
            value["priceRefresh"],
            ("state", "calculatedAt", "navAsOf", "methodVersion", "reason"),
        )
    if isinstance(value.get("historicalPriceWindow"), dict):
        result["historicalPriceWindow"] = _pick(
            value["historicalPriceWindow"],
            ("state", "sampleCount", "requiredSamples", "start", "end"),
        )
    result["orderPriceAvailable"] = False
    price_keys = (
        "entryZoneMin",
        "entryZoneMax",
        "stopLossPrice",
        "takeProfitT1",
        "takeProfitT2",
    )

    def price(raw):
        if isinstance(raw, bool) or _finite(raw) is None or _finite(raw) <= 0:
            return None
        return str(raw)

    result["currentPrice"] = price(value.get("currentPrice"))
    change = value.get("currentChangePct")
    result["currentChangePct"] = (
        str(change)
        if result["currentPrice"] is not None
        and value.get("priceBasis") == "UNIT_NAV"
        and not isinstance(change, bool)
        and _finite(change) is not None
        and _finite(change) > -100
        else None
    )
    result["parameters"] = _pick(
        value.get("parameters") or {},
        (
            "ma",
            "rsi",
            "atr",
            "bollinger",
            "momentum",
            "maximumPePercentile",
            "maximumMA200Gap",
            "oversoldRSI",
            "navAbsoluteChangeWindow",
            "navAbsoluteChangeMultiplier",
            "navStdWindow",
            "navStdMultiplier",
            "navRiskFloorRatio",
            "minimumRewardRiskT1",
            "minimumRewardRiskT2",
            "rewardRiskBasis",
            "maximumTrendRSI",
            "navPercentileWindow",
            "maximumNavPercentile",
            "maximumHighNavMA20Ratio",
            "trendEntryLowScale",
            "trendEntryHighScale",
            "historicalPriceWindow",
            "historicalPercentileMethod",
            "middleHighNavPercentile",
            "maximumTrendBuyPricePercentile",
            "sellZoneLowerPricePercentile",
            "sellZoneUpperPricePercentile",
            "navRewardRiskBasis",
            "middleHighPriceSpanRatio",
            "maximumHighBuyPricePercentile",
            "maximumLowBuyPricePercentile",
            "maximumHighBuyPriceSpanRatio",
            "maximumLowBuyPriceSpanRatio",
            "minimumSellPriceSpanRatio",
            "minimumTargetProfitRatio",
            "historicalPeakSellCapRatio",
        ),
    )
    history_window = (value.get("parameters") or {}).get("historicalPriceWindow")
    if isinstance(history_window, list) and len(history_window) == 2:
        result["parameters"]["historicalPriceWindow"] = [
            _public_value(item) for item in history_window
        ]
    result["indicators"] = _pick(
        value.get("indicators") or {},
        (
            "ma20",
            "ma60",
            "ma200",
            "rsi14",
            "atr14",
            "navVolatility14",
            "bollLower",
            "bollMiddle",
            "bollUpper",
            "momentum20",
            "navPercentile1y",
            "p35",
            "p40",
            "p50",
            "p55",
            "p60",
            "p65",
            "p85",
            "p95",
            "peakHigh",
            "p30",
            "p45",
            "p75",
            "p92",
            "troughLow",
            "priceSpan",
            "excessReturn20d",
            "excessReturn60d",
            "drawdown60d",
        ),
    )
    if "indicatorStates" in value:
        result["indicatorStates"] = {
            key: _pick(state, ("state", "sampleCount", "requiredSamples"))
            for key, state in (value.get("indicatorStates") or {}).items()
            if key in result["indicators"] and isinstance(state, dict)
        }
    result["tracks"] = []
    for row in value.get("tracks", [])[:2]:
        if (
            not isinstance(row, dict)
            or row.get("track") not in {"LEFT_VALUE", "RIGHT_TREND"}
            or row.get("state") not in {"CANDIDATE", "WATCH", "INSUFFICIENT"}
        ):
            continue
        result["tracks"].append(
            {
                **_pick(
                    row,
                    (
                        "track",
                        "state",
                        "priceState",
                        "candidate",
                        "technicalTriggered",
                        "inBuyZone",
                        "entryReady",
                        "entryBlocked",
                        "entryBlockReason",
                        "entryStatus",
                        "entryStatusTone",
                        "inSellZone",
                        "buyZoneAnchor",
                        "industryRiskWarning",
                        "industryRiskAdjustment",
                    ),
                ),
                **{key: price(row.get(key)) for key in price_keys},
                **{
                    key: price(row[key])
                    for key in ("sellZoneMin", "sellZoneMax")
                    if key in row
                },
                **{
                    key: str(row[key])
                    if not isinstance(row[key], bool) and _finite(row[key]) is not None
                    else None
                    for key in (
                        "estimatedProfitMinPct",
                        "estimatedProfitMaxPct",
                        "currentToSellMaxPct",
                    )
                    if key in row
                },
                "reasons": _texts(row.get("reasons")),
            }
        )
    _public_risk_contracts(value, result)
    return result


_MARKET_NAMES = {
    "OIL": r"WTI|Brent|布伦特|布油|美油|纽约原油|国际油价|原油",
    "NASDAQ": r"NASDAQ|纳斯达克|纳指",
    "SP500": r"S\s*&\s*P\s*500|标普(?:500)?",
}
_MARKET_TOKEN = re.compile(
    "|".join(f"(?P<{key}>{pattern})" for key, pattern in _MARKET_NAMES.items()), re.I
)
_NEWS_LOSS = re.compile(
    r"(?:下跌|大跌|急跌|跌幅|跌|下挫|重挫|fell|fall(?:s|en)?|drop(?:ped|s)?|declin(?:ed|es?))\D{0,10}?(\d+(?:\.\d+)?)\s*[%％]|[-−]\s*(\d+(?:\.\d+)?)\s*[%％]",
    re.I,
)


def _fund_markets(row, product):
    known = {"161129": {"OIL"}, "162411": {"OIL"}, "270042": {"NASDAQ"}}
    if row.get("fundCode") in known:
        return known[row["fundCode"]]
    text = str(row.get("label") or product.get("label") or "")
    markets = {
        key
        for key, pattern in _MARKET_NAMES.items()
        if re.search(
            r"S\s*&\s*P\s*500|标普\s*500" if key == "SP500" else pattern, text, re.I
        )
    }
    return markets


def _current_market_losses(packet, now):
    """Only matching, dated observed losses; retrieval cannot refresh old news."""
    today, losses = now.astimezone(_TZ).date(), {}
    codes = {
        "US_NASDAQ_CHANGE_PCT": "NASDAQ",
        "US_SP500_CHANGE_PCT": "SP500",
        "WTI_CHANGE_PCT": "OIL",
        "BRENT_CHANGE_PCT": "OIL",
        "DCOILWTICO": "OIL",
        "DCOILBRENTEU": "OIL",
    }
    for row in [
        *packet.get("series", []),
        *packet.get("energy", []),
        *(packet.get("alphaPublic") or {}).get("observations", []),
    ]:
        if not isinstance(row, dict) or not _visible(row, now):
            continue
        metadata = row.get("definition") or row.get("metadata") or {}
        if row.get("state") not in {
            None,
            "READY",
            "OBSERVED",
            "VERIFIED",
        } or metadata.get("qualityStatus") not in {None, "FRESH"}:
            continue
        day = _day(row.get("asOf") or row.get("referenceDate"))
        code = row.get("code")
        if day != today:
            retrieved = _stamp(row.get("retrievedAt"))
            source_time = metadata.get("sourceAsOf")
            source_stamp = _stamp(source_time)
            source_day = (
                source_stamp.astimezone(ZoneInfo("America/New_York")).date()
                if source_stamp
                else _day(source_time)
            )
            if not (
                code in {"US_NASDAQ_CHANGE_PCT", "US_SP500_CHANGE_PCT"}
                and day == today - timedelta(days=1)
                and retrieved is not None
                and retrieved.astimezone(_TZ).date() == today
                and metadata.get("qualityStatus") == "FRESH"
                and source_day == day
                and (source_stamp is None or source_stamp <= retrieved)
            ):
                continue
        change = (
            _finite(row.get("value"))
            if str(code).endswith("_CHANGE_PCT")
            else _finite(row.get("changePct"))
        )
        if code in codes and change is not None and change <= -2:
            market = codes[code]
            losses[market] = max(losses.get(market, day), day)
    for row in [
        *packet.get("documents", []),
        *(packet.get("alphaPublic") or {}).get("catalysts", []),
    ]:
        if not isinstance(row, dict) or not _visible(row, now):
            continue
        stamp = _stamp(row.get("publishedAt"))
        day = (
            stamp.astimezone(_TZ).date()
            if stamp
            else _day(row.get("publishedDate") or row.get("asOf"))
        )
        if day != today:
            continue
        text = "。".join(
            str(row.get(key) or "") for key in ("title", "quote", "content")
        )
        for clause in re.split(r"[。；;，,！？!?\n]|而|\bwhile\b", text, flags=re.I):
            if re.search(
                r"可能|预计|若|假如|假设|如果|一旦|将|未(?:曾)?|没有|并非|此前|昨日|上周|不足|不到|could|may|forecast|expect|\bif\b",
                clause,
                re.I,
            ):
                continue
            tokens = list(_MARKET_TOKEN.finditer(clause))
            for i, token in enumerate(tokens):
                segment = clause[
                    token.end() : tokens[i + 1].start()
                    if i + 1 < len(tokens)
                    else len(clause)
                ]
                if any(
                    float(match.group(1) or match.group(2)) >= 2
                    for match in _NEWS_LOSS.finditer(segment)
                ):
                    losses[token.lastgroup] = today
    return losses


def apply_entry_guards(value, packet, now):
    """Overlay deterministic risk facts on copied report/model reading surfaces."""
    from backend.investment.subscription_status import normalize_subscription_status

    result = deepcopy(value)
    products = {
        row.get("fundCode"): row
        for row in packet.get("discoveryUniverse", [])
        if isinstance(row, dict)
    }
    subscriptions = packet.get("fundSubscriptionStatus") or {}
    losses = _current_market_losses(packet, now)
    today = now.astimezone(_TZ).date()

    def guard(row):
        if not isinstance(row, dict):
            return row
        code = row.get("fundCode") or (
            row.get("subjectId") if row.get("subjectKind") == "nav" else None
        )
        product = products.get(code, {})
        subscription = normalize_subscription_status(
            subscriptions.get(code)
            or row.get("subscriptionStatus")
            or product.get("subscriptionStatus")
        )
        if subscription is not None:
            row.update(
                subscriptionStatus=subscription,
                subscriptionRestricted=subscription["restricted"],
                subscriptionLabel=subscription["label"],
                preferredEntryEligible=not subscription["restricted"],
            )
        overseas = (
            row.get("overseasCalendarRequired") is True
            or product.get("overseasCalendarRequired") is True
            or "QDII" in str(row.get("label") or "").upper()
            or code in {"161129", "162411", "270042"}
        )
        nav_day = _day(row.get("asOf"))
        delayed = row.get("isDelayedNav") is True or (
            row.get("isDelayedNav") is not False
            and overseas
            and row.get("priceBasis") == "UNIT_NAV"
            and nav_day is not None
            and nav_day < today
        )
        if overseas:
            row["isDelayedNav"] = delayed
        indicators = row.get("indicators") or {}
        percentile, ma20, current = (
            _finite(indicators.get("navPercentile1y")),
            _finite(indicators.get("ma20")),
            _finite(row.get("currentPrice")),
        )
        warning = (
            ("SUBSCRIPTION_RESTRICTED", subscription["label"])
            if subscription is not None and subscription["restricted"]
            else None
        )
        if (
            percentile is not None
            and percentile >= 85
            and ma20 is not None
            and ma20 > 0
            and current is not None
            and current > ma20 * 1.02
        ):
            warning = (
                "HIGH_NAV_PERCENTILE",
                f"⚠️ 处于近1年高位区({percentile:.0f}%分位)·勿追高",
            )
        if (
            delayed
            and nav_day is not None
            and any(
                losses.get(market, nav_day) > nav_day
                for market in _fund_markets({**row, "fundCode": code}, product)
            )
        ):
            warning = ("DELAYED_NAV_MARKET_DROP", "⚠️ 净值滞后1天·当晚外盘急跌暂缓入场")
        selected = row.get("selectedTrack") or row.get("track")
        for track in row.get("tracks", []):
            matching = [
                signal
                for signal in row.get("signals", [])
                if signal.get("track") == track.get("track")
            ]
            for signal in matching:
                signal.update(
                    _pick(
                        track,
                        (
                            "technicalTriggered",
                            "inBuyZone",
                            "entryReady",
                            "entryBlocked",
                            "entryBlockReason",
                            "entryStatus",
                            "entryStatusTone",
                            "inSellZone",
                        ),
                    )
                )
            if selected == track.get("track"):
                row.update(
                    _pick(
                        track,
                        (
                            "technicalTriggered",
                            "inBuyZone",
                            "entryReady",
                            "entryBlocked",
                            "entryBlockReason",
                            "entryStatus",
                            "entryStatusTone",
                            "inSellZone",
                        ),
                    )
                )
        if not selected:
            sell_track = next(
                (
                    track
                    for track in row.get("tracks", [])
                    if track.get("inSellZone") is True
                ),
                None,
            )
            if sell_track is not None:
                row.update(
                    _pick(
                        sell_track,
                        ("inSellZone", "entryStatus", "entryStatusTone"),
                    )
                )
        sell_zone = row.get("inSellZone") is True
        if warning or sell_zone:
            fields = {
                "entryBlocked": True,
                "entryBlockReason": warning[0] if warning else "IN_SELL_ZONE",
                "entryReady": False,
                "inBuyZone": False,
                "preferredEntryEligible": False,
            }
            for target in [row, *row.get("tracks", []), *row.get("signals", [])]:
                target.update(fields)
                # A purchase veto does not erase the separate exit advice.
                if warning and target.get("inSellZone") is not True:
                    target.update(entryStatus=warning[1], entryStatusTone="amber")
        return row

    for field in (
        "discoveryActionableZones",
        "themeActionableZones",
        "actionableZones",
        "actionablePlans",
    ):
        for row in result.get(field, []):
            guard(row)
    for row in (result.get("quantitativeScan") or {}).get("items", []):
        guard(row)
    for direction in result.get("valueDirections", []):
        for row in [
            *direction.get("plans", []),
            *direction.get("quantitativeEvidence", []),
        ]:
            guard(row)
        if direction.get("signal") in {"LEFT_VALUE", "RIGHT_TREND"} and not any(
            not row.get("entryBlocked")
            and (row.get("selectedTrack") or row.get("track")) == direction["signal"]
            and any(
                track.get("track") == direction["signal"]
                and track.get("state") == "CANDIDATE"
                for track in row.get("tracks", [])
            )
            for row in direction["plans"]
        ):
            direction["signal"] = "WATCH"
    # Subscription and entry guards annotate a purchase constraint. They must
    # never erase the independent reference prices, exit advice or chart.
    return result


def _public_continuation(previous, now):
    """Retain public report facts and original questions, never private lifecycle."""
    history = (previous or {}).get("history") or []
    valid = []
    for row in history[:14]:
        stamp = _stamp(row.get("completedAt"))
        report = row.get("report")
        if (
            row.get("status") == "READY"
            and row.get("runId")
            and stamp
            and now - timedelta(days=14) <= stamp <= now
            and isinstance(report, dict)
            and _visible(report, now)
        ):
            valid.append((stamp, row, report))
    events, plans, seen, seen_plans = [], [], set(), set()
    for stamp, record, report in sorted(valid, key=lambda item: item[0], reverse=True):
        sources = {}
        for source in report.get("sources", [])[:MAX_REPORT_SOURCES]:
            if not isinstance(source, dict) or not _visible(source, now):
                continue
            try:
                uri = _url(source.get("url"))
            except ValueError:
                continue
            sources[source.get("id")] = {
                **_pick(
                    source,
                    (
                        "id",
                        "title",
                        "quote",
                        "publishedDate",
                        "publishedAt",
                        "retrievedAt",
                        "verification",
                        "contentHash",
                    ),
                ),
                "url": uri,
            }
        common = {
            "reportRunId": str(record["runId"]),
            "researchedAt": stamp.isoformat(),
            "methodVersion": str(report.get("methodVersion") or VERSION)[:80],
        }
        for event in report.get("events", [])[:12]:
            refs = _texts(event.get("sourceIds"))
            key = event.get("id")
            questions = _texts(event.get("watchFor"))
            unknowns = _texts(event.get("unknowns"))
            if (
                not event.get("id")
                or key in seen
                or not refs
                or any(ref not in sources for ref in refs)
                or event.get("status") in {"CLOSED", "ENDED", "INVALIDATED"}
                or stamp < now - timedelta(days=7)
                and not (questions or unknowns)
            ):
                continue
            seen.add(key)
            events.append(
                {
                    **common,
                    "eventId": str(event["id"])[:100],
                    **_pick(event, ("title", "summary", "phase", "importance")),
                    "affectedDirectionIds": _texts(
                        event.get("affectedDirectionIds"), 16, 80
                    ),
                    "channels": _texts(event.get("channels"), 6),
                    "scenarios": [
                        _pick(s, ("condition", "impact", "counter"))
                        for s in event.get("scenarios", [])[:3]
                        if isinstance(s, dict)
                    ],
                    "watchFor": questions,
                    "unknowns": unknowns,
                    "sources": [sources[ref] for ref in refs],
                    "reasoningType": "MODEL_CONDITIONAL",
                    "marketReaction": "UNVERIFIED",
                }
            )
        for plan in report.get("upcoming", [])[:12]:
            refs = _texts(plan.get("sourceIds"))
            at = _stamp(plan.get("scheduledAt"))
            day = _day(plan.get("scheduledDate"))
            scheduled = at.astimezone(_TZ).date() if at else day
            key = plan.get("id")
            if (
                not plan.get("id")
                or key in seen_plans
                or plan.get("phase") != "PLANNED"
                or not refs
                or any(ref not in sources for ref in refs)
                or scheduled is None
                or not now.astimezone(_TZ).date()
                <= scheduled
                <= (now + timedelta(days=7)).astimezone(_TZ).date()
                or at is not None
                and at <= now
            ):
                continue
            seen_plans.add(key)
            plans.append(
                {
                    **common,
                    "eventId": str(plan["id"])[:100],
                    **_pick(plan, ("title", "phase", "scheduledDate", "scheduledAt")),
                    "watchFor": _texts(plan.get("watchFor")),
                    "sources": [sources[ref] for ref in refs],
                }
            )
    return events[:12], plans[:12]


def _industry_history_rows(records, now):
    """Project only dated, successful server reports and their public risk facts."""
    output = []

    def append(row, stamp, run_id, sources):
        cycle = row.get("industryCycle")
        if not isinstance(cycle, dict):
            return
        keys = []
        if row.get("industry"):
            keys.append(("industry", str(row["industry"])[:100]))
        if row.get("key"):
            keys.append(("key", str(row["key"])[:64]))
        linked = _texts(row.get("reviewIndustryIds"), 16, 100)
        scoped_plans = [
            plan
            for plan in row.get("plans", [])
            if isinstance(plan, dict)
            and isinstance(plan.get("industryCycle"), dict)
            and isinstance(plan.get("discoverySector"), str)
            and plan["discoverySector"] in linked
        ]
        # A multi-industry summary is not an industry-specific observation.
        # Modern reports retain each plan's scoped cycle; old summaries without
        # that provenance remain available by direction key, never broadcast.
        if not scoped_plans and len(linked) <= 1:
            keys.extend(("industry", key) for key in linked)
        refs = set(cycle.get("sourceIds") or [])
        for field, identity in dict.fromkeys(keys):
            output.append(
                {
                    field: identity,
                    "researchedAt": stamp.isoformat(),
                    "reportRunId": str(run_id or "")[:100],
                    "industryCycle": _public_industry_cycle(cycle),
                    "sources": [
                        source for source in sources if source.get("id") in refs
                    ],
                }
            )
        for plan in scoped_plans:
            append(
                {
                    "industry": plan["discoverySector"],
                    "industryCycle": plan["industryCycle"],
                },
                stamp,
                run_id,
                sources,
            )

    for record in (records if isinstance(records, list) else [])[:128]:
        if not isinstance(record, dict) or record.get("status", "READY") != "READY":
            continue
        report = record.get("report")
        stamp = _stamp(
            record.get("completedAt")
            or record.get("researchedAt")
            or record.get("asOf")
        )
        if stamp is None or not now - timedelta(days=14) <= stamp <= now:
            continue
        if isinstance(report, dict):
            report_stamp = _stamp(report.get("asOf"))
            if (
                report_stamp is None
                or report_stamp > stamp
                or report_stamp < now - timedelta(days=14)
            ):
                continue
            sources = _public_industry_sources(report.get("sources"))
            for review in report.get("industryCycleReviews", [])[:64]:
                if isinstance(review, dict):
                    append(
                        {"industry": review.get("industry"), "industryCycle": review},
                        report_stamp,
                        record.get("runId"),
                        sources,
                    )
            for direction in report.get("valueDirections", [])[:MAX_VALUE_DIRECTIONS]:
                if isinstance(direction, dict):
                    append(direction, report_stamp, record.get("runId"), sources)
        else:
            append(
                record,
                stamp,
                record.get("reportRunId"),
                _public_industry_sources(record.get("sources")),
            )
    return output


def _without_recovered_earnings_risks(cycle):
    """A company recovery releases earnings doubts, never sector-wide facts."""
    from backend.macro.industry_cycle import EARNINGS_RISK_FLAGS, industry_risk_policy

    result = deepcopy(cycle)
    prior_policy = industry_risk_policy(cycle)
    for field in (
        "riskFlags",
        "confirmedRiskFlags",
        "suspectedRiskFlags",
        "persistedRiskFlags",
    ):
        result[field] = sorted(
            set(prior_policy.get(field, result.get(field)) or []) - EARNINGS_RISK_FLAGS
        )
    result["confirmedRiskAt"] = {
        flag: stamp
        for flag, stamp in (result.get("confirmedRiskAt") or {}).items()
        if flag not in EARNINGS_RISK_FLAGS
    }
    result["persistedEvidence"] = [
        row
        for row in result.get("persistedEvidence") or []
        if row.get("flag") not in EARNINGS_RISK_FLAGS
    ]
    if not result["persistedRiskFlags"]:
        result["riskFlags"] = [
            flag for flag in result["riskFlags"] if flag != "PERSISTED_RISK_WATCH"
        ]
    if result.get("stage") == "EARLY_COMMERCIALIZATION":
        result["stage"] = "UNKNOWN"
    result["forwardPlansOnly"] = False
    result["evidenceGaps"] = [
        gap
        for gap in result.get("evidenceGaps") or []
        if gap not in EARNINGS_RISK_FLAGS
        and gap != "CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING"
    ]
    # Recompute strength from the surviving flags; the old declaration may
    # describe only the earnings doubts just removed.
    result["riskLevel"] = "NONE"
    result.update(industry_risk_policy(result))
    return result


def _previous_industry_states(packet, now):
    from backend.macro.industry_cycle import industry_risk_policy

    rows = _industry_history_rows(
        [
            *packet.get("previousIndustryRiskStates", []),
            *packet.get("previousValueDirections", []),
            *packet.get("previousResearch", []),
        ],
        now,
    )
    recovered, chosen = {}, {}
    for row in rows:
        key = (
            ("industry", row["industry"]) if "industry" in row else ("key", row["key"])
        )
        if earnings_recovery_verified(row["industryCycle"]):
            stamp = _stamp(row["researchedAt"])
            recovered[key] = max(recovered.get(key, stamp), stamp)
    for row in sorted(
        rows, key=lambda item: _stamp(item["researchedAt"]), reverse=True
    ):
        key = (
            ("industry", row["industry"]) if "industry" in row else ("key", row["key"])
        )
        if key in recovered and _stamp(row["researchedAt"]) <= recovered[key]:
            row = {
                **row,
                "industryCycle": _without_recovered_earnings_risks(
                    row["industryCycle"]
                ),
            }
        if key in chosen:
            # Keep each older category's original clock: a newer observation
            # about a different risk must not replace the entire sector state.
            chosen[key] = {
                **chosen[key],
                "industryCycle": _persist_review_cycle(
                    chosen[key]["industryCycle"], row, now
                ),
            }
        elif industry_risk_policy(row["industryCycle"])["leftDiscountScale"] > 0:
            chosen[key] = row
    return chosen


def _persist_review_cycle(cycle, history, now, *, earnings_recovered=False):
    from backend.macro.industry_cycle import persist_industry_risk

    if history is None:
        return cycle
    previous = {**history["industryCycle"], "sources": history.get("sources", [])}
    result = persist_industry_risk(
        {
            **cycle,
            "earningsVerified": True,
            "earningsIndustryScopeVerified": True,
        }
        if earnings_recovered
        else cycle,
        previous,
        confirmed_at=history["researchedAt"],
        now=now,
    )
    if earnings_recovered:
        # The proof belongs to individual linked companies, not every company
        # in the sector review. Preserve the review's own verification state.
        result["earningsVerified"] = cycle.get("earningsVerified", False)
        if "earningsIndustryScopeVerified" in cycle:
            result["earningsIndustryScopeVerified"] = cycle[
                "earningsIndustryScopeVerified"
            ]
        else:
            result.pop("earningsIndustryScopeVerified", None)
    return result


def _nav_collection_results(rows, now):
    result = {}
    if not isinstance(rows, dict):
        return result
    for code, row in rows.items():
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"[0-9]{6}", code)
            or not isinstance(row, dict)
        ):
            continue
        item = {
            "status": row.get("status")
            if row.get("status") in {"SUCCESS", "FAILED", "UNAVAILABLE", "TIMEOUT"}
            else "UNKNOWN"
        }
        if type(row.get("records")) is int and 0 <= row["records"] <= 100000:
            item["records"] = row["records"]
        if row.get("cache") == "REUSED_CONFIRMED":
            item["cache"] = row["cache"]
        for field in ("reason", "errorClass"):
            if isinstance(row.get(field), str) and re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,79}", row[field]
            ):
                item[field] = row[field]
        retrieved = _stamp(row.get("retrievedAt"))
        if retrieved is not None and retrieved <= now:
            item["retrievedAt"] = retrieved.isoformat()
        result[code] = item
        if len(result) == 46:
            break
    return result


def _cycle_history(row, now, *, is_daily):
    points = {}
    for point in row.get("history") or []:
        if not isinstance(point, dict) or not _visible(point, now):
            continue
        day = _day(point.get("date") or point.get("asOf"))
        if day is None or day > now.date() or point.get("value") is None:
            continue
        points[day] = _pick(point, (
            "date", "asOf", "value", "unit", "source", "sourceUri", "sourceName",
            "state", "publishedAt", "retrievedAt", "availableAt", "definitionKey", "definitionVersion",
        ))
    periods = {}
    for day, point in sorted(points.items()):
        # Freeze the last observed day of every month for long charts. The
        # live API and the already-calculated five-observation moves stay daily.
        key = (day.year, day.month) if is_daily else day
        periods[key] = point
    return list(periods.values())[-36:]


def make_research_packet(
    context, series, evidence, directions, *, now, previous=None, external=None
):
    """Take only bounded public fields; no actor, holdings, secrets or raw history."""
    from backend.integrations.daily_sources import macro_cycle_research_tasks
    from backend.macro.cycle_insights import rate_expectation

    now = _now(now)
    macros, schedules, public_series, indices, actionable = [], [], [], [], []
    for row in context.get("items", [])[:8]:
        if row.get("code") not in _MACROS or not _visible(row, now):
            continue
        item = {
            **_pick(
                row,
                (
                    "code",
                    "label",
                    "state",
                    "value",
                    "unit",
                    "referencePeriod",
                    "asOf",
                    "periodChange",
                    "valueVerification",
                    "publicationStatus",
                ),
            ),
            **_provenance(row),
        }
        item["statistics"] = _pick(
            row.get("statistics") or {},
            (
                "previousValue",
                "change3Months",
                "change6Months",
                "percentile",
                "median",
                "sampleCount",
                "firstPeriod",
                "lastPeriod",
            ),
        )
        if row.get("code") in {"PMI", "CPI_YOY", "PPI_YOY", "M2_YOY"}:
            history = _cycle_history(row, now, is_daily=False)
            if history:
                item["history"] = history
        macros.append(item)
        schedule = row.get("nextRelease") or {}
        if (
            schedule.get("status") in {"SCHEDULED", "OVERDUE"}
            and _visible(schedule, now, planned=True)
            and _stamp(schedule.get("scheduledAt"))
        ):
            try:
                uri = _url(schedule.get("sourceUri"))
            except ValueError:
                continue
            schedules.append(
                {
                    "code": row["code"],
                    **_pick(
                        schedule,
                        ("status", "referencePeriod", "scheduledAt", "retrievedAt"),
                    ),
                    "sourceUri": uri,
                }
            )
    from backend.macro.market_repository import CATALOG

    for row in series[:24]:
        if row.get("code") not in CATALOG or not _visible(row, now):
            continue
        item = {
            **_pick(
                row, ("code", "label", "state", "value", "unit", "frequency", "asOf")
            ),
            **_provenance(row),
        }
        item["changes"] = _pick(
            row.get("changes") or {},
            (
                "change5Samples",
                "periodChange",
                "change3Months",
                "change6Months",
                "changeUnit",
                "sampleCount",
                "percentile",
            ),
        )
        item["definition"] = _pick(
            row.get("metadata") or {},
            (
                "definitionKey",
                "definitionVersion",
                "accumulationStart",
                "accumulationEnd",
                "sourceAsOf",
                "qualityStatus",
            ),
        )
        if row["code"] in {"M1_YOY", "M2_YOY", "CN_T10", "US_T10_NOMINAL", "US_T10_REAL", "US_BRENT"}:
            history = _cycle_history(row, now, is_daily=row.get("frequency") == "DAILY")
            if history:
                item["history"] = history
        public_series.append(item)
    for row in evidence[:8]:
        if row.get("indexId") not in _INDICES or not _visible(row, now):
            continue
        anchor = _public_actionable_plan(
            row.get("actionableZones"), row["indexId"], now
        )
        if anchor:
            actionable.append(anchor)
        item = {
            **_pick(row, ("indexId", "label", "state", "asOf", "historyQuality")),
            **_provenance(row),
        }
        item["trend"] = _pick(
            row.get("trend") or {}, ("state", "ma200", "distanceMA200", "momentum63")
        )
        item["trendWindow"] = _pick(
            row.get("trendWindow") or {}, ("start", "end", "sampleCount")
        )
        item["risk"] = _pick(
            row.get("risk") or {}, ("state", "volatility20", "maxDrawdown", "summary")
        )
        valuation = row.get("valuation") or {}
        item["valuation"] = {
            "state": valuation.get("state", "UNKNOWN"),
            **{
                metric: _pick(
                    valuation.get(metric) or {},
                    (
                        "state",
                        "metric",
                        "latest",
                        "percentile",
                        "sampleCount",
                        "historyStart",
                        "historyEnd",
                        "method",
                        "sourceUri",
                    ),
                )
                for metric in ("pe", "pb")
            },
        }
        indices.append(item)
    documents, energy, candidate_index = [], [], []
    for row in (external or {}).get("documents", [])[:12]:
        if not _visible(row, now):
            continue
        try:
            uri = _url(row.get("url"))
        except ValueError:
            continue
        documents.append(
            {
                **_pick(
                    row,
                    (
                        "id",
                        "title",
                        "quote",
                        "publishedDate",
                        "publishedAt",
                        "retrievedAt",
                        "verification",
                        "contentHash",
                        "sourceKey",
                    ),
                ),
                "url": uri,
            }
        )
    for row in (external or {}).get("candidateIndex", [])[:40]:
        if not isinstance(row, dict) or not _visible(row, now):
            continue
        try:
            uri = _url(row.get("url"))
        except ValueError:
            continue
        candidate_index.append(
            {
                **_pick(
                    row,
                    (
                        "id",
                        "title",
                        "publishedDate",
                        "retrievedAt",
                        "contentHash",
                        "sourceKey",
                    ),
                ),
                "url": uri,
            }
        )
    for row in (external or {}).get("energy", [])[:4]:
        if row.get("code") in _ENERGY and _visible(row, now):
            energy.append(
                {
                    **_pick(
                        row,
                        (
                            "id",
                            "code",
                            "label",
                            "state",
                            "unit",
                            "frequency",
                            "value",
                            "asOf",
                            "previousValue",
                            "previousDate",
                            "change",
                            "changePct",
                            "sampleCount",
                        ),
                    ),
                    **_provenance(row),
                }
            )
    questions = []
    for event in ((previous or {}).get("report") or {}).get("events", [])[:12]:
        for text in (event.get("watchFor") or []) + (event.get("unknowns") or []):
            if isinstance(text, str) and text.strip() and text[:300] not in questions:
                questions.append(text[:300])
    previous_research, previous_upcoming = _public_continuation(previous, now)
    if "history" in (previous or {}):
        questions = _texts(
            [
                text
                for event in previous_research
                for text in event["watchFor"] + event["unknowns"]
            ],
            24,
        )
    source_results = {}
    for key, row in (external or {}).get("sourceResults", {}).items():
        if (
            isinstance(key, str)
            and re.fullmatch(r"[A-Za-z0-9:_-]{1,80}", key)
            and isinstance(row, dict)
        ):
            source_results[key] = {
                "status": row.get("status")
                if row.get("status")
                in {
                    "SUCCESS",
                    "PARTIAL",
                    "FAILED",
                    "UNAVAILABLE",
                    "PERMISSION_REQUIRED",
                    "SKIPPED",
                    "TIMEOUT",
                }
                else "UNKNOWN"
            }
            error = row.get("errorClass")
            source_results[key].update(
                _pick(
                    row,
                    (
                        "reason",
                        "totalEntries",
                        "catalogCount",
                        "catalogCountBefore",
                        "unknownFundTypeCount",
                        "lastSuccessAt",
                        "retrievedAt",
                        "completedAt",
                    ),
                )
            )
            if isinstance(error, str) and re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,79}", error
            ):
                source_results[key]["errorClass"] = error
    return {
        "methodVersion": VERSION,
        "asOf": now.isoformat(),
        "macros": macros,
        "series": public_series,
        "indices": indices,
        "actionableZones": actionable,
        "directions": [
            _pick(row, ("id", "label", "market_id", "marketId"))
            for row in directions[:64]
        ],
        "schedules": schedules,
        "documents": documents,
        "energy": energy,
        "previousQuestions": questions[:24],
        "previousResearch": previous_research,
        "previousIndustryRiskStates": list(
            _previous_industry_states(
                {"previousResearch": (previous or {}).get("history", [])}, now
            ).values()
        )[:64],
        "previousUpcoming": previous_upcoming,
        "candidateIndex": candidate_index,
        "sourceResults": source_results,
        "dynamicNavSourceResults": _nav_collection_results(
            (external or {}).get("dynamicNavSourceResults"), now
        ),
        "industryCycleResearch": {"required": True},
        "macroCycleResearch": macro_cycle_research_tasks(now),
        **({"rateExpectation": rate} if (rate := rate_expectation(public_series)) else {}),
    }


def build_prompt(packet):
    """One public request; source material is untrusted data, never instructions."""
    if packet.get("asOf") is not None:
        packet = apply_entry_guards(packet, packet, _now(packet["asOf"]))
    from backend.integrations.daily_sources import industry_cycle_research_tasks, macro_cycle_research_tasks

    industry_tasks = (
        industry_cycle_research_tasks(packet, _now(packet["asOf"]))
        if packet.get("asOf")
        else {"required": True, "newDirectionRequiresSameChecks": True}
    )
    packet = {
        **packet,
        "industryCycleResearch": industry_tasks,
        **({"macroCycleResearch": macro_cycle_research_tasks(_now(packet["asOf"]))} if packet.get("asOf") else {}),
    }
    public_packet = model_packet(packet, version="daily-model-input-v2")
    from backend.macro.research_topics import (
        TOPIC_MATCH_VERSION,
        STRICT_ALIASES,
        V2_STRICT_ALIASES,
        topic_match_version,
    )
    from backend.macro.industry_contract import STANDARD_INDUSTRY_CODES

    identity_version = (
        topic_match_version(packet) if is_v2(packet) else TOPIC_MATCH_VERSION
    )
    public_packet["topicIdentityRegistry"] = {
        "version": identity_version,
        "standardIndustryCodes": sorted(STANDARD_INDUSTRY_CODES),
        "strictAliases": V2_STRICT_ALIASES
        if identity_version == TOPIC_MATCH_VERSION
        else STRICT_ALIASES,
    }
    if scopes := _request_industry_scopes(packet):
        public_packet["industryCycleResearch"].update(scopes)
    for field in (
        "allocationVersion",
        "selectionReasons",
        "excludedScopes",
        "excludedScopeCount",
    ):
        if field in industry_tasks:
            public_packet["industryCycleResearch"][field] = deepcopy(
                industry_tasks[field]
            )
    from backend.macro.model_prompt import PROMPT_TEXT

    return PROMPT_TEXT + json.dumps(
        public_packet,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def validate_report(raw, packet, source_checks, *, completed_at, research_id=None):
    """Publish parsed research after local repair, preserving its healthy items."""
    from backend.macro.research_sanitize import sanitize_report

    return sanitize_report(
        raw, packet, source_checks, completed_at=completed_at, research_id=research_id
    )


def diagnose_report(raw, packet, source_checks, *, completed_at, research_id=None):
    """Legacy strict diagnostics only; never a publication or model constraint."""
    return _validate_report_core(raw, packet, source_checks,
        completed_at=completed_at, research_id=research_id)


def _validate_report_core(
    raw,
    packet,
    source_checks,
    *,
    completed_at,
    research_id=None,
    _require_candidate_coverage=True,
):
    """Validate shape/references/times; checking a quote cannot certify inference."""
    completed_at = _now(completed_at)
    if _now(packet.get("asOf")) > completed_at:
        raise ValueError("completion predates public input")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw, object_pairs_hook=_unique_object)
        except Exception as exc:
            raise ResearchValidationError(
                "SCHEMA_VALIDATION_FAILED", f"invalid JSON: {exc}"
            ) from exc
    if not isinstance(raw, dict):
        raise ResearchValidationError(
            "SCHEMA_VALIDATION_FAILED",
            "research report must be an object",
        )
    if isinstance(raw.get("valueDirections"), list) and len(raw["valueDirections"]) > direction_limit(packet):
        raise ResearchValidationError(
            "SCHEMA_VALIDATION_FAILED", "value directions exceed the frozen direction budget"
        )
    if "themeActionableZones" not in packet:
        # Compatibility for pre-Phase2 packets only, never a new model request.
        raw = {"macroCycle": "宏观周期证据待核查。", "actionablePlans": [], **raw}
    if "discoveryUniverse" not in packet:
        raw = {"valueDirections": [], **raw}
    malformed_plan_directions = set()
    if is_v2(packet):
        from backend.macro.value_directions import downgrade_invalid_plan_shapes

        raw, malformed_plan_directions = downgrade_invalid_plan_shapes(raw, packet)
    try:
        report = _Report.model_validate(raw).model_dump()
    except Exception as exc:
        raise ResearchValidationError(
            "SCHEMA_VALIDATION_FAILED",
            f"schema validation error: {exc}",
            item_index=_schema_item_index(exc),
        ) from exc
    if (packet.get("industryCycleResearch") or {}).get("required") and report[
        "actionablePlans"
    ]:
        raise ResearchValidationError(
            "FUND_PLAN_INVALID",
            "new industry-review reports require plans inside value directions",
        )
    _safe_narrative(report)
    report = readable_fund_codes(
        report,
        {row.get("fundCode") for row in packet.get("discoveryUniverse", [])},
        known_company_names(packet),
    )
    directions = {row["id"] for row in packet.get("directions", [])}
    sources, aliases, original = {}, {}, {}
    for row in report["sources"]:
        try:
            uri = _url(row["url"])
        except ValueError as exc:
            raise ResearchValidationError(
                "SOURCE_REFERENCE_INVALID", f"invalid source URL: {exc}"
            ) from exc
        if row["id"] in original and original[row["id"]] != uri:
            raise ResearchValidationError(
                "SOURCE_REFERENCE_INVALID", "ambiguous source identity"
            )
        original[row["id"]] = uri
        _validate_publication(row, completed_at)
        identity = _identity("source", uri)
        aliases[row["id"]] = identity
        checked = source_checks.get(row["id"]) or {}
        retrieved = _stamp(checked.get("retrievedAt"))
        try:
            matching_url = _url(checked.get("url")) == uri
        except ValueError:
            matching_url = False
        good = (
            checked.get("verification") == "SOURCE_CHECKED"
            and checked.get("quoteMatched") is True
            and matching_url
            and retrieved is not None
            and retrieved <= completed_at
            and (
                row["publishedDate"] is None
                or checked.get("publishedDate") == row["publishedDate"]
            )
            and re.fullmatch(r"[a-fA-F0-9]{64}", str(checked.get("contentHash") or ""))
            is not None
            and _visible(checked, completed_at)
        )
        proof = {
            **row,
            "id": identity,
            "url": uri,
            "title": str(checked.get("title") or row["title"])[:200]
            if good
            else row["title"],
            "publishedDate": checked.get("publishedDate")
            if good
            else row["publishedDate"],
            # Only an actually checked timestamp may become publication evidence.
            "publishedAt": checked.get("publishedAt") if good else None,
            "retrievedAt": retrieved.isoformat()
            if retrieved and retrieved <= completed_at and matching_url
            else None,
            "verification": "SOURCE_CHECKED"
            if good
            else "UNAVAILABLE"
            if checked.get("verification") == "UNAVAILABLE"
            else "UNVERIFIED",
            "contentHash": checked.get("contentHash") if good else None,
            **source_metadata(row, packet, completed_at),
        }
        if identity not in sources or proof["verification"] == "SOURCE_CHECKED":
            sources[identity] = proof
    from backend.integrations.daily_sources import industry_cycle_research_tasks

    input_at = _now(packet["asOf"])
    industry_tasks = industry_cycle_research_tasks(
        apply_entry_guards(packet, packet, input_at), input_at
    )
    if is_v2(packet):
        from backend.macro.research_topics import frozen_manifest
        allocated_scopes = set(frozen_manifest(packet)["selectedScopes"])
        if any(review["industry"] not in allocated_scopes for review in report["industryCycleReviews"]):
            raise ResearchValidationError("DIRECTION_VALIDATION_FAILED",
                "industry review has no frozen budget allocation", rule_id="INDUSTRY_REVIEW_BUDGET_EXCEEDED")
    if (packet.get("industryCycleResearch") or {}).get("required") and len(
        report["industryCycleReviews"]
    ) > industry_tasks["maximumCandidates"]:
        raise ResearchValidationError(
            "DIRECTION_VALIDATION_FAILED",
            "industry reviews exceed the fixed research budget",
            rule_id="INDUSTRY_REVIEW_BUDGET_EXCEEDED",
        )
    industry_reviews, reviewed = [], set()
    for item_index, review in enumerate(report["industryCycleReviews"]):
        if review["industry"] in reviewed:
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                "duplicate industry review",
                rule_id="INDUSTRY_REVIEW_DUPLICATE",
                item_index=item_index,
            )
        reviewed.add(review["industry"])
        try:
            cycle_review = normalize_industry_cycle(
                {
                    "stage": "UNKNOWN",
                    "evidenceSummary": "候选行业反方检索记录。",
                    "evidence": [],
                    "sourceIds": review["sourceIds"],
                    "riskChecks": review["riskChecks"],
                },
                source_aliases=aliases,
                sources=sources,
                require_checks=True,
                now=completed_at,
                expected_industries=[industry_tasks.get("scopeSearchTerms", {}).get(review["industry"], review["industry"])
                    if is_v2(packet) and review["industry"].startswith("TOPIC_SCOPE:") else review["industry"]],
            )
        except ValidationError as exc:
            raise ResearchValidationError(
                "SCHEMA_VALIDATION_FAILED", str(exc), item_index=item_index
            ) from exc
        except ValueError as exc:
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                str(exc),
                item_index=item_index,
                rule_id=exc.rule_id
                if isinstance(exc, IndustryCycleValidationError)
                else None,
            ) from exc
        industry_reviews.append({"industry": review["industry"], **cycle_review})
    historical_cycles = _previous_industry_states(packet, completed_at)
    audit_completed_scopes = None
    execution_scopes = None
    if is_v2(packet):
        from backend.macro.research_assurance import audit_assurance, verified_tasks
        from backend.macro.research_topics import frozen_manifest
        from backend.macro.industry_cycle import RISK_CHECKS

        assurance = audit_assurance(
            {row["industry"]: row for row in industry_reviews},
            frozen_manifest(packet), source_checks, completed_at,
            research_id=research_id,
        )
        audit_completed_scopes = {
            industry for industry, kinds in assurance["completedKinds"].items()
            if kinds == set(RISK_CHECKS)
        }
        manifest = frozen_manifest(packet)
        executed = verified_tasks(
            source_checks, manifest, completed_at, research_id=research_id
        )
        execution_scopes = {
            scope for scope in manifest["selectedScopes"]
            if all(
                task["taskId"] in executed
                for task in manifest["tasks"] if task["industryId"] == scope
            )
        }
        for review in industry_reviews:
            review["searchExecutionVerified"] = review["industry"] in execution_scopes
    value_directions = diagnose_value_directions(
        report["valueDirections"],
        packet,
        completed_at,
        aliases,
        sources=sources,
        industry_reviews=industry_reviews,
        historical_cycles=historical_cycles,
        audit_completed_scopes=audit_completed_scopes,
    )
    for direction in value_directions:
        if direction["key"] in malformed_plan_directions:
            direction.update(
                qualificationStatus="OBSERVATION_ONLY", investmentEligible=False
            )
            direction["formalEligibilityReasons"] = sorted(
                {
                    *direction.get("formalEligibilityReasons", []),
                    "FUND_PLAN_INVALID",
                }
            )
    if execution_scopes is not None:
        for direction in value_directions:
            linked = set(direction["reviewIndustryIds"])
            verified = bool(linked) and linked <= execution_scopes
            direction["industryCycle"]["searchExecutionVerified"] = verified
            for plan in direction["plans"]:
                if isinstance(plan.get("industryCycle"), dict):
                    plan["industryCycle"]["searchExecutionVerified"] = verified
    if _require_candidate_coverage and not is_v2(packet) and (packet.get("industryCycleResearch") or {}).get(
        "required"
    ):
        expected = {row["industry"] for row in industry_tasks["candidates"]}
        if expected - reviewed:
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                "unreviewed industry candidates",
                rule_id="INDUSTRY_REVIEW_COVERAGE_MISSING",
                missing_industry_codes=sorted(expected - reviewed),
            )
    from backend.macro.industry_cycle import normalize_plan_industry_cycle

    proposed = {row["key"]: row for row in report["valueDirections"]}
    recovered_industries = set()
    for direction in value_directions:
        if not earnings_recovery_verified(direction.get("industryCycle", {})):
            continue
        industries = direction.get("reviewIndustryIds", [])
        if len(industries) == 1:
            # Its already-normalized scope is exactly this one industry.
            recovered_industries.add(industries[0])
            continue
        for industry in industries:
            # Company-level recovery can release doubts only in the scope of
            # its checked facts, including a WATCH direction with no fund plan.
            scoped = normalize_plan_industry_cycle(
                proposed[direction["key"]].get("industryCycle"),
                expected_industries=[industry],
                source_aliases=aliases,
                sources=sources,
                now=completed_at,
            )
            if earnings_recovery_verified(scoped):
                recovered_industries.add(industry)
    industry_reviews = [
        {
            "industry": review["industry"],
            **_persist_review_cycle(
                review,
                historical_cycles.get(("industry", review["industry"])),
                completed_at,
                earnings_recovered=review["industry"] in recovered_industries,
            ),
        }
        for review in industry_reviews
    ]
    directions.update(row["key"] for row in value_directions)
    from backend.macro.research_event_links import (
        LINK_FIELDS,
        merge_reference_resolution,
        normalize_event_links,
    )

    formal_keys = [row["key"] for row in value_directions]
    events = {}
    for row in report["events"]:
        row = normalize_event_links(
            row, packet=packet, formal_keys=formal_keys, source_aliases=aliases,
            topic_catalog=report["topicCatalog"],
        )
        refs = _references(row, aliases, directions)
        identity = _identity(
            "event", "economic-observation-v2", " ".join(row["title"].split()),
            " ".join(row["summary"].split()), row["phase"]
        ) if is_v2(packet) else _identity(
            "event", sorted(sources[ref]["url"] for ref in refs), row["phase"]
        )
        item = {
            **row,
            "id": identity,
            "sourceIds": refs,
            "affectedDirectionIds": sorted(set(row["affectedDirectionIds"])),
            "factVerification": "UNVERIFIED",
            "reasoningType": "MODEL_CONDITIONAL",
            "marketReaction": "UNVERIFIED",
        }
        if identity in events:
            old = events[identity]
            if is_v2(packet):
                merged = _merge_economic_observation(old, item)
                if merged is None:
                    item["id"] = _identity("event", identity, row)
                    events[item["id"]] = item
                else:
                    events[identity] = merged
                continue
            for key in ("channels", "watchFor", "unknowns", "affectedDirectionIds", *LINK_FIELDS):
                old[key] = sorted(set(old[key] + item[key]))
            old["referenceResolution"] = merge_reference_resolution(
                old["referenceResolution"], item["referenceResolution"]
            )
        else:
            events[identity] = item
    upcoming = {}
    for row in report["upcoming"]:
        row = normalize_event_links(
            row, packet=packet, formal_keys=formal_keys, source_aliases=aliases,
            topic_catalog=report["topicCatalog"],
        )
        refs = _references(row, aliases, directions)
        stamp = _stamp(row["scheduledAt"]) if row["scheduledAt"] else None
        day = _day(row["scheduledDate"]) if row["scheduledDate"] else None
        if (
            row["scheduledAt"]
            and stamp is None
            or row["scheduledDate"]
            and (day is None or str(day) != row["scheduledDate"])
        ):
            raise ResearchValidationError(
                "SCHEDULE_DATE_INVALID", "invalid planned date"
            )
        if stamp and day and stamp.astimezone(_TZ).date() != day:
            raise ResearchValidationError(
                "SCHEDULE_DATE_INVALID", "conflicting planned date"
            )
        if (
            stamp
            and stamp <= completed_at
            or day
            and day < completed_at.astimezone(_TZ).date()
        ):
            raise ResearchValidationError(
                "SCHEDULE_DATE_INVALID", "past schedule is not upcoming"
            )
        identity = _identity(
            "planned", "economic-observation-v2", " ".join(row["title"].split()),
            row["scheduledAt"], row["scheduledDate"], sorted(row["watchFor"]),
        ) if is_v2(packet) else _identity(
            "planned",
            sorted(sources[ref]["url"] for ref in refs),
            row["scheduledAt"],
            row["scheduledDate"],
        )
        item = {**row, "id": identity, "sourceIds": refs}
        if old := upcoming.get(identity):
            if is_v2(packet):
                merged = _merge_economic_observation(old, item)
                if merged is None:
                    item["id"] = _identity("planned", identity, row)
                    upcoming[item["id"]] = item
                else:
                    upcoming[identity] = merged
                continue
            for key in ("affectedDirectionIds", *LINK_FIELDS):
                item[key] = sorted(set(old[key] + item[key]))
            item["referenceResolution"] = merge_reference_resolution(
                old["referenceResolution"], item["referenceResolution"]
            )
        upcoming[identity] = item
    for observation in [*events.values(), *upcoming.values()]:
        observation.update(event_metadata(
            observation, [sources[ref] for ref in observation["sourceIds"]], completed_at,
        ))
    energy = {
        row["code"]: row
        for row in packet.get("energy", [])
        if _visible(row, completed_at)
    }
    if any(code not in energy for code in report["energy"]):
        raise ResearchValidationError(
            "DIRECTION_VALIDATION_FAILED", "unknown energy observation"
        )
    legacy_plans = validate_actionable_plans(
        report["actionablePlans"], packet, completed_at
    )
    from backend.investment.quantitative_scan import public_quantitative_scan

    scan = public_quantitative_scan(packet.get("quantitativeScan"), completed_at)
    from backend.investment.opportunity_scoring import rank_report
    from backend.macro.research_mentions import extract_mentioned_funds

    return rank_report(
        apply_entry_guards(
            {
                **report,
                **({"quantitativeScan": scan} if scan is not None else {}),
                "methodVersion": VERSION,
                "asOf": completed_at.isoformat(),
                "sources": sorted(sources.values(), key=lambda r: r["id"]),
                "events": sorted(events.values(), key=lambda r: r["id"]),
                "upcoming": sorted(upcoming.values(), key=lambda r: r["id"]),
                "energy": [energy[code] for code in dict.fromkeys(report["energy"])],
                "valueDirections": value_directions,
                "mentionedFunds": extract_mentioned_funds(report, packet),
                "industryCycleReviews": industry_reviews,
                "industryResearchCoverage": {
                    "maximumCandidates": industry_tasks["maximumCandidates"],
                    "candidateCount": len(industry_tasks["candidates"]),
                    "omittedCandidateCount": industry_tasks["omittedCandidateCount"],
                    "omittedRiskCandidateCount": industry_tasks[
                        "omittedRiskCandidateCount"
                    ],
                    "omittedIndustries": industry_tasks["omittedIndustries"],
                    "omittedPlanIndustryIds": industry_tasks["omittedPlanIndustryIds"],
                    "status": "PARTIAL"
                    if industry_tasks["omittedCandidateCount"]
                    or (
                        industry_tasks.get("allocationVersion")
                        in {"industry-allocation-v2", "industry-allocation-v3"}
                        and (
                            industry_tasks["omittedRiskCandidateCount"]
                            or industry_tasks.get("excludedScopeCount", 0)
                        )
                    )
                    else "COMPLETE",
                },
                "actionablePlans": [
                    plan
                    for direction in value_directions
                    for plan in direction["plans"]
                ]
                + legacy_plans,
            },
            packet,
            completed_at,
        ),
        packet,
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ResearchValidationError(
                "SCHEMA_VALIDATION_FAILED", "duplicate JSON field"
            )
        result[key] = value
    return result


# Reject executable markup; ordinary forecasts and trade language are allowed.
_UNSAFE_MARKUP = re.compile(
    r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s[^<>]*|\s*/?)>"
    r"|<!--[\s\S]*?-->|<![A-Za-z][^<>]*>|<\?[^<>]*>|OFFICIAL_FACT",
    re.IGNORECASE,
)


def _safe_narrative(value):
    if isinstance(value, dict):
        for child in value.values():
            _safe_narrative(child)
    elif isinstance(value, list):
        for child in value:
            _safe_narrative(child)
    elif isinstance(value, str):
        if len(value) > 2000 or _UNSAFE_MARKUP.search(value):
            raise ResearchValidationError(
                "DIRECTION_VALIDATION_FAILED",
                "unsupported research instruction or claim",
            )


def _validate_publication(row, now):
    if not _visible(row, now):
        raise ResearchValidationError(
            "SOURCE_DATE_INVALID", "future or invalid source publication"
        )
    day = row.get("publishedDate")
    if day and (_day(day) is None or str(_day(day)) != day):
        raise ResearchValidationError("SOURCE_DATE_INVALID", "invalid publication date")


def _identity(kind, *values):
    return (
        kind
        + ":"
        + hashlib.sha256(
            json.dumps(values, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:24]
    )


def _merge_economic_observation(old, item):
    """Merge exact facts only when every detail fits the original safe bounds.

    Different summaries are distinct observations, even under one headline.
    Overfull unions stay separate rather than truncating sources or risks.
    """
    from backend.macro.research_event_links import LINK_FIELDS, merge_reference_resolution

    merged = dict(old)
    for key, limit in {
        "sourceIds": 8, "channels": 6, "watchFor": 8, "unknowns": 8,
        "affectedDirectionIds": 16, **dict.fromkeys(LINK_FIELDS, 16),
        "scenarios": 3,
    }.items():
        if key not in old and key not in item:
            continue
        values = list(old.get(key, []))
        values.extend(value for value in item.get(key, []) if value not in values)
        if len(values) > limit:
            return None
        merged[key] = values
    rank = {None: 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    merged["importance"] = max((old.get("importance"), item.get("importance")), key=rank.get)
    merged["referenceResolution"] = merge_reference_resolution(
        old.get("referenceResolution"), item.get("referenceResolution"),
    )
    return merged


def _references(row, aliases, directions):
    if any(key not in aliases for key in row["sourceIds"]):
        raise ResearchValidationError(
            "SOURCE_REFERENCE_INVALID", "unknown research source reference"
        )
    if any(key not in directions for key in row["affectedDirectionIds"]):
        raise ResearchValidationError(
            "DIRECTION_VALIDATION_FAILED", "unknown research direction reference"
        )
    return sorted({aliases[key] for key in row["sourceIds"]})


def _finite(value):
    try:
        number = float(value)
        return number if not isinstance(value, bool) and math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None
