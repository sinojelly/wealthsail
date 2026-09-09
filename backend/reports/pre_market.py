"""Deterministic PRE_MARKET report content built from persisted facts only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

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
    "EX_RIGHTS": "除权除息",
    "RIGHTS_ISSUE": "配股",
    "OTHER": "其他重大公司行动",
}
_NO_RECORDED_ACTIONS = "今日及未来3天暂无已录入公司行动"
_POSITION_QUALITY_PRIORITY = {
    "FRESH": 0,
    "ESTIMATED": 1,
    "STALE": 2,
    "UNAVAILABLE": 3,
    "MISSING": 3,
}
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def safe_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _position_quality(position: dict) -> str:
    qualities: list[str] = []
    for key in ("quality", "changeQuality"):
        candidate = str(position.get(key) or "UNAVAILABLE").upper()
        qualities.append(
            candidate
            if candidate in _POSITION_QUALITY_PRIORITY
            else "UNAVAILABLE"
        )
    worst = max(
        qualities,
        key=lambda value: _POSITION_QUALITY_PRIORITY[value],
    )
    return "UNAVAILABLE" if worst == "MISSING" else worst


def position_table_rows(context: dict) -> list[dict]:
    """Normalize and deterministically order holdings for report rendering."""
    rows: list[dict] = []
    summary = context.get("summary")
    total_asset = (
        safe_decimal(summary.get("totalAsset"))
        if isinstance(summary, dict)
        else None
    )
    for position in context.get("positions") or []:
        if not isinstance(position, dict):
            continue
        asset_type = str(position.get("type") or "").upper()
        is_fund = asset_type in {"场外基金", "OFF_EXCHANGE_FUND", "FUND"}
        quantity = safe_decimal(position.get("quantity"))
        avg_cost = safe_decimal(position.get("avgCost"))
        price = safe_decimal(position.get("price"))
        market_value = safe_decimal(position.get("marketValue"))
        if market_value is None and quantity is not None and price is not None:
            market_value = quantity * price
        pnl_rate = safe_decimal(position.get("pnlRate"))
        if (
            pnl_rate is None
            and price is not None
            and avg_cost is not None
            and avg_cost != 0
        ):
            pnl_rate = (price / avg_cost - Decimal("1")) * Decimal("100")
        weight = safe_decimal(position.get("weight"))
        if (
            weight is None
            and market_value is not None
            and total_asset is not None
            and total_asset > 0
        ):
            weight = market_value / total_asset * Decimal("100")
        rows.append(
            {
                "symbol": str(position.get("symbol") or "--"),
                "name": str(position.get("name") or "未命名标的"),
                "quantity": quantity,
                "unit": (
                    "股"
                    if asset_type in {"A股", "股票", "STOCK", "CN_STOCK"}
                    else "份"
                ),
                "avgCost": avg_cost,
                "price": price,
                "marketValue": market_value,
                "pnlAmount": safe_decimal(position.get("pnlAmount")),
                "pnlRate": pnl_rate,
                "changePct": safe_decimal(position.get("changePct")),
                "changeSourceAsOf": position.get("changeSourceAsOf"),
                "trendDisplay": position.get("trendDisplay"),
                "weight": weight,
                "quality": _position_quality(position),
                "isFund": is_fund,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["weight"] is None,
            -(row["weight"] or Decimal("0")),
            row["symbol"],
        ),
    )


def _markdown_cell(value: object) -> str:
    text = "--" if value is None else str(value)
    escaped = text.replace("\\", "\\\\")
    for character in ("|", "`", "*", "_", "[", "]", "(", ")", "<", ">", "!"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped.replace("\r", " ").replace("\n", " ").strip() or "--"


def _format_table_number(
    value: Decimal | None,
    *,
    places: int | None = None,
    signed: bool = False,
    grouped: bool = False,
    suffix: str = "",
) -> str:
    if value is None or not value.is_finite():
        return "--"
    if places is not None:
        sign = "+" if signed and value >= 0 else ""
        return f"{sign}{value:,.{places}f}{suffix}"
    rendered = format(value, ",f" if grouped else "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def _month_day(value: object) -> str | None:
    parsed: date | None = None
    if isinstance(value, datetime):
        parsed = (
            value.astimezone(_SHANGHAI_TZ).date()
            if value.tzinfo is not None
            else value.date()
        )
    elif isinstance(value, date):
        parsed = value
    elif value:
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            timestamp = datetime.fromisoformat(raw)
            parsed = (
                timestamp.astimezone(_SHANGHAI_TZ).date()
                if timestamp.tzinfo is not None
                else timestamp.date()
            )
        except ValueError:
            try:
                parsed = date.fromisoformat(raw[:10])
            except ValueError:
                parsed = None
    return f"{parsed.month}/{parsed.day}" if parsed is not None else None


def _day_change_text(row: dict) -> str:
    if row["changePct"] is None:
        return "暂不可用"
    change = _format_table_number(
        row["changePct"], places=2, signed=True, suffix="%"
    )
    if not row["isFund"]:
        return change
    source_date = _month_day(row.get("changeSourceAsOf"))
    return f"{change}（{source_date or '日期暂不可用'}）"


def holdings_markdown(context: dict) -> str:
    """Render the eight-column GFM holdings table or an explicit empty state."""
    rows = position_table_rows(context)
    if not rows:
        return "暂无持仓，今日市场概况"
    lines = [
        "| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 仓位 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        warning = "⚠️" if row["quality"] != "FRESH" else ""
        quantity = _format_table_number(row["quantity"], grouped=True)
        price = _format_table_number(row["price"], grouped=True)
        pnl_rate = _format_table_number(
            row["pnlRate"], places=2, signed=True, suffix="%"
        )
        cells = (
            row["symbol"],
            f'{row["name"]}{warning}',
            f'{quantity}{row["unit"]}' if quantity != "--" else "--",
            _format_table_number(row["avgCost"]),
            f"{price}（持仓 {pnl_rate}）",
            _format_table_number(row["marketValue"], grouped=True),
            _day_change_text(row),
            _format_table_number(row["weight"], places=2, suffix="%"),
        )
        lines.append("| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |")
    notes: list[str] = []
    if any(row["quality"] != "FRESH" for row in rows):
        notes.append(
            "⚠️ 表示价格或涨跌数据质量不是 FRESH（估算、过期或不可用）"
        )
    if any(row["isFund"] for row in rows):
        notes.append("基金“当日”采用最近披露净值涨跌幅，括号内为净值日期")
    if notes:
        lines.extend(("", "> " + "；".join(notes) + "。"))
    return "\n".join(lines)


def _format_pnl_amount(value: Decimal | None) -> str:
    if value is None or not value.is_finite():
        return "--"
    return _format_table_number(value, places=2, signed=value > 0, grouped=True)


def _trend_markdown_cell(value: object) -> str:
    """Keep the generated display's readable parentheses while escaping text."""

    return _markdown_cell(value).replace(r"\(", "(").replace(r"\)", ")")


