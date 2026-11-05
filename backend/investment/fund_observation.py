"""Read-only fund observations from frozen server prices, never trade authority."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, timedelta
from itertools import islice
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from backend.investment.quantitative_scan import public_quantitative_scan
from backend.investment.subscription_status import normalize_subscription_status
from backend.macro.daily_research import _public_actionable_plan, _stamp, _url, _visible
from backend.macro.industry_contract import STANDARD_INDUSTRY_CODES
from backend.macro.industry_cycle import RISK_CHECKS, _industry_scope_verified
from backend.macro.research_acceptance_repository import _cycle_projection
from backend.macro.research_topics import frozen_manifest

VERSION = "fund-observation-v1"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TRACKS = ("LEFT_VALUE", "RIGHT_TREND")
_METRICS = (
    "rsi14",
    "ma20",
    "ma60",
    "ma200",
    "excessReturn20d",
    "excessReturn60d",
    "drawdown60d",
)
_PRICE_FIELDS = (
    "entryZoneMin",
    "entryZoneMax",
    "stopLossPrice",
    "takeProfitT1",
    "takeProfitT2",
)
_UNITS = {"CNY", "CNY/份", "元/份", "CNY/unit"}


class FundObservationDataError(ValueError):
    """Frozen input cannot support a fund observation; not a system failure."""


def _bounded(values, maximum):
    rows = list(islice(iter(values or ()), maximum + 1))
    if len(rows) > maximum:
        raise FundObservationDataError(
            "fund observation input exceeds its frozen inventory budget"
        )
    return rows


def _text(value, limit=200):
    return value[:limit] if isinstance(value, str) else None


def _ids(values):
    return (
        list(
            dict.fromkeys(
                value
                for value in values
                if isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9:_-]{1,80}", value)
            )
        )
        if isinstance(values, list)
        else []
    )


def _uri(value):
    try:
        return _url(value)
    except (ValueError, TypeError):
        return None


def _identity(row):
    code, kind, subject = (
        row.get(key) for key in ("fundCode", "subjectKind", "subjectId")
    )
    if (
        not isinstance(code, str)
        or not re.fullmatch(r"[0-9]{6}", code)
        or row.get("market") != "FUND_CN"
    ):
        return None
    if kind == "nav" and subject == code:
        return kind, subject
    if kind == "fund":
        try:
            return kind, str(UUID(subject))
        except (ValueError, TypeError, AttributeError):
            return None
    return None


def _scan_collections_valid(row):
    return isinstance(row, dict) and all(
        isinstance(row.get(field, []), list)
        and all(isinstance(item, dict) for item in row.get(field, []))
        for field in ("tracks", "signals")
    )


def _nav(row, identity, validated_at):
    """Validate NAV independently when a reference-price track is unavailable."""
    if row is None or identity is None or _identity(row) != identity:
        return None
    plan = _public_actionable_plan(
        row, row["subjectId"], validated_at, subject_kind=row["subjectKind"]
    )
    if (
        not plan
        or plan.get("historyQuality") != "READY"
        or plan.get("priceBasis") != "UNIT_NAV"
        or plan.get("unit") not in _UNITS
    ):
        return None
    from backend.macro.alpha_research import _price

    retrieved = _stamp(row.get("retrievedAt"))
    source = _uri(plan.get("sourceUri"))
    try:
        _price(plan.get("currentPrice"))
        nav_day = date.fromisoformat(plan["asOf"])
    except (ValueError, KeyError, TypeError):
        return None
    if (
        not source
        or retrieved is None
        or retrieved > validated_at
        or nav_day > validated_at.astimezone(_SHANGHAI).date()
    ):
        return None
    if (
        type(plan.get("historySampleCount")) is not int
        or plan["historySampleCount"] <= 0
    ):
        return None
    return {
        "value": plan["currentPrice"],
        "asOf": plan["asOf"],
        "retrievedAt": retrieved.isoformat(),
        "sourceUri": source,
    }


def _stale(nav, now):
    return nav is not None and (
        now.astimezone(_SHANGHAI).date() - date.fromisoformat(nav["asOf"])
        > timedelta(days=10)
        or now - _stamp(nav["retrievedAt"]) > timedelta(hours=36)
    )


def _risk(cycle, industry, researched_at):
    result = _cycle_projection(cycle)
    found = [
        row.get("kind")
        for row in cycle.get("riskChecks", [])
        if isinstance(row, dict) and row.get("status") == "FOUND"
    ]
    if found:
        result["riskFlags"] = sorted(
            set(result.get("riskFlags", [])) | (set(found) & set(RISK_CHECKS))
        )
        result.setdefault("riskLevel", "SUSPECTED")
    risk_at = cycle.get("confirmedRiskAt")
    known = set().union(
        *(
            set(result.get(field, []))
            for field in (
                "riskFlags",
                "confirmedRiskFlags",
                "suspectedRiskFlags",
                "persistedRiskFlags",
            )
        )
    )
    result["confirmedRiskAt"] = {
        flag: stamp.isoformat()
        for flag, value in (risk_at.items() if isinstance(risk_at, dict) else [])
        if flag in known
        and (stamp := _stamp(value)) is not None
        and stamp <= _stamp(researched_at)
    }
    if (
        confirmed := _stamp(cycle.get("confirmedAt"))
    ) is not None and confirmed <= _stamp(researched_at):
        result["confirmedAt"] = confirmed.isoformat()
    return {**result, "industry": industry, "researchedAt": researched_at}


def _has_risk(cycle):
    return bool(
        cycle.get("riskLevel") in {"UNPROVEN", "SUSPECTED", "CONFIRMED"}
        or any(
            cycle.get(key)
            for key in (
                "riskFlags",
                "confirmedRiskFlags",
                "suspectedRiskFlags",
                "persistedRiskFlags",
            )
        )
        or any(
            isinstance(row, dict) and row.get("status") == "FOUND"
            for row in cycle.get("riskChecks", [])
        )
    )


def _audit(industries, selected, reviews, coverage):
    missing = set(RISK_CHECKS)
    if not industries or not set(industries) <= selected:
        return "NOT_ALLOCATED", sorted(missing)
    if coverage.get("searchExecutionVerified") is not True:
        return "PENDING_PROOF", sorted(missing)
    missing = set()
    for industry in industries:
        candidates = reviews.get(industry, [])
        checks = candidates[0].get("riskChecks", []) if len(candidates) == 1 else []
        counts = Counter(row.get("kind") for row in checks if isinstance(row, dict))
        for kind in RISK_CHECKS:
            row = next(
                (
                    row
                    for row in checks
                    if isinstance(row, dict) and row.get("kind") == kind
                ),
                {},
            )
            proved = counts[kind] == 1 and (
                row.get("status") == "NOT_FOUND"
                and row.get("searchExecutionVerified") is not False
                or row.get("status") == "FOUND"
                and row.get("verification") == "SOURCE_CHECKED"
                and row.get("claimSupported") is True
                and row.get("industryScopeVerified") is True
                and row.get("eventTimeVerification") == "RECENT"
                and bool(_ids(row.get("sourceIds")))
            )
            if not proved or len(checks) != 5 or set(counts) != set(RISK_CHECKS):
                missing.add(kind)
    return ("PENDING_PROOF" if missing else "STRUCTURE_AND_EXECUTION_VERIFIED"), sorted(
        missing
    )


def _links(industries, topics, sources, validated_at):
    output = []
    source_counts = Counter(
        source.get("id") for source in sources if isinstance(source.get("id"), str)
    )
    for topic in topics:
        match = topic.get("match") if isinstance(topic, dict) else None
        if not isinstance(match, dict):
            continue
        own = set(industries) & set(_ids(match.get("industryIds")))
        related = set(industries) & set(_ids(match.get("relatedIndustryIds")))
        if not own and not related:
            continue
        key = topic.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key):
            continue
        refs = _ids(topic.get("sourceIds"))
        checked = [
            source
            for source in sources
            if source.get("id") in refs
            and source_counts[source["id"]] == 1
            and source.get("verification") == "SOURCE_CHECKED"
            and _uri(source.get("url")) is not None
            and _stamp(source.get("retrievedAt")) is not None
            and _visible(source, validated_at)
        ]
        confirmed = (
            bool(own)
            and topic.get("status") in {"FORMAL", "OBSERVATION_ONLY", "PENDING_REVIEW"}
            and match.get("scopeVerified") is True
            and all(
                any(_industry_scope_verified(source, [industry]) for source in checked)
                for industry in own
            )
        )
        topic_id = topic.get("topicId")
        output.append(
            {
                "topicId": topic_id
                if isinstance(topic_id, str)
                and re.fullmatch(r"topic:[a-f0-9]{32}", topic_id)
                else None,
                "key": key,
                "title": _text(topic.get("title")) or key,
                "relation": "CONFIRMED_SCOPE_LINK"
                if confirmed
                else "DECLARED_RELATED_ONLY",
                "sourceIds": refs,
            }
        )
    return output


def _reference_tracks(raw, clean, stale, nav_plan=None):
    output = []
    for track_name in _TRACKS:
        originals = [
            row
            for row in (raw or {}).get("tracks", [])
            if isinstance(row, dict) and row.get("track") == track_name
        ]
        original = originals[0] if len(originals) == 1 else {}
        tracks = [
            row
            for row in (clean or {}).get("tracks", [])
            if row.get("track") == track_name
        ]
        signals = [
            row
            for row in (clean or {}).get("signals", [])
            if row.get("track") == track_name
        ]
        usable = len(tracks) == len(signals) == 1
        track, signal = (tracks[0], signals[0]) if usable else ({}, {})
        warning_tracks = [
            row
            for row in (clean or nav_plan or {}).get("tracks", [])
            if row.get("track") == track_name
        ]
        warning = warning_tracks[0] if len(warning_tracks) == 1 else {}
        technical = (
            original.get("state")
            if original.get("state") in {"CANDIDATE", "WATCH", "INSUFFICIENT"}
            else "INSUFFICIENT"
        )
        status = (
            "UNAVAILABLE"
            if not usable
            else "STALE"
            if stale
            else "TECHNICAL_IN_ZONE"
            if technical == "CANDIDATE" and signal["inEntryZone"]
            else "WATCH"
            if technical == "WATCH"
            else "AWAY"
        )
        output.append(
            {
                "track": track_name,
                "priceState": _text(original.get("priceState"), 40) or "UNAVAILABLE",
                "technicalState": technical,
                **{key: track.get(key) if usable else None for key in _PRICE_FIELDS},
                "distanceToEntryPct": signal.get("distanceToEntryPct")
                if usable
                else None,
                "inEntryZone": signal.get("inEntryZone") if usable else None,
                "metrics": {
                    key: deepcopy((clean or {}).get("indicators", {}).get(key))
                    for key in _METRICS
                },
                "observationStatus": status,
                "missingEvidence": [] if usable else ["REFERENCE_TRACK_UNAVAILABLE"],
                "entryBlocked": warning.get("entryBlocked")
                if type(warning.get("entryBlocked")) is bool
                else None,
                "entryBlockReason": _text(warning.get("entryBlockReason"), 100),
                "entryStatus": _text(warning.get("entryStatus"), 300),
                "inSellZone": warning.get("inSellZone")
                if type(warning.get("inSellZone")) is bool
                else None,
                "entryStatusTone": _text(warning.get("entryStatusTone"), 20),
            }
        )
    return output


def build_fund_observation(
    packet,
    now,
    *,
    research_id,
    validated_at,
    topics=(),
    industry_reviews=(),
    sources=(),
    coverage=None,
    formal_report=None,
):
    """Project frozen public inputs only; neither themes nor technicals grant a buy."""
    try:
        identity = str(UUID(str(research_id)))
    except ValueError as error:
        raise FundObservationDataError(
            "invalid fund observation research identity"
        ) from error
    if not isinstance(packet, dict):
        raise FundObservationDataError("invalid frozen fund observation packet")
    stamp, evaluated, input_at = (
        _stamp(validated_at),
        _stamp(now),
        _stamp(packet.get("asOf")),
    )
    if (
        stamp is None
        or evaluated is None
        or input_at is None
        or not input_at <= stamp <= evaluated
    ):
        raise FundObservationDataError("invalid fund observation evidence clock")
    catalog = _bounded(packet.get("discoveryUniverse", []), 46)
    scan = packet.get("quantitativeScan")
    scan = scan if isinstance(scan, dict) else {}
    scan_rows = _bounded(scan.get("items", []), 46)
    scan_at = _stamp(scan.get("asOf"))
    scan_clock_valid = scan_at is not None and scan_at <= stamp
    topics = _bounded(topics, 72)
    sources = [row for row in _bounded(sources, 24) if isinstance(row, dict)]
    reviews = defaultdict(list)
    unresolved = set()
    for row in _bounded(industry_reviews, 64):
        if isinstance(row, dict):
            industry = row.get("industry")
            if isinstance(industry, str):
                reviews[industry].append(row)
            if industry not in STANDARD_INDUSTRY_CODES and _has_risk(row):
                unresolved.add(
                    industry
                    if isinstance(industry, str)
                    and re.fullmatch(
                        r"(?:[A-Z][A-Z0-9_]{0,79}|TOPIC_SCOPE:[a-f0-9]{32})", industry
                    )
                    else "UNRESOLVED_SCOPE"
                )
    try:
        manifest = frozen_manifest(packet) if "researchDiscovery" in packet else {}
    except ValueError as error:
        # This parser's ValueErrors describe invalid or unsupported frozen
        # contracts. Other projection failures remain visible to the caller.
        raise FundObservationDataError(
            "invalid frozen fund observation contract"
        ) from error
    selected = set(manifest.get("selectedScopes", []))
    raw_coverage = coverage if isinstance(coverage, dict) else {}
    products, scanned = defaultdict(list), defaultdict(list)
    subjects = defaultdict(set)
    for row in catalog:
        if not isinstance(row, dict) or not re.fullmatch(
            r"[0-9]{6}", str(row.get("fundCode", ""))
        ):
            raise FundObservationDataError("invalid frozen fund inventory")
        products[row["fundCode"]].append(row)
        if _identity(row):
            subjects[_identity(row)].add(row["fundCode"])
    for row in scan_rows:
        if isinstance(row, dict) and isinstance(row.get("fundCode"), str):
            scanned[row["fundCode"]].append(row)
    history = []
    for row in _bounded(packet.get("previousIndustryRiskStates", []), 128):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("industryCycle"), dict)
            or not _has_risk(row["industryCycle"])
        ):
            continue
        industry, researched = row.get("industry"), _stamp(row.get("researchedAt"))
        if (
            industry not in STANDARD_INDUSTRY_CODES
            or researched is None
            or researched > stamp
        ):
            unresolved.add(
                industry
                if isinstance(industry, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", industry)
                else "UNRESOLVED_SCOPE"
            )
            continue
        projected = _risk(row["industryCycle"], industry, researched.isoformat())
        original_clocks = [
            _stamp(value) for value in projected["confirmedRiskAt"].values()
        ]
        if not original_clocks and projected.get("confirmedAt"):
            original_clocks = [_stamp(projected["confirmedAt"])]
        history.append(
            {
                **projected,
                "active": any(
                    evaluated - value <= timedelta(days=14)
                    for value in (original_clocks or [researched])
                ),
            }
        )
    output = []
    for code, matches in products.items():
        product = matches[0]
        coordinate = _identity(product)
        row = scanned[code][0] if len(scanned[code]) == 1 else None
        price_row = row if _scan_collections_valid(row) else None
        verified_at = (
            _stamp(product.get("identityVerifiedAt"))
            if product.get("identityVerifiedAt") is not None
            else None
        )
        matched = bool(
            coordinate
            and len(matches) == 1
            and len(subjects[coordinate]) == 1
            and (not scanned[code] or row is not None and _identity(row) == coordinate)
            and (
                product.get("identityVerifiedAt") is None
                or verified_at is not None
                and verified_at <= stamp
            )
        )
        clean = None
        if matched and price_row and scan_clock_valid:
            try:
                projected = public_quantitative_scan(
                    {**scan, "items": [price_row]}, stamp.astimezone(_SHANGHAI)
                )
            except (ValueError, TypeError, KeyError):
                projected = None
            clean = projected["items"][0] if projected and projected["items"] else None
        nav = (
            _nav(price_row, coordinate, stamp) if matched and scan_clock_valid else None
        )
        if nav is None:
            clean = None
        stale = _stale(nav, evaluated)
        # A valid disclosed NAV may retain an existing warning even when its
        # reference tracks are partial. It never supplies their coordinates.
        nav_plan = (
            _public_actionable_plan(
                price_row,
                price_row["subjectId"],
                stamp,
                subject_kind=price_row["subjectKind"],
            )
            if nav
            else None
        )
        tracks = _reference_tracks(price_row, clean, stale, nav_plan)
        sectors = {
            r.get("discoverySector")
            for r in [product, row or {}]
            if isinstance(r.get("discoverySector"), str)
        }
        industries = (
            sorted(sectors & STANDARD_INDUSTRY_CODES)
            if matched and len(sectors) == 1
            else []
        )
        sector = (
            next(iter(sectors))
            if len(sectors) == 1
            else "CONFLICTING_SECTOR"
            if sectors
            else None
        )
        audit, missing_checks = _audit(industries, selected, reviews, raw_coverage)
        subscription_raw = (
            (packet.get("fundSubscriptionStatus") or {}).get(code)
            or (row or {}).get("subscriptionStatus")
            or product.get("subscriptionStatus")
        )
        subscription = normalize_subscription_status(subscription_raw)
        if (
            subscription
            and subscription.get("retrievedAt") is not None
            and (
                _stamp(subscription["retrievedAt"]) is None
                or _stamp(subscription["retrievedAt"]) > stamp
            )
        ):
            subscription = None
        current = [
            _risk(review, industry, stamp.isoformat())
            for industry in industries
            for review in reviews.get(industry, [])
        ]
        status = (
            "IDENTITY_UNVERIFIED"
            if not matched
            else "NOT_SCANNED"
            if row is None
            else "STALE"
            if stale
            else "OBSERVABLE"
            if clean and nav
            else "PRICE_INCOMPLETE"
        )
        gaps = ([] if matched else ["FUND_IDENTITY_UNVERIFIED"]) + (
            [] if clean else ["COMPLETE_REFERENCE_PRICE_UNAVAILABLE"]
        )
        if nav is None:
            gaps.append("NAV_PROVENANCE_UNVERIFIED")
        if row is not None and price_row is None:
            gaps.append("INVALID_SCAN_STRUCTURE")
        if subscription is None:
            gaps.append("SUBSCRIPTION_STATUS_UNKNOWN")
        if audit != "STRUCTURE_AND_EXECUTION_VERIFIED":
            gaps.append("INDUSTRY_AUDIT_PENDING_PROOF")
        if stale:
            gaps.append("FROZEN_PRICE_REFERENCE_STALE")
        item = {
            "fundCode": code,
            "name": _text(product.get("label")) or code,
            "subjectKind": product.get("subjectKind")
            if product.get("subjectKind") in {"fund", "nav"}
            else None,
            "subjectId": _text(product.get("subjectId"), 100),
            "identityVerified": matched,
            "identityProof": {
                "scope": "FROZEN_SERVER_CATALOG",
                "sourceUri": _uri(product.get("identitySourceUri")),
                "verifiedAt": verified_at.isoformat() if verified_at else None,
            },
            "observationStatus": status,
            "confirmedNav": nav,
            "referenceTracks": tracks,
            "subscription": {
                "status": subscription["purchaseStatus"] if subscription else "UNKNOWN",
                "restricted": subscription["restricted"] if subscription else None,
                "label": subscription["label"] if subscription else None,
                "dailyLimitCny": subscription["dailyLimitCny"]
                if subscription
                else None,
                "retrievedAt": subscription.get("retrievedAt")
                if subscription
                else None,
            },
            "industry": {
                "industryIds": industries,
                "unresolvedSector": None if industries else sector,
                "auditStatus": audit,
                "missingChecks": missing_checks,
            },
            "risk": {
                "current": current,
                "historical": [
                    deepcopy(risk) for risk in history if risk["industry"] in industries
                ],
            },
            "topicAssociations": _links(industries, topics, sources, stamp),
            "missingEvidence": gaps,
            "actionable": False,
            "investmentEligible": False,
        }
        if clean and isinstance(clean.get("relativeStrength"), dict):
            item["relativeStrength"] = deepcopy(clean["relativeStrength"])
        output.append(item)

    def priority(item):
        useful = [
            track
            for track in item["referenceTracks"]
            if track["observationStatus"] not in {"UNAVAILABLE", "STALE"}
        ]
        group = min(
            (
                0
                if track["observationStatus"] == "TECHNICAL_IN_ZONE"
                else 1
                if track["technicalState"] == "CANDIDATE"
                else 2
                for track in useful
            ),
            default=3,
        )
        risk = any(_has_risk(cycle) for cycle in item["risk"]["current"]) or any(
            cycle["active"] for cycle in item["risk"]["historical"]
        )
        distance = min(
            (abs(track["distanceToEntryPct"]) for track in useful), default=float("inf")
        )
        return (
            bool(item["subscription"]["restricted"]),
            bool(risk),
            group,
            distance,
            item["fundCode"],
        )

    observable = [item for item in output if item["observationStatus"] == "OBSERVABLE"]
    highlights = sorted(observable, key=priority)[:10]
    priced = sum(
        any(track["inEntryZone"] is not None for track in item["referenceTracks"])
        for item in output
    )
    nav_count = sum(item["confirmedNav"] is not None for item in output)
    status = (
        "AVAILABLE"
        if output and len(observable) == len(output)
        else "PARTIAL"
        if observable
        else "STALE"
        if any(item["observationStatus"] == "STALE" for item in output)
        else "PARTIAL"
        if nav_count
        else "UNAVAILABLE"
    )
    execution_count = raw_coverage.get("executionVerifiedCheckCount")
    return {
        "version": VERSION,
        "researchId": identity,
        "researchAsOf": stamp.isoformat(),
        "evaluatedAt": evaluated.isoformat(),
        "status": status,
        "actionable": False,
        "investmentEligible": False,
        "coverage": {
            "catalogFundCount": len(output),
            "scannedFundCount": sum(bool(scanned[code]) for code in products),
            "confirmedNavFundCount": nav_count,
            "pricedFundCount": priced,
            "observableFundCount": len(observable),
            "unpricedFundCount": len(output) - priced,
            "highlightCount": len(highlights),
            "selectedScopeCount": len(selected),
            "deferredScopeCount": len(manifest.get("deferredScopes", [])),
            "executionVerifiedCheckCount": execution_count
            if type(execution_count) is int and 0 <= execution_count <= 50
            else 0,
        },
        "highlights": highlights,
        "items": output,
        "unresolvedRiskNotice": "存在尚未归属至标准行业或时间待核实的风险信号；不得据此解除风险。"
        if unresolved
        else None,
        "unresolvedRiskScopes": sorted(unresolved),
    }
