"""Tenant-scoped persistence for Tiantian import previews and batch reads."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import asyncpg

from backend.errors import (
    IdempotencyConflictError,
    NotFoundError,
    ValidationError_,
    VersionConflictError,
)

if TYPE_CHECKING:
    from backend.portfolio.imports.service import (
        ConfirmationItem,
        LedgerEffect,
        PreviewContext,
        PreviewPlan,
        TargetAccount,
    )
    from backend.portfolio.imports.tiantian import TiantianPackage


async def find_preview_by_idempotency(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    idempotency_key: str,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id AS batch_id, package_content_hash
          FROM ledger.portfolio_import_batches
         WHERE tenant_id = $1
           AND idempotency_key = $2
           AND source_type = 'TIANTIAN_FUND_ZIP'
        """,
        tenant_id,
        idempotency_key,
    )


async def find_active_preview_by_package(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    binding_id: str,
    package_content_hash: str,
) -> asyncpg.Record | None:
    """Return the non-cancelled import protected by the package unique index."""
    return await conn.fetchrow(
        """
        SELECT id AS batch_id
          FROM ledger.portfolio_import_batches
         WHERE tenant_id = $1
           AND source_binding_id = $2
           AND package_content_hash = $3
           AND source_type = 'TIANTIAN_FUND_ZIP'
           AND status <> 'CANCELLED'
        """,
        tenant_id,
        binding_id,
        package_content_hash,
    )


async def resolve_target_account(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    external_account_key: str,
    account_id: str | None,
    account_name: str | None,
) -> TargetAccount:
    from backend.portfolio.imports.service import TargetAccount

    binding = await conn.fetchrow(
        """
        SELECT b.id AS binding_id, b.account_id AS binding_account_id,
               b.status AS binding_status, b.display_name,
               account.name AS account_name
          FROM ledger.portfolio_source_bindings b
          JOIN identity.accounts account
            ON account.tenant_id = b.tenant_id AND account.id = b.account_id
         WHERE b.tenant_id = $1
           AND b.provider_code = 'TIANTIAN_FUND'
           AND b.external_account_key = $2::uuid
         FOR UPDATE
        """,
        tenant_id,
        external_account_key,
    )

    if account_id:
        account = await conn.fetchrow(
            """
            SELECT id, name
              FROM identity.accounts
             WHERE tenant_id = $1 AND id = $2
               AND account_type = 'FUND_PLATFORM' AND status = 'ACTIVE'
            """,
            tenant_id,
            account_id,
        )
        if account is None:
            raise NotFoundError("目标基金账户不存在或不可用")
        target_account_id = str(account["id"])
        display_name = str(account["name"])
    elif binding is not None:
        target_account_id = str(binding["binding_account_id"])
        display_name = str(binding["account_name"])
    else:
        normalized_name = (account_name or "").strip()
        if not 1 <= len(normalized_name) <= 120:
            raise ValidationError_("账户名称长度必须为 1 到 120 个字符")
        target_account_id = str(
            await conn.fetchval(
                """
                INSERT INTO identity.accounts
                    (tenant_id, account_type, name, base_currency, provider_ref)
                VALUES ($1, 'FUND_PLATFORM', $2, 'CNY', 'TIANTIAN_FUND')
                RETURNING id
                """,
                tenant_id,
                normalized_name,
            )
        )
        display_name = normalized_name

    return TargetAccount(
        account_id=target_account_id,
        binding_id=(str(binding["binding_id"]) if binding else None),
        binding_status=(str(binding["binding_status"]) if binding else None),
        binding_account_id=(str(binding["binding_account_id"]) if binding else None),
        account_display_name=display_name,
    )


