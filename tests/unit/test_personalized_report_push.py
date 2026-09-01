"""Deterministic push contracts for tenants with active positions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.integrations.model_gateway import ModelResult
from backend.reports import service as report_service
from backend.reports.shared_market_brief import SharedBriefResult


def _context() -> dict:
    return {
        "summary": {
            "dailyPnl": "1250.00",
            "dailyPnlRate": "1.25",
            "qualityStatus": "FRESH",
        },
        "positions": [
            {
                "name": "超长名称核心资产一号",
                "symbol": "600036",
                "quantity": "100",
                "weight": "45.00",
                "quality": "FRESH",
                "valuationStatus": "READY",
            },
            {
                "name": "估值过期基金",
                "symbol": "110022",
                "quantity": "200",
                "weight": "20.00",
                "quality": "STALE",
                "valuationStatus": "READY",
            },
        ],
        "policy": {"single_position_limit": "30.00"},
        "marketSentiment": {
            "totalUp": 3200,
            "totalDown": 1800,
            "sentimentScore": "63.50",
            "qualityStatus": "FRESH",
            "tradeDate": "2026-08-31",
        },
    }


def test_personalized_push_has_four_grounded_sections_and_stays_under_300_chars() -> (
    None
):
    formatter = getattr(report_service, "_format_personalized_push_content", None)
    assert formatter is not None, "personalized push formatter is not implemented"

    content = formatter(_context(), session="POST_MARKET")

    assert content.count("一句话总结：") == 1
    assert content.count("持仓健康度：") == 1
    assert content.count("具体行动建议：") == 1
    assert content.count("今日市场信号：") == 1
    assert "当日 +1.25%" in content
    assert "主要贡献标的暂无法可靠归因" in content
    assert "估值过期基金" in content and "估值过期" in content
    assert "600036" in content and "45.00%" in content and "30.00%" in content
    assert "等待" in content and "暂停增加" in content
    assert "上涨 3200 家、下跌 1800 家" in content
    assert len(content) <= 300
    assert len(content.splitlines()) == 4
    action_line = content.splitlines()[2]
    assert action_line.endswith("再平衡。")
    assert "…" not in action_line


def test_personalized_push_does_not_invent_missing_daily_return_or_contributor() -> (
    None
):
    formatter = getattr(report_service, "_format_personalized_push_content", None)
    assert formatter is not None, "personalized push formatter is not implemented"
    context = _context()
    context["summary"]["dailyPnlRate"] = None
    context["summary"]["dailyPnl"] = None

    content = formatter(context, session="PRE_MARKET")

    assert "当日涨跌幅暂不可用" in content
    assert "主要贡献标的暂无法可靠归因" in content
    assert "None" not in content


def test_personalized_push_ignores_closed_positions_in_health_and_actions() -> None:
    formatter = getattr(report_service, "_format_personalized_push_content", None)
    assert formatter is not None, "personalized push formatter is not implemented"
    context = _context()
    context["positions"].append(
        {
            "name": "已清仓旧标的",
            "symbol": "000001",
            "quantity": "0",
            "weight": "99.00",
            "quality": "STALE",
            "valuationStatus": "READY",
        }
    )

    content = formatter(context, session="POST_MARKET")

    assert "000001" not in content
    assert "已清仓旧标的" not in content


def test_personalized_push_keeps_overweight_threshold_after_multiple_stale_positions() -> (
    None
):
    formatter = getattr(report_service, "_format_personalized_push_content", None)
    assert formatter is not None, "personalized push formatter is not implemented"
    context = _context()
    context["positions"].append(
        {
            "name": "另一只估值过期的超长基金名称",
            "symbol": "161725",
            "quantity": "10",
            "weight": "5.00",
            "quality": "STALE",
            "valuationStatus": "READY",
        }
    )

    content = formatter(context, session="FUND_1430")
    health_line = content.splitlines()[1]

    assert "110022" in health_line and "161725" in health_line
    assert "600036" in health_line
    assert "45.00%" in health_line and "30.00%" in health_line
    assert "…" not in health_line


def test_personalized_push_compacts_long_symbols_without_hiding_actions_or_limits() -> (
    None
):
    formatter = getattr(report_service, "_format_personalized_push_content", None)
    assert formatter is not None, "personalized push formatter is not implemented"
    context = _context()
    context["positions"] = [
        {
            "name": "长期估值标的一",
            "symbol": "US0378331005",
            "quantity": "10",
            "weight": "35.00",
            "quality": "STALE",
        },
        {
            "name": "长期估值标的二",
            "symbol": "US5949181045",
            "quantity": "10",
            "weight": "10.00",
            "quality": "STALE",
        },
        {
            "name": "长期估值标的三",
            "symbol": "US02079K3059",
            "quantity": "10",
            "weight": "5.00",
            "quality": "STALE",
        },
    ]

    content = formatter(context, session="POST_MARKET")
    health_line, action_line = content.splitlines()[1:3]

    assert "35.00%" in health_line and "30.00%" in health_line
    assert "暂停增加暴露" in action_line and action_line.endswith("再平衡。")
    assert not health_line.endswith("…")
    assert not action_line.endswith("…")


@pytest.mark.asyncio
async def test_holdings_generation_uses_one_report_model_call_and_deterministic_push(
    monkeypatch,
) -> None:
    context = _context()
    captured: dict = {}

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    async def persist(_conn, **kwargs):
        captured.update(kwargs)
        return {"id": "report-1"}

    model = AsyncMock(
        return_value=ModelResult(
            content=(
                '{"summary":"报告摘要","items":[{"item_type":"FACT",'
                '"severity":"INFO","title":"事实","body":"正文"}]}'
            ),
            model="test-model",
            usage={},
            cost_cny=Decimal("0"),
        )
    )
    monkeypatch.setattr(report_service, "tenant_transaction", transaction)
    monkeypatch.setattr(
        report_service, "_build_context", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        report_service.repository, "latest_report", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(report_service, "chat_completion", model)
    monkeypatch.setattr(report_service, "_persist_generation", persist)

    result = await report_service.generate_report(
        object(),
        TenantContext("user-1", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
        "POST_MARKET",
    )

    assert result == {"id": "report-1"}
    assert model.await_count == 1
    assert captured["generation_mode"] == "PERSONALIZED"
    assert captured["push_content"].startswith("1. 一句话总结：")
    assert len(captured["push_content"]) <= 300


@pytest.mark.asyncio
async def test_forced_generation_bypasses_recent_replay_and_targets_delivery_user(
    monkeypatch,
) -> None:
    context = _context()
    captured: dict = {}

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    async def persist(_conn, **kwargs):
        captured.update(kwargs)
        return {"id": "report-forced"}

    recent = {
        "id": "report-recent",
        "version": 1,
        "created_at": report_service.now_shanghai(),
    }
    monkeypatch.setattr(report_service, "tenant_transaction", transaction)
    monkeypatch.setattr(
        report_service, "_build_context", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        report_service.repository, "latest_report", AsyncMock(return_value=recent)
    )
    monkeypatch.setattr(
        report_service,
        "chat_completion",
        AsyncMock(
            return_value=ModelResult(
                content='{"summary":"摘要","items":[]}',
                model="test-model",
                usage={},
                cost_cny=Decimal("0"),
            )
        ),
    )
    monkeypatch.setattr(report_service, "_persist_generation", persist)

    result = await report_service.generate_report(
        object(),
        TenantContext("user-1", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
        "POST_MARKET",
        force=True,
        delivery_user_id="user-2",
    )

    assert result == {"id": "report-forced"}
    assert captured["delivery_user_id"] == "user-2"


@pytest.mark.asyncio
@pytest.mark.parametrize("positions", [[], _context()["positions"]])
async def test_pre_market_generation_is_deterministic_for_every_holdings_mode(
    monkeypatch,
    positions,
) -> None:
    context = {
        **_context(),
        "positions": positions,
        "preMarket": {
            "usIndices": [],
            "a50Future": None,
            "economicEvents": [],
            "subscriptionRedemptionChanges": [],
            "corporateActions": [],
            "fundCutoffRules": [],
        },
    }
    captured: dict = {}

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    async def persist(_conn, **kwargs):
        captured.update(kwargs)
        return {"id": "pre-market-report"}

    model = AsyncMock()
    shared_generate = AsyncMock()
    monkeypatch.setattr(report_service, "tenant_transaction", transaction)
    monkeypatch.setattr(
        report_service, "_build_context", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        report_service.repository, "latest_report", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        report_service.shared_market_brief, "get_or_generate", shared_generate
    )
    monkeypatch.setattr(report_service, "chat_completion", model)
    monkeypatch.setattr(report_service, "_persist_generation", persist)

    result = await report_service.generate_report(
        object(),
        TenantContext("user-1", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
        "PRE_MARKET",
    )

    assert result == {"id": "pre-market-report"}
    model.assert_not_awaited()
    shared_generate.assert_not_awaited()
    assert captured["generation_mode"] == "PRE_MARKET_DETERMINISTIC"
    assert captured["model_result"] is None
    assert len(captured["push_content"]) <= 200
    assert captured["draft"]["summary"].startswith("1. 隔夜市场速览：")


@pytest.mark.asyncio
@pytest.mark.parametrize("positions", [[], _context()["positions"]])
async def test_fund_1430_generation_is_deterministic_for_every_holdings_mode(
    monkeypatch,
    positions,
) -> None:
    context = {
        **_context(),
        "positions": positions,
        "fundEstimates": [],
        "fund1430": {"fundCutoffRules": []},
    }
    captured: dict = {}

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    async def persist(_conn, **kwargs):
        captured.update(kwargs)
        return {"id": "fund-report"}

    model = AsyncMock()
    shared_generate = AsyncMock()
    monkeypatch.setattr(report_service, "tenant_transaction", transaction)
    monkeypatch.setattr(
        report_service, "_build_context", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        report_service.repository, "latest_report", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        report_service.shared_market_brief, "get_or_generate", shared_generate
    )
    monkeypatch.setattr(report_service, "chat_completion", model)
    monkeypatch.setattr(report_service, "_persist_generation", persist)

    result = await report_service.generate_report(
        object(),
        TenantContext("user-1", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
        "FUND_1430",
    )

    assert result == {"id": "fund-report"}
    model.assert_not_awaited()
    shared_generate.assert_not_awaited()
    assert captured["generation_mode"] == "FUND_1430_DETERMINISTIC"
    assert captured["model_result"] is None
    assert len(captured["push_content"]) <= 200
    assert captured["draft"]["summary"].startswith("1. 今日估值概览：")


def test_pre_market_audit_names_the_deterministic_generator_without_a_model_run() -> (
    None
):
    details = report_service._delivery_audit_details(
        session="PRE_MARKET",
        version=1,
        degraded=False,
        delivery_count=1,
        generation_mode="PRE_MARKET_DETERMINISTIC",
    )

    assert details["generationMode"] == "PRE_MARKET_DETERMINISTIC"
    assert details["generatorVersion"] == "pre-market-deterministic-v1"


def test_fund_1430_audit_names_deterministic_generator_without_model_run() -> None:
    details = report_service._delivery_audit_details(
        session="FUND_1430",
        version=1,
        degraded=False,
        delivery_count=1,
        generation_mode="FUND_1430_DETERMINISTIC",
    )

    assert details["generationMode"] == "FUND_1430_DETERMINISTIC"
    assert details["generatorVersion"] == "fund-1430-deterministic-v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "generation_mode"),
    [
        ("PRE_MARKET", "PRE_MARKET_DETERMINISTIC"),
        ("FUND_1430", "FUND_1430_DETERMINISTIC"),
    ],
)
async def test_deterministic_report_persistence_does_not_create_a_model_run(
    monkeypatch, session, generation_mode
) -> None:
    insert_model_run = AsyncMock()
    insert_report = AsyncMock(return_value="report-1")
    monkeypatch.setattr(
        report_service.repository, "latest_report", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(report_service.repository, "insert_model_run", insert_model_run)
    monkeypatch.setattr(report_service.repository, "insert_report", insert_report)
    monkeypatch.setattr(report_service.repository, "insert_report_items", AsyncMock())
    monkeypatch.setattr(
        report_service.repository,
        "insert_report_outbox_deliveries",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(report_service, "write_audit", AsyncMock())
    monkeypatch.setattr(
        report_service,
        "get_report_detail",
        AsyncMock(return_value={"id": "report-1"}),
    )

    await report_service._persist_generation(
        object(),
        ctx=TenantContext("user-1", "tenant-1", tenant_role="OWNER"),
        session=session,
        trade_date=date(2026, 9, 1),
        started_at=report_service.now_shanghai(),
        context={"positions": [], "preMarket": {}},
        draft={
            "summary": "盘前摘要",
            "items": [],
            "quality": "UNAVAILABLE",
            "status": "PARTIAL",
            "policy_check_status": "WARNING",
        },
        model_result=None,
        error_class=None,
        job_run_id=None,
        actor_type="USER",
        generation_mode=generation_mode,
        shared_brief_id=None,
        shared_cache_hit=False,
        shared_context_hash=None,
        push_content="摘要",
    )

    insert_model_run.assert_not_awaited()
    assert insert_report.await_args.kwargs["model_run_id"] is None


@pytest.mark.asyncio
async def test_no_holdings_generation_keeps_shared_brief_path(monkeypatch) -> None:
    context = {**_context(), "positions": []}
    captured: dict = {}

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    async def persist(_conn, **kwargs):
        captured.update(kwargs)
        return {"id": "report-shared"}

    shared_result = SharedBriefResult(
        id="brief-1",
        draft={
            "summary": "共享市场简报",
            "items": [
                {
                    "item_type": "FACT",
                    "severity": "INFO",
                    "title": "今日大盘风向",
                    "body": "市场广度保持均衡。",
                }
            ],
        },
        source_context={},
        context_hash="hash-1",
        model_result=None,
        error_class=None,
        cache_hit=True,
    )
    model = AsyncMock()
    shared_generate = AsyncMock(return_value=shared_result)
    monkeypatch.setattr(report_service, "tenant_transaction", transaction)
    monkeypatch.setattr(
        report_service, "_build_context", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        report_service.repository, "latest_report", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        report_service.shared_market_brief, "get_or_generate", shared_generate
    )
    monkeypatch.setattr(report_service, "chat_completion", model)
    monkeypatch.setattr(report_service, "_persist_generation", persist)

    result = await report_service.generate_report(
        object(),
        TenantContext("user-1", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
        "POST_MARKET",
    )

    assert result == {"id": "report-shared"}
    shared_generate.assert_awaited_once()
    model.assert_not_awaited()
    assert captured["generation_mode"] == "NO_HOLDINGS_SHARED"
    assert captured["shared_brief_id"] == "brief-1"
    assert "添加自选" in captured["push_content"]


class _ContextConnection:
    async def fetchrow(self, _sql: str, _tenant_id: str):
        return None

    async def fetch(self, _sql: str, _tenant_id: str):
        return []


@pytest.mark.asyncio
async def test_holdings_context_reads_persisted_market_signal_without_provider_call(
    monkeypatch,
) -> None:
    async def summary(*_args, **_kwargs):
        return {"dailyPnl": "10.00", "dailyPnlRate": None}

    async def positions(*_args, **_kwargs):
        return [{"symbol": "600036", "quantity": "1"}]

    async def sentiment(_conn):
        return {
            "totalUp": 2000,
            "totalDown": 3000,
            "qualityStatus": "FRESH",
        }

    monkeypatch.setattr(report_service, "get_summary", summary)
    monkeypatch.setattr(report_service, "get_positions", positions)
    monkeypatch.setattr(
        report_service.market_service,
        "get_market_sentiment",
        sentiment,
    )
    corporate_actions = AsyncMock(return_value=[])
    cutoff_rules = AsyncMock(return_value=[])
    monkeypatch.setattr(
        report_service.repository,
        "get_pre_market_corporate_actions",
        corporate_actions,
    )
    monkeypatch.setattr(
        report_service.repository,
        "get_pre_market_cutoff_rules",
        cutoff_rules,
    )

    context = await report_service._build_context(
        _ContextConnection(),
        "tenant-1",
        "PRE_MARKET",
    )

    assert context["positions"] == [{"symbol": "600036", "quantity": "1"}]
    assert context["marketSentiment"]["totalDown"] == 3000
    assert context["preMarket"]["corporateActions"] == []
    assert context["preMarket"]["fundCutoffRules"] == []
    corporate_actions.assert_awaited_once()
    cutoff_rules.assert_awaited_once()


@pytest.mark.asyncio
async def test_fund_1430_context_reads_effective_cutoffs_for_held_funds(
    monkeypatch,
) -> None:
    async def summary(*_args, **_kwargs):
        return {"qualityStatus": "UNAVAILABLE"}

    async def positions(*_args, **_kwargs):
        return [
            {
                "instrumentId": "11111111-1111-1111-1111-111111111111",
                "symbol": "110011",
                "type": "场外基金",
                "quantity": "1",
            },
            {
                "instrumentId": "22222222-2222-2222-2222-222222222222",
                "symbol": "600036",
                "type": "A股",
                "quantity": "1",
            },
        ]

    monkeypatch.setattr(report_service, "get_summary", summary)
    monkeypatch.setattr(report_service, "get_positions", positions)
    monkeypatch.setattr(
        report_service.market_service,
        "get_market_sentiment",
        AsyncMock(return_value={"qualityStatus": "UNAVAILABLE"}),
    )
    cutoff_rules = AsyncMock(return_value=[{"symbol": "110011"}])
    monkeypatch.setattr(
        report_service.repository,
        "get_pre_market_cutoff_rules",
        cutoff_rules,
    )

    context = await report_service._build_context(
        _ContextConnection(),
        "tenant-1",
        "FUND_1430",
    )

    assert context["fund1430"]["fundCutoffRules"] == [{"symbol": "110011"}]
    assert cutoff_rules.await_args.args[1] == ["11111111-1111-1111-1111-111111111111"]
