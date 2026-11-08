"""Replay reduced public evidence from the real Windows failure without AI or DB.

Run with ``pytest tests/unit/test_research_autodowngrade_audit.py``. The fixture is
independent of .local; its original empty execution receipt is never upgraded.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from backend.macro.daily_research import validate_report
from backend.macro.research_assurance import verified_tasks
from backend.macro.research_batch import evaluate_research_batch
from backend.macro.research_topics import frozen_manifest


FIXTURE = Path(__file__).parents[1] / "fixtures/research_autodowngrade_d781_public.json"
RUN = "d7810690-ef13-44ad-8591-d3699f3f589a"
KEY = "robotics_mobility"
EXECUTION_KEY = "__wealthsail_execution_v1"


def public_failure():
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return value, datetime.fromisoformat(value["validatedAt"])


def assert_observation(report, original):
    directions = {row["key"]: row for row in report["valueDirections"]}
    assert KEY in directions, "incomplete evidence must retain the research direction"
    direction = directions[KEY]
    assert direction["signal"] == "WATCH"
    assert direction["qualificationStatus"] == "OBSERVATION_ONLY"
    assert direction["investmentEligible"] is False
    assert direction["formalEligibilityReasons"]
    assert direction["plans"] == []
    assert direction["quantitativeFundCodes"] == []
    assert direction["title"] == original["valueDirections"][0]["title"]
    assert direction["thesis"] == original["valueDirections"][0]["thesis"]
    assert [row["summary"] for row in direction["risks"]] == [
        row["summary"] for row in original["valueDirections"][0]["risks"]
    ]
    assert direction["industryCycle"]["searchExecutionVerified"] is False
    assert report["summary"] == original["summary"]
    assert report["macroCycle"] == original["macroCycle"]
    assert report["coverage"] == original["coverage"]
    assert report["topicCatalog"][0]["key"] == KEY
    assert any(
        row["title"] == original["events"][0]["title"] for row in report["events"]
    )
    assert not report["actionablePlans"]


def test_real_fixture_keeps_failed_source_checks_and_empty_execution_proof():
    value, at = public_failure()
    assert value["researchId"] == RUN
    checks, packet = value["sourceChecks"], value["packet"]
    manifest = frozen_manifest(packet)
    assert len(manifest["tasks"]) == 50
    binding = checks[EXECUTION_KEY]
    assert binding["researchId"] == RUN
    assert binding["manifestHash"] == manifest["manifestHash"]
    assert binding["validatedAt"] == value["validatedAt"]
    receipt = binding["receipt"]
    assert receipt["coverage"] == "PARTIAL"
    assert receipt["proofBarrier"] == "UNATTRIBUTED_EVENT"
    assert receipt["queryReceipts"] == receipt["actionProofs"] == []
    assert verified_tasks(checks, manifest, at, research_id=RUN) == set()
    for ref in ("s05", "s06", "s07"):
        assert checks[ref]["reason"] == "SOURCE_NOT_ALLOWLISTED"
        assert checks[ref]["verification"] == "UNVERIFIED"
        assert checks[ref]["quoteMatched"] is False
    assert [
        row["status"] for row in value["raw"]["industryCycleReviews"][0]["riskChecks"]
    ] == ["FOUND", "FOUND", "UNVERIFIED", "UNVERIFIED", "UNVERIFIED"]


def test_real_failed_robot_report_strictly_retains_observation_and_macro():
    value, at = public_failure()
    before = deepcopy(value)
    report = validate_report(
        value["raw"],
        value["packet"],
        value["sourceChecks"],
        completed_at=at,
        research_id=RUN,
    )
    assert_observation(report, value["raw"])
    assert value == before


def test_real_failed_robot_batch_publishes_safe_observation_without_new_evidence():
    value, at = public_failure()
    before = deepcopy(value)
    result = evaluate_research_batch(
        value["raw"],
        value["packet"],
        value["sourceChecks"],
        validated_at=at,
        research_id=RUN,
    )
    assert result["strictPass"] is True
    assert result["autoPublishAllowed"] is True
    assert not result["safetyBlocked"]
    assert_observation(result["formalReport"], value["raw"])
    topic = next(row for row in result["registry"]["items"] if row["key"] == KEY)
    assert topic["status"] == "OBSERVATION_ONLY"
    assert topic["investmentEligible"] is False
    coverage = result["registry"]["coverage"]
    assert coverage["additionalModelCalls"] == 0
    assert coverage["executionVerifiedCheckCount"] == 0
    assert coverage["searchExecutionVerified"] is False
    assert result["registry"]["auditTasks"], "missing checks must remain visible"
    assert value == before


def test_missing_real_nav_anchor_does_not_remove_other_research_content():
    # This authentic incomplete-audit case must stay useful even if its one NAV
    # anchor is absent. Separate synthetic tests cover a fully audited bad plan.
    value, at = public_failure()
    value["packet"]["discoveryActionableZones"] = []
    result = evaluate_research_batch(
        value["raw"],
        value["packet"],
        value["sourceChecks"],
        validated_at=at,
        research_id=RUN,
    )
    assert result["autoPublishAllowed"] is True
    assert_observation(result["formalReport"], value["raw"])


@pytest.mark.asyncio
async def test_worker_entry_persists_real_failed_sample_as_safe_ready_observation(
    monkeypatch,
):
    from backend.integrations.codex_research import ResearchResult
    from backend.jobs import daily_research as worker

    value, at = public_failure()
    packet, raw, checks = value["packet"], value["raw"], value["sourceChecks"]
    before = deepcopy(value)

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield

    @asynccontextmanager
    async def connection(_pool):
        yield Conn()

    async def inference(_settings, *, operation, **_kwargs):
        return await operation()

    async def model(*_args, lifecycle, **_kwargs):
        lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        # Only the external model boundary is replaced by the exact saved output.
        # Its unavailable web proof remains unavailable throughout the worker.
        return ResearchResult(
            deepcopy(raw), execution_receipt=checks[EXECUTION_KEY]["receipt"]
        )

    monkeypatch.setattr(worker, "system_connection", connection)
    monkeypatch.setattr(worker, "now_shanghai", lambda: at)
    monkeypatch.setattr(worker, "collect_daily_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(worker, "prepare_research_inputs", AsyncMock(return_value={}))
    monkeypatch.setattr(
        worker, "load_public_packet", AsyncMock(return_value=deepcopy(packet))
    )
    monkeypatch.setattr(
        worker,
        "preflight_codex",
        AsyncMock(
            return_value={
                "executable": "offline-fixture",
                "version": "saved-output-only",
            }
        ),
    )
    monkeypatch.setattr(worker, "run_codex_research", AsyncMock(side_effect=model))
    monkeypatch.setattr(worker, "trace_inference", inference)
    monkeypatch.setattr(
        worker,
        "verify_research_sources",
        AsyncMock(
            return_value={
                k: deepcopy(v) for k, v in checks.items() if k != EXECUTION_KEY
            }
        ),
    )
    monkeypatch.setattr(worker, "lock_model_dispatch", AsyncMock())
    monkeypatch.setattr(
        worker, "claim_daily_run", AsyncMock(return_value={"run_id": UUID(RUN)})
    )
    monkeypatch.setattr(
        worker, "create_model_guard", AsyncMock(return_value=str(uuid4()))
    )
    monkeypatch.setattr(worker, "release_model_guard", AsyncMock(return_value=True))
    finish = AsyncMock()
    archive = AsyncMock()
    monkeypatch.setattr(worker, "finish_daily_run", finish)
    monkeypatch.setattr(worker.archive_repository, "enqueue_research_archive", archive)
    monkeypatch.setattr(worker, "write_research_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker, "_write_diagnostic_summary", lambda *_args, **_kwargs: None
    )
    settings = SimpleNamespace(
        app_env="test",
        daily_research_enabled=True,
        daily_research_debug_enabled=True,
        daily_research_codex_path="offline-fixture",
        daily_research_model=None,
        daily_research_timeout_seconds=1200,
    )
    result = await worker.sync_daily_research(
        object(),
        settings,
        at,
        debug_request_id=uuid4(),
        source_batch_id=uuid4(),
    )
    assert result["status"] == "READY"
    assert finish.await_count == 1
    assert finish.await_args.kwargs["status"] == "READY"
    assert finish.await_args.kwargs["error_class"] is None
    assert_observation(finish.await_args.kwargs["payload"], raw)
    assert archive.await_count == 1
    archived_checks = archive.await_args.args[4]
    assert archived_checks[EXECUTION_KEY]["receipt"] == checks[EXECUTION_KEY]["receipt"]
    registry = archive.await_args.kwargs["topic_registry"]
    assert (
        next(row for row in registry["items"] if row["key"] == KEY)[
            "investmentEligible"
        ]
        is False
    )
    worker.run_codex_research.assert_awaited_once()
    assert value == before