async def load_preview_context(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
    binding_id: str | None,
    product_codes: list[str],
    external_transaction_ids: list[str],
) -> PreviewContext:
    from backend.portfolio.imports.service import ExistingBaseline, PreviewContext

    fund_rows = await conn.fetch(
        """
        SELECT DISTINCT ON (alias.alias_value)
               alias.alias_value AS product_code,
               instrument.id AS instrument_id,
               instrument.canonical_name
          FROM reference.instrument_aliases alias
          JOIN reference.instruments instrument ON instrument.id = alias.instrument_id
         WHERE alias.alias_type = 'TICKER'
           AND alias.alias_value = ANY($1::text[])
           AND alias.market = 'FUND_CN'
           AND instrument.asset_type = 'OFF_EXCHANGE_FUND'
           AND instrument.lifecycle_status = 'ACTIVE'
           AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
         ORDER BY alias.alias_value, alias.effective_from DESC
        """,
        product_codes,
    )
    resolved = {
        str(row["product_code"]): {
            "instrument_id": str(row["instrument_id"]),
            "canonical_name": str(row["canonical_name"]),
        }
        for row in fund_rows
    }

    baseline_rows = []
    if binding_id:
        baseline_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (item.instrument_id)
                   item.instrument_id, item.source_symbol AS product_code,
                   instrument.canonical_name, item.quantity, item.unit_cost,
                   item.external_record_id
              FROM ledger.ledger_events event
              JOIN ledger.portfolio_import_batches batch
                ON batch.tenant_id = event.tenant_id
               AND batch.id = event.import_batch_id
              JOIN ledger.portfolio_import_items item
                ON item.tenant_id = event.tenant_id
               AND item.batch_id = event.import_batch_id
               AND item.id = event.import_item_id
              JOIN reference.instruments instrument ON instrument.id = item.instrument_id
             WHERE event.tenant_id = $1
               AND batch.source_binding_id = $2
               AND event.status = 'CONFIRMED'
               AND event.event_type = 'ADJUSTMENT'
               AND event.reverses_event_id IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM ledger.ledger_events reversal
                    WHERE reversal.tenant_id = event.tenant_id
                      AND reversal.reverses_event_id = event.id
                      AND reversal.status = 'CONFIRMED'
               )
             ORDER BY item.instrument_id, event.created_at DESC, event.id DESC
            """,
            tenant_id,
            binding_id,
        )
    baselines = {
        str(row["instrument_id"]): ExistingBaseline(
            instrument_id=str(row["instrument_id"]),
            product_code=str(row["product_code"]),
            product_name=str(row["canonical_name"]),
            quantity=Decimal(row["quantity"]),
            unit_cost=Decimal(row["unit_cost"]),
            external_record_id=str(row["external_record_id"]),
        )
        for row in baseline_rows
    }

    instrument_ids = [value["instrument_id"] for value in resolved.values()]
    conflict_rows = []
    if instrument_ids:
        conflict_rows = await conn.fetch(
            """
            SELECT DISTINCT leg.instrument_id
              FROM ledger.ledger_events event
              JOIN ledger.ledger_legs leg
                ON leg.tenant_id = event.tenant_id AND leg.event_id = event.id
              LEFT JOIN ledger.portfolio_import_batches batch
                ON batch.tenant_id = event.tenant_id
               AND batch.id = event.import_batch_id
             WHERE event.tenant_id = $1 AND event.account_id = $2
               AND event.status = 'CONFIRMED'
               AND leg.instrument_id = ANY($3::uuid[])
               AND ($4::uuid IS NULL OR batch.source_binding_id IS DISTINCT FROM $4::uuid)
               AND NOT EXISTS (
                   SELECT 1 FROM ledger.ledger_events reversal
                    WHERE reversal.tenant_id = event.tenant_id
                      AND reversal.reverses_event_id = event.id
                      AND reversal.status = 'CONFIRMED'
               )
            """,
            tenant_id,
            account_id,
            instrument_ids,
            binding_id,
        )

    latest_source_as_of = None
    if binding_id:
        latest_source_as_of = await conn.fetchval(
            """
            SELECT max(source_as_of)
              FROM ledger.portfolio_import_batches
             WHERE tenant_id = $1 AND source_binding_id = $2
               AND source_type = 'TIANTIAN_FUND_ZIP' AND status = 'CONFIRMED'
            """,
            tenant_id,
            binding_id,
        )

    transaction_hashes: dict[str, str] = {}
    if binding_id and external_transaction_ids:
        rows = await conn.fetch(
            """
            SELECT external_transaction_id, content_hash
              FROM ledger.portfolio_source_transactions
             WHERE tenant_id = $1 AND source_binding_id = $2
               AND external_transaction_id = ANY($3::text[])
            """,
            tenant_id,
            binding_id,
            external_transaction_ids,
        )
        transaction_hashes = {
            str(row["external_transaction_id"]): str(row["content_hash"])
            for row in rows
        }

    return PreviewContext(
        resolved_funds=resolved,
        baselines=baselines,
        account_conflict_instrument_ids=frozenset(
            str(row["instrument_id"]) for row in conflict_rows
        ),
        latest_source_as_of=latest_source_as_of,
        binding_account_id=None,
        confirmed_transaction_hashes=transaction_hashes,
    )


async def account_fact_hash(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
) -> str:
    rows = await conn.fetch(
        """
        SELECT event.id, event.event_type, event.status, event.reverses_event_id,
               event.import_batch_id, event.import_item_id
          FROM ledger.ledger_events event
         WHERE event.tenant_id = $1 AND event.account_id = $2
           AND event.status IN ('CONFIRMED', 'REVERSED')
         ORDER BY event.id
        """,
        tenant_id,
        account_id,
    )
    stable = [
        [
            str(row["id"]),
            str(row["event_type"]),
            str(row["status"]),
            str(row["reverses_event_id"] or ""),
            str(row["import_batch_id"] or ""),
            str(row["import_item_id"] or ""),
        ]
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(stable, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def persist_preview(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
    created_by: str,
    binding_id: str | None,
    package: TiantianPackage,
    package_content_hash: str,
    target_account_state_hash: str,
    idempotency_key: str,
    account_display_name: str,
    plan: PreviewPlan,
) -> dict[str, Any]:
    batch_id = str(await conn.fetchval("SELECT gen_random_uuid()"))
    effective_binding_id = binding_id
    if effective_binding_id is None:
        effective_binding_id = str(await conn.fetchval("SELECT gen_random_uuid()"))

    await conn.execute(
        """
        INSERT INTO ledger.portfolio_import_batches (
            id, tenant_id, account_id, created_by, source_type,
            source_content_hash, status, idempotency_key, source_binding_id,
            package_schema_version, package_content_hash, source_as_of,
            parser_version, preview_summary, validation_status,
            validation_errors, target_account_state_hash
        ) VALUES (
            $1, $2, $3, $4, 'TIANTIAN_FUND_ZIP', $5::text, 'DRAFT', $6, $7,
            $8, $5::text, $9, 'tiantian-package/v2', $10::jsonb, $11,
            $12::jsonb, $13
        )
        """,
        batch_id,
        tenant_id,
        account_id,
        created_by,
        package_content_hash,
        idempotency_key,
        effective_binding_id,
        package.manifest.schema_version,
        package.manifest.positions_as_of,
        json.dumps(
            {
                "positionCounts": {"total": len(plan.items), **plan.summary},
                "history": {
                    "importable": plan.history_importable,
                    "duplicated": plan.history_duplicated,
                    "reviewRequired": len(package.review_required),
                },
                "excluded": {
                    "cashManagement": package.excluded_summary.cash_management,
                    "nonPosting": package.excluded_summary.non_posting,
                },
                "sourceWindow": {
                    "start": package.manifest.source_window_start.isoformat(),
                    "end": package.manifest.source_window_end.isoformat(),
                },
                "warnings": list(plan.warnings),
            },
            ensure_ascii=False,
        ),
        "VALID" if plan.can_confirm else "INVALID",
        json.dumps(list(plan.blocking_errors), ensure_ascii=False),
        target_account_state_hash,
    )
    if binding_id is None:
        await conn.execute(
            """
            INSERT INTO ledger.portfolio_source_bindings (
                id, tenant_id, account_id, provider_code, external_account_key,
                display_name, status, created_by_batch_id
            ) VALUES ($1, $2, $3, 'TIANTIAN_FUND', $4::uuid, $5, 'PENDING', $6)
            """,
            effective_binding_id,
            tenant_id,
            account_id,
            str(package.manifest.external_account_key),
            account_display_name,
            batch_id,
        )

    item_ids: list[str] = []
    item_rows: list[tuple[Any, ...]] = []
    for index, item in enumerate(plan.items, start=1):
        if item.instrument_id is None:
            item_ids.append(f"unresolved:{item.external_record_id}")
            continue
        item_id = str(uuid.uuid4())
        item_ids.append(item_id)
        item_rows.append(
            (
                item_id,
                tenant_id,
                batch_id,
                item.instrument_id,
                item.quantity,
                item.unit_cost,
                package.manifest.positions_as_of.date(),
                index,
                "INVALID" if item.comparison_status == "CONFLICT" else "VALID",
                json.dumps(
                    ["ACCOUNT_LEDGER_CONFLICT"]
                    if item.comparison_status == "CONFLICT"
                    else [],
                    ensure_ascii=False,
                ),
                item.external_record_id,
                item.product_code,
                item.market_value,
                item.source_nav,
                item.source_nav_date,
                item.comparison_status,
                item.candidate_holding_started_on,
                item.holding_start_status,
                item.holding_start_method,
                item.holding_start_confidence,
                list(item.holding_start_reasons),
                json.dumps(item.source_payload, ensure_ascii=False),
            )
        )
    await conn.executemany(
        """
        INSERT INTO ledger.portfolio_import_items (
            id, tenant_id, batch_id, item_type, instrument_id, quantity, unit_cost,
            trade_date, source_row, validation_status, validation_errors,
            external_record_id, source_symbol, source_market_value, source_nav,
            source_nav_date, comparison_status, candidate_holding_started_on,
            holding_start_status, holding_start_method, holding_start_confidence,
            holding_start_reasons, source_payload
        ) VALUES (
            $1, $2, $3, 'POSITION_SNAPSHOT', $4, $5, $6, $7, $8, $9,
            $10::jsonb, $11, $12, $13, $14, $15, $16, $17, $18, $19,
            $20, $21::text[], $22::jsonb
        )
        """,
        item_rows,
    )

    source_rows = [
        (
            tenant_id,
            batch_id,
            record.record_type,
            record.external_record_id,
            record.instrument_id,
            record.occurred_at,
            record.classification,
            json.dumps(record.normalized_payload, ensure_ascii=False),
            record.content_hash,
            record.validation_status,
            json.dumps(list(record.validation_errors), ensure_ascii=False),
        )
        for record in plan.source_records
    ]
    await conn.executemany(
        """
        INSERT INTO ledger.portfolio_import_source_records (
            tenant_id, batch_id, record_type, external_record_id, instrument_id,
            occurred_at, classification, normalized_payload, content_hash,
            validation_status, validation_errors
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11::jsonb)
        """,
        source_rows,
    )
    return {
        "batch_id": batch_id,
        "binding_id": effective_binding_id,
        "item_ids": item_ids,
    }


async def write_ledger_effects(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    account_id: str,
    batch_id: str,
    items: dict[str, ConfirmationItem],
    effects: tuple[LedgerEffect, ...],
) -> list[str]:
    """Append baseline effects; reversals carry no legs and are not replayed directly."""
    event_ids: list[str] = []
    for effect in effects:
        item = items[effect.item_id]
        if effect.effect_type == "REVERSAL":
            event_id = str(
                await conn.fetchval(
                    """
                    INSERT INTO ledger.ledger_events (
                        tenant_id, account_id, event_type, status, trade_date,
                        idempotency_key, reverses_event_id, import_batch_id,
                        import_item_id, description, schema_version
                    ) VALUES (
                        $1, $2, 'REVERSAL', 'CONFIRMED', $3, $4, $5, $6, $7,
                        '逐基金撤销旧外部持仓基线；不代表真实卖出', 1
                    ) RETURNING id
                    """,
                    tenant_id,
                    account_id,
                    item.trade_date,
                    f"tiantian:{batch_id}:{item.item_id}:reversal",
                    effect.previous_event_id,
                    batch_id,
                    item.item_id,
                )
            )
        elif effect.effect_type == "ADJUSTMENT":
            event_id = str(
                await conn.fetchval(
                    """
                    INSERT INTO ledger.ledger_events (
                        tenant_id, account_id, event_type, status, trade_date,
                        idempotency_key, import_batch_id, import_item_id,
                        description, schema_version
                    ) VALUES (
                        $1, $2, 'ADJUSTMENT', 'CONFIRMED', $3, $4, $5, $6,
                        '用户确认的外部基金当前持仓基线；不代表真实买入或出入金', 1
                    ) RETURNING id
                    """,
                    tenant_id,
                    account_id,
                    item.trade_date,
                    f"tiantian:{batch_id}:{item.item_id}:adjustment",
                    batch_id,
                    item.item_id,
                )
            )
            await conn.execute(
                """
                INSERT INTO ledger.ledger_legs (
                    tenant_id, event_id, leg_type, instrument_id, currency,
                    quantity, unit_price
                ) VALUES ($1, $2, 'INSTRUMENT', $3, 'CNY', $4, $5)
                """,
                tenant_id,
                event_id,
                item.instrument_id,
                item.quantity,
                item.unit_cost,
            )
        else:
            raise ValidationError_(f"不支持的账本效果：{effect.effect_type}")
        event_ids.append(event_id)
    return event_ids


async def _confirmed_result(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    batch_id: str,
    version: int,
    already_applied: bool,
) -> dict[str, Any]:
    position_count = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM ledger.portfolio_import_items
             WHERE tenant_id = $1 AND batch_id = $2
               AND item_type = 'POSITION_SNAPSHOT' AND quantity > 0
            """,
            tenant_id,
            batch_id,
        )
        or 0
    )
    history_total = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM ledger.portfolio_import_source_records
             WHERE tenant_id = $1 AND batch_id = $2
               AND record_type = 'FUND_TRANSACTION'
               AND validation_status = 'VALID'
            """,
            tenant_id,
            batch_id,
        )
        or 0
    )
    history_imported = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM ledger.portfolio_source_transactions
             WHERE tenant_id = $1 AND source_import_batch_id = $2
            """,
            tenant_id,
            batch_id,
        )
        or 0
    )
    event_rows = await conn.fetch(
        """
        SELECT id FROM ledger.ledger_events
         WHERE tenant_id = $1 AND import_batch_id = $2
         ORDER BY created_at, id
        """,
        tenant_id,
        batch_id,
    )
    return {
        "batch_id": batch_id,
        "version": version,
        "position_count": position_count,
        "history_imported": history_imported,
        "history_duplicated": max(0, history_total - history_imported),
        "event_ids": [str(row["id"]) for row in event_rows],
        "already_applied": already_applied,
    }


