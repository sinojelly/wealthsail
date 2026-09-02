"""08:30 shared macro brief: deterministic sections and one-row daily cache."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock

import pytest

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.reports import pre_market_macro
from backend.reports import service


def _source_context() -> dict:
    return {
        "usIndices": [
            {
                "name": "道琼斯",
                "changePct": "0.25",
                "asOf": "2026-09-01T20:00:00+00:00",
            },
            {
                "name": "纳斯达克",
                "changePct": "-0.10",
                "asOf": "2026-09-01T20:00:00+00:00",
            },
            {
                "name": "标普500",
                "changePct": "0.05",
                "asOf": "2026-09-01T20:00:00+00:00",
            },
        ],
        "a50Future": {
            "name": "富时中国A50期货",
            "changePct": "-0.10",
            "asOf": "2026-09-02T01:00:00+00:00",
        },
        "economicEvents": [],
        "globalEvents": [],
        "qualityStatus": "STALE",
        "sourceName": "EastMoney-Global-Indices",
    }


def test_macro_report_has_only_the_two_required_sections() -> None:
    draft = pre_market_macro.build_report(_source_context())

    assert [item["title"] for item in draft["items"]] == [
        "隔夜市场速览",
        "今日重点关注",
    ]


def test_macro_report_marks_unsupported_sources_as_missing() -> None:
    draft = pre_market_macro.build_report({})

    assert "暂无" in draft["summary"]
    assert draft["quality"] == "UNAVAILABLE"


def test_macro_report_renders_governed_overnight_facts() -> None:
    draft = pre_market_macro.build_report(_source_context())

    body = draft["items"][0]["body"]
    assert "道琼斯+0.25%" in body
    assert "纳斯达克-0.10%" in body
    assert "标普500+0.05%" in body
    assert "富时中国A50期货-0.10%" in body
    assert draft["items"][0]["quality"] == "STALE"
    assert draft["items"][1]["body"] == "经济数据：暂无；全球事件：暂无。"


def test_macro_report_never_marks_empty_sections_fresh() -> None:
    draft = pre_market_macro.build_report(
        {"usIndices": [], "a50Future": None, "qualityStatus": "FRESH"}
    )

    assert draft["items"][0]["quality"] == "UNAVAILABLE"
    assert draft["items"][1]["quality"] == "UNAVAILABLE"


def test_macro_push_content_stays_within_wecom_limit() -> None:
    draft = pre_market_macro.build_report(_source_context())

    content = pre_market_macro.format_push_content(draft)

    assert "隔夜" in content and "关注" in content
    assert "不构成投资建议" in content
    assert len(content.encode("utf-8")) <= 2048


class _Acquire:
    def __init__(self, conn) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, conn) -> None:
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _Transaction:
    """Mirror ``pg_advisory_xact_lock`` by releasing on transaction exit."""

    def __init__(self, conn) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        if self.conn.lock.locked():
            self.conn.lock.release()
        return False


class _Connection:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.statements: list[str] = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement: str, *_args):
        self.statements.append(statement)
        if "pg_advisory_xact_lock" in statement:
            await self.lock.acquire()
        return "SELECT 1"

    def transaction(self):
        return _Transaction(self)


def _install_fakes(monkeypatch, *, cache: dict, snapshot_calls: list) -> _Connection:
    conn = _Connection()

    async def get_cached(_conn, trade_date):
        return cache.get(trade_date.isoformat())

    async def insert_cached(_conn, **payload):
        existing = cache.get(payload["trade_date"].isoformat())
        if existing is not None:
            return existing
        row = {"id": "macro-brief-1", **payload}
        cache[payload["trade_date"].isoformat()] = row
        return row

    async def snapshot():
        # Yield so both callers reach the advisory lock and prove the
        # double-checked read under lock is what prevents double generation.
        await asyncio.sleep(0.01)
        snapshot_calls.append(1)
        return _source_context()

    monkeypatch.setattr(
        pre_market_macro.repository, "get_shared_pre_market_brief", get_cached
    )
    monkeypatch.setattr(
        pre_market_macro.repository, "insert_shared_pre_market_brief", insert_cached
    )
    monkeypatch.setattr(
        pre_market_macro.market_data, "get_pre_market_global_snapshot", snapshot
    )
    return conn


@pytest.mark.asyncio
async def test_macro_brief_is_generated_once_for_concurrent_callers(monkeypatch):
    cache: dict[str, dict] = {}
    calls: list[int] = []
    conn = _install_fakes(monkeypatch, cache=cache, snapshot_calls=calls)

    first, second = await asyncio.gather(
        pre_market_macro.get_or_generate(_Pool(conn), date(2026, 9, 2)),
        pre_market_macro.get_or_generate(_Pool(conn), date(2026, 9, 2)),
    )

    assert len(calls) == 1
    assert len(cache) == 1
    assert {first.cache_hit, second.cache_hit} == {False, True}
    assert first.draft == second.draft
    assert any("pg_advisory_xact_lock" in stmt for stmt in conn.statements)
    assert first.model_result is None


@pytest.mark.asyncio
async def test_macro_brief_records_deterministic_audit_strings(monkeypatch):
    cache: dict[str, dict] = {}
    calls: list[int] = []
    conn = _install_fakes(monkeypatch, cache=cache, snapshot_calls=calls)

    result = await pre_market_macro.get_or_generate(_Pool(conn), date(2026, 9, 2))

    assert result.cache_hit is False
    assert result.error_class is None
    assert pre_market_macro.PROVIDER == "deterministic"
    assert pre_market_macro.MODEL == "shared-pre-market-v1"
    cached = cache["2026-09-02"]
    assert cached["quality_status"] == "STALE"
    assert len(cached["context_hash"]) == 64


@pytest.mark.asyncio
async def test_missing_global_snapshot_still_caches_one_honest_brief(monkeypatch):
    cache: dict[str, dict] = {}
    calls: list[int] = []
    conn = _install_fakes(monkeypatch, cache=cache, snapshot_calls=calls)
    monkeypatch.setattr(
        pre_market_macro.market_data,
        "get_pre_market_global_snapshot",
        AsyncMock(
            return_value={
                "usIndices": [],
                "a50Future": None,
                "economicEvents": [],
                "globalEvents": [],
                "qualityStatus": "UNAVAILABLE",
                "sourceName": "unavailable",
            }
        ),
    )

    result = await pre_market_macro.get_or_generate(_Pool(conn), date(2026, 9, 2))

    assert "暂无" in result.draft["summary"]
    assert result.draft["quality"] == "UNAVAILABLE"
    assert cache["2026-09-02"]["quality_status"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_generate_macro_report_persists_null_audience_without_model(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    brief = pre_market_macro.SharedPreMarketBrief(
        id="macro-brief-1",
        draft=pre_market_macro.build_report(_source_context()),
        source_context=_source_context(),
        context_hash="a" * 64,
        model_result=None,
        error_class=None,
        cache_hit=False,
    )
    get_or_generate = AsyncMock(return_value=brief)

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("macro brief must never call a model")

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    async def insert_report(_conn, **kwargs):
        captured["insert"] = kwargs
        return "macro-report-1"

    outbox = AsyncMock(return_value=2)
    write_audit = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "tenant_transaction", transaction)
    monkeypatch.setattr(service.pre_market_macro, "get_or_generate", get_or_generate)
    monkeypatch.setattr(service, "chat_completion", fail_if_called)
    monkeypatch.setattr(service, "_build_context", AsyncMock(return_value={}))
    monkeypatch.setattr(
        service.repository, "latest_report", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(service.repository, "insert_report", insert_report)
    monkeypatch.setattr(
        service.repository, "insert_report_items", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(service.repository, "insert_report_outbox_deliveries", outbox)
    monkeypatch.setattr(service, "write_audit", write_audit)
    monkeypatch.setattr(
        service, "get_report_detail", AsyncMock(return_value={"id": "macro-report-1"})
    )

    result = await service.generate_report(
        object(),
        TenantContext("alice", "tenant-1", tenant_role="EDITOR"),
        Settings(_env_file=None),
        "PRE_MARKET",
        report_variant="PRE_MARKET_MACRO",
    )

    assert result == {"id": "macro-report-1"}
    assert get_or_generate.await_count == 1
    inserted = captured["insert"]
    assert isinstance(inserted, dict)
    assert inserted["report_variant"] == "PRE_MARKET_MACRO"
    assert inserted["audience_user_id"] is None
    # Fan-out covers every PRE_MARKET subscriber, so no single target user.
    assert outbox.await_args.kwargs["target_user_id"] is None
    details = write_audit.await_args.kwargs["details"]
    assert details["generationMode"] == "PRE_MARKET_MACRO_SHARED"
    assert details["generatorProvider"] == "deterministic"
    assert details["generatorVersion"] == "shared-pre-market-v1"
    assert details["sharedBriefId"] == "macro-brief-1"
    assert details["sharedCacheHit"] is False
