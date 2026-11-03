"""One model call freezes evidence before atomic acceptance/result publication."""

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.integrations.codex_research import CodexOutputError
from backend.macro.daily_research import ResearchValidationError
from tests.unit.test_daily_research_job import NOW, isolate, settings


@dataclass
class Outcome:
    formal_payload: dict | None
    first_error: ResearchValidationError | None = None
    decision: str | None = None

    def to_dict(self):
        return {
            "decision": self.decision
            or ("COMPLETE" if self.formal_payload is not None else "PARTIAL"),
            "accepted": {"observations": [{"summary": "Observation only"}]},
        }


def boundary(monkeypatch, *, complete=True):
    mod, state = isolate(monkeypatch)
    failure = ResearchValidationError(
        "DIRECTION_VALIDATION_FAILED",
        rule_id="DIRECTION_PLAN_SECTOR_UNREVIEWED",
        item_index=0,
    )
    outcome = Outcome(
        {"summary": "formal", "sources": []} if complete else None,
        None if complete else failure,
    )

    def validate(raw, packet, checks, **kwargs):
        assert state["active"] == 0
        state["collector"] = (raw, packet, checks, kwargs)
        return outcome

    async def artifacts(conn, *args, **kwargs):
        state["artifacts"] = args
        state["artifact_kwargs"] = kwargs
        assert not state.get("in_transaction", False)

    async def save_outcome(conn, run_id, value, **kwargs):
        assert state["in_transaction"] is True
        state["outcome"] = (run_id, value, kwargs)

    monkeypatch.setattr(mod, "validate_report_outcome", validate, raising=False)
    monkeypatch.setattr(
        mod,
        "sources_for_verification",
        lambda raw, **kwargs: raw.get("sources", []) if isinstance(raw, dict) else [],
        raising=False,
    )
    monkeypatch.setattr(
        mod,
        "rejected_outcome",
        lambda *args, **kwargs: Outcome(None, decision="REJECTED"),
        raising=False,
    )
    monkeypatch.setattr(
        mod, "save_research_artifacts", AsyncMock(side_effect=artifacts), raising=False
    )
    monkeypatch.setattr(
        mod,
        "save_validation_outcome",
        AsyncMock(side_effect=save_outcome),
        raising=False,
    )
    return mod, state


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_acceptance_and_formal_result_share_transaction_after_separate_artifact_save(
    monkeypatch, complete
):
    mod, state = boundary(monkeypatch, complete=complete)
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == ("READY" if complete else "FAILED")
    assert state["artifacts"][0] == "one"
    assert state["artifacts"][-1] == NOW
    assert state["terminal_in_transactions"] == [True]
    assert state["outcome"][2]["kind"] == "INITIAL"
    assert (state["terminal"][0]["payload"] is not None) is complete
    mod.run_codex_research.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["artifact", "outcome"])
async def test_evidence_or_acceptance_database_failure_raises_without_model_retry(
    monkeypatch, failed
):
    mod, state = boundary(monkeypatch)
    target = (
        mod.save_research_artifacts
        if failed == "artifact"
        else mod.save_validation_outcome
    )
    target.side_effect = ConnectionError("private database message")
    with pytest.raises(ConnectionError):
        await mod.sync_daily_research(object(), settings(), NOW)
    mod.run_codex_research.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()
    if failed == "artifact":
        assert state["terminal"] == []
    else:
        assert "artifacts" in state


@pytest.mark.asyncio
async def test_source_verification_failure_freezes_original_raw_and_null_checks(
    monkeypatch,
):
    mod, state = boundary(monkeypatch)
    mod.verify_research_sources.side_effect = ValueError("private source error")
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "FAILED"
    assert state["artifacts"][3] is None
    assert state["terminal"][0]["payload"] is None
    assert "collector" not in state
    assert state["outcome"][1]["decision"] == "REJECTED"


