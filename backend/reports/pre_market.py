"""Deterministic PRE_MARKET report content built from persisted facts only."""

from __future__ import annotations

from decimal import Decimal

_MISSING = "暂无"
# 无持仓时的占位（区别于"有持仓但无事件"）。
_NO_HOLDINGS = "暂无持仓"
# 有持仓、当日无公司行动且估值与集中度均健康时的确定性结论。
_HEALTHY_HOLDINGS = "今日无重大事件，持仓估值整体新鲜"
# 数据库仅有截止时间，没有渠道实际开放状态，任何"可申赎"表述都必须带此限定。
_CHANNEL_CAVEAT = "（以渠道状态为准）"
# 除权类行动（现金分红/送股/拆股）必然使除权参考价下调，可确定性提示低开。
_EX_RIGHTS_TYPES = frozenset({"CASH_DIVIDEND", "STOCK_DIVIDEND", "SPLIT"})
# 持仓估值质量中视为"过期/缺失"的状态（FRESH/ESTIMATED 视为可用）。
_STALE_QUALITIES = frozenset({"STALE", "MISSING", "UNAVAILABLE"})
ACTION_LABELS = {
    "CASH_DIVIDEND": "现金分红除权",
    "STOCK_DIVIDEND": "送股除权",
    "SPLIT": "拆股除权",
    "MERGER": "合并事项",
    "CODE_CHANGE": "代码变更",
    "DELISTING": "退市事项",
}


def safe_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def instrument_label(item: dict, *, include_symbol: bool = True) -> str:
    name = str(item.get("name") or item.get("symbol") or "未命名标的")
    symbol = str(item.get("symbol") or "")
    return f"{name}({symbol})" if include_symbol and symbol else name


def list_text(items: object, *, value_keys: tuple[str, ...]) -> str:
    if not isinstance(items, list) or not items:
        return _MISSING
    values: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        for key in value_keys:
            value = str(item.get(key) or "").strip()
            if value:
                values.append(value)
                break
    return "；".join(values) if values else _MISSING


def _overnight(pre: dict) -> str:
    indices = pre.get("usIndices")
    index_parts: list[str] = []
    if isinstance(indices, list):
        for item in indices:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            change = safe_decimal(item.get("changePct"))
            if name and change is not None:
                index_parts.append(f"{name}{change:+.2f}%")
    us_text = "、".join(index_parts) if index_parts else _MISSING

    a50 = pre.get("a50Future")
    a50_change = safe_decimal(a50.get("changePct")) if isinstance(a50, dict) else None
    a50_text = f"{a50_change:+.2f}%" if a50_change is not None else _MISSING
    return f"美股主要指数：{us_text}；A50期货：{a50_text}。"


def _action_text(action: dict, *, today: bool) -> str:
    action_label = ACTION_LABELS.get(str(action.get("actionType") or ""), "公司行动")
    prefix = instrument_label(action)
    return f"{prefix}{'今日' if today else ''}{action_label}"


def _attention(pre: dict) -> str:
    economic = list_text(
        pre.get("economicEvents"), value_keys=("summary", "title", "name")
    )
    actions = pre.get("corporateActions")
    action_text = (
        "；".join(
            _action_text(item, today=True) for item in actions if isinstance(item, dict)
        )
        if isinstance(actions, list) and actions
        else _MISSING
    )
    subscription = list_text(
        pre.get("subscriptionRedemptionChanges"),
        value_keys=("summary", "title", "name"),
    )
    return (
        f"经济数据发布：{economic}；持仓公司行动：{action_text}；"
        f"申赎状态变化：{subscription}。"
    )


def _held_instrument_ids(context: dict) -> set[str]:
    """用户持仓的 instrumentId 集合（空集合表示没有持仓，无法归因）。"""
    positions = context.get("positions")
    if not isinstance(positions, list):
        return set()
    return {
        str(position.get("instrumentId"))
        for position in positions
        if isinstance(position, dict) and position.get("instrumentId")
    }


def matched_actions(context: dict) -> list[dict]:
    """今日公司行动 ∩ 用户持仓。没有持仓时返回空列表（不做无源归因）。"""
    held_ids = _held_instrument_ids(context)
    if not held_ids:
        return []
    actions = (context.get("preMarket") or {}).get("corporateActions")
    if not isinstance(actions, list):
        return []
    return [
        action
        for action in actions
        if isinstance(action, dict)
        and str(action.get("instrumentId") or "") in held_ids
    ]


