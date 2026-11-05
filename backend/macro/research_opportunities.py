"""Read safe current-run observations without entering formal report consumers."""

import json
import logging
from uuid import UUID

from backend.errors import ValidationError_
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
