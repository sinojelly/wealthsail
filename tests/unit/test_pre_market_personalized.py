"""08:35 personalized pre-market report: four branches, no model, own facts only."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.reports import pre_market_personalized, service

_HELD_ID = "11111111-1111-1111-1111-111111111111"
_WATCHED_ID = "22222222-2222-2222-2222-222222222222"


def _positions() -> list[dict]:
    return [
        {
            "instrumentId": _HELD_ID,
            "symbol": "600036",
            "name": "招商银行",
            "quantity": "100",
            "type": "股票",
        }
    ]


def _watchlist() -> list[dict]:
    return [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "type": "STOCK",
            "instrumentId": _WATCHED_ID,
            "price": "1680.00",
            "changePct": "1.20",
            "quality": "FRESH",
            "asOf": None,
        }
    ]


def _context(
    *,
    positions: list[dict] | None = None,
    watchlist: list[dict] | None = None,
    pre_market: dict | None = None,
) -> dict:
    return {
        "session": "PRE_MARKET",
        "summary": {},
        "positions": [] if positions is None else positions,
        "policy": None,
        "fundEstimates": [],
        "watchlist": [] if watchlist is None else watchlist,
        "marketSentiment": {},
        "preMarket": pre_market or {},
    }


def test_personalized_report_has_only_the_three_required_sections() -> None:
    draft = pre_market_personalized.build_report(
        _context(positions=_positions(), watchlist=_watchlist())
    )

    assert [item["title"] for item in draft["items"]] == [
        "持仓今日影响",
        "关注标的动态",
        "今日操作提醒",
    ]


def test_personalized_report_marks_missing_facts_as_missing() -> None:
    draft = pre_market_personalized.build_report(
        _context(positions=_positions(), watchlist=_watchlist())
    )

    bodies = {item["title"]: item["body"] for item in draft["items"]}
    # 有持仓、当日无匹配公司行动且估值健康 → 确定性结论（区别于"无数据"的「暂无」）。
    assert bodies["持仓今日影响"] == "今日无重大事件，持仓估值整体新鲜"
    assert "贵州茅台" in bodies["关注标的动态"]
    assert bodies["今日操作提醒"] == "暂无"
    assert draft["quality"] == "FRESH"


def test_personalized_report_reports_held_corporate_action_impact() -> None:
    draft = pre_market_personalized.build_report(
        _context(
            positions=_positions(),
            pre_market={
                "corporateActions": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "600036",
                        "name": "招商银行",
                        "actionType": "CASH_DIVIDEND",
                        "payDate": "2026-09-10",
                    }
                ]
            },
        )
    )

    impact = draft["items"][0]["body"]
    assert "招商银行(600036)现金分红除权" in impact
    assert "2026-09-10" in impact
    assert draft["items"][0]["quality"] == "FRESH"


def test_personalized_report_reports_watched_corporate_action() -> None:
    draft = pre_market_personalized.build_report(
        _context(
            watchlist=_watchlist(),
            pre_market={
                "corporateActions": [
                    {
                        "instrumentId": _WATCHED_ID,
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "actionType": "SPLIT",
                    }
                ]
            },
        )
    )

    body = draft["items"][1]["body"]
    assert "贵州茅台(600519)今日拆股除权" in body
    assert draft["items"][1]["quality"] == "FRESH"


def test_personalized_report_renders_cutoff_reminder() -> None:
    draft = pre_market_personalized.build_report(
        _context(
            positions=_positions(),
            pre_market={
                "fundCutoffRules": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "110022",
                        "name": "易方达消费行业",
                        "salesChannel": "DEFAULT",
                        "cutoffTime": "15:00:00",
                        "timezone": "Asia/Shanghai",
                    }
                ]
            },
        )
    )

    assert "15:00" in draft["items"][2]["body"]
    assert draft["items"][2]["quality"] == "FRESH"


def test_personalized_push_stays_under_200_characters() -> None:
    content = pre_market_personalized.format_push_content(
        _context(
            positions=_positions(),
            watchlist=_watchlist(),
            pre_market={
                "fundCutoffRules": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "110022",
                        "name": "易方达消费行业",
                        "salesChannel": "DEFAULT",
                        "cutoffTime": "15:00:00",
                        "timezone": "Asia/Shanghai",
                    }
                ]
            },
        )
    )

    assert len(content) <= 200
    assert content.count("\n") == 2
    assert "持仓：" in content and "关注：" in content and "提醒：" in content


def _install(monkeypatch, *, positions: list[dict], watchlist: list[dict]) -> dict:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    async def build_report_context(_conn, _tenant_id, _session, **kwargs):
        captured["scope"] = kwargs.get("asset_scope")
        captured["audience"] = kwargs.get("audience_user_id")
        return _context(positions=positions, watchlist=watchlist)

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("personalized phase must never call a model")

    async def insert_report(_conn, **kwargs):
        captured["insert"] = kwargs
        return "personalized-1"

    outbox = AsyncMock(return_value=1)
    write_audit = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "tenant_transaction", transaction)
    monkeypatch.setattr(pre_market_personalized, "tenant_transaction", transaction)
    monkeypatch.setattr(service, "build_report_context", build_report_context)
    monkeypatch.setattr(service, "chat_completion", fail_if_called)
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
        service, "get_report_detail", AsyncMock(return_value={"id": "personalized-1"})
    )
    captured["outbox"] = outbox
    captured["write_audit"] = write_audit
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("positions", "watchlist"),
    [
        (_positions(), _watchlist()),
        (_positions(), []),
        ([], _watchlist()),
    ],
)
async def test_personalized_phase_generates_for_every_non_empty_combination(
    monkeypatch, positions, watchlist
) -> None:
    captured = _install(monkeypatch, positions=positions, watchlist=watchlist)

    result = await pre_market_personalized.generate_pre_market_personalized(
        object(),
        TenantContext("bob", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
    )

    assert result == {"id": "personalized-1"}
    inserted = captured["insert"]
    assert isinstance(inserted, dict)
    assert inserted["report_variant"] == "PRE_MARKET_PERSONALIZED"
    assert inserted["audience_user_id"] == "bob"
    assert captured["outbox"].await_args.kwargs["target_user_id"] == "bob"
    scope = captured["scope"]
    assert scope is not None
    assert (scope.kind, scope.owner_user_id) == ("MINE", "bob")
    details = captured["write_audit"].await_args.kwargs["details"]
    assert details["generationMode"] == "PRE_MARKET_PERSONALIZED_DETERMINISTIC"
    assert details["audienceUserId"] == "bob"


@pytest.mark.asyncio
async def test_personalized_phase_skips_members_without_positions_or_watchlist(
    monkeypatch,
) -> None:
    captured = _install(monkeypatch, positions=[], watchlist=[])

    result = await pre_market_personalized.generate_pre_market_personalized(
        object(),
        TenantContext("bob", "tenant-1", tenant_role="OWNER"),
        Settings(_env_file=None),
    )

    assert result is None
    assert "insert" not in captured
    captured["outbox"].assert_not_awaited()