async def _source_hash_conflict(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    batch_id: str,
    binding_id: str,
) -> str | None:
    value = await conn.fetchval(
        """
        SELECT staged.external_record_id
          FROM ledger.portfolio_import_source_records staged
          JOIN ledger.portfolio_source_transactions confirmed
            ON confirmed.tenant_id = staged.tenant_id
           AND confirmed.source_binding_id = $3
           AND confirmed.external_transaction_id = staged.external_record_id
         WHERE staged.tenant_id = $1 AND staged.batch_id = $2
           AND staged.record_type = 'FUND_TRANSACTION'
           AND confirmed.content_hash <> staged.content_hash
         LIMIT 1
        """,
        tenant_id,
        batch_id,
        binding_id,
    )
    return str(value) if value is not None else None


async def confirm_draft(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    batch_id: str,
    expected_version: int,
    decisions: dict[str, date],
    confirm_empty: bool,
    idempotency_key: str,
    command_content_hash: str,
) -> dict[str, Any]:
    """Atomically confirm source facts, per-fund effects, binding, and data job."""
    from backend.portfolio.imports.service import ConfirmationItem, build_ledger_effects

    batch = await conn.fetchrow(
        """
        SELECT batch.id AS batch_id, batch.account_id, batch.source_binding_id,
               batch.source_as_of, batch.status, batch.version,
               batch.validation_status, batch.target_account_state_hash,
               batch.preview_summary, batch.terminal_command_type,
               batch.terminal_idempotency_key, batch.terminal_content_hash,
               binding.status AS binding_status
          FROM ledger.portfolio_import_batches batch
          JOIN ledger.portfolio_source_bindings binding
            ON binding.tenant_id = batch.tenant_id
           AND binding.id = batch.source_binding_id
         WHERE batch.tenant_id = $1 AND batch.id = $2
           AND batch.source_type = 'TIANTIAN_FUND_ZIP'
         FOR UPDATE OF batch, binding
        """,
        tenant_id,
        batch_id,
    )
    if batch is None:
        raise NotFoundError("天天基金导入批次不存在")
    if batch["status"] == "CONFIRMED":
        if batch["terminal_idempotency_key"] == idempotency_key:
            if batch["terminal_content_hash"] != command_content_hash:
                raise IdempotencyConflictError("确认幂等键对应了不同人工决策")
            return await _confirmed_result(
                conn,
                tenant_id=tenant_id,
                batch_id=batch_id,
                version=int(batch["version"]),
                already_applied=True,
            )
        raise VersionConflictError("该导入批次已经确认")
    if batch["status"] != "DRAFT":
        raise VersionConflictError("只有 DRAFT 导入批次可以确认")
    if int(batch["version"]) != expected_version:
        raise VersionConflictError("导入预览版本已变化，请刷新后重试")
    if batch["validation_status"] != "VALID":
        raise ValidationError_("导入预览存在阻断问题，不能确认")
    if batch["binding_status"] == "DISABLED":
        raise ValidationError_("来源绑定已停用，不能确认")

    account_id = str(batch["account_id"])
    binding_id = str(batch["source_binding_id"])
    await conn.execute(
        """
        SELECT pg_advisory_xact_lock(
            hashtextextended('portfolio-account:' || $1 || ':' || $2, 0)
        )
        """,
        tenant_id,
        account_id,
    )
    current_hash = await account_fact_hash(
        conn,
        tenant_id=tenant_id,
        account_id=account_id,
    )
    if current_hash != str(batch["target_account_state_hash"]):
        raise VersionConflictError("账户事实在预览后已变化，请重新预览")

    item_rows = await conn.fetch(
        """
        SELECT id AS item_id, instrument_id, quantity, unit_cost, trade_date,
               comparison_status, validation_status
          FROM ledger.portfolio_import_items
         WHERE tenant_id = $1 AND batch_id = $2
           AND item_type = 'POSITION_SNAPSHOT'
         ORDER BY source_row, id
         FOR UPDATE
        """,
        tenant_id,
        batch_id,
    )
    if not item_rows or any(row["validation_status"] != "VALID" for row in item_rows):
        raise ValidationError_("导入持仓项目为空或未通过校验")
    known_item_ids = {str(row["item_id"]) for row in item_rows}
    if set(decisions) - known_item_ids:
        raise ValidationError_("人工持仓开始日包含未知项目")
    decision_rows = []
    by_id = {str(row["item_id"]): row for row in item_rows}
    for item_id, started_on in decisions.items():
        if started_on > by_id[item_id]["trade_date"]:
            raise ValidationError_("人工持仓开始日不能晚于来源快照日期")
        decision_rows.append((tenant_id, batch_id, item_id, started_on))
    if decision_rows:
        await conn.executemany(
            """
            UPDATE ledger.portfolio_import_items
               SET user_holding_started_on = $4, updated_at = now()
             WHERE tenant_id = $1 AND batch_id = $2 AND id = $3
            """,
            decision_rows,
        )

    items = [
        ConfirmationItem(
            item_id=str(row["item_id"]),
            instrument_id=str(row["instrument_id"]),
            quantity=Decimal(row["quantity"]),
            unit_cost=Decimal(row["unit_cost"]),
            trade_date=row["trade_date"],
            comparison_status=str(row["comparison_status"]),
        )
        for row in item_rows
    ]
    if all(item.comparison_status == "CLOSED" for item in items) and not confirm_empty:
        raise ValidationError_("空持仓快照需要明确二次确认")

    active_rows = await conn.fetch(
        """
        SELECT DISTINCT ON (current_item.id)
               current_item.id AS item_id, old_event.id AS event_id
          FROM ledger.portfolio_import_items current_item
          JOIN ledger.portfolio_import_batches current_batch
            ON current_batch.tenant_id = current_item.tenant_id
           AND current_batch.id = current_item.batch_id
          JOIN ledger.portfolio_import_batches old_batch
            ON old_batch.tenant_id = current_batch.tenant_id
           AND old_batch.source_binding_id = current_batch.source_binding_id
           AND old_batch.status = 'CONFIRMED'
          JOIN ledger.portfolio_import_items old_item
            ON old_item.tenant_id = old_batch.tenant_id
           AND old_item.batch_id = old_batch.id
           AND old_item.instrument_id = current_item.instrument_id
          JOIN ledger.ledger_events old_event
            ON old_event.tenant_id = old_item.tenant_id
           AND old_event.import_batch_id = old_item.batch_id
           AND old_event.import_item_id = old_item.id
           AND old_event.event_type = 'ADJUSTMENT'
           AND old_event.status = 'CONFIRMED'
           AND old_event.reverses_event_id IS NULL
         WHERE current_item.tenant_id = $1 AND current_item.batch_id = $2
           AND NOT EXISTS (
               SELECT 1 FROM ledger.ledger_events reversal
                WHERE reversal.tenant_id = old_event.tenant_id
                  AND reversal.reverses_event_id = old_event.id
                  AND reversal.event_type = 'REVERSAL'
                  AND reversal.status = 'CONFIRMED'
           )
         ORDER BY current_item.id, old_event.created_at DESC, old_event.id DESC
        """,
        tenant_id,
        batch_id,
    )
    active_event_ids = {
        str(row["item_id"]): str(row["event_id"]) for row in active_rows
    }
    effects = build_ledger_effects(items, active_event_ids)

    conflict_id = await _source_hash_conflict(
        conn,
        tenant_id=tenant_id,
        batch_id=batch_id,
        binding_id=binding_id,
    )
    if conflict_id:
        raise VersionConflictError(f"来源流水 {conflict_id} 内容发生冲突")
    inserted_transactions = await conn.fetch(
        """
        INSERT INTO ledger.portfolio_source_transactions (
            tenant_id, source_binding_id, source_import_batch_id,
            external_transaction_id, instrument_id, product_code, product_name,
            occurred_at, business_code, business_type, application_value,
            application_unit, confirmation_value, confirmation_unit,
            classification, source_payload, content_hash
        )
        SELECT staged.tenant_id, $3, staged.batch_id,
               staged.external_record_id, staged.instrument_id,
               staged.normalized_payload->>'productCode',
               staged.normalized_payload->>'productName', staged.occurred_at,
               COALESCE(staged.normalized_payload->>'businessCode', ''),
               staged.normalized_payload->>'normalizedBusinessType',
               NULLIF(staged.normalized_payload->>'applicationValue', '')::numeric,
               COALESCE(staged.normalized_payload->>'applicationUnit', ''),
               NULLIF(staged.normalized_payload->>'confirmationValue', '')::numeric,
               COALESCE(staged.normalized_payload->>'confirmationUnit', ''),
               COALESCE(staged.normalized_payload->>'classificationReason',
                        staged.classification),
               staged.normalized_payload, staged.content_hash
          FROM ledger.portfolio_import_source_records staged
         WHERE staged.tenant_id = $1 AND staged.batch_id = $2
           AND staged.record_type = 'FUND_TRANSACTION'
           AND staged.validation_status = 'VALID'
        ON CONFLICT (tenant_id, source_binding_id, external_transaction_id)
        DO NOTHING
        RETURNING id
        """,
        tenant_id,
        batch_id,
        binding_id,
    )
    conflict_id = await _source_hash_conflict(
        conn,
        tenant_id=tenant_id,
        batch_id=batch_id,
        binding_id=binding_id,
    )
    if conflict_id:
        raise VersionConflictError(f"来源流水 {conflict_id} 内容发生冲突")

    event_ids = await write_ledger_effects(
        conn,
        tenant_id=tenant_id,
        account_id=account_id,
        batch_id=batch_id,
        items={item.item_id: item for item in items},
        effects=effects,
    )
    source_as_of = batch["source_as_of"]
    binding_updated = await conn.fetchval(
        """
        UPDATE ledger.portfolio_source_bindings
           SET status = 'ACTIVE',
               first_imported_at = COALESCE(first_imported_at, $3),
               last_imported_at = $3,
               updated_at = now()
         WHERE tenant_id = $1 AND id = $2 AND status IN ('PENDING', 'ACTIVE')
        RETURNING id
        """,
        tenant_id,
        binding_id,
        source_as_of,
    )
    if binding_updated is None:
        raise VersionConflictError("来源绑定状态已变化")

    next_version = expected_version + 1
    await conn.execute(
        """
        UPDATE ledger.portfolio_import_batches
           SET status = 'CONFIRMED', confirmed_at = now(), version = $3,
               updated_at = now(), terminal_command_type = 'CONFIRM',
               terminal_idempotency_key = $4, terminal_content_hash = $5
         WHERE tenant_id = $1 AND id = $2 AND status = 'DRAFT' AND version = $6
        """,
        tenant_id,
        batch_id,
        next_version,
        idempotency_key,
        command_content_hash,
        expected_version,
    )
    await conn.execute(
        """
        INSERT INTO workflow.job_runs (
            tenant_id, job_key, status, job_type, payload, available_at
        ) VALUES (
            $1, $2, 'PENDING', 'FUND_NAV_BACKFILL', $3::jsonb, now()
        ) ON CONFLICT (tenant_id, job_key) DO NOTHING
        """,
        tenant_id,
        f"fund-nav-backfill:{batch_id}",
        json.dumps(
            {
                "batchId": batch_id,
                "accountId": account_id,
                "sourceBindingId": binding_id,
                "sourceAsOf": source_as_of.isoformat(),
            },
            ensure_ascii=False,
        ),
    )
    history_total = int(
        await conn.fetchval(
            """
            SELECT count(*) FROM ledger.portfolio_import_source_records
             WHERE tenant_id = $1 AND batch_id = $2
               AND record_type = 'FUND_TRANSACTION'
               AND validation_status = 'VALID'
            """,
            tenant_id,
            batch_id,
        )
        or 0
    )
    return {
        "batch_id": batch_id,
        "version": next_version,
        "position_count": sum(item.quantity > 0 for item in items),
        "history_imported": len(inserted_transactions),
        "history_duplicated": max(0, history_total - len(inserted_transactions)),
        "event_ids": event_ids,
        "already_applied": False,
    }


