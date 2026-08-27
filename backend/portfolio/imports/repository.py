"""Tenant-scoped persistence for Tiantian import previews and batch reads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import asyncpg

from backend.errors import NotFoundError, ValidationError_

if TYPE_CHECKING:
    from backend.portfolio.imports.service import PreviewContext, PreviewPlan, TargetAccount
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
) -> dict[str, str]:
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
                "positionCounts": plan.summary,
                "history": {
                    "importable": plan.history_importable,
                    "duplicated": plan.history_duplicated,
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

    item_rows = [
        (
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
            json.dumps(item.source_payload, ensure_ascii=False),
        )
        for index, item in enumerate(plan.items, start=1)
    ]
    await conn.executemany(
        """
        INSERT INTO ledger.portfolio_import_items (
            tenant_id, batch_id, item_type, instrument_id, quantity, unit_cost,
            trade_date, source_row, validation_status, validation_errors,
            external_record_id, source_symbol, source_market_value, source_nav,
            source_nav_date, comparison_status, candidate_holding_started_on,
            holding_start_status, source_payload
        ) VALUES (
            $1, $2, 'POSITION_SNAPSHOT', $3, $4, $5, $6, $7, $8,
            $9::jsonb, $10, $11, $12, $13, $14, $15, $16, $17, $18::jsonb
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
    return {"batch_id": batch_id, "binding_id": effective_binding_id}


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
               account.id AS account_id, account.name AS account_name
          FROM ledger.portfolio_import_batches b
          JOIN identity.accounts account
            ON account.tenant_id = b.tenant_id AND account.id = b.account_id
          LEFT JOIN ledger.portfolio_source_bindings binding
            ON binding.tenant_id = b.tenant_id AND binding.id = b.source_binding_id
         WHERE b.tenant_id = $1 AND b.id = $2
        """,
        tenant_id,
        batch_id,
    )
    if batch is None:
        raise NotFoundError("导入批次不存在")
    positions = await conn.fetch(
        """
        SELECT item.id AS item_id, item.instrument_id, item.source_symbol,
               instrument.canonical_name, item.quantity, item.unit_cost,
               item.source_market_value, item.source_nav, item.source_nav_date,
               item.comparison_status, item.candidate_holding_started_on,
               item.holding_start_status, item.validation_status,
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
    persisted_summary = _json_object(batch["preview_summary"])
    position_counts = persisted_summary.get("positionCounts", persisted_summary)
    history_summary = persisted_summary.get(
        "history", {"importable": 0, "duplicated": 0}
    )
    warnings = persisted_summary.get("warnings", [])
    return {
        "batchId": str(batch["batch_id"]),
        "status": str(batch["status"]),
        "version": int(batch["version"]),
        "sourceType": str(batch["source_type"]),
        "sourceAsOf": batch["source_as_of"],
        "package": {
            "schemaVersion": batch["package_schema_version"],
            "parserVersion": batch["parser_version"],
        },
        "summary": position_counts,
        "historySummary": history_summary,
        "warnings": warnings,
        "validationStatus": str(batch["validation_status"]),
        "blockingErrors": _json_array(batch["validation_errors"]),
        "canConfirm": batch["status"] == "DRAFT"
        and batch["validation_status"] == "VALID",
        "sourceBinding": {
            "bindingId": str(batch["binding_id"]),
            "status": str(batch["binding_status"]),
            "displayName": str(batch["binding_display_name"]),
        },
        "account": {
            "accountId": str(batch["account_id"]),
            "name": str(batch["account_name"]),
        },
        "positions": [
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
                "validationStatus": str(row["validation_status"]),
                "validationErrors": _json_array(row["validation_errors"]),
            }
            for row in positions
        ],
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
