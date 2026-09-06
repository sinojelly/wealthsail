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
        return f"{period}预估涨跌幅及估值区间：{_MISSING}。{_ESTIMATION_SOURCE}"
    values = [
        f"{_label(metric['estimate'])}预估{metric['changePct']:+.2f}%，"
        f"{_range_phrase(metric)}"
        for metric in metrics
    ]
    reference = (
        f"估值日期{valuation_date or _MISSING}（非交易日历史参考）；"
        if historical
        else ""
    )
    return f"{reference}{'；'.join(values)}。{_ESTIMATION_SOURCE}"


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
    values: list[str] = []
    for estimate in estimates:
        metadata = estimate.get("metadata") or {}
        quality = str(estimate.get("quality") or _MISSING)
        if metadata.get("estimateSourceKind") == "EXTERNAL_PROVIDER":
            source = str(metadata.get("estimateSource") or "外部数据源")
            confirmed_as_of = _display_time(metadata.get("confirmedNavAsOf"))
            values.append(
                f"{_label(estimate)}：{source}外部盘中估值，"
                f"估值截至{_display_time(estimate.get('asOf'))}，"
                f"最近正式净值截至{confirmed_as_of}，"
                f"不提供持仓覆盖率，质量{quality}"
            )
            continue
        coverage = _safe_decimal(metadata.get("coveragePct"))
        coverage_text = f"{coverage:.2f}%" if coverage is not None else _MISSING
        coverage_label = _coverage_label(coverage)
        disclosed_on = str(metadata.get("disclosedOn") or _MISSING)
        confirmed_as_of = str(metadata.get("confirmedNavAsOf") or _MISSING)
        values.append(
            f"{_label(estimate)}覆盖率{coverage_text}，"
            f"基于{disclosed_on}披露持仓，参考价值{coverage_label}，"
            f"估值截至{_display_time(estimate.get('asOf'))}，"
            f"最近正式净值截至{confirmed_as_of}，质量{quality}"
        )
    return f"{'；'.join(values)}。{_ESTIMATION_SOURCE}"


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
    return {
        "summary": "\n".join(
            f"{index}. {title}：{body}"
            for index, (title, body, _item_type, _item_quality) in enumerate(
                sections, start=1
            )
        ),
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


def format_push_content(context: dict) -> str:
    """Build a five-line WeCom FUND_1430 summary capped at 200 characters."""
    metrics = _metrics(context)
    estimates = [
        item for item in (context.get("fundEstimates") or []) if isinstance(item, dict)
    ]
    first_metric = metrics[0] if metrics else None
    historical, valuation_date = _historical_reference(context)
    if first_metric:
        estimate = first_metric["estimate"]
        date_prefix = f"{(valuation_date or _MISSING)[5:]}参考 " if historical else ""
        overview = (
            f"{date_prefix}{_compact_symbol(estimate.get('symbol'))}"
            f"{first_metric['changePct']:+.2f}%（{_range_phrase(first_metric)}）"
        )
    else:
        overview = _MISSING

    first_estimate = estimates[0] if estimates else None
    if first_estimate:
        metadata = first_estimate.get("metadata") or {}
        coverage = _safe_decimal(metadata.get("coveragePct"))
        coverage_text = f"{coverage:.2f}%" if coverage is not None else _MISSING
        as_of = _display_time(first_estimate.get("asOf"))
        as_of_text = as_of[-5:] if as_of != _MISSING else _MISSING
        confidence = (
            f"{_compact_symbol(first_estimate.get('symbol'))}覆盖{coverage_text}，"
            f"{as_of_text}更新"
        )
    else:
        confidence = _MISSING

    rules = [
        item
        for item in ((context.get("fund1430") or {}).get("fundCutoffRules") or [])
        if isinstance(item, dict)
    ]
    if rules:
        rule = rules[0]
        cutoff = str(rule.get("cutoffTime") or "")[:5] or _MISSING
        reminder = (
            f"{_compact_symbol(rule.get('symbol'))}状态暂无；{cutoff}截止参考，"
            "能否申赎以渠道为准"
        )
    else:
        reminder = _MISSING

    if not metrics:
        summary = _MISSING
    else:
        # 推送总长 ≤200 字符，"建议"已合并到"总结"里只占一行。
        triggered_all = _triggered_metrics(metrics)
        positive = any(
            metric["changePct"] > _ACTION_THRESHOLD for metric in triggered_all
        )
        negative = any(
            metric["changePct"] < -_ACTION_THRESHOLD for metric in triggered_all
        )
        if positive and negative:
            summary = "估值分化，按阈值控制"
        elif positive:
            summary = "估值偏强，避免追涨"
        elif negative:
            summary = "估值偏弱，先核风险"
        else:
            summary = "估值未越阈值，原计划"

    lines = (
        _sourced_line("估值", overview, _ESTIMATION_SOURCE, 40),
        _sourced_line("可信", confidence, _ESTIMATION_SOURCE, 38),
        _sourced_line("申赎", reminder, _RULE_SOURCE, 48),
        _sourced_line("总结", summary, _RULE_SOURCE, 50),
    )
    return "\n".join(lines)