def personalized_holdings_markdown(context: dict) -> str:
    """Render the enriched nine-column table for an enabled personal brief."""

    pre_market = context.get("preMarket") or {}
    if not bool(pre_market.get("holdingsEnrichmentsEnabled")):
        return holdings_markdown(context)
    rows = position_table_rows(context)
    if not rows:
        return "暂无持仓，今日市场概况"
    lines = [
        "| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 连续变化 | 仓位 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for row in rows:
        warning = "⚠️" if row["quality"] != "FRESH" else ""
        quantity = _format_table_number(row["quantity"], grouped=True)
        price = _format_table_number(row["price"], grouped=True)
        pnl_rate = _format_table_number(
            row["pnlRate"], places=2, signed=True, suffix="%"
        )
        market_value = _format_table_number(row["marketValue"], grouped=True)
        pnl_amount = _format_pnl_amount(row["pnlAmount"])
        cells = (
            row["symbol"],
            f'{row["name"]}{warning}',
            f'{quantity}{row["unit"]}' if quantity != "--" else "--",
            _format_table_number(row["avgCost"]),
            f"{price}（持仓 {pnl_rate}）",
            f"{market_value}（盈亏 {pnl_amount}）",
            _day_change_text(row),
            row.get("trendDisplay") or "--",
            _format_table_number(row["weight"], places=2, suffix="%"),
        )
        rendered_cells = [
            _trend_markdown_cell(cell) if index == 7 else _markdown_cell(cell)
            for index, cell in enumerate(cells)
        ]
        lines.append("| " + " | ".join(rendered_cells) + " |")
    notes: list[str] = []
    if any(row["quality"] != "FRESH" for row in rows):
        notes.append("⚠️ 表示价格或涨跌数据质量不是 FRESH（估算、过期或不可用）")
    if any(row["isFund"] for row in rows):
        notes.append("基金“当日”采用最近披露净值涨跌幅，括号内为净值日期")
    if notes:
        lines.extend(("", "> " + "；".join(notes) + "。"))
    return "\n".join(lines)


def summary_with_holdings(
    context: dict,
    section_summary: str,
    *,
    holdings_renderer: Callable[[dict], str] = holdings_markdown,
) -> str:
    """Prepend the shared holdings contract to a user-scoped PRE_MARKET body."""
    return f"## 持仓概览\n\n{holdings_renderer(context)}\n\n{section_summary}"


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


def _action_event_date(action: dict) -> str:
    return str(
        action.get("actionDate")
        or action.get("exDate")
        or action.get("payDate")
        or ""
    )[:10]


def _report_date(context: dict) -> str:
    return str((context.get("preMarket") or {}).get("reportDate") or "")[:10]


def _action_label(action: dict) -> str:
    action_type = str(action.get("actionType") or "")
    if action_type == "CASH_DIVIDEND":
        event_date = _action_event_date(action)
        pay_date = str(action.get("payDate") or "")[:10]
        ex_date = str(action.get("exDate") or "")[:10]
        if event_date and pay_date and event_date == pay_date and event_date != ex_date:
            return "分红派息"
    return ACTION_LABELS.get(action_type, "公司行动")


def _action_when(action: dict, report_date: str) -> str:
    event_date = _action_event_date(action)
    if not event_date or not report_date or event_date == report_date:
        return "今日"
    return event_date[5:10]


def format_action_entry(
    action: dict, report_date: str, *, include_summary: bool = False
) -> str:
    """Format one persisted event without turning a future fact into today's event."""
    text = f"{instrument_label(action)}{_action_when(action, report_date)}{_action_label(action)}"
    summary = str(action.get("summary") or "").strip()
    if include_summary and summary and summary not in text:
        text += f"：{summary}"
    notes: list[str] = []
    if str(action.get("qualityStatus") or "FRESH").upper() == "STALE":
        notes.append("数据可能过期")
    sources = action.get("verifiedBy")
    if isinstance(sources, (list, tuple)):
        verified = [str(source).strip() for source in sources if str(source).strip()]
    else:
        verified = []
    if not verified:
        source_name = str(action.get("sourceName") or "").strip()
        if source_name and source_name != "UNATTRIBUTED":
            verified = [source_name]
    if verified:
        notes.append("来源：" + "、".join(verified))
    if notes:
        text += "（" + "；".join(notes) + "）"
    return text


def _is_displayable_action(action: dict) -> bool:
    """Only confirmed or explicitly stale facts may appear as company actions."""
    return str(action.get("qualityStatus") or "FRESH").upper() in {"FRESH", "STALE"}


def relevant_actions(context: dict) -> list[dict]:
    """Return actions scoped to this report's held or watched instruments."""
    relevant_ids = _held_instrument_ids(context)
    relevant_ids.update(
        str(item.get("instrumentId"))
        for item in (context.get("watchlist") or [])
        if isinstance(item, dict) and item.get("instrumentId")
    )
    actions = (context.get("preMarket") or {}).get("corporateActions") or []
    return [
        action
        for action in actions
        if isinstance(action, dict)
        and str(action.get("instrumentId") or "") in relevant_ids
        and _is_displayable_action(action)
    ]


def action_list_text(
    context: dict, *, limit: int | None = None, entry_limit: int = 20
) -> str:
    """Summarize ordered user-scoped actions, preserving the total when folded."""
    actions = relevant_actions(context)
    if not actions:
        return _NO_RECORDED_ACTIONS
    selected = actions if limit is None else actions[:limit]
    entries = [
        _clip(format_action_entry(action, _report_date(context)), entry_limit)
        for action in selected
    ]
    text = "；".join(entries)
    if limit is not None and len(actions) > limit:
        text += f"等共{len(actions)}条"
    return text


def _attention(pre: dict) -> str:
    economic = list_text(
        pre.get("economicEvents"), value_keys=("summary", "title", "name")
    )
    actions = pre.get("corporateActions")
    displayable_actions = (
        [
            item
            for item in actions
            if isinstance(item, dict) and _is_displayable_action(item)
        ]
        if isinstance(actions, list)
        else []
    )
    action_text = (
        "；".join(
            format_action_entry(item, str(pre.get("reportDate") or ""))
            for item in displayable_actions
        )
        if displayable_actions
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
        and _is_displayable_action(action)
    ]


def _is_ex_rights_action(action: dict) -> bool:
    action_type = str(action.get("actionType") or "")
    if action_type in {"EX_RIGHTS", "STOCK_DIVIDEND", "SPLIT"}:
        return True
    if action_type != "CASH_DIVIDEND":
        return False
    event_date = _action_event_date(action)
    ex_date = str(action.get("exDate") or "")[:10]
    return bool(ex_date and event_date == ex_date)


def _action_impact_phrase(
    action: dict, *, concise: bool, report_date: str = ""
) -> str:
    """单条公司行动 → 持仓影响短语。

    ``concise=True`` 用于推送短句（如「招商银行今日除权，预计低开」），
    ``False`` 用于报告正文（保留到账日、税费等限定语）。
    """
    action_type = str(action.get("actionType") or "")
    name = instrument_label(action, include_symbol=not concise)
    when = _action_when(action, report_date)
    is_today = when == "今日"
    action_label = _action_label(action)
    summary = str(action.get("summary") or "").strip()
    if not is_today:
        detail = f"：{summary}" if summary and summary != action_label else ""
        return f"{name}{when}{action_label}{detail}"
    if concise:
        if _is_ex_rights_action(action):
            return f"{name}今日除权，预计低开"
        return f"{name}今日{action_label}，关注公告"
    dated_prefix = "今日" if action.get("actionDate") else ""
    if action_type == "CASH_DIVIDEND" and _is_ex_rights_action(action):
        pay_date = str(action.get("payDate") or _MISSING)
        return (
            f"{name}{dated_prefix}现金分红除权，价格可能出现技术性调整；"
            f"现金到账日为{pay_date}，实际收益以到账和税费为准"
        )
    if action_type == "EX_RIGHTS":
        return f"{name}今日除权除息，价格可能出现技术性调整；以正式公告为准"
    if action_type == "CASH_DIVIDEND":
        return f"{name}今日分红派息，请关注实际到账和税费"
    if action_type in {"STOCK_DIVIDEND", "SPLIT"}:
        return (
            f"{name}{dated_prefix}{ACTION_LABELS[action_type]}，持仓数量与价格口径可能调整，"
            "组合价值以正式结算结果为准"
        )
    return (
        f"{name}{dated_prefix}{action_label}，"
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


def _is_off_exchange_fund(position: dict) -> bool:
    """Whether a report position represents an OTC mutual fund."""
    return str(position.get("type") or "").upper() in {
        "场外基金",
        "OFF_EXCHANGE_FUND",
        "FUND",
    }


def _stale_fund_summary(stale_funds: list[dict]) -> str:
    """Describe multiple stale OTC funds without listing every fund name.

    Fund NAVs are published after the trading day, so a shared stale batch is
    one data-quality issue rather than one independent action per fund. Keep
    the date qualifier truthful when imported NAV dates differ or are absent.
    """
    dates = [str(item.get("valuationDate") or "").strip() for item in stale_funds]
    known_dates = sorted({value for value in dates if value})
    if len(known_dates) == 1 and all(dates):
        date_note = f"（最新净值日为{known_dates[0]}）"
    elif known_dates and len(known_dates) == len(dates):
        date_note = f"（最新净值日期不一致，最早为{known_dates[0]}）"
    elif known_dates:
        date_note = f"（最新净值日期不完整，已知最早为{known_dates[0]}）"
    else:
        date_note = "（最新净值日暂无）"
    return (
        f"{len(stale_funds)}只场外基金估值数据已过期{date_note}，"
        "将影响估值与盈亏归因，请核对后再决策"
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
    # 多只场外基金通常复用同一批迟到的官方净值；正文只给出一条
    # 可执行的质量提醒，避免基金数量放大为同义的逐条噪音。
    stale_funds = [
        position
        for position in stale
        if _is_off_exchange_fund(position)
        and str(position.get("quality") or "").upper() == "STALE"
    ]
    grouped_stale_fund_ids = {
        id(position) for position in stale_funds
    } if len(stale_funds) > 1 else set()
    grouped_funds_emitted = False
    for position in stale:
        if id(position) in grouped_stale_fund_ids:
            if not grouped_funds_emitted:
                risks.append(_stale_fund_summary(stale_funds))
                grouped_funds_emitted = True
            continue
        name = _position_label(position)
        quality = str(position.get("quality") or "").upper()
        is_stale = quality == "STALE"
        valuation_date = str(position.get("valuationDate") or "").strip()
        if concise:
            risks.append(f"{name}估值{'过期' if is_stale else '缺失'}")
        elif is_stale and valuation_date:
            risks.append(
                f"{name}估值已过期（{valuation_date} 以来未更新），"
                "将影响估值与盈亏归因，请核对后再决策"
            )
        elif is_stale:
            risks.append(f"{name}估值已过期，将影响估值与盈亏归因，请核对后再决策")
        else:
            risks.append(f"{name}估值缺失（暂无最新净值），请先更新估值后再决策")

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
        _action_impact_phrase(action, concise=False, report_date=_report_date(context))
        for action in actions
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
    action_phrases = [
        _action_impact_phrase(action, concise=True, report_date=_report_date(context))
        for action in actions
    ]
    risk_phrases = _position_health_risks(context, concise=True)
    parts = action_phrases + risk_phrases
    if not parts:
        return _HEALTHY_HOLDINGS
    text = "；".join(parts[:limit])
    if len(parts) > limit:
        text += f"等{len(parts)}项"
    return text


def holding_health_push_text(context: dict, *, limit: int = 2) -> str:
    """Push-only holding health text; company actions have their own line."""
    if not _held_instrument_ids(context):
        return _NO_HOLDINGS
    risks = _position_health_risks(context, concise=True)
    if not risks:
        return _HEALTHY_HOLDINGS
    text = "；".join(risks[:limit])
    if len(risks) > limit:
        text += f"等{len(risks)}项"
    return text


# 今日提醒的空态消息：有持仓基金但无（截止规则/申赎状态）数据、或无持仓基金。
_NO_CUTOFF_REMINDER = "今日无申赎截止提醒"
# 有场外基金持仓但暂无任何截止/申赎档案时的规则性提示（渠道状态以实际为准）。
_DEFAULT_CUTOFF_HINT = "；持有场外基金默认 15:00 前可提交申购/赎回（以渠道状态为准）"
# 基金申赎开放状态（fund_profiles 由公告回填），展示时带来源限定。
_FUND_STATUS_CAVEAT = "（以基金公告为准）"


def _no_cutoff_reminder_text(context: dict) -> str:
    """今日提醒空态文本。

    - 没有任何场外基金持仓 → 「今日无申赎截止提醒」（无从提示 15:00 默认值）
    - 有场外基金持仓但数据不足 → 追加规则性 15:00 默认截止提示（以渠道状态为准）
    """
    held_funds = [
        position
        for position in (context.get("positions") or [])
        if isinstance(position, dict)
        and str(position.get("type") or "").strip() == "场外基金"
        and safe_decimal(position.get("quantity")) is not None
        and safe_decimal(position.get("quantity")) > 0
    ]
    if not held_funds:
        return _NO_CUTOFF_REMINDER
    return _NO_CUTOFF_REMINDER + _DEFAULT_CUTOFF_HINT


def _reminder_subjects(context: dict) -> list[dict]:
    """今日提醒的标的集合 = 持仓 ∩ (截止规则 ∪ 申赎状态档案)。

    每条为 ``{instrumentId, symbol, name, rules: [...], profile: dict|None}``。
    只看持仓（无持仓基金的截止规则不在此列），档案缺行视为无状态数据。
    """
    pre = context.get("preMarket") or {}
    held_ids = _held_instrument_ids(context)
    subjects: dict[str, dict] = {}
    for source, key in (
        (pre.get("fundCutoffRules"), "rules"),
        (pre.get("fundProfiles"), "profile"),
    ):
        if not isinstance(source, list):
            continue
        for row in source:
            if not isinstance(row, dict):
                continue
            instrument_id = str(row.get("instrumentId") or "")
            if not instrument_id or instrument_id not in held_ids:
                continue
            subject = subjects.setdefault(
                instrument_id,
                {"instrumentId": instrument_id, "rules": [], "profile": None},
            )
            # 名称/代码来自截止规则或申赎档案行（同一标的各来源一致）。
            if not subject.get("name"):
                subject["name"] = str(row.get("name") or "")
                subject["symbol"] = str(row.get("symbol") or "")
            if key == "profile":
                subject["profile"] = row
            elif subject["profile"] is None and row.get("cutoffTime"):
                subject["rules"].append(row)
    return list(subjects.values())


def _suspended_statuses(profile: dict) -> list[str]:
    """档案中处于 SUSPENDED 的申赎方向（空 = 无暂停）。"""
    statuses: list[str] = []
    if str(profile.get("purchaseStatus") or "") == "SUSPENDED":
        statuses.append("申购")
    if str(profile.get("redemptionStatus") or "") == "SUSPENDED":
        statuses.append("赎回")
    return statuses


def _earliest_cutoff(subject: dict) -> str:
    cutoffs = [str(rule.get("cutoffTime") or "")[:5] for rule in subject["rules"]]
    return min(cutoffs) if cutoffs else ""


def _reminder_phrases(context: dict, *, concise: bool) -> list[str]:
    """每只持仓基金的今日提醒短语，排序：暂停 > 有截止窗口 > 可正常无截止。"""
    subjects = _reminder_subjects(context)
    if not subjects:
        return []

    def sort_key(subject: dict) -> tuple[int, str]:
        if _suspended_statuses(subject["profile"] or {}):
            group = 0
        elif subject["rules"]:
            group = 1
        else:
            group = 2
        label = str(subject.get("name") or subject.get("instrumentId"))
        return group, label

    phrases: list[str] = []
    for subject in sorted(subjects, key=sort_key):
        profile = subject["profile"]
        suspended = _suspended_statuses(profile) if profile else []
        label = instrument_label(subject, include_symbol=not concise)
        if suspended:
            phrases.append(f"{label}今日暂停{'/'.join(suspended)}")
            continue
        if subject["rules"]:
            cutoff = _earliest_cutoff(subject)
            if concise:
                phrases.append(f"{label}今日{cutoff}前可提交申购/赎回")
            else:
                channel = str(subject["rules"][0].get("salesChannel") or "DEFAULT")
                channel_text = "默认渠道" if channel == "DEFAULT" else channel
                timezone = str(subject["rules"][0].get("timezone") or "Asia/Shanghai")
                phrases.append(
                    f"{label}{channel_text}截止时间{cutoff}（{timezone}），"
                    f"今日{cutoff}前可提交申购/赎回"
                )
            continue
        if concise:
            phrases.append(f"{label}今日可正常申购/赎回")
        else:
            phrases.append(f"{label}今日可正常申购/赎回（暂无15:00前截止）")
    return phrases


def reminder_has_data(context: dict) -> bool:
    """今日提醒是否具备事实输入（除权除息或基金申赎事实）。"""
    return bool(_today_ex_rights_reminder_phrases(context)) or bool(
        _reminder_subjects(context)
    )


def _today_ex_rights_reminder_phrases(context: dict, *, concise: bool = False) -> list[str]:
    """只把报告日当天、已落库的除权除息写入操作提醒。"""
    report_date = _report_date(context)
    if not report_date:
        return []
    phrases: list[str] = []
    for action in matched_actions(context):
        if not _is_ex_rights_action(action):
            continue
        if _action_event_date(action) != report_date:
            continue
        label = instrument_label(action, include_symbol=True)
        if concise:
            phrases.append(f"{label}今日除权除息")
        else:
            phrases.append(
                f"{label}今日除权除息，价格可能出现技术性调整，"
                "请关注开盘价与实际到账信息"
            )
    return phrases


def cutoff_text(context: dict) -> str:
    """今日操作提醒（报告正文版）。

    - 无持仓基金、或持仓基金无（截止规则/申赎状态）数据 → 「今日无申赎截止提醒」
    - 暂停 → 「X今日暂停申购/赎回（以基金公告为准）」
    - 可正常 + 有截止 → 「X默认渠道截止时间15:00（Asia/Shanghai），今日15:00前可
      提交申购/赎回（以渠道状态为准）」
    - 可正常 + 无截止 → 「X今日可正常申购/赎回（暂无15:00前截止）」
    """
    phrases = _reminder_phrases(context, concise=False)
    if not phrases:
        return _no_cutoff_reminder_text(context)
    suspended_any = any("暂停" in phrase for phrase in phrases)
    caveat = _FUND_STATUS_CAVEAT if suspended_any else _CHANNEL_CAVEAT
    return "；".join(phrases) + caveat + "。"


def cutoff_push_text(context: dict, *, limit: int = 2) -> str:
    """今日提醒（推送短句版），超项折叠为「等N只」。"""
    phrases = _reminder_phrases(context, concise=True)
    if not phrases:
        return _no_cutoff_reminder_text(context)
    text = "；".join(phrases[:limit])
    if len(phrases) > limit:
        text += f"等{len(phrases)}只"
    if "暂停" in "；".join(phrases[:limit]):
        text += _FUND_STATUS_CAVEAT
    else:
        text += _CHANNEL_CAVEAT
    return text


def operation_reminder_text(context: dict) -> str:
    """盘前操作提醒：今日除权除息优先，随后保留已有基金申赎事实。"""
    action_phrases = _today_ex_rights_reminder_phrases(context)
    if not action_phrases:
        return cutoff_text(context)
    fund_reminder = cutoff_text(context)
    text = "；".join(action_phrases)
    if fund_reminder != _NO_CUTOFF_REMINDER:
        text += "；" + fund_reminder.rstrip("。")
    return text + "。"


def operation_reminder_push_text(context: dict, *, limit: int = 2) -> str:
    """企微短句版提醒：不把无关的基金空态附在股票除权提醒后。"""
    action_phrases = _today_ex_rights_reminder_phrases(context, concise=True)
    if not action_phrases:
        return cutoff_push_text(context, limit=limit)
    text = "；".join(action_phrases[:limit])
    if len(action_phrases) > limit:
        text += f"等{len(action_phrases)}项"
    fund_reminder = cutoff_push_text(context, limit=limit)
    if fund_reminder != _NO_CUTOFF_REMINDER:
        text += "；" + fund_reminder
    return text


# 今日焦点事件的空态文案（区别于「暂无」数据缺失的措辞）。
_FOCUS_NONE = "今日暂无重大事件关注"
# 每条焦点事件的最大展示长度。
_FOCUS_PHRASE_LIMIT = 30


def _clip_focus_phrase(text: str) -> str:
    if len(text) <= _FOCUS_PHRASE_LIMIT:
        return text
    return text[: _FOCUS_PHRASE_LIMIT - 1].rstrip("；，。") + "…"


def _announcement_phrase(item: dict) -> str:
    """公司公告 → 焦点短语：只直述标题，不加工增长数字（不调用 AI）。"""
    name = str(item.get("name") or item.get("symbol") or "公司").strip()
    title = str(item.get("title") or "").strip()
    rest = title
    for token in (name, str(item.get("symbol") or "")):
        if token and rest.startswith(token):
            rest = rest[len(token) :].lstrip("：:，, ")
            break
    detail = rest or title or "发布公告"
    return f"{name}发布{detail}"


def _economic_phrase(item: dict) -> str:
    """宏观最新一期 → 焦点短语（最近一期直述，不做"今日待发布"推断）。"""
    name = str(item.get("name") or item.get("title") or "宏观数据").strip()
    value = str(item.get("value") or "").strip()
    unit = str(item.get("unit") or "").strip()
    if value and (not unit or unit == "%"):
        value_text = f"{value}%"
    elif value:
        value_text = f"{value}{unit}"
    else:
        value_text = ""
    period = str(item.get("date") or "").strip().replace("月份", "月")
    period_text = f"（{period}）" if period else ""
    return f"{name}{period_text}{value_text}（已发布）"


def _focus_event_phrases(context: dict, *, limit: int = 3) -> list[str]:
    """今日焦点事件，按优先级 公告 > 除权 > 宏观，最多 ``limit`` 条。

    公告按持仓权重降序取，权重缺失时保持原始顺序；全部来自已持久化/已抓取
    的真实数据，绝不编造。每条短语 ≤ 30 字。
    """
    pre = context.get("preMarket") or {}
    report_date = str(pre.get("reportDate") or "")
    phrases: list[str] = []
    weight_by_instrument: dict[str, Decimal] = {}
    for position in context.get("positions") or []:
        if isinstance(position, dict) and position.get("instrumentId"):
            weight = safe_decimal(position.get("weight"))
            if weight is not None:
                weight_by_instrument[position["instrumentId"]] = weight

    def sort_key(item: dict) -> tuple[Decimal, str]:
        weight = weight_by_instrument.get(str(item.get("instrumentId") or ""))
        return (
            weight if weight is not None else Decimal("-1"),
            str(item.get("title") or item.get("name") or ""),
        )

    announcements = pre.get("announcements")
    if isinstance(announcements, list):
        for announcement in sorted(announcements, key=sort_key, reverse=True):
            if not isinstance(announcement, dict):
                continue
            phrases.append(_clip_focus_phrase(_announcement_phrase(announcement)))
            if len(phrases) >= limit:
                return phrases

    for action in matched_actions(context):
        # 焦点事件只展示"今天"：上下文带 reportDate 时严格按 exDate 过滤。
        if report_date and _action_event_date(action) != report_date:
            continue
        phrases.append(
            _clip_focus_phrase(
                _action_impact_phrase(action, concise=True, report_date=report_date)
            )
        )
        if len(phrases) >= limit:
            return phrases

    economic_events = pre.get("economicEvents")
    if isinstance(economic_events, list):
        for event in economic_events:
            if not isinstance(event, dict):
                continue
            phrases.append(_clip_focus_phrase(_economic_phrase(event)))
            if len(phrases) >= limit:
                return phrases
    return phrases


def focus_event_text(context: dict) -> str:
    """今日焦点事件正文（报告/推送通用）：1-3 条以「；」分隔。"""
    phrases = _focus_event_phrases(context)
    return "；".join(phrases) if phrases else _FOCUS_NONE


# 操作参考：无持仓（数据不足）与有持仓但无触发规则时的确定性占位。
_ADVICE_NO_DATA = "今日无明确操作参考"
_ADVICE_WATCH = "今日无明确操作参考，可保持观望"


def _announcement_advice_phrase(announcement: dict) -> str:
    """公告 → 操作建议：按标题类型给参考关注点，不引用具体业绩数字。"""
    name = str(announcement.get("name") or announcement.get("symbol") or "公司")
    title = str(announcement.get("title") or "")
    rest = title
    for token in (name, str(announcement.get("symbol") or "")):
        if token and rest.startswith(token):
            rest = rest[len(token) :].lstrip("：:，, ")
            break
    detail = rest or "公告"
    if any(kw in title for kw in ("报告", "业绩")):
        focus = "关注业绩预期"
    elif "分红" in title:
        focus = "关注分红到账安排"
    elif any(kw in title for kw in ("回购", "增持", "减持")):
        focus = "关注后续进展"
    else:
        focus = "关注公告后续进展"
    return f"{name}发布{detail}，{focus}"


def _operation_advice_candidates(context: dict) -> list[tuple[int, str, str]]:
    """规则引擎：逐标的取优先级最高的一条，返回 (priority, instrumentId, phrase)。

    优先级：1 除权 > 2 公告 > 3 估值过期 > 4 基金申赎状态。同一标的多事件
    只保留优先级最高者；全部短语 ≤ 30 字、不调用 AI。
    """
    pre = context.get("preMarket") or {}
    report_date = str(pre.get("reportDate") or "")
    best: dict[str, tuple[int, str]] = {}

    def add(instrument_id: str, priority: int, phrase: str) -> None:
        current = best.get(instrument_id)
        if current is None or priority < current[0]:
            best[instrument_id] = (priority, _clip_focus_phrase(phrase))

    # P1 今日除权（仅持仓、仅今日）。
    for action in matched_actions(context):
        if report_date and _action_event_date(action) != report_date:
            continue
        if not _is_ex_rights_action(action):
            continue
        name = instrument_label(action, include_symbol=False)
        add(
            str(action.get("instrumentId") or ""),
            1,
            f"{name}今日除权，若除权后价格偏低，可关注填权行情",
        )

    # P2 公司公告（service 已按持仓 A 股抓取并过滤今日/关键词）。
    announcements = pre.get("announcements")
    if isinstance(announcements, list):
        for announcement in announcements:
            if not isinstance(announcement, dict):
                continue
            instrument_id = str(announcement.get("instrumentId") or "")
            # 公告抓取结果以 symbol 归属；无 instrumentId 时退化为公告代码去重。
            add(
                instrument_id or f"@ann:{announcement.get('symbol') or ''}",
                2,
                _announcement_advice_phrase(announcement),
            )

    # P3 持仓估值过期/缺失（STALE/MISSING）。
    stale_qualities = _STALE_QUALITIES
    for position in context.get("positions") or []:
        if not isinstance(position, dict):
            continue
        quality = str(position.get("quality") or "").upper()
        if quality not in stale_qualities:
            continue
        name = str(position.get("name") or position.get("symbol") or "持仓")
        add(
            str(position.get("instrumentId") or ""),
            3,
            f"{name}估值已过期，建议先更新净值再操作",
        )

    # P4 持仓基金申赎状态（暂停/正常+截止）。
    for subject in _reminder_subjects(context):
        suspended = _suspended_statuses(subject["profile"] or {})
        label = instrument_label(subject, include_symbol=False)
        if suspended:
            add(
                subject["instrumentId"],
                4,
                f"{label}今日暂停{'/'.join(suspended)}，建议暂勿操作",
            )
        else:
            cutoff = _earliest_cutoff(subject)
            if cutoff:
                add(
                    subject["instrumentId"],
                    4,
                    f"{label}今日{cutoff}前可申赎，请留意截止时间",
                )
    return sorted(
        (
            (priority, instrument_id, phrase)
            for instrument_id, (priority, phrase) in best.items()
        ),
        key=lambda entry: (entry[0],),
    )


def operation_advice_text(context: dict, *, limit: int = 2) -> str:
    """操作参考正文：优先展示风险相关建议（1-2 条，以「；」分隔）。

    - 无持仓（数据不足）→ 「今日无明确操作参考」
    - 有持仓但无任何触发 → 「今日无明确操作参考，可保持观望」
    """
    if not _held_instrument_ids(context):
        return _ADVICE_NO_DATA
    candidates = _operation_advice_candidates(context)
    if not candidates:
        return _ADVICE_WATCH
    return "；".join(candidate[2] for candidate in candidates[:limit])


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
    """一句话总结中的「今日事件」片段（持仓基金申赎状态/截止）。

    暂停优先（风险 > 常态截止）；无暂停时按最早截止口径；均无 → ``None``。
    """
    subjects = _reminder_subjects(context)
    if not subjects:
        return None
    suspended = [
        subject for subject in subjects if _suspended_statuses(subject["profile"] or {})
    ]
    if suspended:
        return f"{len(suspended)}只基金暂停申赎"
    cutoffs = [_earliest_cutoff(subject) for subject in subjects if subject["rules"]]
    cutoffs = [cutoff for cutoff in cutoffs if cutoff]
    if cutoffs:
        return f"{len(subjects)}只基金{min(cutoffs)}前可提交"
    return None


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
    if any(
        isinstance(action, dict) and _is_displayable_action(action)
        for action in (pre.get("corporateActions") or [])
    ) or reminder_has_data(context):
        return "FRESH"
    return "UNAVAILABLE"


def build_report(context: dict) -> dict:
    """Build the complete PRE_MARKET report without model I/O."""
    pre = context.get("preMarket") or {}
    overnight = _overnight(pre)
    attention = _attention(pre)
    impact = holding_impact_text(context)
    reminder = operation_reminder_text(context)
    focus = focus_event_text(context)
    if not focus.endswith("。"):
        focus += "。"
    advice = operation_advice_text(context)
    if not advice.endswith("。"):
        advice += "。"
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
            "今日焦点事件",
            focus,
            "FACT",
            quality if _focus_event_phrases(context) else "UNAVAILABLE",
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
            "操作参考",
            advice,
            "ACTION_PLAN",
            quality if _held_instrument_ids(context) else "UNAVAILABLE",
        ),
        (
            "今日操作提醒",
            reminder,
            "ACTION_PLAN",
            quality if reminder_has_data(context) else "UNAVAILABLE",
        ),
        ("一句话总结", conclusion, "FACT", sentiment_quality),
    )
    section_summary = "\n".join(
        f"{index}. {title}：{body}"
        for index, (title, body, _item_type, _item_quality) in enumerate(
            sections, start=1
        )
    )
    return {
        "summary": summary_with_holdings(context, section_summary),
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
    """Build the seven-line WeCom PRE_MARKET summary capped at 700 characters."""
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

    action_text = action_list_text(context, limit=3, entry_limit=30)
    economic = list_text(
        pre.get("economicEvents"), value_keys=("summary", "title", "name")
    )
    subscription = list_text(
        pre.get("subscriptionRedemptionChanges"),
        value_keys=("summary", "title", "name"),
    )

    # 持仓影响：持仓 ∩ 今日公司行动 + 持仓健康度风险。
    impact = impact_push_text(context)
    # 今日提醒：持仓基金申赎状态（暂停/可正常）+ 15:00 前截止窗口。
    reminder = operation_reminder_push_text(context)
    # 今日焦点事件：公告 > 除权 > 宏观（最多 3 条）。
    focus = focus_event_text(context)
    # 操作参考：规则引擎输出 1-2 条可执行建议。
    advice = operation_advice_text(context)
    # 一句话总结：市场情绪 + 持仓影响 + 今日事件，缺项省略。
    summary = "；".join(summary_clauses(context)) or _MISSING

    lines = [
        f"🌏 隔夜市场：美股{us}；A50期货{a50}",
        f"📌 今日关注：经济数据{economic}；公司行动{action_text}；申赎状态{subscription}",
        f"📊 持仓影响：{impact}",
        f"⏰ 今日提醒：{reminder}",
        f"🎯 今日焦点：{focus}",
        f"🧭 操作参考：{advice}",
        f"💡 一句话总结：{summary}",
    ]
    # 预算：单行上限 + 6 个换行 ≤ 700 字符（企微文本消息上限内）。
    limits = (90, 150, 80, 90, 100, 80, 60)
    return "\n".join(
        _clip(line, limit) for line, limit in zip(lines, limits, strict=True)
    )
