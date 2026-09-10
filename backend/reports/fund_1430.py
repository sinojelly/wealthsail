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
    if not metrics:
        return ""
    # The audit table owns the per-fund facts.  This card deliberately remains
    # a one-sentence aggregate so a tenant holding many funds is not presented
    # with the same valuation list twice.
    threshold_note = (
        "已触发±3%估值偏离阈值，请结合风险预算复核。"
        if _triggered_metrics(metrics)
        else "未触发±3%估值偏离阈值。"
    )
    return f"已获取{len(metrics)}只持有基金的盘中预估；{_audit_estimate_summary(metrics)}{threshold_note}"


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
        return ""
    estimated_count = sum(
        _quality(estimate.get("quality")) == "ESTIMATED" for estimate in estimates
    )
    return (
        f"共{len(estimates)}只持有基金，其中{estimated_count}只采用盘中估算，非最终净值；"
        "连续变化仅基于已持久化的可信历史净值。来源：估值系统。"
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


def _held_otc_funds(context: dict) -> list[dict]:
    """Return each held OTC fund once, including records without a status row."""

    funds: dict[str, dict] = {}
    for position in context.get("positions") or []:
        if not isinstance(position, dict) or position.get("type") != "场外基金":
            continue
        instrument_id = str(position.get("instrumentId") or "")
        if instrument_id:
            funds.setdefault(instrument_id, position)
    return list(funds.values())


def _subscription_reminders(context: dict) -> list[tuple[int, str]]:
    """Build verified, per-fund reminders without inferring an open status.

    The priority is used only by the compact WeCom view: an actual suspension
    is more actionable than an unavailable record, which is more useful than
    a normal open status.  The audit card keeps every held fund's reminder.
    """

    fund_context = context.get("fund1430") or {}
    profiles = {
        str(profile.get("instrumentId") or ""): profile
        for profile in (fund_context.get("fundProfiles") or [])
        if isinstance(profile, dict) and profile.get("instrumentId")
    }
    cutoffs: dict[str, dict] = {}
    for rules in _cutoff_rule_groups(context):
        rule = next(
            (
                candidate
                for candidate in rules
                if str(candidate.get("salesChannel") or "DEFAULT") == "DEFAULT"
            ),
            rules[0],
        )
        instrument_id = str(rule.get("instrumentId") or "")
        if instrument_id:
            cutoffs[instrument_id] = rule

    reminders: list[tuple[int, str]] = []
    for fund in _held_otc_funds(context):
        instrument_id = str(fund.get("instrumentId") or "")
        label = _label(fund)
        profile = profiles.get(instrument_id)
        cutoff_rule = cutoffs.get(instrument_id)
        if profile is None:
            cutoff_note = ""
            if cutoff_rule is not None:
                cutoff = str(cutoff_rule.get("cutoffTime") or "")[:5]
                channel = str(cutoff_rule.get("salesChannel") or "DEFAULT")
                channel_text = "默认渠道" if channel == "DEFAULT" else channel
                cutoff_note = f"；{channel_text}{cutoff}截止参考"
            reminders.append(
                (1, f"{label}：未取得可核验状态{cutoff_note}（天天基金状态未取得，实际渠道为准）")
            )
            continue

        purchase = str(profile.get("purchaseStatus") or "").upper()
        redemption = str(profile.get("redemptionStatus") or "").upper()
        suspended: list[str] = []
        if purchase == "SUSPENDED":
            suspended.append("暂停申购")
        if redemption == "SUSPENDED":
            suspended.append("暂停赎回")
        if suspended:
            reminders.append(
                (0, f"{label}：{'、'.join(suspended)}（天天基金状态，实际渠道为准）")
            )
            continue

        if purchase == "OPEN" and redemption == "OPEN":
            cutoff_note = ""
            if cutoff_rule is not None:
                cutoff = str(cutoff_rule.get("cutoffTime") or "")[:5]
                channel = str(cutoff_rule.get("salesChannel") or "DEFAULT")
                channel_text = "默认渠道" if channel == "DEFAULT" else channel
                cutoff_note = f"；{channel_text}{cutoff}截止参考"
            reminders.append(
                (2, f"{label}：状态正常{cutoff_note}（天天基金状态，实际渠道为准）")
            )
            continue

        # A malformed legacy row is not evidence of an open fund.  Preserve
        # the same explicit downgrade used for a source record that is absent.
        reminders.append(
            (1, f"{label}：未取得可核验状态（天天基金状态未取得，实际渠道为准）")
        )
    return reminders


def _reminder(context: dict) -> str:
    reminders = _subscription_reminders(context)
    if not reminders:
        return f"{_MISSING}。{_RULE_SOURCE}"
    return "；".join(text for _, text in reminders)


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


def _attach_estimate_evidence(item: dict, metric: dict) -> dict:
    """Attach one representative persisted snapshot to an aggregate claim.

    Report items have a one-to-one evidence-link contract.  The table remains
    the complete per-fund view, while each concise aggregate card points to
    the source snapshot that directly supports its highlighted fact.
    """

    estimate = metric["estimate"]
    snapshot_id = estimate.get("snapshotId")
    if snapshot_id:
        item["_evidence"] = {
            "source_snapshot_id": snapshot_id,
            "claim_type": "VALUATION",
            "link_claim_type": "FACT",
            "as_of": estimate.get("asOf"),
            "excerpt": item["body"],
            "quality": _quality(estimate.get("quality")),
        }
    return item


def _metric_item(
    *, title: str, body: str, item_type: str, metric: dict, severity: str = "INFO"
) -> dict:
    """Create a compact card and preserve the selected fund's source link."""

    return _attach_estimate_evidence(
        {
            "item_type": item_type,
            "subject_id": metric["estimate"].get("instrumentId"),
            "severity": severity,
            "title": title,
            "body": body,
            "quality": _quality(metric["estimate"].get("quality")),
        },
        metric,
    )


def _compact_items(
    metrics: list[dict], context: dict, estimate_quality: str
) -> list[dict]:
    """Keep FUND_1430 audit cards decision-focused; the table holds all rows."""

    items: list[dict] = []
    if metrics:
        items.append(
            _metric_item(
                title=(
                    "最近交易日估值概览"
                    if _historical_reference(context)[0]
                    else "今日估值概览"
                ),
                body=_overview(metrics, context),
                item_type="FACT",
                metric=metrics[0],
            )
        )
        losses = sorted(
            (metric for metric in metrics if metric["changePct"] < 0),
            key=lambda metric: metric["changePct"],
        )[:3]
        if losses:
            body = "；".join(
                f"{_label(metric['estimate'])} {metric['changePct']:+.2f}%"
                for metric in losses
            )
            items.append(
                _metric_item(
                    title="最大亏损 Top 3",
                    body=body,
                    item_type="RISK",
                    metric=losses[0],
                )
            )
        gains = [metric for metric in metrics if metric["changePct"] > 0]
        if gains:
            best = max(gains, key=lambda metric: metric["changePct"])
            items.append(
                _metric_item(
                    title="最大盈利标的",
                    body=f"{_label(best['estimate'])} {best['changePct']:+.2f}%",
                    item_type="FACT",
                    metric=best,
                )
            )
        confidence_item = _metric_item(
            title="估值可信度",
            body=_confidence(context),
            item_type="FACT",
            metric=metrics[0],
        )
        # Confidence describes the entire estimate set, therefore its quality
        # must reflect the least fresh record rather than only the first row.
        confidence_item["quality"] = estimate_quality
        items.append(confidence_item)
    if _subscription_reminders(context):
        # A single concise reminder supersedes per-fund cutoff cards.  It is
        # operational guidance, not a valuation fact, so no snapshot is faked.
        items.append(
            {
                "item_type": "TODO",
                "severity": "INFO",
                "title": "申赎提醒",
                "body": _reminder(context),
                "quality": "FRESH",
            }
        )
    return items


def build_report(context: dict) -> dict:
    """Build the complete four-section FUND_1430 report without model I/O."""
    metrics = _metrics(context)
    estimates = context.get("fundEstimates") or []
    estimate_quality = _estimate_quality(
        [estimate for estimate in estimates if isinstance(estimate, dict)]
    )
    audit_summary = "\n".join(
        (
            _audit_estimate_table(metrics, context),
            f"所有基金估值来源：{_estimate_source_label(estimates)}，仅供参考。",
            _push_reminder(context),
        )
    )
    return {
        "summary": audit_summary,
        "items": _compact_items(metrics, context, estimate_quality),
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


def _estimated_market_value(estimate: dict, context: dict) -> Decimal | None:
    """Return the estimate-priced fund value only when every quantity is known.

    The audit table labels this value as a market value, but it is deliberately
    calculated with the same persisted 14:30 estimate as the adjacent daily
    change and P&L; it never borrows a stale portfolio value.
    """

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
        if quantity is None:
            return None
        total += estimate_value * quantity
    return total.quantize(Decimal("0.01"))


def _markdown_cell(value: object) -> str:
    """Escape a governed fact before it becomes a GFM table cell."""

    text = str(value if value is not None else "--")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip() or "--"


def _audit_estimate_summary(metrics: list[dict]) -> str:
    """Summarize only persisted, valid estimate changes for the audit view."""

    changes = [metric["changePct"] for metric in metrics]
    if not changes:
        return "今日整体预估均值：--；上涨0只、下跌0只、平盘0只。"
    average = sum(changes, start=Decimal("0")) / len(changes)
    up_count = sum(change > 0 for change in changes)
    down_count = sum(change < 0 for change in changes)
    flat_count = len(changes) - up_count - down_count
    return (
        f"今日整体预估均值：{average:+.2f}%；"
        f"上涨{up_count}只、下跌{down_count}只、平盘{flat_count}只。"
    )


def _audit_estimate_table(metrics: list[dict], context: dict) -> str:
    """Render the audit-only FUND_1430 table; WeCom keeps its text flow."""

    historical, valuation_date = _historical_reference(context)
    period = "最近交易日" if historical else "今日"
    historical_note = (
        f"（{valuation_date or _MISSING}，非交易日历史参考）" if historical else ""
    )
    estimates = [
        item for item in (context.get("fundEstimates") or []) if isinstance(item, dict)
    ]
    header = (
        f"📊 {period}基金估值（盘中估算，非最终净值；数据截至 "
        f"{_estimate_time_short(estimates)}）{historical_note}："
    )
    if not metrics:
        return f"{header}\n基金估值：数据不足。"

    lines = [
        header,
        _audit_estimate_summary(metrics),
        "",
        "| 基金名称 | 代码 | 当日预估 | 市值 | 累计盈亏 | 连续变化 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    # This ordering is audit-web-only.  WeCom keeps the existing list order
    # through its separate formatter so mobile delivery semantics do not change.
    for metric in sorted(metrics, key=lambda item: item["changePct"]):
        estimate = metric["estimate"]
        market_value = _estimated_market_value(estimate, context)
        pnl = _unrealized_pnl(estimate, context)
        market_value_text = f"{market_value:,.2f}" if market_value is not None else "--"
        pnl_text = f"{pnl:+.2f}" if pnl is not None else "--"
        cells = (
            estimate.get("name") or estimate.get("symbol") or "未命名基金",
            estimate.get("symbol") or "--",
            f"{metric['changePct']:+.2f}%",
            market_value_text,
            pnl_text,
            _fund_trend(estimate, context),
        )
        lines.append("| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |")
    return "\n".join(lines)


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
    """Reuse audit reminders, with the three most actionable items for WeCom."""

    reminders = sorted(_subscription_reminders(context), key=lambda item: item[0])[:3]
    if not reminders:
        return "申赎提醒：暂无。"
    return "申赎提醒：" + "；".join(text for _, text in reminders)


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
    rendered_metrics = 0
    for metric in metrics:
        line = _estimate_line(metric, context)
        trial = "\n".join([*lines, line, source_line, reminder_line])
        if len(trial) > 200:
            break
        lines.append(line)
        remaining -= 1
        rendered_metrics += 1
    # Do not claim that additional rows exist when the status reminder itself
    # consumed the compact-message budget before any valuation row could fit.
    if remaining and rendered_metrics:
        more_line = f"另有{remaining}只基金请在完整报告查看。"
        if len("\n".join([*lines, more_line, source_line, reminder_line])) <= 200:
            lines.append(more_line)
    if not metrics:
        lines.append("基金估值：数据不足。")
    lines.extend((source_line, reminder_line))
    return "\n".join(lines)
