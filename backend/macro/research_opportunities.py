"""Read safe current-run observations without entering formal report consumers."""

import json
import logging
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import timedelta
import math
from uuid import UUID

from backend.errors import ForbiddenError, NotFoundError, ValidationError_
from backend.macro import research_acceptance_repository as acceptance
from backend.macro.research_delivery import _time, safe_delivery_coverage
from backend.macro.research_topic_repository import prepare_topic_registry

_LOG = logging.getLogger("wealthsail.research_opportunities")


def _unavailable(identity, validated, now):
    return {
        "version": "fund-observation-v1",
        "researchId": str(identity),
        "researchAsOf": validated.isoformat(),
        "evaluatedAt": now.isoformat(),
        "status": "UNAVAILABLE",
        "actionable": False,
        "investmentEligible": False,
        "coverage": {
            key: 0
            for key in (
                "catalogFundCount",
                "scannedFundCount",
                "confirmedNavFundCount",
                "pricedFundCount",
                "observableFundCount",
                "unpricedFundCount",
                "highlightCount",
                "selectedScopeCount",
                "deferredScopeCount",
                "executionVerifiedCheckCount",
            )
        },
        "highlights": [],
        "items": [],
        "unresolvedRiskScopes": [],
        "unresolvedRiskNotice": "本轮基金观察输入未通过完整性检查；其他研究内容保留，不能据此判断没有基金或风险。",
        "missingEvidence": ["FROZEN_OBSERVATION_INVALID"],
    }


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else None


async def _topics(conn, identity, validated, now):
    snapshot = await conn.fetchrow(
        """SELECT id,validated_at,created_at,total_count,metadata
        FROM research.research_topic_snapshots WHERE run_id=$1
          AND validated_at<=$2 AND created_at<=$2""",
        identity,
        now,
    )
    if not snapshot:
        return [], {}, "NOT_ARCHIVED"
    if (
        _time(snapshot["validated_at"]) != validated
        or (created := _time(snapshot["created_at"])) is None
        or not validated <= created <= now
        or type(snapshot["total_count"]) is not int
        or not 0 <= snapshot["total_count"] <= 72
    ):
        return [], {}, "PROVENANCE_UNVERIFIED"
    rows = await conn.fetch(
        """SELECT body FROM research.research_topic_entries
        WHERE snapshot_id=$1 ORDER BY ordinal LIMIT 73""",
        snapshot["id"],
    )
    if len(rows) != snapshot["total_count"]:
        return [], {}, "INCOMPLETE"
    try:
        metadata = _object(snapshot["metadata"]) or {}
        value = prepare_topic_registry(
            {
                **metadata,
                "researchId": str(identity),
                "validatedAt": validated.isoformat(),
                "items": [_object(row["body"]) for row in rows],
            },
            identity,
            validated,
        )
    except (ValueError, TypeError, ValidationError_):
        return [], {}, "PROJECTION_UNAVAILABLE"
    return value["items"], safe_delivery_coverage(value["coverage"]), "ARCHIVED"