@pytest.mark.asyncio
async def test_completed_model_decode_failure_keeps_exact_raw_and_does_not_rerun(
    monkeypatch,
):
    mod, state = boundary(monkeypatch, complete=False)
    error = CodexOutputError("invalid report JSON")
    error.raw_output = '{"broken":'
    error.inference_succeeded = True
    mod.run_codex_research.side_effect = error
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "FAILED"
    assert state["artifacts"][2] == '{"broken":'
    assert state["artifacts"][3] is None
    assert "collector" not in state
    assert state["outcome"][1]["decision"] == "REJECTED"
    mod.run_codex_research.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_execution_failure_with_valid_json_cannot_publish_complete_acceptance(
    monkeypatch,
):
    from backend.integrations import codex_research
    from backend.macro.daily_research import validate_report
    from backend.macro.research_acceptance import (
        rejected_outcome,
        validate_report_outcome,
    )

    mod, state = boundary(monkeypatch)
    raw = json.dumps(
        {
            "summary": "public",
            "macroCycle": "unverified",
            "coverage": [],
            "sources": [],
            "events": [],
            "upcoming": [],
            "energy": [],
            "actionablePlans": [],
            "valueDirections": [],
            "industryCycleReviews": [],
        }
    )

    async def failed_cli(args, **kwargs):
        Path(args[args.index("--output-last-message") + 1]).write_text(
            raw,
            encoding="utf-8",
            newline="",
        )
        return 1, b'{"type":"turn.completed"}\n', b"PRIVATE CLI stderr"

    cli = AsyncMock(side_effect=failed_cli)
    monkeypatch.setattr(codex_research, "_run", cli)
    mod.run_codex_research.side_effect = codex_research.run_codex_research
    monkeypatch.setattr(mod, "validate_report", validate_report)
    monkeypatch.setattr(mod, "validate_report_outcome", validate_report_outcome)
    monkeypatch.setattr(mod, "rejected_outcome", rejected_outcome)

    result = await mod.sync_daily_research(object(), settings(), NOW)
    saved = state["outcome"][1]
    assert result["status"] == "FAILED"
    assert result["errorClass"] == "CodexExecutionError"
    assert saved["decision"] == "REJECTED"
    assert saved["formalPublishable"] is False
    assert saved["issues"][0]["ruleId"] == "ACCEPTANCE_SYSTEM_FAILURE"
    assert state["terminal"][0]["payload"] is None
    assert state["artifacts"][2] == raw
    assert state["artifacts"][3] is None
    mod.verify_research_sources.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()
    cli.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_review", [False, True])
