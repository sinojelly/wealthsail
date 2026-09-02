"""Deterministic PRE_MARKET report content built from persisted facts only."""

from __future__ import annotations

from decimal import Decimal

_MISSING = "暂无"
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


def holding_impact_text(pre: dict) -> str:
    actions = pre.get("corporateActions")
    if not isinstance(actions, list) or not actions:
        return _MISSING
    impacts: list[str] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        label = instrument_label(action)
        action_type = str(action.get("actionType") or "")
        if action_type == "CASH_DIVIDEND":
            pay_date = str(action.get("payDate") or _MISSING)
            impacts.append(
                f"{label}现金分红除权，价格可能出现技术性调整；"
                f"现金到账日为{pay_date}，实际收益以到账和税费为准"
            )
        elif action_type in {"STOCK_DIVIDEND", "SPLIT"}:
            impacts.append(
                f"{label}{ACTION_LABELS[action_type]}，持仓数量与价格口径可能调整，"
                "组合价值以正式结算结果为准"
            )
        else:
            impacts.append(
                f"{label}{ACTION_LABELS.get(action_type, '发生公司行动')}，"
                "请关注交易规则和账户正式通知"
            )
    return "；".join(impacts) + "。" if impacts else _MISSING


def cutoff_text(pre: dict) -> str:
    rules = pre.get("fundCutoffRules")
    if not isinstance(rules, list) or not rules:
        return _MISSING
    reminders: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        cutoff = str(rule.get("cutoffTime") or "")[:5]
        if not cutoff:
            continue
        channel = str(rule.get("salesChannel") or "DEFAULT")
        channel_text = "默认渠道" if channel == "DEFAULT" else channel
        timezone = str(rule.get("timezone") or "Asia/Shanghai")
        reminders.append(
            f"{instrument_label(rule)}{channel_text}截止时间{cutoff}（{timezone}）"
        )
    if not reminders:
        return _MISSING
    return "；".join(reminders) + "；仅作截止时间参考，能否申赎以销售渠道实际状态为准。"


def _mood(context: dict) -> tuple[str, str]:
    sentiment = context.get("marketSentiment") or {}
    up = safe_decimal(sentiment.get("totalUp"))
    down = safe_decimal(sentiment.get("totalDown"))
    if up is None or down is None:
        return _MISSING, _MISSING
    if up > down * Decimal("1.2"):
        mood = "偏强"
    elif down > up * Decimal("1.2"):
        mood = "偏弱"
    else:
        mood = "均衡"
    pre = context.get("preMarket") or {}
    focus: list[str] = []
    if pre.get("corporateActions"):
        focus.append("持仓公司行动")
    if pre.get("fundCutoffRules"):
        focus.append("基金交易截止时间")
    direction = "和".join(focus) if focus else "市场广度变化"
    if str(sentiment.get("qualityStatus") or "") == "FRESH":
        return f"当前市场情绪{mood}；今日重点关注{direction}。", f"情绪{mood}"
    trade_date = str(sentiment.get("tradeDate") or "未知日期")
    compact_date = trade_date[-5:] if len(trade_date) >= 5 else trade_date
    return (
        f"截至{trade_date}（非实时），市场情绪{mood}；今日重点关注{direction}。",
        f"截至{compact_date}情绪{mood}",
    )


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
    impact = holding_impact_text(pre)
    reminder = cutoff_text(pre)
    conclusion, _ = _mood(context)
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

    impact = _MISSING
    if actions:
        first = actions[0]
        name = instrument_label(first, include_symbol=False)
        if first.get("actionType") == "CASH_DIVIDEND":
            impact = f"{name}除息价或技术调整，到账和税费为准"
        else:
            impact = f"{name}公司行动，关注交易规则和正式通知"

    rules = pre.get("fundCutoffRules") or []
    reminder = _MISSING
    if rules:
        first_rule = rules[0]
        cutoff = str(first_rule.get("cutoffTime") or "")[:5]
        if cutoff:
            reminder = (
                f"{instrument_label(first_rule, include_symbol=False)}{cutoff}截止参考，"
                "以渠道状态为准"
            )

    conclusion, mood = _mood(context)
    if conclusion == _MISSING:
        summary = _MISSING
    else:
        focus = "公司行动与基金截止" if actions or rules else "市场广度"
        summary = f"{mood}，关注{focus}"

    lines = [
        f"隔夜：美股{us}；A50{a50}",
        f"关注：经济数据{economic}；公司行动{action_text}；申赎变化{subscription}",
        f"影响：{impact}",
        f"提醒：{reminder}",
        f"总结：{summary}",
    ]
    limits = (38, 55, 42, 36, 25)
    return "\n".join(
        _clip(line, limit) for line, limit in zip(lines, limits, strict=True)
    )