async def read_fund_observation(
    conn, latest, displayed_run_id, now, *, _projector=None
):
    """The caller already selected a visible run; never borrow its READY fallback.

    A retained previous-day run keeps its actual evidence clock. Private source
    artifacts are never queried; only INITIAL's safe summary and the RLS topic
    projection can enrich the server-owned public scan.
    """
    try:
        identity = UUID(str(displayed_run_id))
    except (ValueError, TypeError, AttributeError):
        return None
    if not latest or str(latest.get("run_id")) != str(identity):
        return None
    dispatched = _time(latest.get("dispatched_at"))
    if dispatched is None or dispatched > now:
        return None
    packet = _object(latest.get("public_packet"))
    if (
        not packet
        or not isinstance(packet.get("quantitativeScan"), dict)
        or not isinstance(packet.get("discoveryUniverse"), list)
    ):
        return None
    receipt = await acceptance.read_validation_summary(conn, identity, now, admin=False)
    if (
        not receipt
        or receipt.get("researchId") != str(identity)
        or receipt.get("kind") != "INITIAL"
    ):
        return None
    validated, recorded = (
        _time(receipt.get("validatedAt")),
        _time(receipt.get("recordedAt")),
    )
    packet_clock = _time(packet.get("asOf"))
    if (
        validated is None
        or recorded is None
        or packet_clock is None
        or not dispatched <= validated <= recorded <= now
        or packet_clock > validated
    ):
        return None
    topics, coverage, topic_state = [], {}, "PENDING_ARCHIVE"
    sources, reviews = [], []
    archive = receipt.get("archiveState")
    if archive == "ARCHIVED":
        from backend.macro.research_delivery import _observations

        observations, _filtered = _observations(receipt.get("accepted"), validated)
        sources, reviews = observations["sources"], observations["industryCycleReviews"]
        topics, coverage, topic_state = await _topics(conn, identity, validated, now)
    formal = None
    if archive == "ARCHIVED" and latest.get("status") == "READY":
        candidate = _object(latest.get("payload"))
        report_clock = _time(candidate.get("asOf")) if candidate else None
        if report_clock is not None and dispatched <= report_clock <= validated:
            formal = candidate
    from backend.investment.fund_observation import (
        FundObservationDataError,
        build_fund_observation,
    )

    if _projector is None:
        _projector = build_fund_observation
    try:
        result = _projector(
            packet,
            now,
            research_id=str(identity),
            validated_at=validated,
            topics=topics,
            industry_reviews=reviews,
            sources=sources,
            coverage=coverage,
            formal_report=formal,
        )
    except FundObservationDataError:
        # An invalid optional projection must not hide independently accepted
        # research. Database errors and unexpected defects still propagate.
        _LOG.warning(
            "fund_observation_unavailable run_id=%s reason=FROZEN_OBSERVATION_INVALID",
            identity,
        )
        result = _unavailable(identity, validated, now)
    return {**result, "archiveState": archive, "topicLinkState": topic_state}


