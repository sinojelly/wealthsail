"""Monthly macro evidence, publication fences, and factual comparisons."""

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.dependencies import get_pool, get_tenant_context
from backend.investment import api, repository, service
from backend.main import create_app

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def _row(period=date(2026, 9, 1), value=Decimal("-0.2"), **changes):
    row = {
        "field_code": "CPI_YOY",
        "label": "CPI 同比",
        "dimension": "inflation",
        "max_age_days": 45,
        "as_of": period,
        "value": value,
        "unit": "%",
        "quality_status": "STALE",
        "revision": 1,
        "published_at": None,
        "observed_at": NOW - timedelta(hours=1),
        "retrieved_at": NOW - timedelta(hours=1),
        "source_name": "AKSHARE_RESEARCH_MACRO",
        "source_uri": "https://data.eastmoney.com/cjsj/cpi.html",
    }
    row.update(changes)
    return row


def _context_sources(monkeypatch, rows, catalogue=None):
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(
        repository, "macro", AsyncMock(return_value=catalogue or [_row(value=None)])
    )
    monkeypatch.setattr(
        repository, "macro_history", AsyncMock(return_value=rows), raising=False
    )


def _client(monkeypatch):
    @asynccontextmanager
    async def transaction(*args):
        yield object()

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        "user-a", "tenant-a"
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "query,expected", [("", 36), ("?months=1", 1), ("?months=120", 120)]
)
def test_market_context_route_returns_requested_history_window(
    monkeypatch, query, expected
):
    _context_sources(monkeypatch, [])
    response = _client(monkeypatch).get("/api/v1/investment/market-context" + query)
    assert response.status_code == 200
    assert response.json()["data"]["months"] == expected


@pytest.mark.parametrize("months", ["0", "121", "invalid", "1.5"])
def test_market_context_rejects_unbounded_history_window(monkeypatch, months):
    _context_sources(monkeypatch, [])
    response = _client(monkeypatch).get(
        f"/api/v1/investment/market-context?months={months}"
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_macro_context_sorts_months_and_observes_negative_cpi_change(monkeypatch):
    _context_sources(
        monkeypatch,
        [
            _row(date(2026, 9, 1), Decimal("-0.2")),
            _row(date(2026, 8, 1), Decimal("0.1")),
        ],
    )
    result = await service.market_context(object(), months=3)
    assert result["windowStart"] == "2026-08-01"
    assert result["windowEnd"] == "2026-10-14"
    assert result["months"] == 3
    item = result["items"][0]
    assert item["value"] == Decimal("-0.2")
    assert item["sourceUri"] == "https://data.eastmoney.com/cjsj/cpi.html"
    assert [(point["asOf"], point["value"]) for point in item["history"]] == [
        ("2026-08-01", Decimal("0.1")),
        ("2026-09-01", Decimal("-0.2")),
    ]
    assert item["historyMonths"] == 3 and item["sampleCount"] == 2
    assert item["periodChange"] == Decimal("-0.3")
    assert item["trendState"] == "OBSERVED"
    assert item["state"] == "UNVERIFIED" and result["state"] == "UNVERIFIED"
    assert item["assessment"] is None and result["assessment"] is None
    assert all(
        point["publishedAt"] is None and point["state"] == "UNVERIFIED"
        for point in item["history"]
    )


@pytest.mark.asyncio
async def test_default_macro_window_spans_calendar_year_boundaries(monkeypatch):
    _context_sources(monkeypatch, [])
    result = await service.market_context(object())
    assert result["windowStart"] == "2023-11-01"
    assert result["items"][0]["history"] == []
    assert result["items"][0]["trendState"] == "INSUFFICIENT"
    assert result["items"][0]["periodChange"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "older",
    [
        {"as_of": date(2026, 7, 1)},
        {"source_uri": "https://different.example/macro"},
        {"unit": "index"},
    ],
)
async def test_period_change_abstains_on_missing_month_or_changed_basis(
    monkeypatch, older
):
    _context_sources(
        monkeypatch, [_row(date(2026, 8, 1), Decimal("0.1"), **older), _row()]
    )
    item = (await service.market_context(object()))["items"][0]
    assert item["periodChange"] is None
    assert item["trendState"] == "GAP"


@pytest.mark.asyncio
async def test_single_macro_month_cannot_create_a_trend(monkeypatch):
    _context_sources(monkeypatch, [_row()])
    item = (await service.market_context(object()))["items"][0]
    assert item["periodChange"] is None and item["trendState"] == "INSUFFICIENT"


@pytest.mark.parametrize("max_age,state", [(3, "STALE"), (60, "READY")])
def test_macro_freshness_uses_indicator_catalog_age(monkeypatch, max_age, state):
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW)
    item = service.macro_dto(
        _row(
            published_at=NOW - timedelta(days=14),
            quality_status="FRESH",
            max_age_days=max_age,
        )
    )
    assert item["state"] == state


