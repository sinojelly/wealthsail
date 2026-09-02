"""Audience isolation and version identity for persisted reports."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.common.tenancy import TenantContext
from backend.reports import api, repository, service


def _compact_sql(sql: str) -> str:
    return " ".join(sql.split())


class _SqlConnection:
    def __init__(self, *, rows=None, row=None) -> None:
        self.rows = [] if rows is None else rows
        self.row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args):
        self.calls.append((_compact_sql(sql), args))
        return self.rows

    async def fetchrow(self, sql: str, *args):
        self.calls.append((_compact_sql(sql), args))
        return self.row


@pytest.mark.asyncio
async def test_list_keeps_macro_and_current_user_personalized_rows_distinct() -> None:
    expected = [
        {"id": "macro", "report_variant": "PRE_MARKET_MACRO"},
        {
            "id": "alice-personalized",
            "report_variant": "PRE_MARKET_PERSONALIZED",
        },
    ]
    conn = _SqlConnection(rows=expected)

    rows = await repository.list_reports(
        conn,
        "tenant-1",
        date(2026, 9, 2),
        "PRE_MARKET",
        actor_user_id="alice",
    )

    assert rows == expected
    sql, args = conn.calls[0]
    assert "DISTINCT ON (report_session, report_variant, audience_user_id)" in sql
    assert "(audience_user_id IS NULL OR audience_user_id = $4)" in sql
    assert (
        "ORDER BY report_session, report_variant, audience_user_id, version DESC" in sql
    )
    assert args == ("tenant-1", date(2026, 9, 2), "PRE_MARKET", "alice")


@pytest.mark.asyncio
async def test_detail_items_and_history_apply_the_actor_visibility_predicate() -> None:
    conn = _SqlConnection(row=None)

    await repository.get_report(
        conn, "tenant-1", "private-report", actor_user_id="alice"
    )
    await repository.get_report_items(
        conn, "tenant-1", "private-report", actor_user_id="alice"
    )
    await repository.version_history(
        conn,
        "tenant-1",
        date(2026, 9, 2),
        "PRE_MARKET",
        "private-report",
        actor_user_id="alice",
        report_variant="PRE_MARKET_PERSONALIZED",
        audience_user_id="alice",
    )

    detail_sql, detail_args = conn.calls[0]
    items_sql, items_args = conn.calls[1]
    history_sql, history_args = conn.calls[2]
    assert "(audience_user_id IS NULL OR audience_user_id = $3)" in detail_sql
    assert detail_args == ("tenant-1", "private-report", "alice")
    assert "parent_report.audience_user_id IS NULL" in items_sql
    assert "parent_report.audience_user_id = $3" in items_sql
    assert items_args == ("tenant-1", "private-report", "alice")
    assert "report_variant = $5" in history_sql
    assert "audience_user_id IS NOT DISTINCT FROM $6" in history_sql
    assert "(audience_user_id IS NULL OR audience_user_id = $7)" in history_sql
    assert history_args == (
        "tenant-1",
        date(2026, 9, 2),
        "PRE_MARKET",
        "private-report",
        "PRE_MARKET_PERSONALIZED",
        "alice",
        "alice",
    )


@pytest.mark.asyncio
async def test_bob_personalized_report_starts_at_version_one(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def latest_report(
        _conn,
        _tenant_id,
        _trade_date,
        _session,
        *,
        actor_user_id=None,
        report_variant="STANDARD",
        audience_user_id=None,
    ):
        captured["latest"] = (
            actor_user_id,
            report_variant,
            audience_user_id,
        )
        # Alice already has v4. Bob must not inherit that chain.
        if audience_user_id == "alice":
            return {"id": "alice-v4", "version": 4}
        return None

    async def insert_report(_conn, **kwargs):
        captured["insert"] = kwargs
        return "bob-v1"

    monkeypatch.setattr(service.repository, "latest_report", latest_report)
    monkeypatch.setattr(service.repository, "insert_report", insert_report)
    monkeypatch.setattr(
        service.repository, "insert_report_items", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        service.repository, "insert_report_outbox_deliveries", AsyncMock(return_value=0)
    )
    write_audit = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "write_audit", write_audit)
    monkeypatch.setattr(
        service, "get_report_detail", AsyncMock(return_value={"id": "bob-v1"})
    )

    result = await service._persist_generation(
        object(),
        ctx=TenantContext("bob", "tenant-1", tenant_role="EDITOR"),
        session="PRE_MARKET",
        report_variant="PRE_MARKET_PERSONALIZED",
        audience_user_id="bob",
        trade_date=date(2026, 9, 2),
        started_at=datetime(2026, 9, 2, 8, 35),
        context={},
        draft={
            "summary": "Bob personalized",
            "items": [],
            "status": "PUBLISHED",
            "quality": "FRESH",
        },
        model_result=None,
        error_class=None,
        job_run_id=None,
        actor_type="WORKER",
        generation_mode="PRE_MARKET_DETERMINISTIC",
        shared_brief_id=None,
        shared_cache_hit=False,
        shared_context_hash=None,
        push_content=None,
        delivery_user_id="bob",
    )

    assert result == {"id": "bob-v1"}
    assert captured["latest"] == (
        "bob",
        "PRE_MARKET_PERSONALIZED",
        "bob",
    )
    inserted = captured["insert"]
    assert isinstance(inserted, dict)
    assert inserted["version"] == 1
    assert inserted["report_variant"] == "PRE_MARKET_PERSONALIZED"
    assert inserted["audience_user_id"] == "bob"
    assert inserted["supersedes_report_id"] is None
    assert write_audit.await_args.kwargs["details"]["variant"] == (
        "PRE_MARKET_PERSONALIZED"
    )
    assert write_audit.await_args.kwargs["details"]["audienceUserId"] == "bob"


def test_variant_metadata_is_exposed_in_report_summary() -> None:
    summary = service._report_summary(
        {
            "id": "macro",
            "report_session": "PRE_MARKET",
            "report_variant": "PRE_MARKET_MACRO",
            "audience_user_id": None,
            "trade_date": date(2026, 9, 2),
            "version": 1,
            "as_of": datetime(2026, 9, 2, 8, 30),
            "status": "PUBLISHED",
            "quality_status": "FRESH",
        }
    )

    assert summary["title"] == "盘前简报·宏观"
    assert summary["subtitle"] == "隔夜市场与今日全球重点"
    assert summary["variant"] == "PRE_MARKET_MACRO"
    assert summary["audienceUserId"] is None


@pytest.mark.asyncio
async def test_public_list_and_detail_forward_current_user(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield SimpleNamespace(fetchval=AsyncMock(return_value=date(2026, 9, 2)))

    async def list_reports(_conn, tenant_id, _trade_date, _session, actor_user_id):
        calls.append((tenant_id, actor_user_id))
        return []

    async def get_report_detail(_conn, tenant_id, _report_id, actor_user_id):
        calls.append((tenant_id, actor_user_id))
        return {"id": "report-1"}

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    monkeypatch.setattr(api.service, "list_reports", list_reports)
    monkeypatch.setattr(api.service, "get_report_detail", get_report_detail)
    ctx = TenantContext("alice", "tenant-1", tenant_role="VIEWER")

    await api.reports_list(
        trade_date=date(2026, 9, 2),
        session="PRE_MARKET",
        pool=object(),
        ctx=ctx,
        request_id="req-list",
    )
    await api.report_detail(
        report_id="report-1",
        pool=object(),
        ctx=ctx,
        request_id="req-detail",
    )

    assert calls == [("tenant-1", "alice"), ("tenant-1", "alice")]