def _action_impact_phrase(action: dict, *, concise: bool) -> str:
    """单条公司行动 → 持仓影响短语。

    ``concise=True`` 用于推送短句（如「招商银行今日除权，预计低开」），
    ``False`` 用于报告正文（保留到账日、税费等限定语）。
    """
    action_type = str(action.get("actionType") or "")
    name = instrument_label(action, include_symbol=not concise)
    if concise:
        if action_type in _EX_RIGHTS_TYPES:
            return f"{name}今日除权，预计低开"
        return f"{name}今日{ACTION_LABELS.get(action_type, '发生公司行动')}，关注公告"
    if action_type == "CASH_DIVIDEND":
        pay_date = str(action.get("payDate") or _MISSING)
        return (
            f"{name}现金分红除权，价格可能出现技术性调整；"
            f"现金到账日为{pay_date}，实际收益以到账和税费为准"
        )
    if action_type in {"STOCK_DIVIDEND", "SPLIT"}:
        return (
            f"{name}{ACTION_LABELS[action_type]}，持仓数量与价格口径可能调整，"
            "组合价值以正式结算结果为准"
        )
    return (
        f"{name}{ACTION_LABELS.get(action_type, '发生公司行动')}，"
        "请关注交易规则和账户正式通知"
    )


def _position_label(position: dict) -> str:
    return str(position.get("name") or position.get("symbol") or "持仓")


def _sorted_by_weight(positions: list[dict]) -> list[dict]:
    return sorted(
        positions,
        key=lambda p: safe_decimal(p.get("weight")) or Decimal("-1"),
        reverse=True,
    )


def _position_health_risks(context: dict, *, concise: bool) -> list[str]:
    """持仓健康度风险项，按优先级返回短语列表（无风险则空）。

    优先级：估值过期/缺失（STALE/MISSING）> 单一持仓超限（超过政策上限）。
    全部基于已持久化的持仓数据（quality/weight）与投资政策（policy），
    不做任何外网或模型调用。
    """
    positions = [
        p
        for p in (context.get("positions") or [])
        if isinstance(p, dict)
        and safe_decimal(p.get("quantity")) is not None
        and safe_decimal(p.get("quantity")) > 0
    ]
    if not positions:
        return []
    policy = context.get("policy") or {}
    limit = safe_decimal(policy.get("single_position_limit"))
    risks: list[str] = []

    # 1. 估值过期/缺失（按权重降序，仅取有风险的）。
    stale = [
        p
        for p in _sorted_by_weight(positions)
        if str(p.get("quality") or "").upper() in _STALE_QUALITIES
    ]
    for position in stale:
        name = _position_label(position)
        quality = str(position.get("quality") or "").upper()
        is_stale = quality == "STALE"
        if concise:
            risks.append(f"{name}估值{'过期' if is_stale else '缺失'}")
        else:
            risks.append(
                f"{name}估值{'过期' if is_stale else '缺失'}，"
                "持仓数据非最新，请核对后再决策"
            )

    # 2. 单一持仓超限（占比 > 政策上限）。
    if limit is not None:
        for position in _sorted_by_weight(positions):
            weight = safe_decimal(position.get("weight"))
            if weight is None or weight <= limit:
                continue
            name = _position_label(position)
            if concise:
                risks.append(f"{name}占比{weight}%超上限{limit}%")
            else:
                risks.append(
                    f"{name}占比{weight}%，超过政策上限{limit}%，建议关注集中度风险"
                )
    return risks


def holding_impact_text(context: dict) -> str:
    """持仓今日影响（报告正文版）。

    组成 = 今日公司行动（持仓 ∩ 当日行动）+ 持仓健康度风险（估值/集中度）。

    - 没有持仓 → 「暂无持仓」（无数据可归因）
    - 有持仓、无行动、无风险 → 「今日无重大事件，持仓估值整体新鲜」
    - 否则列出具体行动与风险项
    """
    if not _held_instrument_ids(context):
        return _NO_HOLDINGS
    actions = matched_actions(context)
    action_phrases = [
        _action_impact_phrase(action, concise=False) for action in actions
    ]
    risk_phrases = _position_health_risks(context, concise=False)
    parts = action_phrases + risk_phrases
    if parts:
        return "；".join(parts) + "。"
    return _HEALTHY_HOLDINGS


def impact_push_text(context: dict, *, limit: int = 2) -> str:
    """持仓影响（推送短句版），超项折叠为「等N项」。"""
    if not _held_instrument_ids(context):
        return _NO_HOLDINGS
    actions = matched_actions(context)
    action_phrases = [_action_impact_phrase(action, concise=True) for action in actions]
    risk_phrases = _position_health_risks(context, concise=True)
    parts = action_phrases + risk_phrases
    if not parts:
        return _HEALTHY_HOLDINGS
    text = "；".join(parts[:limit])
    if len(parts) > limit:
        text += f"等{len(parts)}项"
    return text