async def cancel_draft(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    batch_id: str,
    expected_version: int,
    idempotency_key: str,
    command_content_hash: str,
) -> dict[str, Any]:
    batch = await conn.fetchrow(
        """
        SELECT id AS batch_id, status, version, terminal_command_type,
               terminal_idempotency_key, terminal_content_hash
          FROM ledger.portfolio_import_batches
         WHERE tenant_id = $1 AND id = $2
           AND source_type = 'TIANTIAN_FUND_ZIP'
         FOR UPDATE
        """,
        tenant_id,
        batch_id,
    )
    if batch is None:
        raise NotFoundError("天天基金导入批次不存在")
    if batch["status"] == "CANCELLED":
        if batch["terminal_idempotency_key"] == idempotency_key:
            if batch["terminal_content_hash"] != command_content_hash:
                raise IdempotencyConflictError("取消幂等键对应了不同命令内容")
            return {
                "batch_id": batch_id,
                "version": int(batch["version"]),
                "already_applied": True,
            }
        raise VersionConflictError("该导入批次已经取消")
    if batch["status"] != "DRAFT":
        raise VersionConflictError("只有 DRAFT 导入批次可以取消")
    if int(batch["version"]) != expected_version:
        raise VersionConflictError("导入预览版本已变化，请刷新后重试")
    next_version = expected_version + 1
    await conn.execute(
        """
        UPDATE ledger.portfolio_import_batches
           SET status = 'CANCELLED', cancelled_at = now(), version = $3,
               updated_at = now(), terminal_command_type = 'CANCEL',
               terminal_idempotency_key = $4, terminal_content_hash = $5
         WHERE tenant_id = $1 AND id = $2 AND status = 'DRAFT' AND version = $6
        """,
        tenant_id,
        batch_id,
        next_version,
        idempotency_key,
        command_content_hash,
        expected_version,
    )
    return {"batch_id": batch_id, "version": next_version, "already_applied": False}


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _json_object(value: Any) -> dict[str, Any]:
    decoded = json.loads(value) if isinstance(value, str) else value
    return decoded if isinstance(decoded, dict) else {}


