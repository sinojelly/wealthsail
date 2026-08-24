"""WealthSail Chat Service — Qwen LLM integration with portfolio context injection.

Generates streaming chat completions using the configured DashScope/Qwen API.
Each request automatically receives portfolio context (holdings, latest report,
market sentiment) injected as system context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from backend.config import Settings
from backend.integrations.market_data import Q_UNAVAILABLE, get_quote

logger = logging.getLogger("wealthsail.chat_service")

SYSTEM_PROMPT = """你是 WealthSail 投研平台的 AI 助手，专注于为中国个人投资者提供客观、理性的投资建议。

你的职责：
- 基于用户持仓和研报信息，提供有针对性的分析
- 解释市场现象、行业趋势、技术指标
- 提醒风险，避免给出确定性的买卖建议
- 用简洁的中文回答，重要数字附带说明

你的限制：
- 不提供具体的买卖点位和仓位建议
- 不对市场走势做确定性预测
- 涉及个股时始终提示"投资有风险"

输出格式：使用简洁的 Markdown，支持列表、加粗、代码块。

你可以查询用户提到的任意 A 股代码（6 位数字或带 .SH/.SZ）的实时行情。当对话上下文的「实时行情」小节包含标的数据时，请优先引用其中的最新价、涨跌幅、成交量等真实数据。"""

# 6 位股票代码（可选 .SH/.SZ/.BJ 后缀）；前后不为数字，避免长数字串误匹配。
_SYMBOL_CODE_RE = re.compile(
    r"(?<!\d)(\d{6})(?:\.(SH|SZ|BJ))?(?!\d)", re.IGNORECASE
)
# 名称启发式：去除疑问/常用词后剩余 2-8 字符 → 视为潜在标的名称。
_QUERY_NOISE_WORDS = (
    "多少", "怎么样", "怎样", "如何", "走势", "行情", "价格", "呢", "？", "?",
    "我", "的", "持仓", "现在", "目前", "分析", "一下", "看看", "查询",
    "查一下", "买", "卖", "持有", "是", "吗", "请问", "帮我",
    "市场", "大盘", "板块", "基金", "股票", "仓位", "组合", "账户",
    "资产", "配置", "策略", "行业", "指数", "趋势", "热点", "资金",
    "情绪", "风险", "收益", "估值", "基本面", "技术面",
    "今日", "今天", "昨天", "昨日", "明天", "明日", "本周", "上周",
    "下周", "本月", "上月", "近期", "最近", "最新", "近期", "后市",
)


def _extract_symbol(message: str) -> tuple[str | None, str | None]:
    """从用户消息提取标的：优先代码，其次名称启发式。

    返回 (kind, value)：('code', '300308') 或 ('name', '中际旭创') 或 (None, None)。
    """
    match = _SYMBOL_CODE_RE.search(message or "")
    if match:
        return "code", match.group(1)
    rest = (message or "").strip()
    for word in _QUERY_NOISE_WORDS:
        rest = rest.replace(word, "")
    rest = re.sub(r"\s+", "", rest)
    if 2 <= len(rest) <= 8:
        return "name", rest
    return None, None


def _fmt_volume_cn(volume: float | None) -> str:
    """成交量（手）→ 中文缩写：亿手 / 万手 / 手。"""
    if volume is None:
        return "--"
    if volume >= 1e8:
        return f"{volume / 1e8:.2f} 亿手"
    if volume >= 1e4:
        return f"{volume / 1e4:.2f} 万手"
    return f"{volume:.0f} 手"


def _format_quote(quote: dict) -> str:
    """将 get_quote 结果格式化为结构化上下文文本。"""
    price = quote.get("price")
    price_str = f"{price:.2f}" if price is not None else "--"
    pct = quote.get("change_pct")
    pct_str = f"{pct:+.2f}%" if pct is not None else "--"
    line = (
        f"当前查询的标的：{quote.get('name') or quote.get('symbol')}"
        f"（{quote.get('symbol')}），最新价 {price_str}，涨跌幅 {pct_str}，"
        f"成交量 {_fmt_volume_cn(quote.get('volume'))}"
    )
    extras: list[str] = []
    turnover = quote.get("turnover")
    if turnover is not None:
        extras.append(f"成交额 {turnover / 1e8:.2f} 亿元")
    for label, key in (("今开", "open"), ("最高", "high"), ("最低", "low"), ("昨收", "prev_close")):
        value = quote.get(key)
        if value is not None:
            extras.append(f"{label} {value:.2f}")
    if extras:
        line += "，" + "，".join(extras)
    line += f"（数据质量：{quote.get('quality')}，来源 {quote.get('source')}）"
    return line


@dataclass(frozen=True)
class ChatRequest:
    user_id: str
    tenant_id: str
    message: str
    model: str | None = None


class ChatService:
    """调用 Qwen/通义千问 流式对话服务。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._base_url = self.settings.model_base_url.rstrip("/")
        self._api_key = self.settings.model_api_key
        self._model = self.settings.model_name

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------------
    # Context builders
    # ------------------------------------------------------------------

    async def _build_system_context(
        self, conn: Any, tenant_id: str, message: str | None = None
    ) -> str:
        """从数据库获取持仓摘要、最新报告、市场情绪，并注入实时行情。"""
        context_parts = [SYSTEM_PROMPT]

        try:
            # 持仓摘要（Top 3）
            holdings_summary = await self._fetch_holdings_summary(conn, tenant_id)
            if holdings_summary:
                context_parts.append(f"\n## 用户当前持仓\n{holdings_summary}")

            # 最新报告结论
            latest_report = await self._fetch_latest_report(conn, tenant_id)
            if latest_report:
                context_parts.append(f"\n## 最新投研报告结论\n{latest_report}")

            # 今日市场情绪
            market_sentiment = await self._fetch_market_sentiment(conn, tenant_id)
            if market_sentiment:
                context_parts.append(f"\n## 今日市场情绪\n{market_sentiment}")

            # 实时行情（用户消息提及标的时）
            if message:
                quote_ctx = await self._quote_context(message)
                if quote_ctx:
                    context_parts.append(f"\n## 实时行情\n{quote_ctx}")
        except Exception:
            logger.warning("chat context fetch failed, using default prompt", exc_info=True)

        return "\n".join(context_parts)

    async def _quote_context(self, message: str) -> str | None:
        """解析用户消息中的标的，拉取实时行情并格式化为上下文文本。"""
        kind, value = _extract_symbol(message)
        if value is None:
            return None
        try:
            quote = await get_quote(value)
        except Exception:  # noqa: BLE001 — 行情失败不影响对话
            logger.warning("get_quote failed for %s", value, exc_info=True)
            return None
        if quote.get("quality") == Q_UNAVAILABLE:
            # 明确给出代码但查不到 → 告知用户；名称启发式误判 → 静默跳过
            if kind == "code":
                return f"当前查询的标的：{value}，实时行情暂不可用。"
            return None
        return _format_quote(quote)

    async def _fetch_holdings_summary(
        self, conn: Any, tenant_id: str
    ) -> str | None:
        """获取 Top 3 持仓摘要（与 portfolio repository 同一数据源）。"""
        rows = await conn.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (ps.instrument_id)
                    COALESCE(a.ticker, '') AS ticker,
                    i.canonical_name,
                    i.asset_type,
                    ps.quantity, ps.avg_cost, ps.market_price,
                    ps.unrealized_pnl, ps.market_value
                FROM ledger.position_snapshots ps
                JOIN reference.instruments i ON i.id = ps.instrument_id
                LEFT JOIN LATERAL (
                    SELECT alias_value AS ticker
                    FROM reference.instrument_aliases
                    WHERE instrument_id = ps.instrument_id
                      AND alias_type = 'TICKER'
                      AND (effective_to IS NULL OR effective_to >= ps.as_of)
                    ORDER BY effective_from DESC
                    LIMIT 1
                ) a ON TRUE
                WHERE ps.tenant_id = $1
                ORDER BY ps.instrument_id, ps.as_of DESC
            ) t
            WHERE t.quantity > 0
            ORDER BY t.market_value DESC NULLS LAST
            LIMIT 3
            """,
            tenant_id,
        )
        if not rows:
            return None

        lines = ["| 标的 | 数量 | 成本 | 现价 | 盈亏 | 市值 |", "|---|---|---|---|---|---|"]
        for r in rows:
            pnl_str = ""
            if r["unrealized_pnl"] is not None:
                rate = ""
                if r["avg_cost"] and r["avg_cost"] > 0:
                    rate = f" ({float(r['unrealized_pnl']) / float(r['avg_cost'] * r['quantity']) * 100:.1f}%)"
                pnl_str = f"{float(r['unrealized_pnl']):,.0f}{rate}"
            lines.append(
                f"| {r['ticker']} {r['canonical_name']} "
                f"| {float(r['quantity']):,.0f} "
                f"| {float(r['avg_cost']):.2f} "
                f"| {float(r['market_price']):.2f} "
                f"| {pnl_str} "
                f"| {float(r['market_value']):,.0f} |"
            )
        return "\n".join(lines)

    async def _fetch_latest_report(
        self, conn: Any, tenant_id: str
    ) -> str | None:
        """获取最新一条报告的摘要。"""
        row = await conn.fetchrow(
            """
            SELECT summary, created_at
            FROM reporting.reports
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            tenant_id,
        )
        if not row or not row["summary"]:
            return None
        return f"({row['created_at']:%Y-%m-%d})\n{row['summary'][:500]}"

    async def _fetch_market_sentiment(
        self, conn: Any, tenant_id: str
    ) -> str | None:
        """从市场观测表提取今日收盘观测，组装市场情绪摘要。"""
        rows = await conn.fetch(
            """
            SELECT i.canonical_name, o.field_code, o.value, o.quality_status
            FROM market.market_observations o
            JOIN reference.instruments i ON i.id = o.subject_id
            WHERE o.as_of::date = CURRENT_DATE
              AND o.quality_status = 'FRESH'
            ORDER BY o.as_of DESC
            LIMIT 8
            """,
        )
        if not rows:
            return None
        parts = [
            f"{r['canonical_name']} {r['field_code']} {float(r['value']):,.2f}"
            for r in rows
        ]
        return "；".join(parts) + "（本地行情观测，收盘价）"

    # ------------------------------------------------------------------
    # Streaming completion
    # ------------------------------------------------------------------

    async def stream_completion(
        self,
        conn: Any,
        request: ChatRequest,
    ) -> AsyncIterator[str]:
        """流式调用 LLM，逐 chunk yield SSE 格式的文本。

        每个 chunk 格式: `data: {\"delta\": \"...\"}\n\n`
        结束时发送:     `data: {\"done\": true}\n\n`
        """
        if not self.is_configured:
            yield _sse({"delta": "[模型 API Key 未配置，请在 .env 中设置 MODEL_API_KEY]"})
            yield _sse({"done": True})
            return

        model = request.model or self._model
        system_context = await self._build_system_context(
            conn, request.tenant_id, request.message
        )

        url = f"{self._base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_context},
                {"role": "user", "content": request.message},
            ],
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        logger.info("chat stream start model=%s user=%s", model, request.user_id)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        logger.error("chat API %s: %s", resp.status_code, body[:300].decode())
                        yield _sse({"delta": f"模型服务返回 {resp.status_code} 错误"})
                        yield _sse({"done": True})
                        return

                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or line == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            raw = line[6:]
                            try:
                                chunk = json.loads(raw)
                                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if delta:
                                    yield _sse({"delta": delta})
                            except (json.JSONDecodeError, IndexError, KeyError):
                                continue
        except httpx.ConnectError:
            yield _sse({"delta": "模型服务连接失败，请检查网络配置"})
        except httpx.ReadTimeout:
            yield _sse({"delta": "模型响应超时，请稍后重试"})
        except Exception as exc:
            logger.exception("chat stream error")
            yield _sse({"delta": f"对话生成异常: {exc}"})
        finally:
            yield _sse({"done": True})


def _sse(obj: dict[str, Any]) -> str:
    """将一个 dict 编码为 SSE data 行。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
