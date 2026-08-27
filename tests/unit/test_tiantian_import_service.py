"""TDD contracts for Tiantian import preview analysis and orchestration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.common.tenancy import TenantContext
from backend.errors import ForbiddenError, ValidationError_, VersionConflictError
from backend.portfolio.imports.service import (
    ConfirmationItem,
    ExistingBaseline,
    PreviewContext,
    TargetAccount,
    analyze_preview,
    build_ledger_effects,
    cancel_tiantian_import,
    confirm_tiantian_import,
    preview_tiantian_import,
    source_record_hash,
)
from backend.portfolio.imports.tiantian import TiantianPosition, TiantianTransaction


def _position(
    code: str,
    *,
    quantity: str = "100",
    average_cost: str = "1.20",
    market_value: str = "130",
) -> TiantianPosition:
    return TiantianPosition.model_validate(
        {
            "sourceProvider": "TIANTIAN_FUND",
            "sourceRecordId": f"holding-{code}",
            "productCode": code,
            "productName": f"来源名称-{code}",
            "quantity": quantity,
            "averageCost": average_cost,
            "costAmount": str(Decimal(quantity) * Decimal(average_cost)),
            "marketValue": market_value,
            "accountMask": "****5678",
            "capturedAt": "2026-08-28T08:00:00+08:00",
        }
    )


def _transaction(source_id: str, code: str = "110022") -> TiantianTransaction:
    return TiantianTransaction.model_validate(
        {
            "sourceProvider": "TIANTIAN_FUND",
            "sourceId": source_id,
            "occurredAt": "2026-08-20 12:00:00",
            "productCode": code,
            "productName": f"来源名称-{code}",
            "businessType": "买入",
            "normalizedBusinessType": "PURCHASE",
            "confirmationValue": "100",
            "confirmationUnit": "份",
            "status": "成功",
            "category": "FUND_TRADE",
            "classificationReason": "FUND_PRODUCT_CODE",
            "detailRef": source_id,
            "sourceWindowStart": "2020-01-01",
            "sourceWindowEnd": "2026-08-28",
            "sourcePage": 1,
        }
    )


def _package(
    positions: list[TiantianPosition],
    transactions: list[TiantianTransaction] | None = None,
    *,
    source_as_of: datetime = datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
    completeness: str = "COMPLETE",
    review_required: tuple[dict, ...] = (),
):
    return SimpleNamespace(
        positions=tuple(positions),
        transactions=tuple(transactions or []),
        review_required=review_required,
        manifest=SimpleNamespace(
            positions_as_of=source_as_of,
            completeness=completeness,
            external_account_key="1f8a8407-9d91-4a1d-b954-9ad46fbd9585",
            schema_version="wealthsail-tiantian-import/v2",
            collector_version="0.2.0",
            source_window_start=date(2020, 1, 1),
            source_window_end=date(2026, 8, 28),
        ),
        excluded_summary=SimpleNamespace(cash_management=10, non_posting=2),
    )


def _context(**overrides) -> PreviewContext:
    values = {
        "resolved_funds": {
            "110022": {"instrument_id": "fund-1", "canonical_name": "标准基金一"},
            "000001": {"instrument_id": "fund-2", "canonical_name": "标准基金二"},
        },
        "baselines": {},
        "account_conflict_instrument_ids": frozenset(),
        "latest_source_as_of": None,
        "binding_account_id": None,
        "confirmed_transaction_hashes": {},
    }
    values.update(overrides)
    return PreviewContext(**values)


def test_position_comparison_covers_new_unchanged_changed_closed_and_conflict() -> None:
    baselines = {
        "fund-1": ExistingBaseline(
            instrument_id="fund-1",
            product_code="110022",
            product_name="标准基金一",
            quantity=Decimal("100"),
            unit_cost=Decimal("1.20"),
            external_record_id="old-110022",
        ),
        "fund-2": ExistingBaseline(
            instrument_id="fund-2",
            product_code="000001",
            product_name="标准基金二",
            quantity=Decimal("8"),
            unit_cost=Decimal("2.00"),
            external_record_id="old-000001",
        ),
    }

    unchanged_and_closed = analyze_preview(
        _package([_position("110022")]),
        _context(baselines=baselines),
        target_account_id="account-1",
    )
    changed = analyze_preview(
        _package([_position("110022", quantity="101")]),
        _context(baselines=baselines),
        target_account_id="account-1",
    )
    new = analyze_preview(
        _package([_position("110022")]),
        _context(),
        target_account_id="account-1",
    )
    conflict = analyze_preview(
        _package([_position("110022")]),
        _context(
            baselines=baselines,
            account_conflict_instrument_ids=frozenset({"fund-1"}),
        ),
        target_account_id="account-1",
    )

    assert [item.comparison_status for item in unchanged_and_closed.items] == [
        "UNCHANGED",
        "CLOSED",
    ]
    assert changed.items[0].comparison_status == "CHANGED"
    assert new.items[0].comparison_status == "NEW"
    assert conflict.items[0].comparison_status == "CONFLICT"
    assert conflict.can_confirm is False
    assert "ACCOUNT_LEDGER_CONFLICT" in conflict.blocking_errors


def test_preview_blocks_old_snapshot_binding_mismatch_unknown_code_and_hash_conflict() -> None:
    transaction = _transaction("tx-1")
    plan = analyze_preview(
        _package(
            [_position("110022"), _position("999999")],
            [transaction],
            source_as_of=datetime.fromisoformat("2026-08-27T08:00:00+08:00"),
            review_required=({"reason": "UNKNOWN_BUSINESS"},),
        ),
        _context(
            latest_source_as_of=datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
            binding_account_id="account-other",
            confirmed_transaction_hashes={"tx-1": "different-hash"},
        ),
        target_account_id="account-1",
    )

    assert plan.can_confirm is False
    assert set(plan.blocking_errors) >= {
        "SOURCE_SNAPSHOT_OLDER_THAN_CONFIRMED",
        "SOURCE_BINDING_ACCOUNT_CONFLICT",
        "UNKNOWN_FUND_CODE",
        "SOURCE_TRANSACTION_HASH_CONFLICT",
        "REVIEW_REQUIRED_RECORDS",
    }
    unknown = [
        record
        for record in plan.source_records
        if record.record_type == "POSITION_SNAPSHOT"
        and record.external_record_id == "holding-999999"
    ]
    assert len(unknown) == 1
    assert unknown[0].validation_errors == ("UNKNOWN_FUND_CODE",)


def test_partial_history_warns_but_does_not_block_an_otherwise_valid_preview() -> None:
    plan = analyze_preview(
        _package(
            [_position("110022")],
            [_transaction("tx-1")],
            completeness="PARTIAL",
        ),
        _context(),
        target_account_id="account-1",
    )

    assert plan.can_confirm is True
    assert "PARTIAL_HISTORY" in plan.warnings


def test_same_source_transaction_hash_is_classified_as_duplicate() -> None:
    transaction = _transaction("tx-1")
    plan = analyze_preview(
        _package([_position("110022")], [transaction]),
        _context(confirmed_transaction_hashes={"tx-1": source_record_hash(transaction)}),
        target_account_id="account-1",
    )

    assert plan.history_importable == 0
    assert plan.history_duplicated == 1
    assert plan.can_confirm is True


@asynccontextmanager
async def _tx(_pool, _user_id, _tenant_id):
    yield MagicMock()


@pytest.mark.asyncio
async def test_viewer_is_rejected_before_package_parser_or_transaction(tmp_path: Path) -> None:
    package_path = tmp_path / "upload.zip"
    package_path.write_bytes(b"not parsed")
    ctx = TenantContext("viewer-1", "tenant-1", tenant_role="VIEWER")

    with (
        patch("backend.portfolio.imports.service.parse_tiantian_package") as parser,
        patch("backend.portfolio.imports.service.tenant_transaction", new=_tx) as tx,
    ):
        with pytest.raises(ForbiddenError):
            await preview_tiantian_import(
                MagicMock(),
                ctx,
                package_path,
                account_id="account-1",
                account_name=None,
                idempotency_key="preview-1",
            )

    parser.assert_not_called()
    assert tx is _tx


@pytest.mark.asyncio
async def test_parser_failure_opens_no_database_transaction(tmp_path: Path) -> None:
    package_path = tmp_path / "upload.zip"
    package_path.write_bytes(b"bad zip")
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    transaction = MagicMock()

    with (
        patch(
            "backend.portfolio.imports.service.parse_tiantian_package",
            side_effect=ValidationError_("invalid package"),
        ),
        patch("backend.portfolio.imports.service.tenant_transaction", transaction),
    ):
        with pytest.raises(ValidationError_):
            await preview_tiantian_import(
                MagicMock(),
                ctx,
                package_path,
                account_id="account-1",
                account_name=None,
                idempotency_key="preview-1",
            )

    transaction.assert_not_called()


@pytest.mark.asyncio
async def test_valid_preview_persists_only_a_recoverable_draft(tmp_path: Path) -> None:
    package_path = tmp_path / "upload.zip"
    package_path.write_bytes(b"synthetic")
    package = _package([_position("110022")], [_transaction("tx-1")])
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    target = TargetAccount(
        account_id="account-1",
        binding_id=None,
        binding_status=None,
        binding_account_id=None,
    )
    persist = AsyncMock(
        return_value={
            "batch_id": "batch-1",
            "binding_id": "binding-1",
            "item_ids": ["item-1"],
        }
    )

    with (
        patch("backend.portfolio.imports.service.parse_tiantian_package", return_value=package),
        patch("backend.portfolio.imports.service.tenant_transaction", new=_tx),
        patch(
            "backend.portfolio.imports.service.repository.find_preview_by_idempotency",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.portfolio.imports.service.repository.resolve_target_account",
            AsyncMock(return_value=target),
        ),
        patch(
            "backend.portfolio.imports.service.repository.load_preview_context",
            AsyncMock(return_value=_context()),
        ),
        patch(
            "backend.portfolio.imports.service.repository.account_fact_hash",
            AsyncMock(return_value="b" * 64),
        ),
        patch("backend.portfolio.imports.service.repository.persist_preview", persist),
        patch("backend.portfolio.imports.service.write_audit", AsyncMock()) as audit,
    ):
        result = await preview_tiantian_import(
            MagicMock(),
            ctx,
            package_path,
            account_id="account-1",
            account_name=None,
            idempotency_key="preview-1",
        )

    assert result["batchId"] == "batch-1"
    assert result["status"] == "DRAFT"
    assert result["sourceAsOf"] == "2026-08-28T08:00:00+08:00"
    assert result["summary"]["total"] == 1
    assert result["summary"]["new"] == 1
    assert result["package"]["sourceWindowStart"] == "2020-01-01"
    assert result["package"]["sourceWindowEnd"] == "2026-08-28"
    assert result["excludedSummary"] == {
        "cashManagement": 10,
        "nonPosting": 2,
    }
    assert result["historySummary"]["reviewRequired"] == 0
    assert result["canConfirm"] is True
    assert result["positions"][0]["itemId"] == "item-1"
    persist.assert_awaited_once()
    persisted_plan = persist.await_args.kwargs["plan"]
    assert persisted_plan.items[0].comparison_status == "NEW"
    assert audit.await_args.kwargs["details"] == {
        "positionCount": 1,
        "historyCount": 1,
        "blockingErrorCount": 0,
        "warningCount": 0,
    }


@pytest.mark.asyncio
async def test_account_target_fields_are_mutually_exclusive(tmp_path: Path) -> None:
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")

    with pytest.raises(ValidationError_, match="二选一"):
        await preview_tiantian_import(
            MagicMock(),
            ctx,
            tmp_path / "upload.zip",
            account_id="account-1",
            account_name="另一个账户",
            idempotency_key="preview-1",
        )


def _confirmation_item(status: str, *, item_id: str = "item-1") -> ConfirmationItem:
    return ConfirmationItem(
        item_id=item_id,
        instrument_id="fund-1",
        quantity=Decimal("100" if status != "CLOSED" else "0"),
        unit_cost=Decimal("1.20"),
        trade_date=date(2026, 8, 28),
        comparison_status=status,
    )


def test_effect_plan_replaces_only_changed_funds_and_never_models_source_trades() -> None:
    effects = build_ledger_effects(
        [
            _confirmation_item("NEW", item_id="new"),
            _confirmation_item("UNCHANGED", item_id="same"),
            _confirmation_item("CHANGED", item_id="changed"),
            _confirmation_item("CLOSED", item_id="closed"),
        ],
        {
            "same": "old-event-same",
            "changed": "old-event-changed",
            "closed": "old-event-closed",
        },
    )

    assert [(effect.item_id, effect.effect_type) for effect in effects] == [
        ("new", "ADJUSTMENT"),
        ("changed", "REVERSAL"),
        ("changed", "ADJUSTMENT"),
        ("closed", "REVERSAL"),
    ]
    assert not {effect.effect_type for effect in effects} & {
        "BUY",
        "SELL",
        "DIVIDEND_CASH",
        "DIVIDEND_REINVEST",
    }


@pytest.mark.asyncio
async def test_first_confirmation_rebuilds_inside_transaction_and_returns_pending_metrics() -> None:
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    confirm = AsyncMock(
        return_value={
            "batch_id": "batch-1",
            "version": 2,
            "position_count": 1,
            "history_imported": 3,
            "history_duplicated": 1,
            "event_ids": ["event-1"],
            "already_applied": False,
        }
    )

    with (
        patch("backend.portfolio.imports.service.tenant_transaction", new=_tx),
        patch("backend.portfolio.imports.service.repository.confirm_draft", confirm),
        patch(
            "backend.portfolio.service._recompute_projections",
            AsyncMock(),
        ) as recompute,
        patch("backend.portfolio.imports.service.write_audit", AsyncMock()) as audit,
    ):
        result = await confirm_tiantian_import(
            MagicMock(),
            ctx,
            "batch-1",
            expected_version=1,
            decisions={"item-1": date(2025, 1, 1)},
            confirm_empty=False,
            idempotency_key="confirm-1",
        )

    assert result == {
        "batchId": "batch-1",
        "status": "CONFIRMED",
        "version": 2,
        "metricsStatus": "PENDING",
        "positionCount": 1,
        "historyImported": 3,
        "historyDuplicated": 1,
    }
    confirm.assert_awaited_once()
    recompute.assert_awaited_once()
    assert audit.await_args.kwargs["details"] == {
        "positionCount": 1,
        "historyImported": 3,
        "historyDuplicated": 1,
        "ledgerEffectCount": 1,
    }


@pytest.mark.asyncio
async def test_replayed_confirmation_does_not_rebuild_or_write_a_second_audit() -> None:
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    confirm = AsyncMock(
        return_value={
            "batch_id": "batch-1",
            "version": 2,
            "position_count": 1,
            "history_imported": 3,
            "history_duplicated": 1,
            "event_ids": ["event-1"],
            "already_applied": True,
        }
    )

    with (
        patch("backend.portfolio.imports.service.tenant_transaction", new=_tx),
        patch("backend.portfolio.imports.service.repository.confirm_draft", confirm),
        patch("backend.portfolio.service._recompute_projections", AsyncMock()) as recompute,
        patch("backend.portfolio.imports.service.write_audit", AsyncMock()) as audit,
    ):
        await confirm_tiantian_import(
            MagicMock(),
            ctx,
            "batch-1",
            expected_version=1,
            decisions={},
            confirm_empty=False,
            idempotency_key="confirm-1",
        )

    recompute.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_cancellation_is_terminal_and_has_no_projection_side_effect() -> None:
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    cancel = AsyncMock(
        return_value={"batch_id": "batch-1", "version": 2, "already_applied": False}
    )

    with (
        patch("backend.portfolio.imports.service.tenant_transaction", new=_tx),
        patch("backend.portfolio.imports.service.repository.cancel_draft", cancel),
        patch("backend.portfolio.imports.service.write_audit", AsyncMock()) as audit,
    ):
        result = await cancel_tiantian_import(
            MagicMock(),
            ctx,
            "batch-1",
            expected_version=1,
            idempotency_key="cancel-1",
        )

    assert result == {"batchId": "batch-1", "status": "CANCELLED", "version": 2}
    assert audit.await_args.kwargs["details"] == {"previousVersion": 1}


@pytest.mark.asyncio
async def test_confirmation_failure_writes_neither_projection_nor_audit() -> None:
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")

    with (
        patch("backend.portfolio.imports.service.tenant_transaction", new=_tx),
        patch(
            "backend.portfolio.imports.service.repository.confirm_draft",
            AsyncMock(side_effect=VersionConflictError("stale")),
        ),
        patch("backend.portfolio.service._recompute_projections", AsyncMock()) as recompute,
        patch("backend.portfolio.imports.service.write_audit", AsyncMock()) as audit,
    ):
        with pytest.raises(VersionConflictError):
            await confirm_tiantian_import(
                MagicMock(),
                ctx,
                "batch-1",
                expected_version=1,
                decisions={},
                confirm_empty=False,
                idempotency_key="confirm-1",
            )

    recompute.assert_not_awaited()
    audit.assert_not_awaited()
