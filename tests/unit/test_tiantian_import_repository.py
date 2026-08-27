"""Repository contracts for draft-only persistence and recoverable batch reads."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.errors import IdempotencyConflictError, VersionConflictError
from backend.portfolio.imports.repository import (
    cancel_draft,
    confirm_draft,
    get_import_batch,
    load_preview_context,
    list_import_batches,
    persist_preview,
    retry_position_metrics,
    write_ledger_effects,
)
from backend.portfolio.imports.service import (
    ConfirmationItem,
    LedgerEffect,
    PreviewItem,
    PreviewPlan,
)


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


class _RetryMetricsConnection:
    def __init__(self, *, created: bool = True) -> None:
        self.created = created
        self.sql: list[str] = []
        self.args: tuple = ()

    async def fetchrow(self, sql: str, *_args):
        self.sql.append(sql)
        return {
            "id": "batch-1",
            "account_id": "account-1",
            "source_binding_id": "binding-1",
            "source_as_of": datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
        }

    async def fetchval(self, sql: str, *args):
        self.sql.append(sql)
        self.args = args
        return "job-1" if self.created else None


@pytest.mark.asyncio
async def test_metrics_retry_is_durable_idempotent_and_never_replays_ledger() -> None:
    first = _RetryMetricsConnection()
    replay = _RetryMetricsConnection(created=False)

    created = await retry_position_metrics(
        first,
        tenant_id="tenant-1",
        batch_id="batch-1",
        idempotency_key="retry-command-1",
    )
    repeated = await retry_position_metrics(
        replay,
        tenant_id="tenant-1",
        batch_id="batch-1",
        idempotency_key="retry-command-1",
    )

    assert created["created"] is True
    assert repeated == {"job_key": created["job_key"], "created": False}
    combined = "\n".join(first.sql + replay.sql)
    assert "POSITION_PERIOD_METRICS_REBUILD" in combined
    assert "ON CONFLICT (tenant_id, job_key) DO NOTHING" in combined
    assert "ledger_events" not in combined


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


class _EffectConnection:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.event_ids = iter(["reversal-event", "adjustment-event"])
        self.legs: list[tuple] = []

    async def fetchval(self, sql: str, *_args):
        self.sql.append(sql)
        return next(self.event_ids)

    async def execute(self, sql: str, *args):
        self.sql.append(sql)
        if "ledger_legs" in sql:
            self.legs.append(args)
        return "INSERT 0 1"


@pytest.mark.asyncio
async def test_effect_writer_emits_reversal_without_leg_and_adjustment_with_one_leg() -> None:
    conn = _EffectConnection()
    item = ConfirmationItem(
        item_id="item-1",
        instrument_id="fund-1",
        quantity=Decimal("125.5"),
        unit_cost=Decimal("1.2345"),
        trade_date=datetime.fromisoformat("2026-08-28T08:00:00+08:00").date(),
        comparison_status="CHANGED",
    )
    effects = (
        LedgerEffect("item-1", "REVERSAL", "old-event"),
        LedgerEffect("item-1", "ADJUSTMENT", None),
    )

    event_ids = await write_ledger_effects(
        conn,
        tenant_id="tenant-1",
        account_id="account-1",
        batch_id="batch-1",
        items={"item-1": item},
        effects=effects,
    )

    combined = "\n".join(conn.sql)
    assert "'REVERSAL'" in combined
    assert "'ADJUSTMENT'" in combined
    assert "'BUY'" not in combined and "'SELL'" not in combined
    assert len(conn.legs) == 1
    assert conn.legs[0][-2:] == (Decimal("125.5"), Decimal("1.2345"))
    assert event_ids == ["reversal-event", "adjustment-event"]


class _TerminalConnection:
    def __init__(self, batch: dict) -> None:
        self.batch = batch

    async def fetchrow(self, _sql: str, *_args):
        return self.batch

    async def fetchval(self, _sql: str, *_args):
        return 0

    async def fetch(self, _sql: str, *_args):
        return []


def _terminal_batch(**overrides) -> dict:
    batch = {
        "batch_id": "batch-1",
        "account_id": "account-1",
        "source_binding_id": "binding-1",
        "source_as_of": datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
        "status": "CONFIRMED",
        "version": 2,
        "validation_status": "VALID",
        "target_account_state_hash": "a" * 64,
        "preview_summary": "{}",
        "terminal_command_type": "CONFIRM",
        "terminal_idempotency_key": "confirm-1",
        "terminal_content_hash": "b" * 64,
        "binding_status": "ACTIVE",
    }
    batch.update(overrides)
    return batch


@pytest.mark.asyncio
async def test_confirm_command_replays_exact_terminal_content() -> None:
    result = await confirm_draft(
        _TerminalConnection(_terminal_batch()),
        tenant_id="tenant-1",
        batch_id="batch-1",
        expected_version=1,
        decisions={},
        confirm_empty=False,
        idempotency_key="confirm-1",
        command_content_hash="b" * 64,
    )

    assert result["already_applied"] is True
    assert result["version"] == 2


@pytest.mark.asyncio
async def test_same_confirm_key_with_different_decisions_is_an_idempotency_conflict() -> None:
    with pytest.raises(IdempotencyConflictError):
        await confirm_draft(
            _TerminalConnection(_terminal_batch()),
            tenant_id="tenant-1",
            batch_id="batch-1",
            expected_version=1,
            decisions={},
            confirm_empty=False,
            idempotency_key="confirm-1",
            command_content_hash="c" * 64,
        )


@pytest.mark.asyncio
async def test_stale_expected_version_fails_before_any_effect_write() -> None:
    with pytest.raises(VersionConflictError, match="刷新"):
        await confirm_draft(
            _TerminalConnection(_terminal_batch(status="DRAFT", version=2)),
            tenant_id="tenant-1",
            batch_id="batch-1",
            expected_version=1,
            decisions={},
            confirm_empty=False,
            idempotency_key="confirm-1",
            command_content_hash="b" * 64,
        )


@pytest.mark.asyncio
async def test_confirmed_batch_cannot_be_cancelled() -> None:
    with pytest.raises(VersionConflictError):
        await cancel_draft(
            _TerminalConnection(_terminal_batch()),
            tenant_id="tenant-1",
            batch_id="batch-1",
            expected_version=1,
            idempotency_key="cancel-1",
            command_content_hash="d" * 64,
        )
