"""Repository contracts for draft-only persistence and recoverable batch reads."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.portfolio.imports.repository import (
    get_import_batch,
    load_preview_context,
    list_import_batches,
    persist_preview,
)
from backend.portfolio.imports.service import PreviewItem, PreviewPlan


class _RecordingConnection:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.fetchval_results = iter(["batch-1", "binding-1"])

    async def execute(self, sql: str, *_args):
        self.sql.append(sql)
        return "INSERT 0 1"

    async def fetchval(self, sql: str, *_args):
        self.sql.append(sql)
        return next(self.fetchval_results)

    async def executemany(self, sql: str, _rows):
        self.sql.append(sql)


def _plan() -> PreviewPlan:
    return PreviewPlan(
        items=(
            PreviewItem(
                instrument_id="fund-1",
                product_code="110022",
                product_name="标准基金",
                external_record_id="holding-110022",
                quantity=Decimal("100"),
                unit_cost=Decimal("1.20"),
                market_value=Decimal("130"),
                source_nav=None,
                source_nav_date=None,
                comparison_status="NEW",
                candidate_holding_started_on=None,
                holding_start_status="INSUFFICIENT_DATA",
                source_payload={"productCode": "110022"},
            ),
        ),
        source_records=(),
        warnings=(),
        blocking_errors=(),
        history_importable=0,
        history_duplicated=0,
        can_confirm=True,
    )


@pytest.mark.asyncio
async def test_persist_preview_writes_draft_tables_and_never_ledger_events() -> None:
    conn = _RecordingConnection()
    package = SimpleNamespace(
        manifest=SimpleNamespace(
            schema_version="wealthsail-tiantian-import/v2",
            collector_version="0.2.0",
            positions_as_of=datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
            external_account_key="1f8a8407-9d91-4a1d-b954-9ad46fbd9585",
        )
    )

    result = await persist_preview(
        conn,
        tenant_id="tenant-1",
        account_id="account-1",
        created_by="user-1",
        binding_id=None,
        package=package,
        package_content_hash="a" * 64,
        target_account_state_hash="b" * 64,
        idempotency_key="preview-1",
        account_display_name="天天基金账户",
        plan=_plan(),
    )

    combined = "\n".join(conn.sql)
    assert "portfolio_import_batches" in combined
    assert "portfolio_source_bindings" in combined
    assert "portfolio_import_items" in combined
    assert "portfolio_import_source_records" in combined
    assert "ledger_events" not in combined
    assert combined.count("$5::text") >= 2
    assert result == {"batch_id": "batch-1", "binding_id": "binding-1"}


class _ReadConnection:
    async def fetchrow(self, sql: str, *_args):
        assert "WHERE b.tenant_id = $1 AND b.id = $2" in sql
        return {
            "batch_id": "batch-1",
            "status": "DRAFT",
            "version": 1,
            "source_type": "TIANTIAN_FUND_ZIP",
            "source_as_of": datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
            "package_schema_version": "wealthsail-tiantian-import/v2",
            "parser_version": "tiantian-package/v2",
            "preview_summary": (
                '{"positionCounts":{"new":1},'
                '"history":{"importable":0,"duplicated":0},'
                '"warnings":["PARTIAL_HISTORY"]}'
            ),
            "validation_status": "VALID",
            "validation_errors": "[]",
            "binding_id": "binding-1",
            "binding_status": "PENDING",
            "binding_display_name": "天天基金账户",
            "account_id": "account-1",
            "account_name": "天天基金账户",
            "created_at": datetime.fromisoformat("2026-08-28T08:01:00+08:00"),
            "updated_at": datetime.fromisoformat("2026-08-28T08:01:00+08:00"),
        }

    async def fetch(self, sql: str, *_args):
        if "portfolio_import_items" in sql:
            return [{
                "item_id": "item-1",
                "instrument_id": "fund-1",
                "source_symbol": "110022",
                "canonical_name": "标准基金",
                "quantity": Decimal("100"),
                "unit_cost": Decimal("1.20"),
                "source_market_value": Decimal("130"),
                "source_nav": None,
                "source_nav_date": None,
                "comparison_status": "NEW",
                "candidate_holding_started_on": None,
                "holding_start_status": "INSUFFICIENT_DATA",
                "validation_status": "VALID",
                "validation_errors": "[]",
            }]
        return [{
            "batch_id": "batch-1",
            "status": "DRAFT",
            "version": 1,
            "source_as_of": datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
            "preview_summary": {
                "positionCounts": {"new": 1},
                "history": {"importable": 0, "duplicated": 0},
                "warnings": ["PARTIAL_HISTORY"],
            },
            "validation_status": "VALID",
            "account_id": "account-1",
            "account_name": "天天基金账户",
            "created_at": datetime.fromisoformat("2026-08-28T08:01:00+08:00"),
        }]


@pytest.mark.asyncio
async def test_get_and_list_batch_support_page_refresh_and_history() -> None:
    conn = _ReadConnection()

    detail = await get_import_batch(conn, "tenant-1", "batch-1")
    listing = await list_import_batches(
        conn,
        "tenant-1",
        source_type="TIANTIAN_FUND_ZIP",
        cursor=None,
        limit=20,
    )

    assert detail["batchId"] == "batch-1"
    assert detail["positions"][0]["comparisonStatus"] == "NEW"
    assert detail["summary"] == {"new": 1}
    assert detail["historySummary"] == {"importable": 0, "duplicated": 0}
    assert detail["warnings"] == ["PARTIAL_HISTORY"]
    assert listing["items"][0]["batchId"] == "batch-1"
    assert listing["items"][0]["summary"] == {"new": 1}
    assert listing["nextCursor"] is None


class _FirstBindingConflictConnection:
    def __init__(self) -> None:
        self.fetch_count = 0

    async def fetch(self, sql: str, *_args):
        self.fetch_count += 1
        if self.fetch_count == 1:
            return [{
                "product_code": "110022",
                "instrument_id": "fund-1",
                "canonical_name": "标准基金",
            }]
        assert "ledger_legs" in sql
        assert "$4::uuid IS NULL OR" in sql
        return [{"instrument_id": "fund-1"}]


@pytest.mark.asyncio
async def test_first_binding_treats_existing_manual_account_facts_as_conflicts() -> None:
    context = await load_preview_context(
        _FirstBindingConflictConnection(),
        tenant_id="tenant-1",
        account_id="account-1",
        binding_id=None,
        product_codes=["110022"],
        external_transaction_ids=[],
    )

    assert context.account_conflict_instrument_ids == frozenset({"fund-1"})
