"""Tiantian Fund ZIP preview orchestration and pure snapshot comparison."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import asyncpg

from backend.common.database import tenant_transaction
from backend.dependencies import write_audit
from backend.errors import (
    IdempotencyConflictError,
    ValidationError_,
    VersionConflictError,
)
from backend.portfolio import repository as portfolio_repository
from backend.portfolio import service as portfolio_service
from backend.portfolio.imports import repository
from backend.portfolio.imports.analytics import ShareTransaction, derive_holding_start
from backend.portfolio.imports.package import ImportLimits, parse_tiantian_package
from backend.portfolio.imports.tiantian import (
    TiantianPackage,
    TiantianPosition,
    TiantianTransaction,
)

PARSER_VERSION = "tiantian-package/v2"


@dataclass(frozen=True, slots=True)
class ExistingBaseline:
    instrument_id: str
    product_code: str
    product_name: str
    quantity: Decimal
    unit_cost: Decimal
    external_record_id: str


@dataclass(frozen=True, slots=True)
class PreviewContext:
    resolved_funds: Mapping[str, Mapping[str, str]]
    baselines: Mapping[str, ExistingBaseline]
    account_conflict_instrument_ids: frozenset[str]
    latest_source_as_of: datetime | None
    binding_account_id: str | None
    confirmed_transaction_hashes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class TargetAccount:
    account_id: str
    binding_id: str | None
    binding_status: str | None
    binding_account_id: str | None
    account_display_name: str = "天天基金账户"


@dataclass(frozen=True, slots=True)
class PreviewItem:
    instrument_id: str | None
    product_code: str
    product_name: str
    external_record_id: str
    quantity: Decimal
    unit_cost: Decimal
    market_value: Decimal | None
    source_nav: Decimal | None
    source_nav_date: date | None
    comparison_status: str
    candidate_holding_started_on: date | None
    holding_start_status: str
    source_payload: dict[str, Any]
    holding_start_method: str = "INSUFFICIENT_DATA"
    holding_start_confidence: str = "NONE"
    holding_start_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreviewSourceRecord:
    record_type: str
    external_record_id: str
    instrument_id: str | None
    occurred_at: datetime | None
    classification: str
    normalized_payload: dict[str, Any]
    content_hash: str
    validation_status: str
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreviewPlan:
    items: tuple[PreviewItem, ...]
    source_records: tuple[PreviewSourceRecord, ...]
    warnings: tuple[str, ...]
    blocking_errors: tuple[str, ...]
    history_importable: int
    history_duplicated: int
    can_confirm: bool

    @property
    def summary(self) -> dict[str, int]:
        statuses = (item.comparison_status for item in self.items)
        counts = {
            status: 0
            for status in ("NEW", "UNCHANGED", "CHANGED", "CLOSED", "CONFLICT")
        }
        for status in statuses:
            counts[status] += 1
        return {key.lower(): value for key, value in counts.items()}


@dataclass(frozen=True, slots=True)
class ConfirmationItem:
    item_id: str
    instrument_id: str
    quantity: Decimal
    unit_cost: Decimal
    trade_date: date
    comparison_status: str


@dataclass(frozen=True, slots=True)
class LedgerEffect:
    item_id: str
    effect_type: str
    previous_event_id: str | None


def build_ledger_effects(
    items: list[ConfirmationItem],
    active_event_ids: Mapping[str, str],
) -> tuple[LedgerEffect, ...]:
    """Map reviewed snapshot comparisons to baseline-only ledger effects."""
    effects: list[LedgerEffect] = []
    for item in items:
        previous = active_event_ids.get(item.item_id)
        if item.comparison_status == "CONFLICT":
            raise ValidationError_("冲突持仓不能确认")
        if item.comparison_status == "NEW":
            if previous is not None:
                raise VersionConflictError("NEW 持仓已出现有效来源基线，请重新预览")
            effects.append(LedgerEffect(item.item_id, "ADJUSTMENT", None))
        elif item.comparison_status == "UNCHANGED":
            if previous is None:
                raise VersionConflictError("UNCHANGED 持仓的有效来源基线不存在")
        elif item.comparison_status == "CHANGED":
            if previous is None:
                raise VersionConflictError("CHANGED 持仓的旧来源基线不存在")
            effects.extend(
                (
                    LedgerEffect(item.item_id, "REVERSAL", previous),
                    LedgerEffect(item.item_id, "ADJUSTMENT", None),
                )
            )
        elif item.comparison_status == "CLOSED":
            if previous is None:
                raise VersionConflictError("CLOSED 持仓的旧来源基线不存在")
            effects.append(LedgerEffect(item.item_id, "REVERSAL", previous))
        else:
            raise ValidationError_(f"不支持的持仓比较状态：{item.comparison_status}")
    return tuple(effects)


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return value
    return dict(vars(value))


def source_record_hash(value: Any) -> str:
    payload = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _append_unique(target: list[str], value: str) -> None:
    if value not in target:
        target.append(value)


def _parse_occurred_at(value: str) -> datetime:
    normalized = value.strip().replace(" ", "T", 1)
    return datetime.fromisoformat(normalized)


def _share_quantity(transaction: TiantianTransaction) -> Decimal | None:
    if transaction.normalized_business_type == "DIVIDEND_CASH":
        return None
    if transaction.confirmation_unit == "份":
        return transaction.confirmation_value
    if transaction.application_unit == "份":
        return transaction.application_value
    return None


def _account_mask_suffix(value: object) -> str | None:
    match = re.search(r"\*{4}(\d{4})\s*$", str(value or "").strip())
    return match.group(1) if match else None


def _portfolio_activity_fallback(
    position: TiantianPosition,
    review_required: tuple[dict[str, Any], ...],
    base,
):
    """Estimate a component start only from same-account portfolio evidence.

    Aggregate portfolio orders do not prove which component entered on which
    date. They are therefore a visible, low/medium-confidence fallback and
    never override a reconciled per-fund replay or a share mismatch.
    """
    # Even incomplete per-fund share history identifies the component more
    # directly than an aggregate portfolio order. Use aggregate evidence only
    # when no component-level date can be derived at all.
    if base.status != "INSUFFICIENT_DATA":
        return base
    account_suffix = _account_mask_suffix(position.account_mask)
    if account_suffix is None:
        return base

    activities: list[tuple[datetime, dict[str, Any]]] = []
    for raw in review_required:
        if raw.get("classificationReason") != "PORTFOLIO_ACTIVITY":
            continue
        if _account_mask_suffix(raw.get("accountMask")) != account_suffix:
            continue
        try:
            occurred_at = _parse_occurred_at(str(raw.get("occurredAt") or ""))
        except ValueError:
            continue
        if occurred_at.date() > position.captured_at.date():
            continue
        activities.append((occurred_at, raw))

    purchases = [
        (occurred_at, raw)
        for occurred_at, raw in activities
        if raw.get("normalizedBusinessType") == "PURCHASE"
        and "组合" in str(raw.get("businessType") or "")
    ]
    rebalances = [
        (occurred_at, raw)
        for occurred_at, raw in activities
        if "调仓" in str(raw.get("businessType") or "")
    ]
    if purchases:
        started_on = min(occurred_at.date() for occurred_at, _ in purchases)
        rebalance_only = False
    elif rebalances:
        # A rebalance proves only that the component may have been present or
        # introduced by that date. It is still more useful than a blank field,
        # but remains explicitly low-confidence and editable.
        started_on = min(occurred_at.date() for occurred_at, _ in rebalances)
        rebalance_only = True
    else:
        return base
    has_rebalance = bool(rebalances)
    reasons = [
        "PORTFOLIO_ACTIVITY_ACCOUNT_MATCH",
        "PORTFOLIO_COMPONENT_DETAILS_UNAVAILABLE",
    ]
    if has_rebalance:
        reasons.append("PORTFOLIO_REBALANCE_PRESENT")
    if rebalance_only:
        reasons.append("PORTFOLIO_REBALANCE_ONLY")
    confidence = "LOW" if has_rebalance else "MEDIUM"
    return replace(
        base,
        started_on=started_on,
        holding_days=(position.captured_at.date() - started_on).days + 1,
        status="PARTIAL_HISTORY",
        reason_codes=tuple(reasons),
        method="PORTFOLIO_ACTIVITY_ESTIMATE",
        confidence=confidence,
    )


def _holding_result(
    position: TiantianPosition,
    transactions: tuple[TiantianTransaction, ...],
    review_required: tuple[dict[str, Any], ...] = (),
):
    events = [
        ShareTransaction(
            occurred_at=_parse_occurred_at(transaction.occurred_at),
            source_id=transaction.source_id,
            business_type=transaction.normalized_business_type,
            share_quantity=_share_quantity(transaction),
        )
        for transaction in transactions
        if transaction.product_code == position.product_code
        and transaction.normalized_business_type != "NON_POSITION"
    ]
    base = derive_holding_start(
        events,
        position.quantity,
        as_of=position.captured_at.date(),
    )
    return _portfolio_activity_fallback(position, review_required, base)


def _position_payload(position: TiantianPosition) -> dict[str, Any]:
    return position.model_dump(mode="json", by_alias=True)


def analyze_preview(
    package: TiantianPackage | Any,
    context: PreviewContext,
    *,
    target_account_id: str,
) -> PreviewPlan:
    """Compare an authoritative source snapshot without producing ledger facts."""
    warnings: list[str] = []
    blocking: list[str] = []
    items: list[PreviewItem] = []
    source_records: list[PreviewSourceRecord] = []
    incoming_instrument_ids: set[str] = set()

    if context.binding_account_id not in (None, target_account_id):
        _append_unique(blocking, "SOURCE_BINDING_ACCOUNT_CONFLICT")
    if (
        context.latest_source_as_of is not None
        and package.manifest.positions_as_of < context.latest_source_as_of
    ):
        _append_unique(blocking, "SOURCE_SNAPSHOT_OLDER_THAN_CONFIRMED")
    if package.manifest.completeness == "PARTIAL":
        _append_unique(warnings, "PARTIAL_HISTORY")

    for position in package.positions:
        resolved = context.resolved_funds.get(position.product_code)
        if resolved is None:
            _append_unique(blocking, "UNKNOWN_FUND_CODE")
            payload = _position_payload(position)
            source_records.append(
                PreviewSourceRecord(
                    record_type="POSITION_SNAPSHOT",
                    external_record_id=position.source_record_id,
                    instrument_id=None,
                    occurred_at=position.captured_at,
                    classification="UNKNOWN_FUND_CODE",
                    normalized_payload=payload,
                    content_hash=source_record_hash(payload),
                    validation_status="INVALID",
                    validation_errors=("UNKNOWN_FUND_CODE",),
                )
            )
            holding = _holding_result(
                position, package.transactions, package.review_required
            )
            items.append(
                PreviewItem(
                    instrument_id=None,
                    product_code=position.product_code,
                    product_name=position.product_name,
                    external_record_id=position.source_record_id,
                    quantity=position.quantity,
                    unit_cost=position.average_cost,
                    market_value=position.market_value,
                    source_nav=position.latest_nav,
                    source_nav_date=position.latest_nav_date,
                    comparison_status="CONFLICT",
                    candidate_holding_started_on=holding.started_on,
                    holding_start_status=holding.status,
                    source_payload=payload,
                    holding_start_method=holding.method,
                    holding_start_confidence=holding.confidence,
                    holding_start_reasons=holding.reason_codes,
                )
            )
            continue
        instrument_id = str(resolved["instrument_id"])
        incoming_instrument_ids.add(instrument_id)
        if (
            position.quantity <= 0
            or position.average_cost < 0
            or position.market_value < 0
        ):
            _append_unique(blocking, "INVALID_POSITION_VALUE")
            payload = _position_payload(position)
            source_records.append(
                PreviewSourceRecord(
                    record_type="POSITION_SNAPSHOT",
                    external_record_id=position.source_record_id,
                    instrument_id=instrument_id,
                    occurred_at=position.captured_at,
                    classification="INVALID_POSITION_VALUE",
                    normalized_payload=payload,
                    content_hash=source_record_hash(payload),
                    validation_status="INVALID",
                    validation_errors=("INVALID_POSITION_VALUE",),
                )
            )
            continue

        baseline = context.baselines.get(instrument_id)
        if instrument_id in context.account_conflict_instrument_ids:
            comparison = "CONFLICT"
            _append_unique(blocking, "ACCOUNT_LEDGER_CONFLICT")
        elif baseline is None:
            comparison = "NEW"
        elif (
            baseline.quantity == position.quantity
            and baseline.unit_cost == position.average_cost
        ):
            comparison = "UNCHANGED"
        else:
            comparison = "CHANGED"

        holding = _holding_result(
            position, package.transactions, package.review_required
        )
        if holding.status == "PARTIAL_HISTORY":
            _append_unique(warnings, "PARTIAL_HISTORY")
        elif holding.status == "INSUFFICIENT_DATA":
            _append_unique(warnings, "HOLDING_START_INSUFFICIENT")
        elif holding.status == "MISMATCH":
            _append_unique(blocking, "HOLDING_SHARE_MISMATCH")
        if holding.method == "PORTFOLIO_ACTIVITY_ESTIMATE":
            _append_unique(warnings, "PORTFOLIO_ACTIVITY_ESTIMATE")

        items.append(
            PreviewItem(
                instrument_id=instrument_id,
                product_code=position.product_code,
                product_name=str(resolved["canonical_name"]),
                external_record_id=position.source_record_id,
                quantity=position.quantity,
                unit_cost=position.average_cost,
                market_value=position.market_value,
                source_nav=position.latest_nav,
                source_nav_date=position.latest_nav_date,
                comparison_status=comparison,
                candidate_holding_started_on=holding.started_on,
                holding_start_status=holding.status,
                source_payload=_position_payload(position),
                holding_start_method=holding.method,
                holding_start_confidence=holding.confidence,
                holding_start_reasons=holding.reason_codes,
            )
        )

    for baseline in context.baselines.values():
        if baseline.instrument_id in incoming_instrument_ids:
            continue
        items.append(
            PreviewItem(
                instrument_id=baseline.instrument_id,
                product_code=baseline.product_code,
                product_name=baseline.product_name,
                external_record_id=f"closed:{baseline.external_record_id}",
                quantity=Decimal("0"),
                unit_cost=baseline.unit_cost,
                market_value=Decimal("0"),
                source_nav=None,
                source_nav_date=None,
                comparison_status="CLOSED",
                candidate_holding_started_on=None,
                holding_start_status="INSUFFICIENT_DATA",
                source_payload={
                    "productCode": baseline.product_code,
                    "reason": "ABSENT_FROM_AUTHORITATIVE_SNAPSHOT",
                },
                holding_start_method="INSUFFICIENT_DATA",
                holding_start_confidence="NONE",
                holding_start_reasons=("POSITION_CLOSED",),
            )
        )

    history_importable = 0
    history_duplicated = 0
    for transaction in package.transactions:
        transaction_hash = source_record_hash(transaction)
        confirmed_hash = context.confirmed_transaction_hashes.get(transaction.source_id)
        if confirmed_hash is None:
            classification = "IMPORTABLE"
            validation_status = "VALID"
            errors: tuple[str, ...] = ()
            history_importable += 1
        elif confirmed_hash == transaction_hash:
            classification = "DUPLICATE"
            validation_status = "VALID"
            errors = ()
            history_duplicated += 1
        else:
            classification = "SOURCE_CONFLICT"
            validation_status = "INVALID"
            errors = ("SOURCE_TRANSACTION_HASH_CONFLICT",)
            _append_unique(blocking, "SOURCE_TRANSACTION_HASH_CONFLICT")
        resolved = context.resolved_funds.get(transaction.product_code)
        if resolved is None:
            validation_status = "INVALID"
            errors = tuple(dict.fromkeys((*errors, "UNKNOWN_FUND_CODE")))
            _append_unique(blocking, "UNKNOWN_FUND_CODE")
        source_records.append(
            PreviewSourceRecord(
                record_type="FUND_TRANSACTION",
                external_record_id=transaction.source_id,
                instrument_id=(str(resolved["instrument_id"]) if resolved else None),
                occurred_at=_parse_occurred_at(transaction.occurred_at),
                classification=classification,
                normalized_payload=transaction.model_dump(mode="json", by_alias=True),
                content_hash=transaction_hash,
                validation_status=validation_status,
                validation_errors=errors,
            )
        )

    for index, review_record in enumerate(package.review_required, start=1):
        payload = dict(review_record)
        is_portfolio_activity = (
            payload.get("classificationReason") == "PORTFOLIO_ACTIVITY"
        )
        try:
            review_occurred_at = _parse_occurred_at(
                str(payload.get("occurredAt") or "")
            )
        except ValueError:
            review_occurred_at = None
        source_records.append(
            PreviewSourceRecord(
                record_type="REVIEW_REQUIRED",
                external_record_id=str(payload.get("sourceId") or f"review-{index}"),
                instrument_id=None,
                occurred_at=review_occurred_at,
                classification=(
                    "PORTFOLIO_ACTIVITY_EVIDENCE"
                    if is_portfolio_activity
                    else "NEEDS_REVIEW"
                ),
                normalized_payload=payload,
                content_hash=source_record_hash(payload),
                validation_status="NEEDS_REVIEW",
                validation_errors=("REVIEW_REQUIRED",),
            )
        )
    if any(
        record.get("classificationReason") != "PORTFOLIO_ACTIVITY"
        for record in package.review_required
    ):
        # These records are retained for review but never fabricated as ledger
        # trades, so they do not invalidate an authoritative position snapshot.
        _append_unique(warnings, "REVIEW_REQUIRED_RECORDS")

    if not package.positions and not context.baselines:
        _append_unique(blocking, "EMPTY_SNAPSHOT_WITHOUT_BASELINE")

    items.sort(
        key=lambda item: (
            item.comparison_status == "CLOSED",
            item.product_code,
            item.instrument_id,
        )
    )
    return PreviewPlan(
        items=tuple(items),
        source_records=tuple(source_records),
        warnings=tuple(warnings),
        blocking_errors=tuple(blocking),
        history_importable=history_importable,
        history_duplicated=history_duplicated,
        can_confirm=not blocking,
    )


def _package_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _preview_response(
    *,
    batch_id: str,
    binding_id: str,
    target: TargetAccount,
    package: TiantianPackage | Any,
    plan: PreviewPlan,
    item_ids: list[str],
) -> dict[str, Any]:
    if len(item_ids) != len(plan.items):
        raise RuntimeError("persisted preview item ids do not match analyzed positions")
    return {
        "batchId": batch_id,
        "status": "DRAFT",
        "version": 1,
        "sourceAsOf": package.manifest.positions_as_of.isoformat(),
        "package": {
            "schemaVersion": package.manifest.schema_version,
            "collectorVersion": package.manifest.collector_version,
            "positionsAsOf": package.manifest.positions_as_of.isoformat(),
            "sourceWindowStart": package.manifest.source_window_start.isoformat(),
            "sourceWindowEnd": package.manifest.source_window_end.isoformat(),
        },
        "sourceBinding": {
            "bindingId": binding_id,
            "status": target.binding_status or "PENDING",
            "accountId": target.account_id,
            "displayName": target.account_display_name,
        },
        "summary": {"total": len(package.positions), **plan.summary},
        "positions": [
            {
                "itemId": item_id,
                "instrumentId": item.instrument_id,
                "symbol": item.product_code,
                "name": item.product_name,
                "quantity": format(item.quantity, "f"),
                "unitCost": format(item.unit_cost, "f"),
                "marketValue": (
                    format(item.market_value, "f")
                    if item.market_value is not None
                    else None
                ),
                "comparisonStatus": item.comparison_status,
                "candidateHoldingStartedOn": (
                    item.candidate_holding_started_on.isoformat()
                    if item.candidate_holding_started_on
                    else None
                ),
                "holdingStartStatus": item.holding_start_status,
                "holdingStartMethod": item.holding_start_method,
                "holdingStartConfidence": item.holding_start_confidence,
                "holdingStartReasons": list(item.holding_start_reasons),
                "validationStatus": (
                    "INVALID" if item.comparison_status == "CONFLICT" else "VALID"
                ),
                "validationErrors": (
                    ["UNKNOWN_FUND_CODE"]
                    if item.instrument_id is None
                    else ["ACCOUNT_LEDGER_CONFLICT"]
                    if item.comparison_status == "CONFLICT"
                    else []
                ),
            }
            for item_id, item in zip(item_ids, plan.items, strict=True)
        ],
        "historySummary": {
            "importable": plan.history_importable,
            "duplicated": plan.history_duplicated,
            "reviewRequired": len(package.review_required),
        },
        "excludedSummary": {
            "cashManagement": package.excluded_summary.cash_management,
            "nonPosting": package.excluded_summary.non_posting,
        },
        "warnings": list(plan.warnings),
        "blockingErrors": list(plan.blocking_errors),
        "canConfirm": plan.can_confirm,
    }


async def preview_tiantian_import(
    pool: asyncpg.Pool,
    ctx,
    upload: Path,
    account_id: str | None,
    account_name: str | None,
    idempotency_key: str,
    *,
    limits: ImportLimits | None = None,
    provider_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Parse outside the DB, then atomically persist a recoverable DRAFT preview."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if bool(account_id) == bool(account_name):
        raise ValidationError_("accountId 与 accountName 必须二选一")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")

    package = parse_tiantian_package(upload, limits=limits or ImportLimits())
    content_hash = _package_hash(upload)
    product_codes = sorted(
        {
            *(position.product_code for position in package.positions),
            *(transaction.product_code for transaction in package.transactions),
        }
    )
    verified_fund_names = await portfolio_service.verify_fund_master_names(
        product_codes,
        timeout_seconds=provider_timeout_seconds,
    )
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        prior = await repository.find_preview_by_idempotency(
            conn,
            tenant_id=ctx.tenant_id,
            idempotency_key=idempotency_key,
        )
        if prior is not None:
            if prior["package_content_hash"] != content_hash:
                raise IdempotencyConflictError("相同 Idempotency-Key 对应了不同导入包")
            return await repository.get_import_batch(
                conn,
                ctx.tenant_id,
                str(prior["batch_id"]),
            )

        for product_code, canonical_name in verified_fund_names.items():
            await portfolio_repository.upsert_instrument(
                conn,
                ticker=product_code,
                name=canonical_name,
                asset_type="OFF_EXCHANGE_FUND",
                primary_market="FUND_CN",
            )

        target = await repository.resolve_target_account(
            conn,
            tenant_id=ctx.tenant_id,
            external_account_key=str(package.manifest.external_account_key),
            account_id=account_id,
            account_name=account_name,
        )
        context = await repository.load_preview_context(
            conn,
            tenant_id=ctx.tenant_id,
            account_id=target.account_id,
            binding_id=target.binding_id,
            product_codes=product_codes,
            external_transaction_ids=[
                transaction.source_id for transaction in package.transactions
            ],
        )
        context = replace(context, binding_account_id=target.binding_account_id)
        plan = analyze_preview(
            package,
            context,
            target_account_id=target.account_id,
        )
        account_state_hash = await repository.account_fact_hash(
            conn,
            tenant_id=ctx.tenant_id,
            account_id=target.account_id,
        )
        persisted = await repository.persist_preview(
            conn,
            tenant_id=ctx.tenant_id,
            account_id=target.account_id,
            created_by=ctx.user_id,
            binding_id=target.binding_id,
            package=package,
            package_content_hash=content_hash,
            target_account_state_hash=account_state_hash,
            idempotency_key=idempotency_key,
            account_display_name=target.account_display_name,
            plan=plan,
        )
        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action="TIANTIAN_IMPORT_PREVIEW_CREATED",
            resource_type="ledger.portfolio_import_batches",
            resource_id=persisted["batch_id"],
            details={
                "positionCount": len(plan.items),
                "historyCount": len(package.transactions),
                "blockingErrorCount": len(plan.blocking_errors),
                "warningCount": len(plan.warnings),
            },
        )
    return _preview_response(
        batch_id=persisted["batch_id"],
        binding_id=persisted["binding_id"],
        target=target,
        package=package,
        plan=plan,
        item_ids=persisted["item_ids"],
    )


def _terminal_content_hash(
    command_type: str,
    *,
    decisions: Mapping[str, date] | None = None,
    confirm_empty: bool = False,
) -> str:
    payload = {
        "commandType": command_type,
        "confirmEmpty": confirm_empty,
        "decisions": {
            item_id: started_on.isoformat()
            for item_id, started_on in sorted((decisions or {}).items())
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def confirm_tiantian_import(
    pool: asyncpg.Pool,
    ctx,
    batch_id: str,
    *,
    expected_version: int,
    decisions: Mapping[str, date] | None,
    confirm_empty: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    """Confirm source facts, ledger effects, projections, job, and audit atomically."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if expected_version <= 0:
        raise ValidationError_("If-Match 版本必须为正整数")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    content_hash = _terminal_content_hash(
        "CONFIRM",
        decisions=decisions,
        confirm_empty=confirm_empty,
    )
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        confirmed = await repository.confirm_draft(
            conn,
            tenant_id=ctx.tenant_id,
            batch_id=batch_id,
            expected_version=expected_version,
            decisions=dict(decisions or {}),
            confirm_empty=confirm_empty,
            idempotency_key=idempotency_key,
            command_content_hash=content_hash,
        )
        if not confirmed["already_applied"]:
            from backend.portfolio.service import recompute_projections

            await recompute_projections(conn, tenant_id=ctx.tenant_id)
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="TIANTIAN_IMPORT_CONFIRMED",
                resource_type="ledger.portfolio_import_batches",
                resource_id=batch_id,
                details={
                    "positionCount": confirmed["position_count"],
                    "historyImported": confirmed["history_imported"],
                    "historyDuplicated": confirmed["history_duplicated"],
                    "ledgerEffectCount": len(confirmed["event_ids"]),
                },
            )
    return {
        "batchId": batch_id,
        "status": "CONFIRMED",
        "version": confirmed["version"],
        "metricsStatus": "PENDING",
        "positionCount": confirmed["position_count"],
        "historyImported": confirmed["history_imported"],
        "historyDuplicated": confirmed["history_duplicated"],
    }


