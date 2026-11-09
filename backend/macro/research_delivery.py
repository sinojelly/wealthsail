"""Current-run economic observations, independent of retained investment reports."""

from copy import deepcopy
from datetime import date, datetime
import json
import math
from uuid import UUID

from backend.macro import research_acceptance_repository as acceptance

VERSION = "economic-research-delivery-v1"
_COUNT_FIELDS = (
    "totalCount",
    "storedCount",
    "plannedScopeCount",
    "reviewedScopeCount",
    "pendingScopeCount",
    "deferredScopeCount",
    "structureCompleteScopeCount",
    "executionVerifiedScopeCount",
    "evidenceVerifiedCheckCount",
    "executionVerifiedCheckCount",
    "strictDirectionCount",
    "autoPublishBlockerCount",
    "executionPlannedTaskCount",
    "executionMatchedTaskCount",
    "executionPendingDirectionCount",
    "executionObservedQueryCount",
    "executionUnmatchedQueryHashCount",
    "discoveredTopicCount",
    "evidenceBackedTopicCount",
    "evaluableFundDirectionCount",
    "qualifiedFormalDirectionCount",
    "validEventCount",
    "validUpcomingCount",
    "unresolvedReferenceCount",
    "pendingVerificationTopicCount",
    "maxTopics",
    "maxFormalDirections",
    "maxScopes",
    "maxQueries",
    "maxSources",
    "maxModelCalls",
    "additionalModelCalls",
    "maxInputBytes",
    "maxOutputBytes",
    "maxExecutionSeconds",
)
_ENUM_FIELDS = {
    "researchDeliveryStatus": {
        "VALIDATED_READ_ONLY",
        "OBSERVATION_READ_ONLY",
        "UNAVAILABLE",
    },
    "investmentQualificationStatus": {
        "QUALIFIED_CANDIDATES",
        "NO_QUALIFIED_OPPORTUNITIES",
        "BLOCKED",
    },
    "executionMatchStatus": {
        "EXECUTION_MATCHED",
        "EXECUTION_PARTIALLY_MATCHED",
        "EXECUTION_UNMATCHED",
        "EXECUTION_UNVERIFIED",
        "EXECUTION_NOT_OBSERVED",
    },
}


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _time(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return (
        result if result.tzinfo is not None and result.utcoffset() is not None else None
    )


def safe_delivery_coverage(value):
    """No missing legacy counts, model claims, or booleans are turned into facts."""
    value = _object(value)
    result = {
        key: value[key]
        for key in _COUNT_FIELDS
        if type(value.get(key)) is int and 0 <= value[key] <= 10000000
    }
    for key in (
        "strictRevalidated",
        "autoPublishAllowed",
        "searchExecutionVerified",
        "hasMore",
    ):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key, allowed in _ENUM_FIELDS.items():
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    ratio = value.get("evidenceBackedTopicRatioPercent")
    if "evidenceBackedTopicRatioPercent" in value and (
        ratio is None
        or type(ratio) in (int, float)
        and math.isfinite(ratio)
        and 0 <= ratio <= 100
    ):
        result["evidenceBackedTopicRatioPercent"] = ratio
    if isinstance(value.get("formalEligibilityReasons"), dict):
        from backend.macro.research_topics import FORMAL_ELIGIBILITY_REASONS

        result["formalEligibilityReasons"] = {
            key: count
            for key, count in value["formalEligibilityReasons"].items()
            if key in FORMAL_ELIGIBILITY_REASONS
            and type(count) is int
            and 0 <= count <= 10000
        }
    counts = value.get("countsByStatus")
    if isinstance(counts, dict):
        result["countsByStatus"] = {
            key: counts[key]
            for key in ("FORMAL", "OBSERVATION_ONLY", "PENDING_REVIEW", "QUARANTINED")
            if type(counts.get(key)) is int and 0 <= counts[key] <= 10000
        }
    from backend.integrations.research_execution import (
        sanitize_execution_transport_reason,
    )

    reason = sanitize_execution_transport_reason(
        value.get("executionReceiptTransportReason")
    )
    if reason is not None:
        result["executionReceiptTransportReason"] = reason
    return result


def _empty(run_id=None):
    return {
        "version": VERSION,
        "researchId": str(run_id) if run_id is not None else None,
        "status": "UNKNOWN",
        "asOf": None,
        "validatedAt": None,
        "recordedAt": None,
        "archiveState": None,
        "archivePending": False,
        "coverage": None,
        "observations": acceptance._accepted({}),
        "acceptedContentComplete": False,
        "actionable": False,
    }


def _source_visible(source, validated_at):
    from backend.macro.daily_repository import local

    if not isinstance(source.get("id"), str) or not isinstance(source.get("url"), str):
        return False
    for key in ("publishedAt", "retrievedAt"):
        value = source.get(key)
        if value is not None:
            stamp = _time(value)
            if stamp is None or stamp > validated_at:
                return False
    if source.get("publishedDate") is not None:
        try:
            published = date.fromisoformat(source["publishedDate"])
        except (TypeError, ValueError):
            return False
        if published > local(validated_at).date():
            return False
    return True


def _observations(value, validated_at):
    result = acceptance._accepted(value)
    before = deepcopy(result)
    result["sources"] = [
        row for row in result["sources"] if _source_visible(row, validated_at)
    ]
    visible = {row["id"] for row in result["sources"]}
    for component in ("events", "upcoming", "industryCycleReviews", "valueDirections"):
        result[component] = [
            row for row in result[component] if set(row.get("sourceIds", [])) <= visible
        ]
    overview = result.get("macroOverview")
    if isinstance(overview, dict) and not set(overview.get("sourceIds", [])) <= visible:
        result.pop("macroOverview")
    return result, result != before


async def read_economic_research(conn, run_id, now):
    """Read only this visible Shanghai-day run under the caller's existing RLS."""
    from backend.macro.daily_repository import local

    stamp = local(now)
    try:
        identity = UUID(str(run_id))
    except (TypeError, ValueError, AttributeError):
        return _empty()
    run = await conn.fetchrow(
        """SELECT r.run_id,r.slot_date,r.dispatched_at,t.status,t.recorded_at
        FROM research.daily_research_runs r LEFT JOIN research.daily_research_results t
          ON t.run_id=r.run_id AND t.recorded_at<=$3
        WHERE r.run_id=$1 AND r.slot_date=$2 AND r.dispatched_at<=$3""",
        identity,
        stamp.date(),
        stamp,
    )
    if (
        not run
        or str(run["run_id"]) != str(identity)
        or run["slot_date"] != stamp.date()
    ):
        return _empty()
    dispatched = _time(run["dispatched_at"])
    if dispatched is None or dispatched > stamp:
        return _empty()
    result = _empty(identity)
    receipt = await acceptance.read_validation_summary(
        conn, identity, stamp, admin=False
    )
    if receipt is None:
        result["status"] = "PENDING" if run.get("status") is None else "UNKNOWN"
        return result
    if receipt.get("researchId") != str(identity) or receipt.get("kind") != "INITIAL":
        return result
    validated, recorded = (
        _time(receipt.get("validatedAt")),
        _time(receipt.get("recordedAt")),
    )
    if (
        validated is None
        or recorded is None
        or not dispatched <= validated <= recorded <= stamp
    ):
        return result
    archive = receipt.get("archiveState")
    if archive not in {"ARCHIVED", "AUDIT_PENDING", "ARCHIVING", "UNRECOVERABLE"}:
        return result
    result.update(
        archiveState=archive,
        archivePending=archive in {"AUDIT_PENDING", "ARCHIVING"},
        asOf=validated.isoformat(),
        validatedAt=validated.isoformat(),
        recordedAt=recorded.isoformat(),
    )
    if archive != "ARCHIVED":
        result["status"] = "PENDING" if result["archivePending"] else "UNAVAILABLE"
        return result
    snapshot = await conn.fetchrow(
        """SELECT validated_at,created_at,metadata->'coverage' AS coverage
        FROM research.research_topic_snapshots
        WHERE run_id=$1 AND validated_at<=$2 AND created_at<=$2""",
        identity,
        stamp,
    )
    if (
        snapshot
        and _time(snapshot["validated_at"]) == validated
        and (created := _time(snapshot["created_at"])) is not None
        and created <= stamp
    ):
        result["coverage"] = safe_delivery_coverage(snapshot["coverage"])
    observations, filtered = _observations(receipt.get("accepted"), validated)
    result["observations"] = observations
    result["acceptedContentComplete"] = (
        receipt.get("acceptedContentComplete") is not False
    )
    for key in ("issueCount", "issueListComplete"):
        value = receipt.get(key)
        if (
            key == "issueCount"
            and type(value) is int
            and value >= 0
            or key == "issueListComplete"
            and type(value) is bool
        ):
            result[key] = value
    measured = result["coverage"] or {}
    economic_issues = receipt.get("economicIssueCount")
    result["economicIssueCount"] = (
        economic_issues
        if receipt.get("economicIssueScopeVersion")
        == acceptance.ECONOMIC_ISSUE_SCOPE_VERSION
        and type(economic_issues) is int
        and 0 <= economic_issues <= result.get("issueCount", 0)
        else result.get("issueCount", 0)
    )
    available = any(
        observations.get(key)
        for key in (
            "sources",
            "events",
            "upcoming",
            "valueDirections",
            "industryCycleReviews",
            "macroOverview",
            "mentionedFunds",
        )
    )
    partial = (
        filtered
        or not result["acceptedContentComplete"]
        or result["economicIssueCount"] > 0
        or result.get("issueListComplete") is False
        or measured.get("pendingVerificationTopicCount") != 0
    )
    result["status"] = (
        "UNAVAILABLE"
        if not available
        else "AVAILABLE"
        if measured.get("researchDeliveryStatus") == "VALIDATED_READ_ONLY"
        and not partial
        else "PARTIAL"
    )
    return result
