"""Deterministic FUND_1430 report content built from persisted valuation facts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

_MISSING = "暂无"
_ESTIMATION_SOURCE = "【基于估值系统】"
_RULE_SOURCE = "【基于规则引擎】"
_ACTION_THRESHOLD = Decimal("3")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _safe_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _label(item: dict, *, include_name: bool = True) -> str:
    name = str(item.get("name") or item.get("symbol") or "未命名基金")
    symbol = str(item.get("symbol") or "").strip()
    if include_name and symbol and symbol != name:
        return f"{name}({symbol})"
    return name if include_name else (symbol or name)


def _compact_symbol(value: object, limit: int = 8) -> str:
    symbol = str(value or "基金")
    if len(symbol) <= limit:
        return symbol
    return f"{symbol[:3]}…{symbol[-3:]}"


def _display_time(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return value.strip()[:16].replace("T", " ")
    else:
        return _MISSING
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_SHANGHAI)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _estimate_metric(estimate: dict) -> dict | None:
    metadata = estimate.get("metadata") or {}
    confirmed_nav = _safe_decimal(metadata.get("confirmedNav"))
    value = _safe_decimal(estimate.get("value"))
    if confirmed_nav is None or confirmed_nav <= 0 or value is None:
        return None
    lower = _safe_decimal(estimate.get("lowerValue"))
    upper = _safe_decimal(estimate.get("upperValue"))
    hundred = Decimal("100")
    return {
        "estimate": estimate,
        "changePct": (value / confirmed_nav - 1) * hundred,
        "lowerPct": (lower / confirmed_nav - 1) * hundred
        if lower is not None
        else None,
        "upperPct": (upper / confirmed_nav - 1) * hundred
        if upper is not None
        else None,
    }


def _metrics(context: dict) -> list[dict]:
    result: list[dict] = []
    for estimate in context.get("fundEstimates") or []:
        if not isinstance(estimate, dict):
            continue
        metric = _estimate_metric(estimate)
        if metric is not None:
            result.append(metric)
    return result


def _range_phrase(metric: dict) -> str:
    lower = metric.get("lowerPct")
    upper = metric.get("upperPct")
    if lower is None or upper is None:
        metadata = metric["estimate"].get("metadata") or {}
        if metadata.get("estimateSourceKind") == "EXTERNAL_PROVIDER":
            return "来源不提供区间"
        return f"区间{_MISSING}"
    return f"区间{lower:+.2f}%～{upper:+.2f}%"


def _historical_reference(context: dict) -> tuple[bool, str | None]:
    """Return the provider valuation date when this is a holiday/weekend view."""
    for estimate in context.get("fundEstimates") or []:
        if not isinstance(estimate, dict):
            continue
        metadata = estimate.get("metadata") or {}
        if not metadata.get("nonTradingDayReference"):
            continue
        valuation_date = str(metadata.get("valuationDate") or "").strip()
        if not valuation_date:
            displayed = _display_time(estimate.get("asOf"))
            valuation_date = displayed[:10] if displayed != _MISSING else ""
        return True, valuation_date or None
    return False, None


def _overview(metrics: list[dict], context: dict) -> str:
    historical, valuation_date = _historical_reference(context)
    period = "最近交易日" if historical else "今日"
    if not metrics:
        return f"📊 {period}基金估值（数据截至 {_MISSING}）：\n基金估值：数据不足。"
    historical_note = (
        f"（{valuation_date or _MISSING}，非交易日历史参考）" if historical else ""
    )
    header = (
        f"📊 {period}基金估值（数据截至 "
        f"{_estimate_time_short(context.get('fundEstimates') or [])}）{historical_note}："
    )
    return "\n".join([header, *(_audit_estimate_line(metric, context) for metric in metrics)])


def _coverage_label(coverage: Decimal | None) -> str:
    """按持仓覆盖率给出参考价值标签（不臆造，仅对真实覆盖率分级）。"""
    if coverage is None:
        return _MISSING
    if coverage >= Decimal("85"):
        return "较高"
    if coverage >= Decimal("60"):
        return "中等"
    return "有限"


def _confidence(context: dict) -> str:
    estimates = [
        item for item in (context.get("fundEstimates") or []) if isinstance(item, dict)
    ]
    if not estimates:
        return f"持仓覆盖率：{_MISSING}；数据新鲜度：{_MISSING}。{_ESTIMATION_SOURCE}"
    estimated_count = sum(
        _quality(estimate.get("quality")) == "ESTIMATED" for estimate in estimates
    )
    return (
        f"本报告含{len(estimates)}只持有基金，其中{estimated_count}只为盘中估值"
        "（以基金名称后的“（估）”标记）；数据不足时不计算连续变化或盈亏。"
        f"{_ESTIMATION_SOURCE}"
    )


def _cutoff_rules(context: dict) -> list[dict]:
    fund_context = context.get("fund1430") or {}
    return [
        item
        for item in (fund_context.get("fundCutoffRules") or [])
        if isinstance(item, dict)
    ]


def _cutoff_rule_groups(context: dict) -> list[list[dict]]:
    groups: dict[str, list[dict]] = {}
    for rule in _cutoff_rules(context):
        key = str(
            rule.get("instrumentId")
            or rule.get("symbol")
            or rule.get("name")
            or id(rule)
        )
        groups.setdefault(key, []).append(rule)
    return list(groups.values())


def _reminder(context: dict) -> str:
    rule_groups = _cutoff_rule_groups(context)
    if not rule_groups:
        return f"{_MISSING}。{_RULE_SOURCE}"
    reminders: list[str] = []
    for rules in rule_groups[:2]:
        rule = next(
            (
                candidate
                for candidate in rules
                if str(candidate.get("salesChannel") or "DEFAULT") == "DEFAULT"
            ),
            rules[0],
        )
        cutoff = str(rule.get("cutoffTime") or "")[:5] or _MISSING
        channel = str(rule.get("salesChannel") or "DEFAULT")
        channel_text = "默认渠道" if channel == "DEFAULT" else channel
        reminders.append(
            f"{_label(rule)}：状态：{_MISSING}；{channel_text}{cutoff}截止参考，"
            "实际能否申赎以销售渠道为准"
        )
    remaining = len(rule_groups) - len(reminders)
    if remaining:
        reminders.append(f"另有{remaining}只基金的截止规则，请在报告明细中核对")
    return f"{'；'.join(reminders)}。{_RULE_SOURCE}"


def _triggered_metrics(metrics: list[dict]) -> list[dict]:
    return sorted(
        [metric for metric in metrics if abs(metric["changePct"]) > _ACTION_THRESHOLD],
        key=lambda metric: abs(metric["changePct"]),
        reverse=True,
    )


def _earliest_cutoff(context: dict) -> str | None:
    """Return the earliest persisted cutoff; never invent a default time."""
    cutoffs = sorted(
        {
            str(rule.get("cutoffTime") or "")[:5]
            for rule in _cutoff_rules(context)
            if str(rule.get("cutoffTime") or "")[:5]
        }
    )
    return cutoffs[0] if cutoffs else None


def _summary(metrics: list[dict], context: dict) -> str:
    """综合判断：估值方向 + ±3% 阈值触发 + 申赎截止状态 → 单条结论。

    数据全空时输出兜底；只要有估算或截止任一输入即给具体判断，不臆造。
    """
    triggered = _triggered_metrics(metrics)
    historical, valuation_date = _historical_reference(context)
    period = "最近交易日" if historical else "今日"
    historical_note = (
        f"（{valuation_date or _MISSING}，非交易日历史参考）" if historical else ""
    )
    has_metrics = bool(metrics)
    cutoff_rules = _cutoff_rules(context)
    if not has_metrics and not cutoff_rules:
        return (
            f"{period}估值与申赎均暂无数据，请以基金公告与渠道状态为准。"
            f"{_ESTIMATION_SOURCE}"
        )
    positive = any(metric["changePct"] > _ACTION_THRESHOLD for metric in triggered)
    negative = any(metric["changePct"] < -_ACTION_THRESHOLD for metric in triggered)
    triggered_n = len(triggered)
    if positive and not negative:
        valuation_view = (
            f"{period}估值偏强（{triggered_n}只基金预估涨幅超过+3%）"
            if triggered_n
            else f"{period}估值偏强"
        )
        advice = "已有减仓计划时先复核阈值与风险预算"
    elif negative and not positive:
        valuation_view = (
            f"{period}估值偏弱（{triggered_n}只基金预估跌幅低于-3%）"
            if triggered_n
            else f"{period}估值偏弱"
        )
        advice = "建议先核对风险预算"
    elif positive and negative:
        positive_n = sum(
            1 for metric in triggered if metric["changePct"] > _ACTION_THRESHOLD
        )
        negative_n = sum(
            1 for metric in triggered if metric["changePct"] < -_ACTION_THRESHOLD
        )
        valuation_view = (
            f"{period}估值分化（{positive_n}只基金预估涨幅超过+3%、"
            f"{negative_n}只基金预估跌幅低于-3%）"
        )
        advice = "按阈值控制并优先复核风险预算"
    else:
        valuation_view = f"{period}估值整体温和（未触发±3%阈值）"
        advice = "建议按原计划操作"
    cutoff = _earliest_cutoff(context)
    channel_note = (
        f"申赎状态以销售渠道为准；已记录最早截止时间{cutoff}，操作前请复核"
        if cutoff
        else "暂无申赎截止数据，能否申赎以销售渠道为准"
    )
    return (
        f"{valuation_view}{historical_note}，{advice}；{channel_note}。{_RULE_SOURCE}"
    )


def _quality(value: object) -> str:
    normalized = str(value or "UNAVAILABLE").upper()
    return (
        normalized if normalized in {"FRESH", "ESTIMATED", "STALE"} else "UNAVAILABLE"
    )


def _estimate_quality(estimates: list[dict]) -> str:
    """Return the least trustworthy persisted estimate quality in the report."""
    if not estimates:
        return "UNAVAILABLE"
    priority = {"FRESH": 0, "ESTIMATED": 1, "STALE": 2, "UNAVAILABLE": 3}
    return max(
        (_quality(estimate.get("quality")) for estimate in estimates),
        key=priority.__getitem__,
    )


def _evidence_items(context: dict) -> list[dict]:
    items: list[dict] = []
    for estimate in context.get("fundEstimates") or []:
        if not isinstance(estimate, dict):
            continue
        metric = _estimate_metric(estimate)
        quality = _quality(estimate.get("quality"))
        if metric is None:
            body = f"预估涨跌幅及区间：{_MISSING}。{_ESTIMATION_SOURCE}"
            severity = "WARNING"
        else:
            body = (
                f"预估涨跌幅{metric['changePct']:+.2f}%，"
                f"{_range_phrase(metric)}。{_ESTIMATION_SOURCE}"
            )
            severity = "INFO"
        item = {
            "item_type": "FUND_ESTIMATE",
            "subject_id": estimate.get("instrumentId"),
            "severity": severity,
            "title": f"{_label(estimate)}估值明细",
            "body": body,
            "quality": quality,
        }
        if estimate.get("snapshotId"):
            item["_evidence"] = {
                "source_snapshot_id": estimate["snapshotId"],
                "claim_type": "VALUATION",
                "link_claim_type": "FACT",
                "as_of": estimate.get("asOf"),
                "excerpt": body,
                "quality": quality,
            }
        items.append(item)
    return items


def _cutoff_detail_items(context: dict) -> list[dict]:
    items: list[dict] = []
    for rule in _cutoff_rules(context):
        cutoff = str(rule.get("cutoffTime") or "")[:5] or _MISSING
        channel = str(rule.get("salesChannel") or "DEFAULT")
        channel_text = "默认渠道" if channel == "DEFAULT" else channel
        items.append(
            {
                "item_type": "TODO",
                "subject_id": rule.get("instrumentId"),
                "severity": "INFO",
                "title": f"{_label(rule)}{channel_text}申赎截止明细",
                "body": (
                    f"状态：{_MISSING}；{channel_text}{cutoff}截止参考，"
                    f"实际能否申赎以销售渠道为准。{_RULE_SOURCE}"
                ),
                "quality": "FRESH",
            }
        )
    return items


def build_report(context: dict) -> dict:
    """Build the complete four-section FUND_1430 report without model I/O."""
    metrics = _metrics(context)
    historical, _valuation_date = _historical_reference(context)
    overview = _overview(metrics, context)
    confidence = _confidence(context)
    reminder = _reminder(context)
    summary = _summary(metrics, context)
    estimates = context.get("fundEstimates") or []
    estimate_quality = _estimate_quality(
        [estimate for estimate in estimates if isinstance(estimate, dict)]
    )
    cutoff_rules = (context.get("fund1430") or {}).get("fundCutoffRules") or []
    sections = (
        (
            "最近交易日估值概览" if historical else "今日估值概览",
            overview,
            "FACT",
            "ESTIMATED" if metrics else "UNAVAILABLE",
        ),
        (
            "估值可信度",
            confidence,
            "FACT",
            estimate_quality,
        ),
        ("申赎提醒", reminder, "TODO", "FRESH" if cutoff_rules else "UNAVAILABLE"),
        (
            "总结",
            summary,
            "ACTION_PLAN",
            "ESTIMATED" if (metrics or cutoff_rules) else "UNAVAILABLE",
        ),
    )
    section_items = [
        {
            "item_type": item_type,
            "severity": "WARNING" if item_quality == "UNAVAILABLE" else "INFO",
            "title": title,
            "body": body,
            "quality": item_quality,
        }
        for title, body, item_type, item_quality in sections
    ]
    audit_summary = "\n".join(
        (
            overview,
            f"所有基金估值来源：{_estimate_source_label(estimates)}，仅供参考。",
            _push_reminder(context),
        )
    )
    return {
        "summary": audit_summary,
        "items": section_items
        + _evidence_items(context)
        + _cutoff_detail_items(context),
        "quality": estimate_quality if metrics else "UNAVAILABLE",
        # Authoritative subscription/redemption status is not persisted yet.
        "status": "PARTIAL",
        "policy_check_status": "WARNING",
    }


def _sourced_line(label: str, body: str, source: str, limit: int) -> str:
    prefix = f"{label}："
    available = limit - len(prefix) - len(source)
    if len(body) > available:
        body = body[: available - 1].rstrip("；，。") + "…"
    return f"{prefix}{body}{source}"


def _estimate_time_label(estimates: list[dict]) -> str:
    """Show the newest persisted valuation timestamp once for the whole push."""

    times = [
        _display_time(estimate.get("asOf"))
        for estimate in estimates
        if _display_time(estimate.get("asOf")) != _MISSING
    ]
    return max(times) if times else _MISSING


def _estimate_time_short(estimates: list[dict]) -> str:
    displayed = _estimate_time_label(estimates)
    return displayed[-5:] if displayed != _MISSING else _MISSING


def _estimate_source_label(estimates: list[dict]) -> str:
    """Normalize the persisted provider label without claiming an unknown source."""

    sources = {
        str((estimate.get("metadata") or {}).get("estimateSource") or "").strip()
        for estimate in estimates
    }
    sources.discard("")
    if len(sources) == 1:
        source = next(iter(sources))
        if "sina" in source.lower() or "新浪" in source:
            return "新浪财经"
        return source
    if not sources:
        return "数据源未标注"
    return "多数据源"


def _unrealized_pnl(estimate: dict, context: dict) -> Decimal | None:
    """Return the held fund's estimate-based P&L only with complete facts."""

    instrument_id = str(estimate.get("instrumentId") or "")
    estimate_value = _safe_decimal(estimate.get("value"))
    if not instrument_id or estimate_value is None:
        return None
    holdings = [
        position
        for position in (context.get("positions") or [])
        if isinstance(position, dict)
        and str(position.get("instrumentId") or "") == instrument_id
    ]
    if not holdings:
        return None
    total = Decimal("0")
    for position in holdings:
        quantity = _safe_decimal(position.get("quantity"))
        avg_cost = _safe_decimal(position.get("avgCost"))
        if quantity is None or avg_cost is None:
            return None
        total += (estimate_value - avg_cost) * quantity
    return total.quantize(Decimal("0.01"))


