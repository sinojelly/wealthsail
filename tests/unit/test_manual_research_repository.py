"""Manual requests freeze their mode and never borrow another run's diagnosis."""

from importlib import import_module, util
from unittest.mock import AsyncMock

import pytest

from backend.macro import research_debug_repository as repo
from test_research_debug_repository import NOW, row


def guards():
    assert util.find_spec("backend.macro.model_dispatch_repository"), (
        "durable model gate missing"
    )
    return import_module("backend.macro.model_dispatch_repository")


def test_manual_request_dto_retains_mode_and_own_terminal_diagnostics():
    actual = repo.request_dto(
        row(
            request_mode="MANUAL",
            status="FAILED",
            completed_at=NOW,
            report_run_id="own-run",
            report_status="FAILED",
            failure_stage="REPORT_VALIDATION",
            failure_reason_code="DIRECTION_VALIDATION_FAILED",
            failure_rule_id="INDUSTRY_REVIEW_DUPLICATE",
            failure_item_index=0,
        ),
        NOW,
    )
    assert actual["requestMode"] == "MANUAL"
    assert actual["failureStage"] == "REPORT_VALIDATION"
    assert actual["failureReasonCode"] == "DIRECTION_VALIDATION_FAILED"
    assert actual["failureRuleId"] == "INDUSTRY_REVIEW_DUPLICATE"
    assert actual["failureItemIndex"] == 0


@pytest.mark.parametrize("status", [None, "READY"])
def test_request_dto_does_not_borrow_unmatched_or_ready_run_diagnostics(status):
    actual = repo.request_dto(
        row(
            status="FAILED",
            completed_at=NOW,
            report_run_id="own-run",
            report_status=status,
            failure_stage="REPORT_VALIDATION",
            failure_reason_code="TIMEOUT",
            failure_item_index=5,
        ),
        NOW,
    )
    assert actual["requestMode"] == "DEBUG"
    assert all(
        actual[key] is None
        for key in (
            "failureStage",
            "failureReasonCode",
            "failureRuleId",
            "failureItemIndex",
        )
    )


@pytest.mark.asyncio
async def test_unknown_request_mode_is_rejected_before_any_query():
    conn = AsyncMock()
    with pytest.raises(ValueError):
        await repo.enqueue_refresh(
            conn, "tenant", "actor", "command", NOW, request_mode="UNTRUSTED"
        )
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_latest_request_can_select_enabled_saved_modes():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    assert (
        await repo.latest_refresh(
            conn, "tenant", "actor", NOW, allowed_modes=["MANUAL"]
        )
        is None
    )
    assert conn.fetchrow.await_args.args[-1] == ["MANUAL"]


@pytest.mark.asyncio
async def test_empty_allowed_modes_never_claim_work():
    conn = AsyncMock()
    assert await repo.claim_refresh(conn, NOW, allowed_modes=[]) is None
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_lock_requires_a_transaction_before_trying_to_lock():
    conn = AsyncMock()
    conn.is_in_transaction = lambda: False
    with pytest.raises(RuntimeError, match="transaction"):
        await guards().lock_model_dispatch(conn)
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistent_guard_blocks_dispatch_independently_of_clock_or_request_window():
    conn = AsyncMock()
    conn.is_in_transaction = lambda: True
    conn.fetchval.return_value = True
    mod = guards()
    with pytest.raises(mod.ResearchModelBusy):
        await mod.lock_model_dispatch(conn)
