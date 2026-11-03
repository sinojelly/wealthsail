"""History repair projections are isolated observations, never a formal report."""

from importlib import import_module
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, Mock

import pytest

from backend.errors import ValidationError_

from tests.unit.test_research_acceptance_repository import NOW, outcome


def repo():
    return import_module("backend.macro.research_history_repair_repository")


def result(run_id):
    return {
        "decision": "REPAIRED",
        "strictPass": True,
        "autoPublishAllowed": False,
        "historicalPublishAllowed": True,
        "safetyBlocked": False,
        "formalReport": {
            "valueDirections": [
                {
                    "key": "observe",
                    "title": "保留观察",
                    "thesis": "资料仍须复核",
                    "plans": [{"price": "PRIVATE_PRICE"}],
                }
            ]
        },
        "candidateRaw": {"PRIVATE_RAW": "original proposal"},
        "originalOutcome": outcome(researchId=str(run_id)),
        "quarantined": [
            {
                "itemType": "EVENT",
                "itemIndex": 1,
                "path": "events[1].PRIVATE_FIELD",
                "businessKey": "PRIVATE_ID",
                "ruleId": "ACCEPTANCE_SCHEMA_INVALID",
                "reason": "PRIVATE_MODEL_REASON",
                "sourceIds": ["s1"],
                "title": "被隔离事件",
                "summary": "证据不完整",
                "importance": "MEDIUM",
                "originalItem": {"PRIVATE": True},
            }
        ],
        "retained": {
            "counts": {"valueDirections": 1},
            "directionKeys": ["observe"],
            "planIdentities": [],
        },
        "blockers": [],
        "autoBlockers": [],
        "hardBlockers": [],
        "repairVersion": "history-repair-v1",
        "repairRevision": "a" * 64,
        "buildCommit": "b" * 40,
        "validatedAt": NOW.isoformat(),
    }


def test_history_projection_preserves_original_partial_and_strict_candidate_separately():
    run_id = uuid4()
    value = repo().safe_repair_summary(
        result(run_id),
        source_run_id=run_id,
        now=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )
    assert value["originalOutcome"]["decision"] == "PARTIAL"
    assert value["strictPass"] is True and value["autoPublishAllowed"] is False
    assert (
        value["actionable"] is False
        and value["publicationScope"] == "HISTORICAL_READ_ONLY"
    )
    assert value["currentFreshness"] == "EXPIRED"
    assert value["accepted"]["valueDirections"][0]["title"] == "保留观察"
    assert value["quarantined"][0]["title"] == "被隔离事件"
    assert "PRIVATE" not in str(value)
    assert "plans" not in str(value)


def test_history_projection_never_converts_a_safety_block_into_publish_permission():
    run_id = uuid4()
    raw = result(run_id)
    raw.update(safetyBlocked=True, historicalPublishAllowed=True)
    value = repo().safe_repair_summary(raw, source_run_id=run_id, now=NOW)
    assert value["historicalPublishAllowed"] is False
    assert value["safetyBlocked"] is True


def test_published_projection_filters_unexpected_stored_fields_and_preserves_fixed_annex_reason():
    run_id = uuid4()
    module = repo()
    summary = module.safe_repair_summary(result(run_id), source_run_id=run_id, now=NOW)
    summary["PRIVATE_FIELD"] = {"candidateRaw": "PRIVATE"}
    raw = {
        "summary": summary,
        "id": str(uuid4()),
        "sourceRunId": str(run_id),
        "state": "PUBLISHED",
        "createdAt": NOW.isoformat(),
        "validatedAt": NOW.isoformat(),
        "canInspectRepairDetails": False,
        "canReview": False,
        "canReject": False,
        "canPublish": False,
    }
    projected = module._dto(raw, now=NOW)
    assert "PRIVATE" not in str(projected)
    assert projected["quarantined"][0]["reason"] != "PRIVATE_MODEL_REASON"
    assert projected["canReject"] is False


def test_admin_projection_keeps_independent_reject_permission_when_approval_is_blocked():
    run_id = uuid4()
    value = result(run_id)
    value.update(safetyBlocked=True, historicalPublishAllowed=False)
    projected = repo()._dto(
        {
            "summary": repo().safe_repair_summary(
                value, source_run_id=run_id, now=NOW
            ),
            "id": str(uuid4()),
            "sourceRunId": str(run_id),
            "state": "DRAFT",
            "createdAt": NOW.isoformat(),
            "validatedAt": NOW.isoformat(),
            "canInspectRepairDetails": True,
            "canReview": False,
            "canReject": True,
            "canPublish": False,
            "privateResult": value,
        },
        now=NOW,
        admin=True,
    )
    assert projected["canReject"] is True
    assert projected["canReview"] is False


def test_public_quarantine_keeps_risk_and_scenario_prose_and_marks_text_excerpts():
    run_id = uuid4()
    value = result(run_id)
    value["quarantined"][0]["originalItem"] = {
        "risks": [
            {"summary": "存在订单推迟风险", "sourceIds": ["s1"], "PRIVATE": True}
        ],
        "watchFor": ["等待订单"],
        "unknowns": ["风险尚未排除"],
        "channels": ["订单传导"],
        "scenarios": [
            {
                "condition": "延期",
                "impact": "盈利承压",
                "counter": "核对回款",
                "PRIVATE": True,
            }
        ],
    }
    summary = repo().safe_repair_summary(value, source_run_id=run_id, now=NOW)
    annex = summary["quarantined"][0]
    assert annex["risks"] == [{"summary": "存在订单推迟风险", "sourceIds": ["s1"]}]
    assert annex["watchFor"] == ["等待订单"] and annex["channels"] == ["订单传导"]
    assert annex["unknowns"] == ["风险尚未排除"]
    assert annex["scenarios"][0]["impact"] == "盈利承压"
    assert "PRIVATE" not in str(annex)
    value["quarantined"][0]["originalItem"]["scenarios"][0]["impact"] = "字" * 301
    summary = repo().safe_repair_summary(value, source_run_id=run_id, now=NOW)
    assert len(summary["quarantined"][0]["scenarios"][0]["impact"]) == 300
    assert summary["acceptedContentComplete"] is False


@pytest.mark.asyncio
async def test_invalid_derived_write_keeps_failed_attempt_in_outer_transaction(
    monkeypatch,
):
    module = repo()
    run_id, attempt_id = uuid4(), uuid4()
    conn = AsyncMock()
    conn.transaction = Mock(return_value=AsyncMock())
    conn.fetchrow.return_value = {"total_bytes": 1000, "summary_bytes": 1000}
    value = result(run_id)
    monkeypatch.setattr(
        module,
        "_repair",
        AsyncMock(
            return_value=(
                {"originalOutcome": value["originalOutcome"], "sourceDigests": {}},
                value,
                NOW,
            )
        ),
    )
    calls = []

    async def rpc(_conn, name, *args):
        calls.append((name, args))
        if name == "reserve_history_repair":
            return {"id": str(attempt_id), "status": "RUNNING", "newAttempt": True}
        if name == "save_history_repair":
            raise ValidationError_("invalid generated contract")
        return True

    monkeypatch.setattr(module, "_rpc", rpc)
    failure = await module.generate_history_repair(
        conn, run_id, uuid4(), uuid4(), uuid4(), NOW
    )
    assert isinstance(failure, module.ReplayFailure)
    assert calls[-1] == (
        "finish_history_repair_failure",
        (attempt_id, "INVALID_OUTPUT"),
    )
