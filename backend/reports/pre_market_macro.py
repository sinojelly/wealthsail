"""Daily tenant-neutral 08:30 macro brief, cached once per trade date.

The macro half of the split pre-market report is identical for every tenant,
so it is generated once per ``trade_date`` into the append-only
``reporting.shared_pre_market_briefs`` cache and reused afterwards. Content is
built from governed structured data only — there is no model call, and fields
without a reliable source render as 暂无 instead of being inferred.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backend.common.database import api_system_transaction
from backend.dependencies import now_shanghai
from backend.integrations import market_data
from backend.reports import repository

_LOCK_PREFIX = "wealthsail.shared-pre-market-brief.v1:"
PROVIDER = "deterministic"
MODEL = "shared-pre-market-v1"

_MISSING = "暂无"
_DISCLAIMER = "以上仅为市场信息与投资思考，不构成投资建议。"
_SECTION_TITLES = ("隔夜市场速览", "今日重点关注")
_TRUSTED_QUALITIES = ("FRESH", "ESTIMATED", "STALE")


@dataclass(frozen=True)
class SharedPreMarketBrief:
    """One cached macro draft plus the audit strings that produced it."""

    id: str
    draft: dict
    source_context: dict
    context_hash: str
    model_result: None
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


def _safe_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _quality(source_context: dict) -> str:
    quality = str(source_context.get("qualityStatus") or "UNAVAILABLE").upper()
    return quality if quality in _TRUSTED_QUALITIES else "UNAVAILABLE"


def _list_text(items: object) -> str:
    if not isinstance(items, list) or not items:
        return _MISSING
    values: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        for key in ("summary", "title", "name"):
            value = str(item.get(key) or "").strip()
            if value:
                values.append(value)
                break
    return "；".join(values) if values else _MISSING


def _overnight_sentiment(indices: object) -> str | None:
    """美股主要指数情绪方向（基于可解析的涨跌幅多数）。

    - 多数上涨 → 乐观；多数下跌 → 谨慎；涨跌互现 → 分歧
    - 可判读指数不足 2 个时不输出情绪句（数据不足不解释）。
    只对真实涨跌符号做多数判断，不引用任何板块/资金等未采集维度。
    """
    changes = [
        change
        for item in (indices if isinstance(indices, list) else [])
        if isinstance(item, dict)
        and (change := _safe_decimal(item.get("changePct"))) is not None
    ]
    if len(changes) < 2:
        return None
    ups = sum(1 for change in changes if change > 0)
    downs = sum(1 for change in changes if change < 0)
    if ups > downs:
        return "美股普涨，市场情绪偏乐观"
    if downs > ups:
        return "美股回调，市场情绪偏谨慎"
    return "美股涨跌互现，市场存在分歧"


def _overnight_text(source_context: dict) -> str:
    indices = source_context.get("usIndices")
    parts: list[str] = []
    if isinstance(indices, list):
        for item in indices:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            change = _safe_decimal(item.get("changePct"))
            if name and change is not None:
                parts.append(f"{name}{change:+.2f}%")
    us_text = "、".join(parts) if parts else _MISSING
    sentiment = _overnight_sentiment(indices)
    if parts and sentiment:
        us_text = f"{us_text}（{sentiment}）"

    a50 = source_context.get("a50Future")
    a50_change = _safe_decimal(a50.get("changePct")) if isinstance(a50, dict) else None
    a50_name = (
        str(a50.get("name") or "富时中国A50期货").strip()
        if isinstance(a50, dict)
        else ""
    )
    a50_text = f"{a50_name}{a50_change:+.2f}%" if a50_change is not None else _MISSING
    return f"美股主要指数：{us_text}；A50期货：{a50_text}。"


def _economic_insight(title: str, value: Decimal) -> str | None:
    """单个经济指标基于数值口径的解读（不比较历史、不臆造趋势）。

    - PMI：>50 扩张、<50 收缩、贴近荣枯线则平稳（官方口径）
    - CPI 同比：按绝对值区间给出温和/高位描述；单期数据不做「抬头/回落」断言
    """
    upper = (title or "").upper()
    if "PMI" in upper:
        if value > Decimal("50"):
            return "高于荣枯线，制造业处扩张区间"
        if value < Decimal("50"):
            return "低于荣枯线，制造业处收缩区间，关注政策对冲"
        return "贴近荣枯线，制造业景气度平稳"
    if "CPI" in upper:
        if value >= Decimal("3"):
            return "同比高位，通胀压力需关注"
        if value >= Decimal("1"):
            return "同比温和，物价总体平稳"
        return "同比低位，通胀温和偏弱"
    return None


def _economic_text(economic: object) -> str:
    """经济数据节：名称（期数）+ 数值 + 解读句；数值缺失则只列名称。"""
    if not isinstance(economic, list) or not economic:
        return _MISSING
    parts: list[str] = []
    for item in economic:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        value = _safe_decimal(item.get("value"))
        unit = str(item.get("unit") or "").strip()
        date_text = str(item.get("date") or "").strip()
        label = f"{title}（{date_text}）" if date_text else title
        if value is None:
            parts.append(label)
            continue
        value_text = f"{value:.1f}{unit}" if unit else str(value)
        insight = _economic_insight(title, value)
        parts.append(
            f"{label}{value_text}——{insight}" if insight else f"{label}{value_text}"
        )
    return "；".join(parts) if parts else _MISSING


def _attention_text(source_context: dict) -> str:
    economic = _economic_text(source_context.get("economicEvents"))
    global_events = _list_text(source_context.get("globalEvents"))
    return f"经济数据：{economic}；全球事件：{global_events}。"


def build_report(source_context: dict, *, non_trading_debug: bool = False) -> dict:
    """Build the two-section macro brief without any model I/O.

    ``non_trading_debug`` is presentation metadata for a controlled manual run;
    it never changes or overwrites the shared source snapshot.
    """
    overnight = _overnight_text(source_context)
    if non_trading_debug:
        source_as_of = str(source_context.get("sourceAsOf") or "").strip()
        as_of_note = f"（数据截至{source_as_of[:10]}）" if source_as_of else ""
        overnight = (
            f"非交易日手动调试：沿用上一交易日行情{as_of_note}；{overnight}"
        )
    attention = _attention_text(source_context)
    snapshot_quality = _quality(source_context)
    has_overnight = bool(source_context.get("usIndices")) or bool(
        source_context.get("a50Future")
    )
    has_events = bool(source_context.get("economicEvents")) or bool(
        source_context.get("globalEvents")
    )
    sections = (
        (
            _SECTION_TITLES[0],
            overnight,
            "FACT",
            snapshot_quality if has_overnight else "UNAVAILABLE",
        ),
        (
            _SECTION_TITLES[1],
            attention,
            "FACT",
            snapshot_quality if has_events else "UNAVAILABLE",
        ),
    )
    return {
        "summary": "\n".join(
            f"{index}. {title}：{body}"
            for index, (title, body, _item_type, _item_quality) in enumerate(
                sections, start=1
            )
        ),
        "items": [
            {
                "item_type": item_type,
                "severity": "WARNING" if item_quality == "UNAVAILABLE" else "INFO",
                "title": title,
                "body": body,
                "quality": item_quality,
            }
            for title, body, item_type, item_quality in sections
        ],
        # Overnight global coverage is not sourced yet, so the macro brief is
        # explicitly partial even when the snapshot claims fresh data.
        "quality": snapshot_quality if (has_overnight or has_events) else "UNAVAILABLE",
        "status": "PARTIAL",
        "policy_check_status": "WARNING",
    }


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[: max_bytes - 3].decode("utf-8", errors="ignore") + "…"


def format_push_content(draft: dict) -> str:
    """Render the macro brief as a complete WeCom text message (<= 2048 bytes)."""
    lines = ["📊 WealthSail 盘前简报·宏观"]
    for item in draft.get("items", []):
        lines.append(
            f"\n【{item.get('title', '市场观察')}】\n"
            f"{_truncate_utf8(str(item.get('body') or ''), 240)}"
        )
    lines.append(f"\n{_DISCLAIMER}")
    return _truncate_utf8("".join(lines), 2048)


def _from_row(row: object, *, cache_hit: bool) -> SharedPreMarketBrief:
    data = dict(row)
    return SharedPreMarketBrief(
        id=str(data["id"]),
        draft=_json(data["draft"]),
        source_context=_json(data["source_context"]),
        context_hash=str(data["context_hash"]),
        model_result=None,
        error_class=None,
        cache_hit=cache_hit,
    )


async def get_or_generate(pool, trade_date: date) -> SharedPreMarketBrief:
    """Return one durable macro brief per day, serializing concurrent writers."""
    # api_system_transaction applies SET LOCAL ROLE wealthsail_api, so both the
    # NOINHERIT API login and the Worker pool reach only the explicitly
    # granted global cache. It also pins every statement to one transaction,
    # which is what makes the advisory lock release on commit.
    async with api_system_transaction(pool) as conn:
        cached = await repository.get_shared_pre_market_brief(conn, trade_date)
        if cached is not None:
            return _from_row(cached, cache_hit=True)

        lock_key = _LOCK_PREFIX + trade_date.isoformat()
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0));", lock_key
        )
        # Re-read under the lock: a competing worker may have committed while
        # this caller was waiting for the lock.
        cached = await repository.get_shared_pre_market_brief(conn, trade_date)
        if cached is not None:
            return _from_row(cached, cache_hit=True)

        source_context = await market_data.get_pre_market_global_snapshot()
        draft = build_report(source_context)
        row = await repository.insert_shared_pre_market_brief(
            conn,
            trade_date=trade_date,
            draft=draft,
            source_context=source_context,
            context_hash=_hash(source_context),
            quality_status=_quality(source_context),
            generated_at=now_shanghai(),
        )
        return _from_row(row, cache_hit=False)
