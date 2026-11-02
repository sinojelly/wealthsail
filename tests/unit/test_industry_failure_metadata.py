"""Standard server industry codes remain bounded, durable and administrator-only."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.macro.daily_research import ResearchValidationError
from backend.macro.daily_repository import finish_daily_run, read_daily_research
from backend.macro.research_debug_repository import request_dto
from backend.macro.research_diagnostics import sanitize_failure_diagnostics
from tests.unit.test_daily_repository import NOW, run
from tests.unit.test_research_debug_repository import row


SAFE_CODES = ["NONFERROUS", "RARE_EARTH"]
RULE = "DIRECTION_PLAN_SECTOR_UNREVIEWED"


@pytest.mark.parametrize(
    "rule",
    [
        "DIRECTION_DECLARED_INDUSTRY_UNKNOWN",
        RULE,
        "INDUSTRY_REVIEW_BUDGET_EXCEEDED",
    ],
)
def test_new_diagnostic_rules_are_explicitly_allowlisted(rule):
    error = ResearchValidationError("DIRECTION_VALIDATION_FAILED", rule_id=rule)
    assert error.rule_id == rule
    assert sanitize_failure_diagnostics(rule_id=rule)["failure_rule_id"] == rule


def test_exception_and_sanitizer_only_preserve_unique_standard_industry_codes():
    value = ["RARE_EARTH", "PRIVATE_MODEL_SECTOR", "NONFERROUS", "RARE_EARTH", {}]
    error = ResearchValidationError(
        "DIRECTION_VALIDATION_FAILED", missing_industry_codes=value
    )
    assert error.missing_industry_codes == SAFE_CODES
    actual = sanitize_failure_diagnostics(missing_industry_codes=value)
    assert actual["failure_industry_codes"] == SAFE_CODES


@pytest.mark.parametrize("value", [None, [], "NONFERROUS", {}, True, ["PRIVATE"]])
def test_absent_or_unsafe_industry_codes_keep_the_legacy_four_key_shape(value):
    actual = sanitize_failure_diagnostics(missing_industry_codes=value)
    assert actual == {
        "failure_stage": None,
        "failure_reason_code": None,
        "failure_rule_id": None,
        "failure_item_index": None,
    }


def test_industry_code_list_is_sorted_and_bounded_after_deduplication():
    from backend.macro.industry_contract import STANDARD_INDUSTRY_CODES

    values = sorted(STANDARD_INDUSTRY_CODES, reverse=True)
    assert len(values) > 10
    actual = sanitize_failure_diagnostics(missing_industry_codes=[*values, *values])
    assert actual["failure_industry_codes"] == sorted(values)[:10]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "UNCERTAIN", "READY"])
async def test_terminal_insert_sanitizes_codes_and_ready_always_stores_null(status):
    conn = AsyncMock()
    await finish_daily_run(
        conn,
        "run-1",
        NOW,
        status=status,
        payload={"summary": "ready"} if status == "READY" else None,
        failure_industry_codes=[*SAFE_CODES, "MODEL_PRIVATE"],
    )
    sql, *args = conn.execute.await_args.args
    assert "failure_industry_codes" in sql
    assert args[5] == (None if status == "READY" else SAFE_CODES)
    assert "ON CONFLICT(run_id) DO NOTHING" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("include", [False, True])
async def test_main_dto_requires_explicit_admin_projection_for_industry_codes(
    monkeypatch, include
):
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=None),
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(status="FAILED", recorded_at=NOW, failure_industry_codes=SAFE_CODES),
        None,
    ]
    actual = await read_daily_research(
        conn, NOW, include_failure_industry_codes=include
    )
    assert actual["failureIndustryCodes"] == (SAFE_CODES if include else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline_run_id", ["run-1", "other-run", None])
async def test_main_dto_only_reads_codes_from_its_displayed_terminal_run(
    monkeypatch, pipeline_run_id
):
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(
            return_value={
                "state": "FAILED",
                "researchRunId": pipeline_run_id,
                "startedAt": NOW,
                "finishedAt": NOW,
                "errorClass": "ValueError",
            }
        ),
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        run(status="FAILED", recorded_at=NOW, failure_industry_codes=SAFE_CODES),
        None,
    ]
    actual = await read_daily_research(conn, NOW, include_failure_industry_codes=True)
    assert actual["failureIndustryCodes"] == (
        SAFE_CODES if pipeline_run_id == "run-1" else None
    )


@pytest.mark.parametrize("status", ["FAILED", "READY", None])
def test_manual_dto_keeps_codes_with_its_own_matching_failure_only(status):
    actual = request_dto(
        row(
            status="FAILED",
            completed_at=NOW,
            report_run_id="own-run",
            report_status=status,
            failure_industry_codes=[*SAFE_CODES, "MODEL_PRIVATE"],
        ),
        NOW,
    )
    assert actual["failureIndustryCodes"] == (SAFE_CODES if status == "FAILED" else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("manage", [False, True])
async def test_opportunity_context_passes_only_its_management_capability(
    monkeypatch, manage
):
    from tests.unit.test_opportunity_context import dependencies

    module = dependencies(monkeypatch)
    conn = AsyncMock()
    conn.fetch.return_value = []
    await module.get_context(
        conn,
        "tenant-a",
        "actor-a",
        fund_code="000001",
        fund_market="HK",
        now=NOW,
        can_manage_operations=manage,
    )
    assert module.read_daily_research.await_args.kwargs[
        "include_failure_industry_codes"
    ] is manage


@pytest.mark.asyncio
async def test_worker_log_terminal_and_summary_preserve_only_safe_industry_codes(
    monkeypatch, caplog, tmp_path
):
    from tests.unit.test_daily_research_job import NOW as JOB_NOW, isolate, settings

    module, state = isolate(monkeypatch)
    configured = settings()
    configured.daily_research_audit_dir = str(tmp_path)
    identity = uuid4()
    monkeypatch.setattr(
        module, "claim_daily_run", AsyncMock(return_value={"run_id": str(identity)})
    )

    def invalid(*args, **kwargs):
        raise ResearchValidationError(
            "DIRECTION_VALIDATION_FAILED",
            "PRIVATE-MESSAGE",
            rule_id=RULE,
            item_index=1,
            missing_industry_codes=[*SAFE_CODES, "PRIVATE_MODEL_SECTOR"],
        )

    monkeypatch.setattr(module, "validate_report", invalid)
    with caplog.at_level("WARNING"):
        result = await module.sync_daily_research(object(), configured, JOB_NOW)
    assert result["status"] == "FAILED"
    assert state["terminal"][0]["failure_industry_codes"] == SAFE_CODES
    assert "NONFERROUS" in caplog.text and "RARE_EARTH" in caplog.text
    assert "PRIVATE" not in caplog.text
    summary = json.loads((tmp_path / f"{identity}.diagnostic.json").read_text("utf-8"))
    assert summary["industryCodes"] == SAFE_CODES


def test_audit_summary_sanitizes_industry_codes_independently(tmp_path):
    from backend.macro.research_audit import write_research_diagnostic

    identity = uuid4()
    assert write_research_diagnostic(
        SimpleNamespace(daily_research_audit_dir=str(tmp_path)),
        identity,
        stage="REPORT_VALIDATION",
        error_class="ResearchValidationError",
        report_status="FAILED",
        completed_at=NOW,
        missing_industry_codes=["NONFERROUS", "MODEL_PRIVATE"],
    )
    payload = json.loads((tmp_path / f"{identity}.diagnostic.json").read_text("utf-8"))
    assert payload["industryCodes"] == ["NONFERROUS"]
