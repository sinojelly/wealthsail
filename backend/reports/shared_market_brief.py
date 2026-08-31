"""Daily tenant-neutral market brief for users who do not hold positions."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backend.common.database import api_system_transaction
from backend.dependencies import now_shanghai
from backend.integrations import market_data
from backend.integrations.model_gateway import ModelResult
from backend.reports import repository

_LOCK_PREFIX = "wealthsail.shared-market-brief.v1:"
_SESSION_TITLES = {
    "PRE_MARKET": "盘前简报",
    "INTRADAY": "盘中跟踪",
    "FUND_1430": "基金 2:30 报告",
    "POST_MARKET": "盘后交互复盘",
}
_DISCLAIMER = "以上仅为市场信息与投资思考，不构成投资建议。"
_CTA = "可在 WealthSail 添加自选或导入持仓，后续报告将自动升级为个性化持仓分析。"


@dataclass(frozen=True)
class SharedBriefResult:
    id: str
    draft: dict
    source_context: dict
    context_hash: str
    model_result: ModelResult | None
    error_class: str | None
    cache_hit: bool


def _json(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _hash(value: dict) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _quality(source_context: dict) -> str:
    qualities = [
        str(source_context.get(key, {}).get("qualityStatus") or "UNAVAILABLE")
        for key in ("majorIndices", "marketSentiment", "sectorFocus")
    ]
    for candidate in ("UNAVAILABLE", "STALE", "ESTIMATED", "FRESH"):
        if candidate in qualities:
            return candidate
    return "UNAVAILABLE"


def _money_yi(value: object) -> str:
    try:
        return f"{Decimal(str(value)) / Decimal('100000000'):.2f} 亿元"
    except (ArithmeticError, ValueError, TypeError):
        return "数据不可用"


def _market_direction(source_context: dict) -> str:
    index_snapshot = source_context.get("majorIndices") or {}
    indices = index_snapshot.get("items") or []
    if indices:
        index_text = "；".join(
            f"{item.get('name', '--')} {item.get('changePct', '--')}%"
            for item in indices
        )
        if index_snapshot.get("qualityStatus") != "FRESH":
            source_as_of = str(index_snapshot.get("sourceAsOf") or "未知时间")[:10]
            index_text = f"主要指数数据截至 {source_as_of}（非今日实时）：{index_text}"
    else:
        index_text = "主要指数实时涨跌暂不可用"
    sentiment = source_context.get("marketSentiment") or {}
    up, down = sentiment.get("totalUp"), sentiment.get("totalDown")
    breadth = (
        f"上涨 {up} 家、下跌 {down} 家，涨跌家数比 {Decimal(str(up)) / Decimal(str(down)):.2f}"
        if up is not None and down not in (None, 0, "0")
        else "涨跌家数比暂不可用"
    )
    turnover = _money_yi(sentiment.get("turnover"))
    breadth_prefix = ""
    if sentiment.get("qualityStatus") != "FRESH":
        breadth_prefix = f"市场广度截至 {sentiment.get('tradeDate') or '未知日期'}（非今日实时），"
    return f"{index_text}；{breadth_prefix}全市场成交额 {turnover}；{breadth}。"


def _sector_focus(source_context: dict) -> str:
    snapshot = source_context.get("sectorFocus") or {}
    item = snapshot.get("item") or {}
    if item:
        as_of = "今日" if snapshot.get("qualityStatus") == "FRESH" else (
            f"截至 {str(snapshot.get('sourceAsOf') or '未知日期')[:10]}"
        )
        return (
            f"{item.get('name', '行业')}板块值得关注：{as_of}涨跌幅 "
            f"{item.get('changePct', '--')}%，按涨跌幅排序在行业板块快照中居前。"
            "该排序只反映短期价格相对表现，不代表行业基本面趋势。"
        )
    return (
        "值得关注的行业板块数据暂不可用；系统不依据公司名称猜测行业，"
        "请等待权威行业快照恢复。"
    )


def _data_insight(source_context: dict) -> str:
    snapshot = source_context.get("sectorFocus") or {}
    item = snapshot.get("item") or {}
    if item:
        as_of = "今日" if snapshot.get("qualityStatus") == "FRESH" else (
            f"截至 {str(snapshot.get('sourceAsOf') or '未知日期')[:10]}"
        )
        return (
            f"行业数据解读：{item.get('name', '该行业')}板块{as_of}涨跌幅 "
            f"{item.get('changePct', '--')}%，在同口径行业板块中按涨跌幅排序居前。"
            "这只描述当日价格相对表现，不能据此推断行业基本面或未来收益。"
        )
    return "今日可审计的经济或行业数据暂不可用，系统不对缺失数据作推断。"


def _draft_status(source_context: dict) -> str:
    qualities = [
        str(source_context.get(key, {}).get("qualityStatus") or "UNAVAILABLE")
        for key in ("majorIndices", "marketSentiment", "sectorFocus")
    ]
    return "ABSTAIN" if all(value == "UNAVAILABLE" for value in qualities) else "PARTIAL"


def build_fallback_draft(source_context: dict) -> dict:
    """Produce a fully grounded five-section brief when the model is unavailable."""
    direction = _market_direction(source_context)
    insight = _data_insight(source_context)
    source_quality = _quality(source_context)
    return {
        "summary": f"市场公共简报：{direction} {_DISCLAIMER}",
        "items": [
            {"item_type": "FACT", "severity": "INFO", "title": "今日大盘风向", "body": direction, "quality": source_quality},
            {"item_type": "FACT", "severity": "INFO", "title": "值得关注的板块", "body": _sector_focus(source_context), "quality": source_quality},
            {"item_type": "FACT", "severity": "INFO", "title": "今日数据解读", "body": insight, "quality": source_quality},
            {"item_type": "TODO", "severity": "INFO", "title": "投资思考", "body": f"先判断市场广度和成交是否相互印证，再决定是否承担新的风险。{_DISCLAIMER}", "quality": "UNAVAILABLE"},
            {"item_type": "ACTION_PLAN", "severity": "INFO", "title": "开始建立你的投资观察", "body": _CTA, "quality": "FRESH"},
        ],
        "quality": _quality(source_context),
        "status": _draft_status(source_context),
        "policy_check_status": "WARNING",
    }


def _from_row(row: object, *, cache_hit: bool) -> SharedBriefResult:
    data = dict(row)
    completed = data["generation_status"] == "COMPLETED"
    model_result = (
        ModelResult(
            content="",
            model=f"shared-cache/{data['model']}",
            usage={},
            cost_cny=Decimal("0.0000"),
        )
        if completed
        else None
    )
    return SharedBriefResult(
        id=str(data["id"]),
        draft=_json(data["draft"]),
        source_context=_json(data["source_context"]),
        context_hash=str(data["context_hash"]),
        model_result=model_result,
        error_class=None if completed else "SharedBriefFallbackCache",
        cache_hit=cache_hit,
    )


async def get_or_generate(pool, trade_date: date, market_context: dict) -> SharedBriefResult:
    """Return one durable brief per day, serializing concurrent first writers."""
    # This helper applies SET LOCAL ROLE wealthsail_api, so both the NOINHERIT
    # API login and the Worker pool can reach only the explicitly granted
    # global cache. It also pins all statements to one database transaction.
    async with api_system_transaction(pool) as conn:
        cached = await repository.get_shared_market_brief(conn, trade_date)
        if cached is not None:
            return _from_row(cached, cache_hit=True)

        lock_key = _LOCK_PREFIX + trade_date.isoformat()
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0));", lock_key
        )
        cached = await repository.get_shared_market_brief(conn, trade_date)
        if cached is not None:
            return _from_row(cached, cache_hit=True)

        indices, sector = await asyncio.gather(
            market_data.get_major_indices_snapshot(),
            market_data.get_sector_focus_snapshot(),
        )
        source_context = {
            "tradeDate": trade_date.isoformat(),
            "majorIndices": indices,
            "sectorFocus": sector,
            "marketSentiment": market_context.get("marketSentiment") or {},
        }
        draft = build_fallback_draft(source_context)
        model_result = ModelResult(
            content="",
            model="shared-market-deterministic-v1",
            usage={},
            cost_cny=Decimal("0.0000"),
        )

        row = await repository.insert_shared_market_brief(
            conn,
            trade_date=trade_date,
            draft=draft,
            source_context=source_context,
            context_hash=_hash(source_context),
            provider="deterministic",
            model=model_result.model,
            generation_status="COMPLETED",
            token_usage={},
            cost_cny=Decimal("0.0000"),
            quality_status=_quality(source_context),
            generated_at=now_shanghai(),
        )
        result = _from_row(row, cache_hit=False)
        return SharedBriefResult(
            id=result.id,
            draft=result.draft,
            source_context=result.source_context,
            context_hash=result.context_hash,
            model_result=model_result,
            error_class=None,
            cache_hit=False,
        )


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[: max_bytes - 3].decode("utf-8", errors="ignore") + "…"


def format_push_content(draft: dict, *, session: str) -> str:
    """Render the shared brief as a complete WeCom text message (<= 2048 bytes)."""
    title = _SESSION_TITLES.get(session, session)
    lines = [f"📊 WealthSail {title}｜市场观察版"]
    for item in draft.get("items", []):
        if item.get("title") == "开始建立你的投资观察":
            continue
        lines.append(f"\n【{item.get('title', '市场观察')}】\n{_truncate_utf8(str(item.get('body') or ''), 240)}")
    lines.extend((f"\n{_DISCLAIMER}", f"\n{_CTA}"))
    return _truncate_utf8("".join(lines), 2048)