def _json_array(value: Any) -> list[Any]:
    decoded = json.loads(value) if isinstance(value, str) else value
    return decoded if isinstance(decoded, list) else []


async def get_import_batch(
    conn: asyncpg.Connection,
    tenant_id: str,
    batch_id: str,
) -> dict[str, Any]:
    batch = await conn.fetchrow(
        """
        SELECT b.id AS batch_id, b.status, b.version, b.source_type, b.source_as_of,
               b.package_schema_version, b.parser_version,
               b.preview_summary, b.validation_status, b.validation_errors,
               b.created_at, b.updated_at, binding.id AS binding_id,
               binding.status AS binding_status,
               binding.display_name AS binding_display_name,
               account.id AS account_id, account.name AS account_name,
               data_job.status AS data_job_status,
               metrics.metric_statuses
          FROM ledger.portfolio_import_batches b
          JOIN identity.accounts account
            ON account.tenant_id = b.tenant_id AND account.id = b.account_id
          LEFT JOIN ledger.portfolio_source_bindings binding
            ON binding.tenant_id = b.tenant_id AND binding.id = b.source_binding_id
          LEFT JOIN LATERAL (
              SELECT run.status
                FROM workflow.job_runs run
               WHERE run.tenant_id = b.tenant_id
                 AND run.job_type IN (
                     'FUND_NAV_BACKFILL', 'POSITION_PERIOD_METRICS_REBUILD'
                 )
                 AND run.payload->>'batchId' = b.id::text
               ORDER BY run.available_at DESC, run.id DESC
               LIMIT 1
          ) data_job ON TRUE
          LEFT JOIN LATERAL (
              SELECT array_agg(DISTINCT latest.calculation_status)
                         AS metric_statuses
                FROM (
                    SELECT DISTINCT ON (metric.instrument_id)
                           metric.instrument_id, metric.calculation_status
                      FROM ledger.position_period_metrics metric
                     WHERE metric.tenant_id = b.tenant_id
                       AND metric.account_id = b.account_id
                       AND metric.source_binding_id = b.source_binding_id
                     ORDER BY metric.instrument_id, metric.as_of DESC
                ) latest
          ) metrics ON TRUE
         WHERE b.tenant_id = $1 AND b.id = $2
           AND b.source_type = 'TIANTIAN_FUND_ZIP'
        """,
        tenant_id,
        batch_id,
    )
    if batch is None:
        raise NotFoundError("天天基金导入批次不存在")
    positions = await conn.fetch(
        """
        SELECT item.id AS item_id, item.instrument_id, item.source_symbol,
               instrument.canonical_name, item.quantity, item.unit_cost,
               item.source_market_value, item.source_nav, item.source_nav_date,
               item.comparison_status, item.candidate_holding_started_on,
               item.holding_start_status, item.holding_start_method,
               item.holding_start_confidence, item.holding_start_reasons,
               item.validation_status,
               item.validation_errors
          FROM ledger.portfolio_import_items item
          JOIN reference.instruments instrument ON instrument.id = item.instrument_id
         WHERE item.tenant_id = $1 AND item.batch_id = $2
           AND item.item_type = 'POSITION_SNAPSHOT'
         ORDER BY item.source_symbol, item.id
        """,
        tenant_id,
        batch_id,
    )
    unresolved_positions = await conn.fetch(
        """
        SELECT record.id AS source_record_id, record.external_record_id,
               record.normalized_payload, record.validation_status,
               record.validation_errors
          FROM ledger.portfolio_import_source_records record
         WHERE record.tenant_id = $1 AND record.batch_id = $2
           AND record.record_type = 'POSITION_SNAPSHOT'
           AND record.instrument_id IS NULL
         ORDER BY record.external_record_id, record.id
        """,
        tenant_id,
        batch_id,
    )
    persisted_summary = _json_object(batch["preview_summary"])
    position_counts = persisted_summary.get("positionCounts", persisted_summary)
    if "total" not in position_counts:
        position_counts = {"total": len(positions), **position_counts}
    history_summary = persisted_summary.get(
        "history", {"importable": 0, "duplicated": 0}
    )
    excluded_summary = persisted_summary.get(
        "excluded", {"cashManagement": 0, "nonPosting": 0}
    )
    source_window = persisted_summary.get("sourceWindow", {})
    warnings = persisted_summary.get("warnings", [])
    metrics_status: str | None = None
    if batch["status"] == "CONFIRMED":
        job_status = batch["data_job_status"]
        statuses = set(batch["metric_statuses"] or [])
        if job_status in {"PENDING", "RUNNING"}:
            metrics_status = "PENDING"
        elif job_status == "FAILED":
            metrics_status = "FAILED"
        elif "CONFLICT" in statuses:
            metrics_status = "CONFLICT"
        elif "PARTIAL" in statuses:
            metrics_status = "PARTIAL"
        elif statuses == {"READY"}:
            metrics_status = "READY"
        else:
            metrics_status = "PENDING"
    position_dtos = [
        {
            "itemId": str(row["item_id"]),
            "instrumentId": str(row["instrument_id"]),
            "symbol": str(row["source_symbol"]),
            "name": str(row["canonical_name"]),
            "quantity": _decimal_text(row["quantity"]),
            "unitCost": _decimal_text(row["unit_cost"]),
            "marketValue": _decimal_text(row["source_market_value"]),
            "sourceNav": _decimal_text(row["source_nav"]),
            "sourceNavDate": row["source_nav_date"],
            "comparisonStatus": str(row["comparison_status"]),
            "candidateHoldingStartedOn": row["candidate_holding_started_on"],
            "holdingStartStatus": str(row["holding_start_status"]),
            "holdingStartMethod": str(row["holding_start_method"]),
            "holdingStartConfidence": str(row["holding_start_confidence"]),
            "holdingStartReasons": list(row["holding_start_reasons"] or []),
            "validationStatus": str(row["validation_status"]),
            "validationErrors": _json_array(row["validation_errors"]),
        }
        for row in positions
    ]
    for row in unresolved_positions:
        payload = _json_object(row["normalized_payload"])
        position_dtos.append(
            {
                "itemId": f"unresolved:{row['source_record_id']}",
                "instrumentId": None,
                "symbol": str(payload.get("productCode", "")),
                "name": str(payload.get("productName", "")),
                "quantity": str(payload.get("quantity", "")),
                "unitCost": str(payload.get("averageCost", "")),
                "marketValue": str(payload.get("marketValue", "")),
                "sourceNav": (
                    str(payload["latestNav"])
                    if payload.get("latestNav") is not None
                    else None
                ),
                "sourceNavDate": payload.get("latestNavDate"),
                "comparisonStatus": "CONFLICT",
                "candidateHoldingStartedOn": None,
                "holdingStartStatus": "INSUFFICIENT_DATA",
                "holdingStartMethod": "INSUFFICIENT_DATA",
                "holdingStartConfidence": "NONE",
                "holdingStartReasons": ["UNKNOWN_FUND_CODE"],
                "validationStatus": str(row["validation_status"]),
                "validationErrors": _json_array(row["validation_errors"]),
            }
        )
    position_dtos.sort(key=lambda item: (item["symbol"], item["itemId"]))

    return {
        "batchId": str(batch["batch_id"]),
        "status": str(batch["status"]),
        "version": int(batch["version"]),
        "sourceType": str(batch["source_type"]),
        "sourceAsOf": batch["source_as_of"],
        "package": {
            "schemaVersion": batch["package_schema_version"],
            "parserVersion": batch["parser_version"],
            "positionsAsOf": batch["source_as_of"],
            "sourceWindowStart": source_window.get("start"),
            "sourceWindowEnd": source_window.get("end"),
        },
        "summary": position_counts,
        "historySummary": history_summary,
        "excludedSummary": excluded_summary,
        "warnings": warnings,
        "validationStatus": str(batch["validation_status"]),
        "blockingErrors": _json_array(batch["validation_errors"]),
        "canConfirm": batch["status"] == "DRAFT"
        and batch["validation_status"] == "VALID",
        "metricsStatus": metrics_status,
        "sourceBinding": {
            "bindingId": str(batch["binding_id"]),
            "status": str(batch["binding_status"]),
            "displayName": str(batch["binding_display_name"]),
        },
        "account": {
            "accountId": str(batch["account_id"]),
            "name": str(batch["account_name"]),
        },
        "positions": position_dtos,
        "createdAt": batch["created_at"],
        "updatedAt": batch["updated_at"],
    }


