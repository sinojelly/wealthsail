"""Server-bound execution observations; structure is never proof of a search."""

from copy import deepcopy
from datetime import datetime
from uuid import UUID

from backend.integrations.research_execution import (
    executed_task_ids,
    execution_match_stats,
    sanitize_execution_receipt,
    sanitize_execution_transport_reason,
)

EXECUTION_KEY = "__wealthsail_execution_v1"


def bind_execution(checks, receipt, manifest, research_id, validated_at, *, transport_reason=None):
    """Only the worker calls this with out-of-band provider metadata."""
    result = deepcopy(checks)
    result[EXECUTION_KEY] = {
        "version": "research-execution-binding-v1",
        "researchId": str(research_id),
        "manifestHash": manifest["manifestHash"],
        "inputAsOf": manifest["asOf"],
        "validatedAt": validated_at.isoformat(),
        "receipt": sanitize_execution_receipt(receipt),
    }
    reason = sanitize_execution_transport_reason(transport_reason)
    if reason is not None:
        result[EXECUTION_KEY]["transportReason"] = reason
    return result


def _execution_binding(checks, manifest, validated_at, research_id):
    binding = checks.get(EXECUTION_KEY) if isinstance(checks, dict) else None
    if not isinstance(binding, dict):
        return None
    try:
        expected = str(UUID(str(research_id)))
        identity = str(UUID(binding["researchId"]))
        same_clock = datetime.fromisoformat(binding["validatedAt"]) == validated_at
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if (
        binding.get("version") != "research-execution-binding-v1"
        or binding.get("manifestHash") != manifest["manifestHash"]
        or binding.get("inputAsOf") != manifest["asOf"]
        or not same_clock
        or identity != expected
    ):
        return None
    return binding


def verified_tasks(checks, manifest, validated_at, *, research_id=None):
    binding = _execution_binding(checks, manifest, validated_at, research_id)
    return executed_task_ids(binding.get("receipt"), manifest) if binding else set()


def audit_assurance(reviews, manifest, checks, validated_at, *, research_id=None):
    from backend.macro.industry_cycle import RISK_CHECKS

    executed = verified_tasks(checks, manifest, validated_at, research_id=research_id)
    binding = _execution_binding(checks, manifest, validated_at, research_id)
    stats = execution_match_stats(binding.get("receipt") if binding else None, manifest)
    tasks = {(row["industryId"], row["kind"]): row["taskId"] for row in manifest["tasks"]}
    completed, evidence, structure = {}, set(), set()
    for industry, review in reviews.items():
        rows = review.get("riskChecks", [])
        if len(rows) == len(RISK_CHECKS) and {row["kind"] for row in rows} == set(RISK_CHECKS):
            structure.add(industry)
        completed[industry] = set()
        for row in rows:
            kind = row["kind"]
            if (
                row.get("status") == "FOUND"
                and row.get("claimSupported") is True
                and row.get("verification") == "SOURCE_CHECKED"
                and row.get("eventTimeVerification") == "RECENT"
                and row.get("industryScopeVerified") is True
            ):
                evidence.add((industry, kind))
                completed[industry].add(kind)
            elif row.get("status") == "NOT_FOUND" and tasks.get((industry, kind)) in executed:
                completed[industry].add(kind)
    executed_scopes = {
        industry for industry in manifest["selectedScopes"]
        if all(tasks.get((industry, kind)) in executed for kind in RISK_CHECKS)
    }
    result = {
        "completedKinds": completed,
        "structureCompleteScopeCount": len(structure),
        "executionVerifiedScopeCount": len(executed_scopes),
        "executionVerifiedCheckCount": len(executed),
        "evidenceVerifiedCheckCount": len(evidence),
        "searchExecutionVerified": bool(manifest["selectedScopes"])
        and set(manifest["selectedScopes"]) <= executed_scopes,
        "executionMatchStatus": stats["classification"],
        "executionPlannedTaskCount": stats["plannedTaskCount"],
        "executionMatchedTaskCount": stats["matchedTaskCount"],
        "executionObservedQueryCount": stats["observedQueryCount"],
        "executionUnmatchedQueryHashCount": stats["unmatchedQueryHashCount"],
    }
    reason = sanitize_execution_transport_reason(binding.get("transportReason")) if binding else None
    if reason is not None:
        result["executionReceiptTransportReason"] = reason
    return result