async def cancel_tiantian_import(
    pool: asyncpg.Pool,
    ctx,
    batch_id: str,
    *,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """Cancel a DRAFT without deleting its auditable preview."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if expected_version <= 0:
        raise ValidationError_("If-Match 版本必须为正整数")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    content_hash = _terminal_content_hash("CANCEL")
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        cancelled = await repository.cancel_draft(
            conn,
            tenant_id=ctx.tenant_id,
            batch_id=batch_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            command_content_hash=content_hash,
        )
        if not cancelled["already_applied"]:
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="TIANTIAN_IMPORT_CANCELLED",
                resource_type="ledger.portfolio_import_batches",
                resource_id=batch_id,
                details={"previousVersion": expected_version},
            )
    return {
        "batchId": batch_id,
        "status": "CANCELLED",
        "version": cancelled["version"],
    }


async def get_tiantian_import(pool: asyncpg.Pool, ctx, batch_id: str) -> dict[str, Any]:
    """Restore one tenant-scoped import, including the full review state."""
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        return await repository.get_import_batch(conn, ctx.tenant_id, batch_id)


async def list_tiantian_imports(
    pool: asyncpg.Pool,
    ctx,
    *,
    source_type: str,
    cursor: datetime | None,
    limit: int,
) -> dict[str, Any]:
    """List recoverable imports without exposing another tenant's batches."""
    if source_type != "TIANTIAN_FUND_ZIP":
        raise ValidationError_("sourceType 当前只支持 TIANTIAN_FUND_ZIP")
    if not 1 <= limit <= 100:
        raise ValidationError_("limit 必须在 1 到 100 之间")
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        return await repository.list_import_batches(
            conn,
            ctx.tenant_id,
            source_type=source_type,
            cursor=cursor,
            limit=limit,
        )


async def retry_tiantian_metrics(
    pool: asyncpg.Pool,
    ctx,
    batch_id: str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    """Queue an idempotent retry without replaying any financial effects."""
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValidationError_("Idempotency-Key 必填且不能超过 128 个字符")
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        queued = await repository.retry_position_metrics(
            conn,
            tenant_id=ctx.tenant_id,
            batch_id=batch_id,
            idempotency_key=idempotency_key,
        )
        if queued["created"]:
            await write_audit(
                conn,
                tenant_id=ctx.tenant_id,
                actor_type="USER",
                actor_id=ctx.user_id,
                action="TIANTIAN_IMPORT_METRICS_RETRY_QUEUED",
                resource_type="ledger.portfolio_import_batches",
                resource_id=batch_id,
                details={"jobKey": queued["job_key"]},
            )
    return {
        "batchId": batch_id,
        "metricsStatus": "PENDING",
        "jobKey": queued["job_key"],
    }