def _cutoff_reminders(pre: dict, *, limit: int | None, concise: bool) -> list[str]:
    rules = pre.get("fundCutoffRules")
    if not isinstance(rules, list) or not rules:
        return []
    reminders: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        cutoff = str(rule.get("cutoffTime") or "")[:5]
        if not cutoff:
            continue
        label = instrument_label(rule, include_symbol=not concise)
        if concise:
            reminders.append(f"{label}今日{cutoff}前可提交申购/赎回")
        else:
            channel = str(rule.get("salesChannel") or "DEFAULT")
            channel_text = "默认渠道" if channel == "DEFAULT" else channel
            timezone = str(rule.get("timezone") or "Asia/Shanghai")
            reminders.append(
                f"{label}{channel_text}截止时间{cutoff}（{timezone}），"
                f"今日{cutoff}前可提交申购/赎回"
            )
        if limit is not None and len(reminders) >= limit:
            break
    return reminders


def cutoff_text(pre: dict) -> str:
    """今日操作提醒（报告正文版）：当日 15:00 前截止的基金申赎窗口。

    规则查询已按 ``cutoff_time <= 15:00`` 过滤（见 repository），此处只做渲染。
    数据库没有渠道实际开放状态，因此每条提醒都带「以渠道状态为准」限定。
    """
    reminders = _cutoff_reminders(pre, limit=None, concise=False)
    if not reminders:
        return _MISSING
    return "；".join(reminders) + _CHANNEL_CAVEAT + "。"


def cutoff_push_text(pre: dict, *, limit: int = 2) -> str:
    """今日提醒（推送短句版），超项折叠为「等N只」。"""
    reminders = _cutoff_reminders(pre, limit=limit, concise=True)
    if not reminders:
        return _MISSING
    total = pre.get("fundCutoffRules")
    text = "；".join(reminders)
    if isinstance(total, list) and len(total) > limit:
        text += f"等{len(total)}只"
    return text + _CHANNEL_CAVEAT


def _mood_phrase(context: dict) -> str | None:
    """市场情绪短语。无涨跌家数时返回 ``None``（不编造情绪判断）。"""
    sentiment = context.get("marketSentiment") or {}
    up = safe_decimal(sentiment.get("totalUp"))
    down = safe_decimal(sentiment.get("totalDown"))
    if up is None or down is None:
        return None
    if up > down * Decimal("1.2"):
        mood = "偏强"
    elif down > up * Decimal("1.2"):
        mood = "偏弱"
    else:
        mood = "均衡"
    if str(sentiment.get("qualityStatus") or "") == "FRESH":
        return f"情绪{mood}"
    trade_date = str(sentiment.get("tradeDate") or "未知日期")
    compact_date = trade_date[-5:] if len(trade_date) >= 5 else trade_date
    return f"截至{compact_date}情绪{mood}"


def _impact_clause(context: dict) -> str | None:
    """一句话总结中的「持仓影响」片段；无持仓时返回 ``None``。"""
    if not _held_instrument_ids(context):
        return None
    actions = matched_actions(context)
    risks = _position_health_risks(context, concise=True)
    if actions and risks:
        return f"{len(actions)}项行动、{len(risks)}项风险"
    if risks:
        return f"{len(risks)}项持仓风险"
    if not actions:
        return "持仓估值整体新鲜"
    if len(actions) == 1:
        return f"{instrument_label(actions[0], include_symbol=False)}除权"
    return f"{len(actions)}只持仓除权"


def _event_clause(context: dict) -> str | None:
    """一句话总结中的「今日事件」片段（基金申赎截止时间）。"""
    rules = (context.get("preMarket") or {}).get("fundCutoffRules")
    if not isinstance(rules, list) or not rules:
        return None
    cutoffs = {
        str(rule.get("cutoffTime") or "")[:5]
        for rule in rules
        if isinstance(rule, dict) and str(rule.get("cutoffTime") or "")[:5]
    }
    if not cutoffs:
        return None
    earliest = min(cutoffs)
    return f"{len(rules)}只基金{earliest}前可提交"


def summary_clauses(context: dict) -> list[str]:
    """一句话总结的组成片段：市场情绪 + 持仓影响 + 今日事件。

    三项各自独立取用，缺失的片段直接省略（绝不编造）；全部缺失时返回空列表，
    由调用方渲染为「暂无」。
    """
    clauses: list[str] = []
    for clause in (
        _mood_phrase(context),
        _impact_clause(context),
        _event_clause(context),
    ):
        if clause:
            clauses.append(clause)
    return clauses


def summary_text(context: dict) -> str:
    """一句话总结：市场情绪 + 持仓影响 + 今日事件的综合判断。"""
    clauses = summary_clauses(context)
    return "；".join(clauses) + "。" if clauses else _MISSING


