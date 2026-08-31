"""No-holdings report mode contracts."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from backend.reports import shared_market_brief as shared
from backend.reports.service import _active_positions


def _market_context() -> dict:
    return {
        "marketSentiment": {
            "totalUp": 3200,
            "totalDown": 1800,
            "totalFlat": 100,
            "turnover": "1080000000000",
            "sentimentScore": "63.5",
            "qualityStatus": "FRESH",
        },
        "sectorFocus": {
            "item": {"symbol": "BK1036", "name": "半导体", "changePct": "2.18"},
            "qualityStatus": "FRESH",
        },
    }


def test_no_holdings_fallback_has_five_grounded_sections_and_disclaimer() -> None:
    draft = shared.build_fallback_draft(
        {
            **_market_context(),
            "majorIndices": {
                "items": [
                    {"name": "上证指数", "changePct": "0.52", "turnover": "486000000000"},
                    {"name": "深证成指", "changePct": "-0.18", "turnover": "594000000000"},
                ],
                "qualityStatus": "FRESH",
            },
        }
    )

    assert [item["title"] for item in draft["items"]] == [
        "今日大盘风向",
        "值得关注的板块",
        "今日数据解读",
        "投资思考",
        "开始建立你的投资观察",
    ]
    all_text = draft["summary"] + " ".join(item["body"] for item in draft["items"])
    assert "上证指数" in all_text
    assert "上涨 3200 家、下跌 1800 家" in all_text
    assert "半导体板块值得关注" in all_text
    assert "行业数据解读" in all_text
    assert "不构成投资建议" in all_text
    assert "添加自选" in all_text and "导入持仓" in all_text
    assert all("quality" in item for item in draft["items"])


def test_zero_quantity_snapshots_do_not_count_as_active_holdings() -> None:
    positions = [
        {"symbol": "600036", "quantity": "0"},
        {"symbol": "510300", "quantity": "-1"},
        {"symbol": "110022", "quantity": "2.5"},
    ]

    assert _active_positions(positions) == [positions[2]]


def test_investment_thought_is_deterministic_and_not_actionable() -> None:
    thought = shared.build_fallback_draft(_market_context())["items"][3]["body"]

    assert "不构成投资建议" in thought
    assert all(term not in thought for term in ("买入", "卖出", "加仓", "仓位"))


def test_stale_index_snapshot_is_not_described_as_today_realtime() -> None:
    context = {
        **_market_context(),
        "majorIndices": {
            "items": [{"name": "上证指数", "changePct": "0.5"}],
            "qualityStatus": "STALE",
            "sourceAsOf": "2026-08-28T15:00:00+08:00",
        },
    }

    direction = shared.build_fallback_draft(context)["items"][0]["body"]

    assert "截至 2026-08-28" in direction
    assert "非今日实时" in direction


def test_no_holdings_push_is_complete_and_within_wecom_limit() -> None:
    draft = {
        "items": [
            {
                "title": title,
                "body": "市场观察" * 500,
                "item_type": "FACT",
                "severity": "INFO",
                "quality": "FRESH",
            }
            for title in (
                "今日大盘风向",
                "值得关注的板块",
                "今日数据解读",
                "投资思考",
                "开始建立你的投资观察",
            )
        ]
    }

    content = shared.format_push_content(draft, session="POST_MARKET")

    assert "盘后交互复盘" in content
    for title in ("今日大盘风向", "值得关注的板块", "今日数据解读", "投资思考"):
        assert title in content
    assert "添加自选" in content and "导入持仓" in content
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
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Connection:
    def __init__(self) -> None:
        self.execute = AsyncMock(return_value="SELECT 1")

    def transaction(self):
        return _Transaction()


@pytest.mark.asyncio
async def test_shared_market_brief_is_generated_once_then_reused(monkeypatch) -> None:
    cache: dict[str, dict] = {}
    conn = _Connection()

    async def get_cached(_conn, trade_date):
        return cache.get(trade_date.isoformat())

    async def insert_cached(_conn, **payload):
        row = {"id": "brief-1", **payload}
        cache[payload["trade_date"].isoformat()] = row
        return row

    indices = AsyncMock(
        return_value={
            "items": [{"name": "上证指数", "changePct": "0.5", "turnover": "1"}],
            "qualityStatus": "FRESH",
            "source": "EastMoney",
        }
    )
    sector = AsyncMock(return_value=_market_context()["sectorFocus"])
    monkeypatch.setattr(shared.repository, "get_shared_market_brief", get_cached)
    monkeypatch.setattr(shared.repository, "insert_shared_market_brief", insert_cached)
    monkeypatch.setattr(shared.market_data, "get_major_indices_snapshot", indices)
    monkeypatch.setattr(shared.market_data, "get_sector_focus_snapshot", sector)

    first = await shared.get_or_generate(
        _Pool(conn), date(2026, 8, 31), _market_context()
    )
    second = await shared.get_or_generate(
        _Pool(conn), date(2026, 8, 31), _market_context()
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.draft == second.draft
    assert "上证指数 0.5%" in first.draft["items"][0]["body"]
    assert "半导体板块值得关注" in first.draft["items"][1]["body"]
    assert "pg_advisory_xact_lock" in conn.execute.await_args_list[1].args[0]
    assert indices.await_count == 1
    assert sector.await_count == 1
    cached = cache["2026-08-31"]
    assert cached["provider"] == "deterministic"
    assert cached["token_usage"] == {}
    assert str(cached["cost_cny"]) == "0.0000"