def project_topic_recommendations(
    topics,
    recalls,
    *,
    sources,
    reviews,
    coverage,
    packet,
    validated_at,
    now,
    accepted=None,
):
    """Attach real products to news theses; risk evidence annotates every match.

    Candidate directions do not depend on formal valueDirections or the audit
    quota. Neither a thematic match nor a completed audit establishes a buy.
    """
    from backend.investment.fund_observation import _audit, _has_risk
    from backend.macro.research_topics import frozen_manifest
    from backend.macro.research_topic_polarity import classify_topic_polarity
    from backend.macro.research_topic_priority import IMPORTANCE, topic_highlights
    from backend.investment.opportunity_fund_matching import fund_price_priority

    if not isinstance(topics, list) or len(topics) > 72:
        raise ValidationError_("机会推荐超过主题目录边界")
    display = {
        row["topic"]["topicId"]: row["priority"]
        for row in topic_highlights(topics, accepted=accepted, limit=72)["items"]
    }
    association_order = {
        "DIRECT_INDEX": 3,
        "CATEGORY_MATCH": 2,
        "NAME_CANDIDATE": 1,
        "BROAD_FALLBACK": 0,
    }
    reviews_by_scope = defaultdict(list)
    for review in reviews:
        if isinstance(review, dict) and isinstance(review.get("industry"), str):
            reviews_by_scope[review["industry"]].append(review)
    try:
        selected = (
            set(frozen_manifest(packet)["selectedScopes"])
            if "researchDiscovery" in packet
            else {
                row["industry"]
                for row in (packet.get("industryCycleResearch") or {}).get(
                    "candidates", []
                )
                if isinstance(row, dict) and isinstance(row.get("industry"), str)
            }
        )
    except ValueError:
        selected = set()
    counts = Counter(row.get("id") for row in sources)
    checked_ids = {
        row["id"]
        for row in sources
        if counts[row.get("id")] == 1 and row.get("verification") == "SOURCE_CHECKED"
    }
    ranked = []
    for ordinal, topic in enumerate(topics):
        if topic.get("status") == "QUARANTINED":
            continue
        polarity = classify_topic_polarity(topic, sources=sources)
        economic_direction = polarity["topicPolarity"]
        match = topic.get("match") or {}
        industries = set(match.get("industryIds", []))
        audit_scopes = industries | {
            row["industryId"]
            for row in match.get("proposedScopes", [])
            if isinstance(row, dict) and isinstance(row.get("industryId"), str)
        }
        unresolved = set(match.get("unresolvedClaims", []))
        scopes = audit_scopes | unresolved
        associated = [
            row for scope in scopes for row in reviews_by_scope.get(scope, [])
        ]
        for old in packet.get("previousIndustryRiskStates", []):
            if not isinstance(old, dict) or old.get("industry") not in scopes:
                continue
            researched = _time(old.get("researchedAt"))
            cycle = old.get("industryCycle")
            if (
                researched
                and researched <= validated_at
                and now - researched <= timedelta(days=14)
                and isinstance(cycle, dict)
            ):
                associated.append(cycle)
        adverse = [row for row in associated if _has_risk(row)]
        audit, missing_checks = _audit(
            audit_scopes, selected, reviews_by_scope, coverage
        )
        refs = set(topic.get("sourceIds", []))
        backed = bool(refs) and refs <= checked_ids
        stale_audit = now - validated_at > timedelta(days=3)
        verified = (
            audit == "STRUCTURE_AND_EXECUTION_VERIFIED"
            and backed
            and match.get("scopeVerified") is True
            and not unresolved
            and not stale_audit
        )
        risk_status = (
            "RISK_ALERT"
            if adverse or economic_direction in {"NEGATIVE_RISK", "MIXED"}
            else "VERIFIED_SAFE"
            if verified
            else "UNVERIFIED_RISK"
        )
        warnings = []
        if economic_direction in {"NEGATIVE_RISK", "MIXED"}:
            warnings.append(
                "主题文字包含负面经济信号，相关基金用于辨识风险敞口；请核对原始事实与分歧。"
            )
        for review in adverse:
            warnings.extend(
                str(check["summary"])[:600]
                for check in review.get("riskChecks", [])
                if isinstance(check, dict)
                and check.get("status") == "FOUND"
                and check.get("summary")
            )
            if review.get("evidenceSummary"):
                warnings.append(str(review["evidenceSummary"])[:600])
        if adverse and not warnings:
            warnings.append("关联行业存在负面风险记录，请核对风险来源及分歧。")
        if not verified:
            warnings.append("负面风险核查尚未完整核验，基金匹配不代表已经排除风险。")
        penalty = (
            20
            if any(row.get("riskLevel") == "CONFIRMED" for row in adverse)
            else 10
            if adverse or economic_direction in {"NEGATIVE_RISK", "MIXED"}
            else 0
        )
        recalled = recalls.get(topic.get("topicId"), recalls.get(topic.get("key"), {}))
        direct = deepcopy(
            [
                row
                for row in recalled.get("funds", [])
                if row.get("matchLevel") != "BROAD_FALLBACK"
                and row.get("associationLevel") != "BROAD_FALLBACK"
            ]
        )
        indirect = deepcopy(
            [
                *recalled.get("indirectFunds", []),
                *(
                    row
                    for row in recalled.get("funds", [])
                    if row.get("matchLevel") == "BROAD_FALLBACK"
                    or row.get("associationLevel") == "BROAD_FALLBACK"
                ),
            ]
        )
        funds = (
            direct if economic_direction in {"POSITIVE_OPPORTUNITY", "MIXED"} else []
        )
        risk_funds = direct if economic_direction == "NEGATIVE_RISK" else []
        observations = (
            direct
            if economic_direction in {"MACRO_OBSERVATION", "UNDETERMINED"}
            else []
        )
        for fund in [*direct, *indirect]:
            score = fund.get("quantitativeScore")
            if (
                type(score) in (int, float)
                and math.isfinite(score)
                and 0 <= score <= 100
            ):
                applied = fund.get("quantitativeRiskPenaltyApplied", 0)
                applied = (
                    applied
                    if type(applied) in (int, float)
                    and math.isfinite(applied)
                    and 0 <= applied <= 20
                    else 0
                )
                fund["unadjustedQuantitativeScore"] = min(100, score + applied)
                fund["quantitativeScore"] = max(0, score - max(0, penalty - applied))
            else:
                fund["quantitativeScore"] = None
            fund.update(
                actionable=False,
                canBuyNow=False,
                riskStatus=risk_status,
                riskScorePenalty=penalty,
            )
            fund["riskWarnings"] = list(
                dict.fromkeys([*fund.get("riskWarnings", []), *warnings])
            )
        direct.sort(key=fund_price_priority)
        indirect.sort(key=fund_price_priority)
        del direct[3:]
        del indirect[3:]
        gaps = ([] if backed else ["POSITIVE_SOURCE_UNVERIFIED"]) + (
            [] if verified else ["RISK_AUDIT_INCOMPLETE"]
        )
        if stale_audit:
            gaps.append("STALE_RISK_AUDIT")
        if not direct and not indirect:
            gaps.append(
                recalled.get("unavailableReason") or "NO_SUITABLE_FUND_IN_LOCAL_CATALOG"
            )
        elif economic_direction == "NEGATIVE_RISK":
            gaps.append("NEGATIVE_RISK_OBSERVATION_ONLY")
        elif economic_direction in {"MACRO_OBSERVATION", "UNDETERMINED"}:
            gaps.append("ECONOMIC_DIRECTION_PENDING")
        scores = [
            fund["quantitativeScore"]
            for fund in funds
            if fund["quantitativeScore"] is not None
        ]
        item = {
            **polarity,
            "topicId": topic.get("topicId"),
            "key": topic["key"],
            "title": topic["title"],
            "thesis": topic.get("summary", ""),
            "sourceIds": sorted(refs),
            "riskStatus": risk_status,
            "riskWarnings": list(dict.fromkeys(warnings)),
            "riskScorePenalty": penalty,
            "missingRiskChecks": missing_checks,
            "recommendedFunds": funds,
            "relatedRiskFunds": risk_funds,
            "observationFunds": observations,
            "indirectFunds": indirect,
            "recommendationStatus": "SOURCE_PENDING"
            if not backed
            else "RISK_ONLY"
            if economic_direction == "NEGATIVE_RISK"
            else "OBSERVATION_ONLY"
            if economic_direction in {"MACRO_OBSERVATION", "UNDETERMINED"}
            else "MATCHED"
            if funds
            else "INDIRECT_ONLY"
            if indirect
            else "NO_MATCH",
            "missingEvidence": gaps,
            "matchMessage": recalled.get("message"),
            "candidateDirection": {
                "key": topic["key"],
                "title": topic["title"],
                "thesis": topic.get("summary", ""),
                "origin": "NEWS_TOPIC",
            }
            if economic_direction in {"POSITIVE_OPPORTUNITY", "MIXED"}
            else None,
            "priority": {
                **display.get(topic.get("topicId"), {}),
                "matchedFundCount": len(funds),
                "checkedSourceCount": len(refs & checked_ids),
                "quantitativeScore": max(scores) if scores else None,
                "riskAuditComplete": verified,
                "associationStrength": max(
                    (
                        association_order.get(fund.get("associationLevel"), 1)
                        for fund in funds
                    ),
                    default=0,
                ),
            },
            "actionable": False,
        }
        ranked.append(
            (
                (
                    min((fund_price_priority(fund)[0] for fund in [*direct, *indirect]), default=5),
                    -int(bool(funds)),
                    -IMPORTANCE.get(item["priority"].get("importance"), 0),
                    -len(refs & checked_ids),
                    -item["priority"]["associationStrength"],
                    -max(scores) if scores else 1,
                    -int(verified),
                    ordinal,
                ),
                item,
            )
        )
    ranked.sort(key=lambda row: row[0])
    return [item for _, item in ranked]