@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("Infinity"), float("inf"), "invalid", True]
)
def test_macro_dto_never_returns_non_finite_or_non_numeric_values(monkeypatch, value):
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW)
    item = service.macro_dto(_row(value=value))
    assert item["value"] is None
    assert item["state"] == "CONFLICT"


def test_verified_publication_without_value_is_not_collected(monkeypatch):
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW)
    item = service.macro_dto(
        _row(value=None, published_at=NOW - timedelta(days=1), quality_status="FRESH")
    )
    assert item["value"] is None and item["state"] == "NOT_COLLECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["as_of", "observed_at", "retrieved_at", "published_at"]
)
async def test_future_macro_facts_never_appear_in_context(monkeypatch, field):
    future = _row(**{field: NOW + timedelta(days=32)})
    _context_sources(monkeypatch, [future], catalogue=[future])
    item = (await service.market_context(object()))["items"][0]
    assert item["value"] is None and item["history"] == []
    assert item["state"] == "NOT_COLLECTED"
    assert (
        item["asOf"] is None
        and item["publishedAt"] is None
        and item["retrievedAt"] is None
    )


@pytest.mark.asyncio
async def test_history_context_preserves_latest_bad_period_without_old_value_fallback(monkeypatch):
    _context_sources(
        monkeypatch,
        [
            _row(date(2026, 8, 1), Decimal("0.1"), revision=1),
            _row(date(2026, 8, 1), Decimal("0.2"), revision=2),
            _row(date(2026, 9, 1), Decimal("NaN")),
        ],
    )
    item = (await service.market_context(object()))["items"][0]
    assert item["sampleCount"] == 1
    assert item["history"][0]["value"] == Decimal("0.2")
    assert item["history"][0]["revision"] == 2
    assert item["value"] is None
    assert item["state"] == "CONFLICT"
    assert item["asOf"] == "2026-09-01"
    assert item["periodChange"] is None


@pytest.mark.parametrize("quality", ["CONFLICT", "UNAVAILABLE"])
def test_macro_dto_preserves_explicit_failed_quality_for_finite_values(monkeypatch, quality):
    monkeypatch.setattr(service, "now_shanghai", lambda: NOW)
    item = service.macro_dto(_row(value=Decimal("99"), quality_status=quality))
    assert item["state"] == quality
    assert item["value"] is None
    assert item["asOf"] == "2026-09-01"
    assert item["sourceUri"] == "https://data.eastmoney.com/cjsj/cpi.html"


@pytest.mark.asyncio
@pytest.mark.parametrize("quality", ["CONFLICT", "UNAVAILABLE"])
async def test_failed_latest_macro_month_cannot_create_value_or_period_change(monkeypatch, quality):
    _context_sources(monkeypatch, [
        _row(date(2026, 8, 1), Decimal("49")),
        _row(date(2026, 9, 1), Decimal("99"), quality_status=quality),
    ])
    item = (await service.market_context(object()))["items"][0]
    assert item["value"] is None and item["state"] == quality
    assert item["asOf"] == "2026-09-01"
    assert item["sampleCount"] == 1
    assert [p["value"] for p in item["history"]] == [Decimal("49")]
    assert item["periodChange"] is None
    assert item["trendState"] in {"GAP", "INSUFFICIENT"}


@pytest.mark.asyncio
async def test_latest_bad_revision_cannot_restore_an_earlier_valid_revision(monkeypatch):
    _context_sources(monkeypatch, [
        _row(date(2026, 8, 1), Decimal("49")),
        _row(date(2026, 9, 1), Decimal("50"), revision=1),
        _row(date(2026, 9, 1), Decimal("99"), quality_status="CONFLICT", revision=2),
    ])
    item = (await service.market_context(object()))["items"][0]
    assert item["value"] is None and item["state"] == "CONFLICT"
    assert item["revision"] == 2
    assert [(p["asOf"], p["value"]) for p in item["history"]] == [("2026-08-01", Decimal("49"))]
    assert item["periodChange"] is None


@pytest.mark.asyncio
async def test_latest_catalog_conflict_is_not_replaced_by_older_valid_history(monkeypatch):
    _context_sources(monkeypatch, [
        _row(date(2026, 8, 1), Decimal("49")),
        _row(date(2026, 9, 1), Decimal("50")),
    ], catalogue=[_row(date(2026, 10, 1), Decimal("99"), quality_status="CONFLICT")])
    item = (await service.market_context(object()))["items"][0]
    assert item["value"] is None and item["state"] == "CONFLICT"
    assert item["asOf"] == "2026-10-01"
    assert item["sampleCount"] == 2
    assert item["periodChange"] is None
    assert item["trendState"] == "GAP"