def _mood(context: dict) -> tuple[str, str]:
    """兼容旧调用：返回 (完整结论句, 情绪短语)。"""
    phrase = _mood_phrase(context)
    if phrase is None:
        return _MISSING, _MISSING
    return f"当前市场{phrase}。", phrase


def _quality(context: dict) -> str:
    sentiment_quality = str(
        (context.get("marketSentiment") or {}).get("qualityStatus") or "UNAVAILABLE"
    ).upper()
    if sentiment_quality in {"FRESH", "ESTIMATED", "STALE"}:
        return sentiment_quality
    pre = context.get("preMarket") or {}
    if pre.get("corporateActions") or pre.get("fundCutoffRules"):
        return "FRESH"
    return "UNAVAILABLE"


def build_report(context: dict) -> dict:
    """Build the complete five-section PRE_MARKET report without model I/O."""
    pre = context.get("preMarket") or {}
    overnight = _overnight(pre)
    attention = _attention(pre)
    impact = holding_impact_text(context)
    reminder = cutoff_text(pre)
    conclusion = summary_text(context)
    quality = _quality(context)
    sentiment_quality = str(
        (context.get("marketSentiment") or {}).get("qualityStatus") or "UNAVAILABLE"
    ).upper()
    if sentiment_quality not in {"FRESH", "ESTIMATED", "STALE"}:
        sentiment_quality = "UNAVAILABLE"
    sections = (
        (
            "隔夜市场速览",
            overnight,
            "FACT",
            quality if pre.get("usIndices") or pre.get("a50Future") else "UNAVAILABLE",
        ),
        (
            "今日重点关注",
            attention,
            "FACT",
            quality
            if any(
                pre.get(key)
                for key in (
                    "economicEvents",
                    "corporateActions",
                    "subscriptionRedemptionChanges",
                )
            )
            else "UNAVAILABLE",
        ),
        (
            "持仓今日影响",
            impact,
            "RISK",
            quality if pre.get("corporateActions") else "UNAVAILABLE",
        ),
        (
            "今日操作提醒",
            reminder,
            "ACTION_PLAN",
            quality if pre.get("fundCutoffRules") else "UNAVAILABLE",
        ),
        ("一句话总结", conclusion, "FACT", sentiment_quality),
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
        "quality": quality,
        # US/A50 and economic calendars are not yet sourced, so this report
        # remains explicitly partial even when all current local facts exist.
        "status": "PARTIAL",
        "policy_check_status": "WARNING",
    }


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("；，。") + "…"


def format_push_content(context: dict) -> str:
    """Build a five-line WeCom PRE_MARKET summary capped at 200 characters."""
    pre = context.get("preMarket") or {}
    overnight = _overnight(pre)
    us = (
        _MISSING
        if "美股主要指数：暂无" in overnight
        else overnight.split("；", 1)[0].replace("美股主要指数：", "")
    )
    a50 = (
        _MISSING
        if "A50期货：暂无" in overnight
        else overnight.split("A50期货：", 1)[1].rstrip("。")
    )

    actions = pre.get("corporateActions") or []
    action_text = (
        "、".join(
            f"{instrument_label(item, include_symbol=False)}{ACTION_LABELS.get(str(item.get('actionType') or ''), '公司行动')}"
            for item in actions[:2]
            if isinstance(item, dict)
        )
        or _MISSING
    )
    economic = list_text(
        pre.get("economicEvents"), value_keys=("summary", "title", "name")
    )
    subscription = list_text(
        pre.get("subscriptionRedemptionChanges"),
        value_keys=("summary", "title", "name"),
    )

    # 持仓影响：持仓 ∩ 今日公司行动；无持仓→暂无，无匹配→「无重大事件影响」。
    impact = impact_push_text(context)
    # 今日提醒：当日 15:00 前截止的基金申赎窗口（规则查询已按 <= 15:00 过滤）。
    reminder = cutoff_push_text(pre)
    # 一句话总结：市场情绪 + 持仓影响 + 今日事件，缺项省略。
    summary = "；".join(summary_clauses(context)) or _MISSING

    lines = [
        f"🌏 隔夜市场：美股{us}；A50期货{a50}",
        f"📌 今日关注：经济数据{economic}；公司行动{action_text}；申赎状态{subscription}",
        f"📊 持仓影响：{impact}",
        f"⏰ 今日提醒：{reminder}",
        f"💡 一句话总结：{summary}",
    ]
    # 预算：5 行 + 4 个换行 ≤ 200 字符（企微推送上限）。
    limits = (38, 42, 40, 42, 34)
    return "\n".join(
        _clip(line, limit) for line, limit in zip(lines, limits, strict=True)
    )
