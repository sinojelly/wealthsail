"""Repository contracts for draft-only persistence and recoverable batch reads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.errors import IdempotencyConflictError, NotFoundError, VersionConflictError
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
    PreviewSourceRecord,
)


class _RecordingConnection:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.fetchval_results = iter(["batch-1", "binding-1"])
        self.executemany_calls: list[tuple[str, list[tuple]]] = []

    async def execute(self, sql: str, *_args):
        self.sql.append(sql)
        return "INSERT 0 1"

    async def fetchval(self, sql: str, *_args):
        self.sql.append(sql)
        return next(self.fetchval_results)

    async def executemany(self, sql: str, rows):
        self.sql.append(sql)
        self.executemany_calls.append((sql, list(rows)))


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
            source_window_start=datetime.fromisoformat("2020-01-01T00:00:00").date(),
            source_window_end=datetime.fromisoformat("2026-08-28T00:00:00").date(),
        ),
        review_required=(),
        excluded_summary=SimpleNamespace(cash_management=10, non_posting=2),
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
    assert "tiantian-preview/3.0.0" in combined
    assert result["batch_id"] == "batch-1"
    assert result["binding_id"] == "binding-1"
    assert len(result["item_ids"]) == 1


@pytest.mark.asyncio
async def test_persist_preview_keeps_unknown_fund_out_of_instrument_items() -> None:
    conn = _RecordingConnection()
    package = SimpleNamespace(
        manifest=SimpleNamespace(
            schema_version="wealthsail-tiantian-import/v2",
            collector_version="0.2.0",
            positions_as_of=datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
            external_account_key="1f8a8407-9d91-4a1d-b954-9ad46fbd9585",
            source_window_start=datetime.fromisoformat("2020-01-01T00:00:00").date(),
            source_window_end=datetime.fromisoformat("2026-08-28T00:00:00").date(),
        ),
        review_required=(),
        excluded_summary=SimpleNamespace(cash_management=0, non_posting=0),
    )
    source_payload = {
        "sourceProvider": "TIANTIAN_FUND",
        "sourceRecordId": "holding-999999",
        "productCode": "999999",
        "productName": "来源基金",
        "quantity": "100",
        "averageCost": "1.2",
        "costAmount": "120",
        "marketValue": "130",
        "capturedAt": "2026-08-28T08:00:00+08:00",
    }
    plan = PreviewPlan(
        items=(
            PreviewItem(
                instrument_id=None,
                product_code="999999",
                product_name="来源基金",
                external_record_id="holding-999999",
                quantity=Decimal("100"),
                unit_cost=Decimal("1.2"),
                market_value=Decimal("130"),
                source_nav=None,
                source_nav_date=None,
                comparison_status="CONFLICT",
                candidate_holding_started_on=None,
                holding_start_status="INSUFFICIENT_DATA",
                source_payload=source_payload,
            ),
        ),
        source_records=(
            PreviewSourceRecord(
                record_type="POSITION_SNAPSHOT",
                external_record_id="holding-999999",
                instrument_id=None,
                occurred_at=datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
                classification="UNKNOWN_FUND_CODE",
                normalized_payload=source_payload,
                content_hash="a" * 64,
                validation_status="INVALID",
                validation_errors=("UNKNOWN_FUND_CODE",),
            ),
        ),
        warnings=(),
        blocking_errors=("UNKNOWN_FUND_CODE",),
        history_importable=0,
        history_duplicated=0,
        can_confirm=False,
    )

    result = await persist_preview(
        conn,
        tenant_id="tenant-1",
        account_id="account-1",
        created_by="user-1",
        binding_id=None,
        package=package,
        package_content_hash="b" * 64,
        target_account_state_hash="c" * 64,
        idempotency_key="preview-unknown",
        account_display_name="天天基金账户",
        plan=plan,
    )

    item_rows = next(
        rows for sql, rows in conn.executemany_calls if "portfolio_import_items" in sql
    )
    source_rows = next(
        rows
        for sql, rows in conn.executemany_calls
        if "portfolio_import_source_records" in sql
    )
    assert item_rows == []
    assert len(source_rows) == 1
    assert result["item_ids"] == ["unresolved:holding-999999"]


class _ReadConnection:
    def __init__(
        self,
        *,
        status: str = "DRAFT",
        job_status: str = "RUNNING",
        parser_version: str = "tiantian-preview/3.0.0",
    ) -> None:
        self.status = status
        self.job_status = job_status
        self.parser_version = parser_version
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, sql: str, *_args):
        assert "WHERE b.tenant_id = $1 AND b.id = $2" in sql
        return {
            "batch_id": "batch-1",
            "status": self.status,
            "version": 1,
            "source_type": "TIANTIAN_FUND_ZIP",
            "source_as_of": datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
            "package_schema_version": "wealthsail-tiantian-import/v2",
            "parser_version": self.parser_version,
            "target_account_state_hash": hashlib.sha256(
                json.dumps([], separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "latest_confirmed_source_as_of": None,
            "terminal_reason": None,
            "supersedes_batch_id": None,
            "preview_summary": (
                '{"positionCounts":{"total":1,"new":1},'
                '"history":{"importable":0,"duplicated":0,"reviewRequired":0},'
                '"excluded":{"cashManagement":10,"nonPosting":2},'
                '"sourceWindow":{"start":"2020-01-01","end":"2026-08-28"},'
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
            "data_job_status": self.job_status,
            "metric_statuses": ["READY"],
        }

    async def fetch(self, sql: str, *_args):
        self.fetch_calls.append((sql, _args))
        if "FROM ledger.ledger_events event" in sql:
            return []
        if "portfolio_import_items" in sql:
            return [
                {
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
                    "holding_start_method": "INSUFFICIENT_DATA",
                    "holding_start_confidence": "NONE",
                    "holding_start_reasons": ["NO_SHARE_TRANSACTIONS"],
                    "validation_status": "VALID",
                    "validation_errors": "[]",
                }
            ]
        if "portfolio_import_source_records" in sql:
            return []
        return [
            {
                "batch_id": "batch-1",
                "status": "DRAFT",
                "version": 1,
                "source_as_of": datetime.fromisoformat("2026-08-28T08:00:00+08:00"),
                "preview_summary": {
                    "positionCounts": {"total": 1, "new": 1},
                    "history": {"importable": 0, "duplicated": 0, "reviewRequired": 0},
                    "excluded": {"cashManagement": 10, "nonPosting": 2},
                    "sourceWindow": {"start": "2020-01-01", "end": "2026-08-28"},
                    "warnings": ["PARTIAL_HISTORY"],
                },
                "validation_status": "VALID",
                "target_account_state_hash": hashlib.sha256(
                    json.dumps([], separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "parser_version": self.parser_version,
                "terminal_reason": None,
                "supersedes_batch_id": None,
                "binding_status": "PENDING",
                "latest_confirmed_source_as_of": None,
                "account_id": "account-1",
                "account_name": "天天基金账户",
                "created_at": datetime.fromisoformat("2026-08-28T08:01:00+08:00"),
                "updated_at": datetime.fromisoformat("2026-08-28T08:02:00+08:00"),
            }
        ]


class _ManualBatchReadConnection(_ReadConnection):
    """Model the database result for a batch owned by another import flow."""

    async def fetchrow(self, sql: str, *_args):
        if "b.source_type = 'TIANTIAN_FUND_ZIP'" in sql:
            return None
        return await super().fetchrow(sql, *_args)


@pytest.mark.asyncio
async def test_get_batch_rejects_a_manual_import_batch() -> None:
    """Removing the source predicate must expose the regression caught here."""
    conn = _ManualBatchReadConnection()

    with pytest.raises(NotFoundError, match="天天基金导入批次不存在"):
        await get_import_batch(conn, "tenant-1", "manual-batch-1")


@pytest.mark.asyncio
async def test_get_and_list_batch_support_page_refresh_and_history() -> None:
    conn = _ReadConnection()

    detail = await get_import_batch(conn, "tenant-1", "batch-1")
    listing = await list_import_batches(
        conn,
        "tenant-1",
        source_type="TIANTIAN_FUND_ZIP",
        status=None,
        cursor=None,
        limit=20,
    )

    assert detail["batchId"] == "batch-1"
    assert detail["positions"][0]["comparisonStatus"] == "NEW"
    assert detail["summary"] == {"total": 1, "new": 1}
    assert detail["historySummary"] == {
        "importable": 0,
        "duplicated": 0,
        "reviewRequired": 0,
    }
    assert detail["excludedSummary"] == {"cashManagement": 10, "nonPosting": 2}
    assert detail["package"]["sourceWindowStart"] == "2020-01-01"
    assert detail["package"]["sourceWindowEnd"] == "2026-08-28"
    assert detail["warnings"] == ["PARTIAL_HISTORY"]
    assert detail["metricsStatus"] is None
    assert detail["freshness"] == "FRESH"
    assert detail["freshnessReasons"] == []
    assert detail["availableActions"] == ["VIEW", "CONFIRM", "CANCEL", "REPARSE"]
    assert detail["terminalReason"] is None
    assert detail["supersedesBatchId"] is None
    assert listing["items"][0]["batchId"] == "batch-1"
    assert listing["items"][0]["summary"] == {"total": 1, "new": 1}
    assert listing["items"][0]["freshness"] == "FRESH"
    assert listing["items"][0]["availableActions"] == [
        "VIEW",
        "CONFIRM",
        "CANCEL",
        "REPARSE",
    ]
    assert listing["nextCursor"] is None

    confirmed = await get_import_batch(
        _ReadConnection(status="CONFIRMED", job_status="RUNNING"),
        "tenant-1",
        "batch-1",
    )
    assert confirmed["metricsStatus"] == "PENDING"
    assert confirmed["availableActions"] == ["VIEW"]


@pytest.mark.asyncio
async def test_old_preview_engine_marks_draft_stale_and_removes_confirm_action() -> None:
    detail = await get_import_batch(
        _ReadConnection(parser_version="tiantian-package/v2"),
        "tenant-1",
        "batch-1",
    )

    assert detail["freshness"] == "STALE"
    assert detail["freshnessReasons"] == ["PREVIEW_ENGINE_CHANGED"]
    assert detail["availableActions"] == ["VIEW", "CANCEL", "REPARSE"]


@pytest.mark.asyncio
async def test_list_batches_pushes_draft_status_into_the_database_query() -> None:
    conn = _ReadConnection()

    result = await list_import_batches(
        conn,
        "tenant-1",
        source_type="TIANTIAN_FUND_ZIP",
        status="DRAFT",
        cursor=None,
        limit=20,
    )

    list_sql, list_args = next(
        (sql, args)
        for sql, args in conn.fetch_calls
        if "ORDER BY b.created_at DESC" in sql
    )
    assert "($3::text IS NULL OR b.status = $3)" in list_sql
    assert list_args[2] == "DRAFT"
    assert result["items"][0]["status"] == "DRAFT"


class _UnresolvedReadConnection(_ReadConnection):
    async def fetch(self, sql: str, *_args):
        if "portfolio_import_items" in sql:
            return []
        if "portfolio_import_source_records" in sql:
            return [
                {
                    "source_record_id": "source-1",
                    "external_record_id": "holding-999999",
                    "normalized_payload": {
                        "productCode": "999999",
                        "productName": "来源基金",
                        "quantity": "100",
                        "averageCost": "1.2",
                        "marketValue": "130",
                        "latestNav": "1.3",
                        "latestNavDate": "2026-08-27",
                    },
                    "validation_status": "INVALID",
                    "validation_errors": ["UNKNOWN_FUND_CODE"],
                }
            ]
        return await super().fetch(sql, *_args)


@pytest.mark.asyncio
async def test_get_batch_restores_unknown_fund_as_visible_conflict() -> None:
    conn = _UnresolvedReadConnection()
    detail = await get_import_batch(conn, "tenant-1", "batch-1")

    assert detail["positions"] == [
        {
            "itemId": "unresolved:source-1",
            "instrumentId": None,
            "symbol": "999999",
            "name": "来源基金",
            "quantity": "100",
            "unitCost": "1.2",
            "marketValue": "130",
            "sourceNav": "1.3",
            "sourceNavDate": "2026-08-27",
            "comparisonStatus": "CONFLICT",
            "candidateHoldingStartedOn": None,
            "holdingStartStatus": "INSUFFICIENT_DATA",
            "holdingStartMethod": "INSUFFICIENT_DATA",
            "holdingStartConfidence": "NONE",
            "holdingStartReasons": ["UNKNOWN_FUND_CODE"],
            "validationStatus": "INVALID",
            "validationErrors": ["UNKNOWN_FUND_CODE"],
        }
    ]


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
    # 人工重试必须重新拉取可能完全缺失的官方净值；仅重算已有缓存会永久 PENDING。
    assert "FUND_NAV_BACKFILL" in combined
    assert "POSITION_PERIOD_METRICS_REBUILD" not in combined
    assert "ON CONFLICT (tenant_id, job_key) DO NOTHING" in combined
    assert "ledger_events" not in combined


class _FirstBindingConflictConnection:
    def __init__(self) -> None:
        self.fetch_count = 0

    async def fetch(self, sql: str, *_args):
        self.fetch_count += 1
        if self.fetch_count == 1:
            return [
                {
                    "product_code": "110022",
                    "instrument_id": "fund-1",
                    "canonical_name": "标准基金",
                }
            ]
        assert "ledger_legs" in sql
        assert "$4::uuid IS NULL OR" in sql
        return [{"instrument_id": "fund-1"}]


@pytest.mark.asyncio
async def test_first_binding_treats_existing_manual_account_facts_as_conflicts() -> (
    None
):
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
async def test_effect_writer_emits_reversal_without_leg_and_adjustment_with_one_leg() -> (
    None
):
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

    async def execute(self, _sql: str, *_args):
        return "SELECT 1"


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
        "parser_version": "tiantian-preview/3.0.0",
        "latest_confirmed_source_as_of": None,
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
async def test_same_confirm_key_with_different_decisions_is_an_idempotency_conflict() -> (
    None
):
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
async def test_confirm_rechecks_preview_engine_under_the_batch_lock() -> None:
    empty_account_hash = hashlib.sha256(
        json.dumps([], separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with pytest.raises(VersionConflictError, match="预览引擎"):
        await confirm_draft(
            _TerminalConnection(
                _terminal_batch(
                    status="DRAFT",
                    version=1,
                    target_account_state_hash=empty_account_hash,
                    parser_version="tiantian-package/v2",
                )
            ),
            tenant_id="tenant-1",
            batch_id="batch-1",
            expected_version=1,
            decisions={},
            confirm_empty=False,
            idempotency_key="confirm-stale-engine",
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