@pytest.mark.asyncio
async def test_latest_catalog_conflict_compares_reporting_month_in_shanghai_timezone(monkeypatch):
    _context_sources(monkeypatch, [
        _row(date(2026, 8, 1), Decimal("49")),
        _row(date(2026, 9, 1), Decimal("50")),
    ], catalogue=[_row(
        datetime(2026, 9, 30, 16, tzinfo=ZoneInfo("UTC")), Decimal("99"),
        quality_status="CONFLICT",
    )])
    item = (await service.market_context(object()))["items"][0]
    assert item["value"] is None and item["state"] == "CONFLICT"
    assert item["periodChange"] is None


@pytest.mark.asyncio
async def test_adjacent_utc_reporting_timestamps_use_local_months_for_change(monkeypatch):
    _context_sources(monkeypatch, [
        _row(datetime(2026, 7, 31, 16, tzinfo=ZoneInfo("UTC")), Decimal("49")),
        _row(datetime(2026, 8, 31, 16, tzinfo=ZoneInfo("UTC")), Decimal("50")),
    ])
    item = (await service.market_context(object()))["items"][0]
    assert [p["asOf"] for p in item["history"]] == ["2026-08-01", "2026-09-01"]
    assert item["periodChange"] == 1 and item["trendState"] == "OBSERVED"


@pytest.mark.asyncio
async def test_macro_history_query_is_bounded_read_and_returns_source_contract():
    conn = AsyncMock()
    conn.fetch.return_value = []
    await repository.macro_history(conn, date(2026, 8, 1), NOW)
    sql, start, end = conn.fetch.await_args.args
    assert start == date(2026, 8, 1) and end == NOW
    assert "DISTINCT ON" in sql and "revision DESC" in sql
    assert (
        "o.as_of" in sql
        and "o.observed_at" in sql
        and "s.retrieved_at" in sql
        and "s.published_at" in sql
    )
    assert "max_age_days" in sql and "source_uri" in sql and "o.unit" in sql
    assert "INSERT" not in sql and "UPDATE" not in sql and "DELETE" not in sql


@pytest.mark.asyncio
async def test_macro_history_real_postgres_revisions_and_future_publication_fences():
    dsn = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("isolated investment database not configured")
    if urlsplit(dsn).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("macro context tests require loopback database")
    conn = await asyncpg.connect(dsn)
    transaction = conn.transaction()
    await transaction.start()
    try:
        cutoff = await conn.fetchval("SELECT now()")
        month = cutoff.astimezone(ZoneInfo("Asia/Shanghai")).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        period = (month - timedelta(days=1)).replace(day=1)
        source, subject = uuid4(), uuid4()
        code = "TEST_MACRO_" + subject.hex
        await conn.execute(
            "INSERT INTO reference.macro_indicator_catalog(field_code,label,dimension,max_age_days) VALUES($1,'fixture','inflation',3)",
            code,
        )
        await conn.execute(
            "INSERT INTO reference.data_sources(id,name,source_type,license_model,allowed_usage) VALUES($1,$2,'MACRO','fixture','fixture')",
            source,
            code,
        )
        for revision, fence in [
            (1, None),
            (2, None),
            (3, "observed"),
            (4, "retrieved"),
            (5, "published"),
            (6, "period"),
        ]:
            future = cutoff + timedelta(days=32)
            observed = future if fence == "observed" else cutoff - timedelta(minutes=1)
            retrieved = (
                future if fence == "retrieved" else cutoff - timedelta(minutes=1)
            )
            published = future if fence == "published" else None
            as_of = future if fence == "period" else period
            snapshot = await conn.fetchval(
                "INSERT INTO market.source_snapshots(source_id,retrieval_key,origin_uri,published_at,as_of,retrieved_at,content_hash,quality_status) VALUES($1,$2,'https://fixture.example/macro',$3,$4,$5,'fixture','STALE') RETURNING id",
                source,
                f"fixture:{revision}",
                published,
                as_of,
                retrieved,
            )
            await conn.execute(
                "INSERT INTO market.market_observations(subject_type,subject_id,field_code,value,unit,as_of,observed_at,snapshot_id,source_id,quality_status,revision) VALUES('MACRO_SERIES',$1,$2,$3,'%', $4,$5,$6,$7,'STALE',$8)",
                subject,
                code,
                Decimal(revision),
                as_of,
                observed,
                snapshot,
                source,
                revision,
            )
        await conn.execute("SET LOCAL ROLE wealthsail_api")
        rows = await repository.macro_history(conn, period.date(), cutoff)
        facts = [dict(row) for row in rows if row["field_code"] == code]
        assert len(facts) == 1 and facts[0]["value"] == 2 and facts[0]["revision"] == 2
        assert facts[0]["source_uri"] == "https://fixture.example/macro"
    finally:
        await transaction.rollback()
        await conn.close()