async def list_import_batches(
    conn: asyncpg.Connection,
    tenant_id: str,
    *,
    source_type: str,
    cursor: datetime | None,
    limit: int,
) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT b.id AS batch_id, b.status, b.version, b.source_as_of,
               b.preview_summary, b.validation_status, b.created_at,
               account.id AS account_id, account.name AS account_name
          FROM ledger.portfolio_import_batches b
          JOIN identity.accounts account
            ON account.tenant_id = b.tenant_id AND account.id = b.account_id
         WHERE b.tenant_id = $1 AND b.source_type = $2
           AND ($3::timestamptz IS NULL OR b.created_at < $3)
         ORDER BY b.created_at DESC, b.id DESC
         LIMIT $4
        """,
        tenant_id,
        source_type,
        cursor,
        limit + 1,
    )
    has_more = len(rows) > limit
    page = rows[:limit]

    def position_counts(row: asyncpg.Record) -> dict[str, int]:
        summary = _json_object(row["preview_summary"])
        return summary.get("positionCounts", summary)

    return {
        "items": [
            {
                "batchId": str(row["batch_id"]),
                "status": str(row["status"]),
                "version": int(row["version"]),
                "sourceAsOf": row["source_as_of"],
                "summary": position_counts(row),
                "validationStatus": str(row["validation_status"]),
                "account": {
                    "accountId": str(row["account_id"]),
                    "name": str(row["account_name"]),
                },
                "createdAt": row["created_at"],
            }
            for row in page
        ],
        "nextCursor": page[-1]["created_at"].isoformat() if has_more else None,
    }


async def retry_position_metrics(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    batch_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Retry official NAV backfill plus materialization without touching the ledger.

    A terminal first attempt may have failed before any NAV was persisted.  A
    rebuild-only retry would then materialize PENDING from an empty cache and
    falsely complete, so the explicit user retry deliberately starts at the
    provider backfill boundary.
    """
    batch = await conn.fetchrow(
        """
        SELECT id, account_id, source_binding_id, source_as_of
          FROM ledger.portfolio_import_batches
         WHERE tenant_id = $1 AND id = $2
           AND source_type = 'TIANTIAN_FUND_ZIP' AND status = 'CONFIRMED'
        """,
        tenant_id,
        batch_id,
    )
    if batch is None:
        raise NotFoundError("已确认的天天基金导入批次不存在")
    command_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    job_key = f"position-period-metrics:{batch_id}:retry:{command_hash}"
    created = await conn.fetchval(
        """
        INSERT INTO workflow.job_runs (
            tenant_id, job_key, status, job_type, payload, available_at
        ) VALUES (
            $1, $2, 'PENDING', 'FUND_NAV_BACKFILL', $3::jsonb, now()
        )
        ON CONFLICT (tenant_id, job_key) DO NOTHING
        RETURNING id
        """,
        tenant_id,
        job_key,
        json.dumps(
            {
                "batchId": batch_id,
                "accountId": str(batch["account_id"]),
                "sourceBindingId": str(batch["source_binding_id"]),
                "sourceAsOf": batch["source_as_of"].isoformat(),
            },
            ensure_ascii=False,
        ),
    )
    return {"job_key": job_key, "created": created is not None}