async def read_topic_fund_recommendations(conn, run_id, now, *, _recall=None):
    """Read the complete visible topic snapshot and the current local catalog.

    Only public, archived INITIAL observations supply theses and risk proof.
    Fund recall reads local reference data and never calls a model or provider.
    """
    from backend.macro.research_delivery import _observations

    identity = UUID(str(run_id))
    allowed = await conn.fetchval(
        "SELECT research.validation_live_member(research.validation_current_tenant(),false)"
    )
    if not allowed:
        raise ForbiddenError("需要当前有效租户成员身份")
    receipt = await acceptance.read_validation_summary(conn, identity, now, admin=False)
    if (
        not receipt
        or receipt.get("researchId") != str(identity)
        or receipt.get("kind") != "INITIAL"
    ):
        raise NotFoundError("该研究没有可见的机会推荐材料")
    validated, recorded = (
        _time(receipt.get("validatedAt")),
        _time(receipt.get("recordedAt")),
    )
    if validated is None or recorded is None or not validated <= recorded <= now:
        raise ValidationError_("机会推荐材料时间无效")
    result = {
        "version": "topic-fund-recommendations-v2",
        "researchId": str(identity),
        "validatedAt": validated.isoformat(),
        "evaluatedAt": now.isoformat(),
        "archiveState": receipt.get("archiveState"),
        "status": "UNAVAILABLE",
        "items": [],
        "coverage": {
            "topicCount": 0,
            "matchedTopicCount": 0,
            "recommendedFundCount": 0,
        },
        "missingEvidence": [],
        "actionable": False,
    }
    if receipt.get("archiveState") != "ARCHIVED":
        result["missingEvidence"] = ["TOPIC_ARCHIVE_PENDING"]
        return result
    row = await conn.fetchrow(
        """SELECT run_id,dispatched_at,public_packet FROM research.daily_research_runs
        WHERE run_id=$1 AND dispatched_at<=$2""",
        identity,
        now,
    )
    packet = _object(row.get("public_packet")) if row else None
    dispatched = _time(row.get("dispatched_at")) if row else None
    packet_at = _time(packet.get("asOf")) if packet else None
    if (
        not packet
        or dispatched is None
        or packet_at is None
        or not dispatched <= validated
        or packet_at > validated
    ):
        result["missingEvidence"] = ["FROZEN_PACKET_UNAVAILABLE"]
        return result
    topics, coverage, state = await _topics(conn, identity, validated, now)
    if state != "ARCHIVED":
        result["missingEvidence"] = ["TOPIC_" + state]
        return result
    candidates = [topic for topic in topics if topic.get("status") != "QUARANTINED"]
    if _recall is None:
        from backend.investment.opportunity_fund_cache import (
            read_cached_topic_funds,
            read_topic_fund_cache_status,
        )
        from backend.investment.opportunity_fund_recall import enrich_topic_funds
        from backend.investment.opportunity_fund_prices import (
            enrich_fund_price_positions,
        )

        discovered = await read_cached_topic_funds(
            conn, identity, validated, candidates, now
        )
        if discovered is None:
            state = await read_topic_fund_cache_status(conn, identity, validated, now)
            result["missingEvidence"] = [
                "FUND_RECALL_FAILED" if state == "FAILED" else "FUND_RECALL_PENDING"
            ]
            return result
        recalls = await enrich_topic_funds(conn, discovered, packet, now, display_limit=None)
        recalls = await enrich_fund_price_positions(conn, recalls, packet, now)
    else:
        # Explicit test seam. Production reads always use the durable cache.
        recalls = await _recall(conn, candidates, packet, now)
    observations, _ = _observations(receipt.get("accepted"), validated)
    items = project_topic_recommendations(
        topics,
        recalls,
        sources=observations["sources"],
        reviews=observations["industryCycleReviews"],
        coverage=coverage,
        packet=packet,
        validated_at=validated,
        now=now,
        accepted=observations,
    )
    result.update(
        items=items,
        status="PARTIAL"
        if any(item["missingEvidence"] for item in items)
        else "AVAILABLE",
        coverage={
            "topicCount": len(items),
            "matchedTopicCount": sum(bool(item["recommendedFunds"]) for item in items),
            "recommendedFundCount": sum(
                len(item["recommendedFunds"]) for item in items
            ),
            "directIndexFundCount": sum(
                fund.get("associationLevel") == "DIRECT_INDEX"
                for item in items
                for fund in item["recommendedFunds"]
            ),
            "subscriptionVerifiedFundCount": sum(
                fund.get("canSubscribe") is True
                for item in items
                for fund in item["recommendedFunds"]
            ),
            "priceEvaluableFundCount": sum(
                (fund.get("pricePosition") or {}).get("status")
                in {"ENTRY_ZONE", "NEAR_ENTRY_ZONE", "NEUTRAL", "OVERHEATED"}
                for item in items
                for fund in item["recommendedFunds"]
            ),
            "riskTopicCount": sum(
                item["topicPolarity"] == "NEGATIVE_RISK" for item in items
            ),
            "indirectFundCount": sum(len(item["indirectFunds"]) for item in items),
        },
    )
    return result
