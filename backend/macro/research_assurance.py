"""Server-bound execution observations; structure is never proof of a search."""

from copy import deepcopy
from datetime import datetime
from uuid import UUID

from backend.integrations.research_execution import (
    executed_task_ids,
    sanitize_execution_receipt,
)

EXECUTION_KEY = "__wealthsail_execution_v1"


def bind_execution(checks, receipt, manifest, research_id, validated_at):
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
    return result


def verified_tasks(checks, manifest, validated_at, *, research_id=None):
    binding = checks.get(EXECUTION_KEY) if isinstance(checks, dict) else None
    if not isinstance(binding, dict):
        return set()
    try:
        identity = str(UUID(binding["researchId"]))
        same_clock = datetime.fromisoformat(binding["validatedAt"]) == validated_at
    except (KeyError, ValueError, TypeError, AttributeError):
        return set()
    if (
        binding.get("version") != "research-execution-binding-v1"
        or binding.get("manifestHash") != manifest["manifestHash"]
        or binding.get("inputAsOf") != manifest["asOf"]
        or not same_clock
        or (research_id is not None and identity != str(research_id))
    ):
        return set()
    return executed_task_ids(binding.get("receipt"), manifest)


def audit_assurance(reviews, manifest, checks, validated_at, *, research_id=None):
    from backend.macro.industry_cycle import RISK_CHECKS

    executed = verified_tasks(checks, manifest, validated_at, research_id=research_id)
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
    return {
        "completedKinds": completed,
        "structureCompleteScopeCount": len(structure),
        "executionVerifiedScopeCount": len(executed_scopes),
        "executionVerifiedCheckCount": len(executed),
        "evidenceVerifiedCheckCount": len(evidence),
        "searchExecutionVerified": bool(manifest["selectedScopes"])
        and set(manifest["selectedScopes"]) <= executed_scopes,
    }