async def test_real_collector_keeps_partial_observations_out_of_formal_payload(
    monkeypatch, missing_review
):
    from backend.macro.daily_research import validate_report
    from backend.macro.research_acceptance import (
        validate_report_outcome,
        sources_for_verification,
    )
    from tests.unit.test_industrial_metals_research_regression import case
    from tests.unit.test_daily_research import NOW as REPORT_NOW

    mod, state = boundary(monkeypatch)
    packet, raw, checks = case()
    if missing_review:
        raw["industryCycleReviews"] = raw["industryCycleReviews"][:2]
    monkeypatch.setattr(mod, "validate_report", validate_report)
    monkeypatch.setattr(mod, "validate_report_outcome", validate_report_outcome)
    monkeypatch.setattr(mod, "sources_for_verification", sources_for_verification)
    monkeypatch.setattr(mod, "now_shanghai", lambda: REPORT_NOW)
    mod.run_codex_research.side_effect = None
    mod.run_codex_research.return_value = raw
    mod.verify_research_sources.side_effect = None
    mod.verify_research_sources.return_value = checks
    result = await mod._execute_daily_research(
        object(), settings(), REPORT_NOW, packet=packet
    )
    assert result["status"] == ("FAILED" if missing_review else "READY")
    assert (state["terminal"][0]["payload"] is None) is missing_review
    saved = state["outcome"][1]
    assert saved["decision"] == ("PARTIAL" if missing_review else "COMPLETE")
    assert saved["accepted"]["sources"]
    assert "plans" not in str(saved["accepted"])
    assert state["artifacts"][2] == raw
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_bad_source_shapes_never_reach_the_http_verifier(monkeypatch):
    from backend.macro.research_acceptance import sources_for_verification

    mod, state = boundary(monkeypatch)
    raw = {
        "summary": "public",
        "sources": [None, "bad", {"id": "x", "url": "file:///private"}],
    }
    mod.run_codex_research.side_effect = None
    mod.run_codex_research.return_value = raw
    monkeypatch.setattr(mod, "sources_for_verification", sources_for_verification)
    await mod.sync_daily_research(object(), settings(), NOW)
    assert mod.verify_research_sources.await_args.args[0] == []
    assert state["artifacts"][2]["sources"] == raw["sources"]


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_cancelled_usage_bookkeeping_keeps_returned_raw_without_retry(
    monkeypatch, remote
):
    from backend.integrations import codex_client
    from backend.ai_usage import tracker

    mod, state = boundary(monkeypatch)
    configured = settings()
    raw = {"summary": "retained original", "sources": []}

    async def cancelled_after_operation(_settings, *, operation, **kwargs):
        await operation()
        raise asyncio.CancelledError()

    if remote:
        configured.codex_runner_url = "http://fixture"
        monkeypatch.setattr(
            codex_client, "preflight", AsyncMock(return_value={"version": "fixture"})
        )
        model = AsyncMock(return_value=raw)
        monkeypatch.setattr(codex_client, "_research", model)
        monkeypatch.setattr(tracker, "trace_inference", cancelled_after_operation)
    else:
        model = mod.run_codex_research
        model.side_effect = None
        model.return_value = raw
        monkeypatch.setattr(mod, "trace_inference", cancelled_after_operation)
    with pytest.raises(asyncio.CancelledError):
        await mod.sync_daily_research(object(), configured, NOW)
    assert state["artifacts"][2] == raw
    assert state["artifacts"][3] is None
    assert state["terminal"][0]["status"] == "UNCERTAIN"
    assert state["terminal"][0]["payload"] is None
    assert state["outcome"][1]["decision"] == "REJECTED"
    model.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_parse_rejection_freezes_exact_broken_output(monkeypatch):
    from backend.macro.daily_research import validate_report
    from backend.macro.research_acceptance import (
        rejected_outcome,
        validate_report_outcome,
    )

    mod, state = boundary(monkeypatch)
    error = CodexOutputError("invalid report JSON")
    error.raw_output = '{"broken":'
    error.inference_succeeded = True
    mod.run_codex_research.side_effect = error
    monkeypatch.setattr(mod, "validate_report_outcome", validate_report_outcome)
    monkeypatch.setattr(mod, "rejected_outcome", rejected_outcome)
    monkeypatch.setattr(mod, "validate_report", validate_report)
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "FAILED"
    assert state["artifacts"][2] == error.raw_output
    assert state["outcome"][1]["decision"] == "REJECTED"
    assert state["outcome"][1]["issues"][0]["ruleId"] == "ACCEPTANCE_PARSE_FAILED"
    assert state["terminal"][0]["payload"] is None
    mod.verify_research_sources.assert_not_awaited()
    mod.run_codex_research.assert_awaited_once()


@pytest.mark.asyncio
async def test_oversize_decode_output_keeps_hash_and_size_without_body(monkeypatch):
    mod, state = boundary(monkeypatch)
    error = CodexOutputError("invalid report JSON")
    error.inference_succeeded = True
    error.raw_output = None
    error.raw_output_bytes = 2 * 1024 * 1024 + 1
    error.raw_output_sha256 = "a" * 64
    error.raw_output_omission = "SIZE_LIMIT"
    mod.run_codex_research.side_effect = error
    result = await mod.sync_daily_research(object(), settings(), NOW)
    assert result["status"] == "FAILED"
    assert state["artifacts"][2] is None
    assert state["artifact_kwargs"]["raw_metadata"] == {
        "sha256": "a" * 64,
        "byteLength": 2 * 1024 * 1024 + 1,
        "omissionReason": "SIZE_LIMIT",
    }
    mod.run_codex_research.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_validation_time_is_frozen_before_persistence(monkeypatch):
    mod, state = boundary(monkeypatch)
    clock = [NOW]
    collector = mod.validate_report_outcome

    async def verified(*args, **kwargs):
        clock[0] = NOW + timedelta(minutes=1)
        return {}

    def validate(*args, **kwargs):
        result = collector(*args, **kwargs)
        clock[0] = NOW + timedelta(minutes=2)
        return result

    monkeypatch.setattr(mod, "now_shanghai", lambda: clock[0])
    monkeypatch.setattr(mod, "validate_report_outcome", validate)
    mod.verify_research_sources.side_effect = verified
    await mod.sync_daily_research(object(), settings(), NOW)
    assert state["collector"][3]["completed_at"] == NOW + timedelta(minutes=1)
    assert state["artifacts"][-1] == NOW + timedelta(minutes=1)
    assert state["outcome"][2]["recorded_at"] == NOW + timedelta(minutes=2)