def _fund_trend(estimate: dict, context: dict) -> str:
    direct_display = estimate.get("trendDisplay")
    if direct_display and str(direct_display) != "--":
        return str(direct_display)
    trends = context.get("fundTrends") or {}
    if not isinstance(trends, dict):
        return "--"
    display = trends.get(str(estimate.get("instrumentId") or ""))
    return str(display) if display and str(display) != "--" else "--"


def _estimate_line(metric: dict, context: dict) -> str:
    """One compact, complete fund valuation line for a WeCom text message."""

    estimate = metric["estimate"]
    marker = "（估）" if _quality(estimate.get("quality")) == "ESTIMATED" else ""
    pnl = _unrealized_pnl(estimate, context)
    pnl_text = f"{pnl:+.2f}" if pnl is not None else "数据不足"
    return (
        f"{_label(estimate)}{marker}预估 {metric['changePct']:+.2f}% "
        f"{_fund_trend(estimate, context)} 盈亏 {pnl_text}"
    )


def _audit_estimate_line(metric: dict, context: dict) -> str:
    """Render one complete fund row for the report-audit summary text flow."""

    estimate = metric["estimate"]
    marker = "（估）" if _quality(estimate.get("quality")) == "ESTIMATED" else ""
    trend = _fund_trend(estimate, context)
    pnl = _unrealized_pnl(estimate, context)
    pnl_text = f"{pnl:+.2f}" if pnl is not None else "数据不足"
    return (
        f"{_label(estimate)} 预估 {metric['changePct']:+.2f}%{marker}{trend} "
        f"盈亏 {pnl_text}"
    )


