"""CSV position imports are validated, idempotent, and ledger-backed."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.common.tenancy import TenantContext
from backend.errors import (
    DataUnavailableError,
    ForbiddenError,
    IdempotencyConflictError,
    ValidationError_,
)
from backend.portfolio import repository, service


def test_csv_parser_accepts_quoted_names_and_preserves_decimal_precision() -> None:
    rows = service.parse_csv_position_rows(
        'STOCK,600036,"招商银行, A股",8500,38.8200\n'
        "FUND,110022,易方达消费行业,118420.52,1.7326"
    )

    assert [
        (
            row.row_number,
            row.asset_type,
            row.symbol,
            row.display_name,
            row.quantity,
            row.unit_cost,
        )
        for row in rows
    ] == [
        (1, "STOCK", "600036", "招商银行, A股", Decimal("8500"), Decimal("38.8200")),
        (
            2,
            "FUND",
            "110022",
            "易方达消费行业",
            Decimal("118420.52"),
            Decimal("1.7326"),
        ),
    ]


@pytest.mark.parametrize(
    ("csv_data", "field"),
    [
        ("STOCK,600036,招商银行,8500", "rows[1]"),
        ("BOND,600036,招商银行,8500,38.82", "rows[1].assetType"),
        ("STOCK,600036,招商银行,0,38.82", "rows[1].quantity"),
        ("STOCK,600036,招商银行,8500,nan", "rows[1].avgCost"),
    ],
)
def test_csv_parser_rejects_the_entire_batch_with_a_row_field_error(
    csv_data: str, field: str
) -> None:
    with pytest.raises(ValidationError_) as exc_info:
        service.parse_csv_position_rows(csv_data)

    assert exc_info.value.field_errors[0]["field"] == field


def test_csv_parser_enforces_numeric_28_10_database_bounds() -> None:
    accepted = service.parse_csv_position_rows(
        "STOCK,600036,招商银行,999999999999999999.9999999999,0.0000000001"
    )[0]
    assert accepted.quantity == Decimal("999999999999999999.9999999999")

    for quantity in ("1000000000000000000", "1e18"):
        with pytest.raises(ValidationError_) as exc_info:
            service.parse_csv_position_rows(
                f"STOCK,600036,招商银行,{quantity},38.82"
            )
        assert exc_info.value.field_errors[0]["field"] == "rows[1].quantity"


@pytest.mark.asyncio
async def test_viewer_cannot_submit_csv_positions() -> None:
    ctx = TenantContext("viewer-1", "tenant-1", tenant_role="VIEWER")

    with pytest.raises(ForbiddenError):
        await service.import_csv_positions(
            MagicMock(),
            ctx,
            "STOCK,600036,招商银行,8500,38.82",
            baseline_date=date(2026, 8, 30),
            idempotency_key="csv-viewer-1",
        )


@asynccontextmanager
async def _tx(_pool, _user_id, _tenant_id):
    yield MagicMock()


@pytest.mark.asyncio
async def test_csv_import_replays_same_confirmed_batch_before_provider_calls() -> None:
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    prior = {
        "batch_id": "batch-1",
        "source_type": "CSV",
        "source_content_hash": service._csv_import_content_hash(
            service.parse_csv_position_rows("STOCK,600036,招商银行,8500,38.82"),
            baseline_date=date(2026, 8, 30),
        ),
        "status": "CONFIRMED",
        "version": 2,
        "position_count": 1,
        "event_ids": ["event-1"],
    }

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_tx),
        patch(
            "backend.portfolio.service.repository.find_csv_import_by_idempotency_key",
            AsyncMock(return_value=prior),
        ),
        patch(
            "backend.portfolio.service._resolve_verified_instrument",
            AsyncMock(),
        ) as resolve,
    ):
        result = await service.import_csv_positions(
            MagicMock(),
            ctx,
            "STOCK,600036,招商银行,8500,38.82",
            baseline_date=date(2026, 8, 30),
            idempotency_key="csv-1",
        )

    assert result == {
        "batchId": "batch-1",
        "status": "CONFIRMED",
        "version": 2,
        "importedCount": 1,
        "eventIds": ["event-1"],
        "replayed": True,
    }
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_csv_import_rejects_reusing_a_key_for_different_content() -> None:
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    prior = {
        "batch_id": "batch-1",
        "source_type": "CSV",
        "source_content_hash": "sha256:different",
        "status": "CONFIRMED",
        "version": 2,
        "position_count": 1,
        "event_ids": ["event-1"],
    }

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_tx),
        patch(
            "backend.portfolio.service.repository.find_csv_import_by_idempotency_key",
            AsyncMock(return_value=prior),
        ),
    ):
        with pytest.raises(IdempotencyConflictError):
            await service.import_csv_positions(
                MagicMock(),
                ctx,
                "STOCK,600036,招商银行,8500,38.82",
                baseline_date=date(2026, 8, 30),
                idempotency_key="csv-1",
            )


@pytest.mark.asyncio
async def test_csv_import_confirms_all_rows_and_rebuilds_projections() -> None:
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    created = AsyncMock(return_value="batch-1")
    confirmed = AsyncMock(
        return_value={
            "batch_id": "batch-1",
            "version": 2,
            "event_id": "event-1",
            "event_ids": ["event-1", "event-2"],
            "already_confirmed": False,
        }
    )
    recompute = AsyncMock()

    with (
        patch("backend.portfolio.service.tenant_transaction", new=_tx),
        patch(
            "backend.portfolio.service.repository.find_csv_import_by_idempotency_key",
            AsyncMock(side_effect=[None, None]),
        ),
        patch(
            "backend.portfolio.service._resolve_verified_instrument",
            AsyncMock(
                side_effect=[
                    {
                        "id": None,
                        "symbol": "600036",
                        "name": "招商银行",
                        "asset_type": "CN_STOCK",
                        "market": "SSE",
                    },
                    {
                        "id": None,
                        "symbol": "110022",
                        "name": "易方达消费行业",
                        "asset_type": "OFF_EXCHANGE_FUND",
                        "market": "FUND_CN",
                    },
                ]
            ),
        ),
        patch(
            "backend.portfolio.service.repository.pick_or_create_manual_account",
            AsyncMock(return_value="account-1"),
        ),
        patch(
            "backend.portfolio.service.repository.find_instrument_by_ticker",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.portfolio.service.repository.upsert_instrument",
            AsyncMock(side_effect=["instrument-1", "instrument-2"]),
        ),
        patch(
            "backend.portfolio.service.repository.create_csv_import_draft",
            created,
        ),
        patch(
            "backend.portfolio.service.repository.confirm_import_batch",
            confirmed,
        ),
        patch("backend.portfolio.service.recompute_projections", recompute),
        patch("backend.portfolio.service.write_audit", AsyncMock()),
    ):
        result = await service.import_csv_positions(
            MagicMock(),
            ctx,
            "STOCK,600036,招商银行,8500,38.82\nFUND,110022,易方达消费行业,100,1.70",
            baseline_date=date(2026, 8, 30),
            idempotency_key="csv-success-1",
        )

    assert result == {
        "batchId": "batch-1",
        "status": "CONFIRMED",
        "version": 2,
        "importedCount": 2,
        "eventIds": ["event-1", "event-2"],
        "replayed": False,
    }
    assert [item["row_number"] for item in created.await_args.kwargs["items"]] == [
        1,
        2,
    ]
    confirmed.assert_awaited_once()
    recompute.assert_awaited_once()


@pytest.mark.asyncio
async def test_csv_provider_resolution_deduplicates_symbols() -> None:
    rows = service.parse_csv_position_rows(
        "STOCK,600036,招商银行,100,38.82\n"
        "STOCK,600036,招商银行,200,39.10"
    )
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    resolve = AsyncMock(
        return_value={
            "id": None,
            "symbol": "600036",
            "name": "招商银行",
            "asset_type": "CN_STOCK",
            "market": "SSE",
        }
    )
    with patch("backend.portfolio.service._resolve_verified_instrument", resolve):
        verified = await service._resolve_csv_instruments(
            MagicMock(),
            ctx,
            rows,
            provider_timeout_seconds=1.0,
        )

    assert [item[1]["symbol"] for item in verified] == ["600036", "600036"]
    resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_csv_provider_resolution_has_one_batch_deadline() -> None:
    rows = service.parse_csv_position_rows("STOCK,600036,招商银行,100,38.82")
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")

    async def never_finishes(*_args, **_kwargs):
        await __import__("asyncio").sleep(1)

    with patch(
        "backend.portfolio.service._resolve_verified_instrument",
        side_effect=never_finishes,
    ):
        with pytest.raises(DataUnavailableError):
            await service._resolve_csv_instruments(
                MagicMock(),
                ctx,
                rows,
                provider_timeout_seconds=1.0,
                batch_timeout_seconds=0.01,
            )


@pytest.mark.asyncio
async def test_csv_provider_resolution_cancels_siblings_after_one_failure() -> None:
    import asyncio

    rows = service.parse_csv_position_rows(
        "STOCK,600036,招商银行,100,38.82\n"
        "ETF,510300,沪深300ETF,100,4.10"
    )
    ctx = TenantContext("editor-1", "tenant-1", tenant_role="EDITOR")
    blocker_started = asyncio.Event()
    blocker_cancelled = asyncio.Event()

    async def resolve(_pool, _ctx, symbol, *_args, **_kwargs):
        if symbol == "600036":
            await blocker_started.wait()
            raise ValidationError_("标的无效")
        blocker_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            blocker_cancelled.set()
            raise

    with patch(
        "backend.portfolio.service._resolve_verified_instrument",
        side_effect=resolve,
    ):
        with pytest.raises(ValidationError_):
            await service._resolve_csv_instruments(
                MagicMock(),
                ctx,
                rows,
                provider_timeout_seconds=1.0,
            )

    assert blocker_cancelled.is_set()


class _CsvDraftConnection:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    async def fetchval(self, _sql: str, *_args):
        return "batch-1"

    async def executemany(self, sql: str, rows: list[tuple]):
        assert "ledger.portfolio_import_items" in sql
        self.rows = rows


@pytest.mark.asyncio
async def test_csv_draft_persists_every_row_under_one_csv_batch() -> None:
    conn = _CsvDraftConnection()

    batch_id = await repository.create_csv_import_draft(
        conn,
        tenant_id="tenant-1",
        account_id="account-1",
        created_by="editor-1",
        idempotency_key="csv-1",
        source_content_hash="sha256:content",
        items=[
            {
                "row_number": 1,
                "instrument_id": "instrument-1",
                "quantity": Decimal("8500"),
                "unit_cost": Decimal("38.82"),
                "trade_date": date(2026, 8, 30),
            },
            {
                "row_number": 2,
                "instrument_id": "instrument-2",
                "quantity": Decimal("118420.52"),
                "unit_cost": Decimal("1.7326"),
                "trade_date": date(2026, 8, 30),
            },
        ],
    )

    assert batch_id == "batch-1"
    assert len(conn.rows) == 2
    assert conn.rows[0][2:5] == (
        "instrument-1",
        Decimal("8500"),
        Decimal("38.82"),
    )
