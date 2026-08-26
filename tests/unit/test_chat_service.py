"""Unit tests for backend.chat.service — context injection + SSE streaming.

No real DB / network: connection and httpx client are fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.chat.service import (
    ChatRequest,
    ChatService,
    _extract_symbol,
    _format_quote,
    _sse,
)
from backend.config import Settings


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeConn:
    """Minimal asyncpg-like connection returning canned rows per query."""

    def __init__(self, positions, report, sentiment) -> None:
        self._positions = positions
        self._report = report
        self._sentiment = sentiment

    async def fetch(self, query: str, *args):
        # 持仓查询: ledger.position_snapshots
        if "ledger.position_snapshots" in query:
            return self._positions
        # 市场情绪: market.market_observations
        return self._sentiment

    async def fetchrow(self, query: str, *args):
        # 最新报告: reporting.reports
        if "reporting.reports" in query:
            return self._report
        return None


def _settings(**overrides) -> Settings:
    return Settings(
        model_base_url=overrides.get(
            "model_base_url", "https://api.example.com/v1"
        ),
        model_api_key=overrides.get("model_api_key", "test-key"),
        model_name=overrides.get("model_name", "test-model"),
        model_timeout_seconds=float(
            overrides.get("model_timeout_seconds", 45.0)
        ),
    )


def _row(**fields) -> dict:
    """模拟 asyncpg.Record 的 dict 式访问。"""
    return fields


# ---------------------------------------------------------------------------
# 实时行情提取与格式化
# ---------------------------------------------------------------------------
def test_extract_symbol_code_variants() -> None:
    assert _extract_symbol("300308怎么样") == ("code", "300308")
    assert _extract_symbol("帮我看看600519.SH") == ("code", "600519")
    assert _extract_symbol("000858 现在多少") == ("code", "000858")
    # 长数字串（非股票代码）不误匹配
    assert _extract_symbol("我的账户号码是1234567") == (None, None)


def test_extract_symbol_name_heuristic() -> None:
    assert _extract_symbol("中际旭创现在多少") == ("name", "中际旭创")
    assert _extract_symbol("贵州茅台走势如何") == ("name", "贵州茅台")
    # 无标的意图 → 不触发
    assert _extract_symbol("我的持仓怎么样") == (None, None)
    assert _extract_symbol("帮我分析一下市场") == (None, None)


def test_format_quote_renders_structured_text() -> None:
    quote = {
        "symbol": "300308",
        "name": "中际旭创",
        "price": 45.67,
        "change_pct": 2.3,
        "volume": 12345678.0,
        "turnover": 5.6e9,
        "high": 46.0,
        "low": 45.0,
        "open": 45.2,
        "prev_close": 44.64,
        "quality": "FRESH",
        "source": "akshare/spot-em",
    }
    text = _format_quote(quote)
    assert "中际旭创（300308）" in text
    assert "最新价 45.67" in text
    assert "涨跌幅 +2.30%" in text
    assert "1234.57 万手" in text
    assert "成交额 56.00 亿元" in text
    assert "数据质量：FRESH" in text


def test_format_quote_handles_none_fields() -> None:
    quote = {
        "symbol": "600519",
        "name": "贵州茅台",
        "price": None,
        "change_pct": None,
        "volume": None,
        "turnover": None,
        "high": None,
        "low": None,
        "open": None,
        "prev_close": None,
        "quality": "STALE",
        "source": "sina/hq",
    }
    text = _format_quote(quote)
    assert "最新价 --" in text and "涨跌幅 --" in text


# ---------------------------------------------------------------------------
# _quote_context（mock get_quote）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_quote_context_injects_fresh_quote(monkeypatch) -> None:
    async def fake_get_quote(value: str) -> dict:
        assert value == "300308"
        return {
            "symbol": "300308", "name": "中际旭创", "price": 45.67,
            "change_pct": 2.3, "volume": 12345678.0, "turnover": 5.6e9,
            "high": 46.0, "low": 45.0, "open": 45.2, "prev_close": 44.64,
            "quality": "FRESH", "source": "akshare/spot-em",
        }

    monkeypatch.setattr("backend.chat.service.get_quote", fake_get_quote)
    svc = ChatService(_settings())
    ctx = await svc._quote_context("300308 怎么样")
    assert ctx is not None
    assert "中际旭创" in ctx and "45.67" in ctx and "+2.30%" in ctx


@pytest.mark.asyncio
async def test_quote_context_unavailable_code_announces_name_silent(monkeypatch) -> None:
    async def fake_get_quote(value: str) -> dict:
        return {
            "symbol": value, "name": None, "price": None, "change_pct": None,
            "volume": None, "turnover": None, "high": None, "low": None,
            "open": None, "prev_close": None, "quality": "UNAVAILABLE",
            "as_of": None, "source": "sina/hq-error",
        }

    monkeypatch.setattr("backend.chat.service.get_quote", fake_get_quote)
    svc = ChatService(_settings())
    # 明确代码 → 告知行情不可用
    ctx = await svc._quote_context("300308 现在多少")
    assert ctx is not None and "暂不可用" in ctx
    # 名称启发式误判 → 静默跳过，不污染上下文
    ctx2 = await svc._quote_context("我的持仓怎么样")
    assert ctx2 is None


@pytest.mark.asyncio
async def test_quote_context_skips_without_symbol(monkeypatch) -> None:
    async def fake_get_quote(value: str) -> dict:
        raise AssertionError("不应调用 get_quote")

    monkeypatch.setattr("backend.chat.service.get_quote", fake_get_quote)
    svc = ChatService(_settings())
    assert await svc._quote_context("帮我分析一下我的持仓") is None


# ---------------------------------------------------------------------------
# _news_context（mock fetch_news）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_news_context_skips_without_intent(monkeypatch) -> None:
    async def fake_fetch_news(value: str, limit: int) -> list[dict]:
        raise AssertionError("不应调用 fetch_news")

    monkeypatch.setattr("backend.chat.service.fetch_news", fake_fetch_news)
    svc = ChatService(_settings())
    # 无新闻意图词 → 不查询
    assert await svc._news_context("中际旭创现在多少") is None


@pytest.mark.asyncio
async def test_news_context_injects_titles_and_sources(monkeypatch) -> None:
    async def fake_fetch_news(value: str, limit: int) -> list[dict]:
        assert value == "中际旭创"
        assert limit == 5
        return [
            {
                "title": "中际旭创:投资者关系活动记录表",
                "time": "2026-08-23",
                "source": "东方财富公告",
                "summary": "…",
                "url": "https://example.com/1",
            },
            {
                "title": "中际旭创:2026年半年度报告",
                "time": "2026-08-22",
                "source": "东方财富公告",
                "summary": "…",
                "url": None,
            },
        ]

    monkeypatch.setattr("backend.chat.service.fetch_news", fake_fetch_news)
    svc = ChatService(_settings())
    ctx = await svc._news_context("中际旭创最近有什么新闻")
    assert ctx is not None
    assert "《中际旭创:投资者关系活动记录表》" in ctx
    assert "来源：东方财富公告" in ctx
    assert "https://example.com/1" in ctx
    assert "《中际旭创:2026年半年度报告》" in ctx


@pytest.mark.asyncio
async def test_news_context_empty_result(monkeypatch) -> None:
    async def fake_fetch_news(value: str, limit: int) -> list[dict]:
        return []

    monkeypatch.setattr("backend.chat.service.fetch_news", fake_fetch_news)
    svc = ChatService(_settings())
    ctx = await svc._news_context("茅台最近有什么公告")
    assert ctx is not None and "未找到" in ctx


# ---------------------------------------------------------------------------
# _sse encoding
# ---------------------------------------------------------------------------
def test_sse_encodes_delta_and_done() -> None:
    assert _sse({"delta": "你好"}) == 'data: {"delta": "你好"}\n\n'
    assert _sse({"done": True}) == 'data: {"done": true}\n\n'


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_build_system_context_injects_all_sections() -> None:
    svc = ChatService(_settings())
    conn = FakeConn(
        positions=[
            _row(
                ticker="600519", canonical_name="贵州茅台", asset_type="CN_STOCK",
                quantity=300, avg_cost=1400.0, market_price=1420.0,
                unrealized_pnl=6000.0, market_value=426000.0,
            )
        ],
        report=_row(
            summary="盘后复盘：组合净值 506,000 元。",
            created_at=datetime(2026, 8, 24, 15, 36, tzinfo=timezone.utc),
        ),
        sentiment=[
            _row(canonical_name="贵州茅台", field_code="CLOSE", value=1304.66,
                 quality_status="FRESH"),
        ],
    )
    context = await svc._build_system_context(conn, "tenant-1")

    assert "贵州茅台" in context
    assert "426,000" in context
    assert "盘后复盘：组合净值 506,000 元" in context
    assert "贵州茅台 CLOSE 1,304.66" in context


@pytest.mark.asyncio
async def test_build_system_context_degrades_gracefully() -> None:
    """任何上下文获取异常都不应中断（回退到基础 system prompt）。"""
    svc = ChatService(_settings())

    class BoomConn:
        async def fetch(self, query, *args):
            raise RuntimeError("boom")

        async def fetchrow(self, query, *args):
            raise RuntimeError("boom")

    context = await svc._build_system_context(BoomConn(), "tenant-1")
    assert "你是 WealthSail 投研平台的 AI 助手" in context


# ---------------------------------------------------------------------------
# Streaming completion
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stream_completion_without_api_key(monkeypatch) -> None:
    svc = ChatService(_settings(model_api_key=None))
    chunks = [
        c async for c in svc.stream_completion(
            ChatRequest(user_id="u1", tenant_id="t1", message="你好"),
            "system context",
        )
    ]
    assert chunks[0].startswith("data: ")
    assert "未配置" in chunks[0]
    assert chunks[-1] == _sse({"done": True})


@pytest.mark.asyncio
async def test_stream_completion_parses_openai_sse_chunks(monkeypatch) -> None:
    svc = ChatService(_settings())

    class FakeResponse:
        status_code = 200

        async def aread(self):
            return b""

        def aiter_lines(self):
            async def gen():
                yield 'data: {"choices":[{"delta":{"content":"你"}}]}'
                yield 'data: {"choices":[{"delta":{"content":"好"}}]}'
                yield "data: [DONE]"
            return gen()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None, headers=None):
            return _CtxResponse(FakeResponse())

    class _CtxResponse:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        "backend.chat.service.httpx.AsyncClient", FakeClient
    )
    chunks = [
        c async for c in svc.stream_completion(
            ChatRequest(user_id="u1", tenant_id="t1", message="你好"),
            "system context",
        )
    ]
    assert chunks == [
        _sse({"delta": "你"}),
        _sse({"delta": "好"}),
        _sse({"done": True}),
    ]