def _push_reminder(context: dict) -> str:
    """Keep the WeCom reminder factual and brief; full rules remain auditable."""

    rules = _cutoff_rule_groups(context)
    if not rules:
        return "申赎提醒：暂无。"
    rule = next(
        (
            candidate
            for candidate in rules[0]
            if str(candidate.get("salesChannel") or "DEFAULT") == "DEFAULT"
        ),
        rules[0][0],
    )
    cutoff = str(rule.get("cutoffTime") or "")[:5] or _MISSING
    return f"申赎提醒：{_label(rule)} {cutoff}前可操作（以渠道状态为准）。"


def format_push_content(context: dict) -> str:
    """Build a compact WeCom FUND_1430 text flow capped at 200 characters.

    Unlike the audit detail, the notification reports each fund's estimated
    move, prior-day trend and estimate-based P&L exactly once.  It never
    repeats the source, timestamp, range limitation or quality label per row.
    """

    metrics = _metrics(context)
    estimates = [
        item for item in (context.get("fundEstimates") or []) if isinstance(item, dict)
    ]
    historical, valuation_date = _historical_reference(context)
    period = "最近交易日" if historical else "今日"
    historical_note = (
        f"（{valuation_date or _MISSING}，非交易日历史参考）" if historical else ""
    )
    header = f"📊 {period}基金估值（数据截至 {_estimate_time_label(estimates)}）{historical_note}："
    source_line = f"所有基金估值来源：{_estimate_source_label(estimates)}，仅供参考。"
    reminder_line = _push_reminder(context)
    lines = [header]
    remaining = len(metrics)
    for metric in metrics:
        line = _estimate_line(metric, context)
        trial = "\n".join([*lines, line, source_line, reminder_line])
        if len(trial) > 200:
            break
        lines.append(line)
        remaining -= 1
    if remaining:
        more_line = f"另有{remaining}只基金请在完整报告查看。"
        if len("\n".join([*lines, more_line, source_line, reminder_line])) <= 200:
            lines.append(more_line)
    if not metrics:
        lines.append("基金估值：数据不足。")
    lines.extend((source_line, reminder_line))
    return "\n".join(lines)
