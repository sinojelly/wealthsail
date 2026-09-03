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


def _range_text(metric: dict) -> str:
    lower = metric.get("lowerPct")
    upper = metric.get("upperPct")
    if lower is None or upper is None:
        return _MISSING
    return f"{lower:+.2f}%～{upper:+.2f}%"


def _overview(metrics: list[dict]) -> str:
    if not metrics:
        return f"今日预估涨跌幅及估值区间：{_MISSING}。{_ESTIMATION_SOURCE}"
    values = [
        f"{_label(metric['estimate'])}预估{metric['changePct']:+.2f}%，"
        f"区间{_range_text(metric)}"
        for metric in metrics
    ]
    return f"{'；'.join(values)}。{_ESTIMATION_SOURCE}"


def _coverage_label(coverage: Decimal | None) -> str:
    """按持仓覆盖率给出参考价值标签（不臆造，仅对真实覆盖率分级）。"""
    if coverage is None:
        return _MISSING
    if coverage >= Decimal("85"):
        return "较高"
    if coverage >= Decimal("60"):
        return "中等"
    return "有限"


# ---------------------------------------------------------------------------
# Real-data paths: each held OTC fund's holdings snapshot + live return rate
# ---------------------------------------------------------------------------


def _fund_metrics_by_id(context: dict) -> dict[str, dict]:
    """Aggregate holdings-weighted changePct per fund, plus coverage/reporting.

    For every fund in ``fund1430.fundLookthrough`` we pick only holdings
    whose live quote has a numeric ``return_rate`` (others are skipped to
    avoid skewing the weighted average).  ``coveragePct`` is the sum of
    weights we actually scored; if it is too low to trust, downstream
    callers render "参考价值有限" rather than the average.
    """
    fund_ctx = context.get("fund1430") or {}
    lookthrough = [
        row for row in (fund_ctx.get("fundLookthrough") or []) if isinstance(row, dict)
    ]
    if not lookthrough:
        return {}
    profiles = [
        row for row in (fund_ctx.get("fundProfiles") or []) if isinstance(row, dict)
    ]
    profile_by_id = {row.get("instrumentId"): row for row in profiles}
    holding_quotes = (
        fund_ctx.get("holdingQuoteBySymbol") or {}
        if isinstance(fund_ctx.get("holdingQuoteBySymbol"), dict)
        else {}
    )
    by_fund: dict[str, dict] = {}
    for row in lookthrough:
        fund_id = str(row.get("fundInstrumentId") or "")
        holding_symbol = str(row.get("holdingSymbol") or "")
        if not fund_id or not holding_symbol:
            continue
        weight = _safe_decimal(row.get("weightPct"))
        if weight is None or weight <= 0:
            continue
        observation = holding_quotes.get(holding_symbol)
        return_rate = getattr(observation, "return_rate", None) if observation else None
        return_rate_decimal = (
            _safe_decimal(return_rate) if return_rate is not None else None
        )
        if return_rate_decimal is None:
            continue
        bucket = by_fund.setdefault(
            fund_id,
            {
                "fundSymbol": str(row.get("fundSymbol") or ""),
                "fundName": str(row.get("fundName") or ""),
                "weightedSum": Decimal(0),
                "coveragePct": Decimal(0),
                "holdingCount": 0,
                "reportPeriod": str(row.get("reportPeriod") or ""),
                "reportPeriodDate": row.get("reportPeriod"),
                "profile": profile_by_id.get(fund_id),
            },
        )
        bucket["weightedSum"] += return_rate_decimal * weight
        bucket["coveragePct"] += weight
        bucket["holdingCount"] += 1
    result: dict[str, dict] = {}
    for fund_id, bucket in by_fund.items():
        coverage = bucket["coveragePct"]
        change_pct: Decimal | None = None
        if coverage > 0:
            change_pct = bucket["weightedSum"] / coverage
        result[fund_id] = {
            "fundSymbol": bucket["fundSymbol"],
            "fundName": bucket["fundName"],
            "changePct": change_pct,
            "coveragePct": coverage,
            "holdingCount": bucket["holdingCount"],
            "reportPeriod": bucket["reportPeriod"],
            "reportPeriodDate": bucket["reportPeriodDate"],
            "profile": bucket["profile"],
        }
    return result


def _fund_label(bucket: dict) -> str:
    symbol = bucket.get("fundSymbol") or ""
    name = bucket.get("fundName") or ""
    if symbol and name:
        return f"{name}({symbol})"
    return name or symbol or _MISSING


def _overview_lookthrough(metrics_by_fund: dict[str, dict]) -> str:
    """基于持仓加权计算的预估涨跌幅；按当前持仓顺序展示（外部按 positions 排序）。"""
    if not metrics_by_fund:
        return _MISSING
    parts: list[str] = []
    for fund_id, bucket in metrics_by_fund.items():
        change = bucket.get("changePct")
        if change is None:
            continue
        parts.append(
            f"{_fund_label(bucket)}今日预估 {change:+.2f}%"
            f"（基于 {bucket.get('holdingCount', 0)} 只持仓加权）"
        )
    if not parts:
        return _MISSING
    return "；".join(parts)


def _confidence_lookthrough(context: dict) -> str:
    """覆盖率、报告期、参考价值分级 + STALE 标记。

    每只在 ``fundLookthrough`` 出现的基金都展示一条（含无可用行情的基金），
    让用户看到报告期、覆盖率（按 Σ holdings weight_pct）和分级，超 90 天标 STALE。
    """
    from datetime import date as _date

    fund_ctx = context.get("fund1430") or {}
    lookthrough = [
        row for row in (fund_ctx.get("fundLookthrough") or []) if isinstance(row, dict)
    ]
    if not lookthrough:
        return _MISSING
    by_fund: dict[str, dict] = {}
    for row in lookthrough:
        fund_id = str(row.get("fundInstrumentId") or "")
        if not fund_id:
            continue
        weight = _safe_decimal(row.get("weightPct"))
        if weight is None or weight < 0:
            continue
        bucket = by_fund.setdefault(
            fund_id,
            {
                "fundSymbol": str(row.get("fundSymbol") or ""),
                "fundName": str(row.get("fundName") or ""),
                "totalWeight": Decimal(0),
                "holdingCount": 0,
                "reportPeriod": str(row.get("reportPeriod") or ""),
                "reportPeriodDate": row.get("reportPeriodDate"),
            },
        )
        bucket["totalWeight"] += weight
        bucket["holdingCount"] += 1

    parts: list[str] = []
    today = _date.today()
    for fund_id, bucket in by_fund.items():
        coverage = bucket["totalWeight"]
        holding_count = bucket["holdingCount"]
        coverage_text = f"{coverage:.2f}%" if coverage is not None else _MISSING
        coverage_label = _coverage_label(coverage)
        period_text = bucket["reportPeriod"] or _MISSING
        period_date = bucket.get("reportPeriodDate")
        stale_note = ""
        if isinstance(period_date, _date) and (today - period_date).days > 90:
            stale_note = "，持仓数据已超过 3 个月，仅供参考"
        parts.append(
            f"{_fund_label(bucket)}基于 {period_text} 持仓数据，覆盖 {coverage_text} 权重"
            f"（已纳入 {holding_count} 只持仓），参考价值 {coverage_label}{stale_note}"
        )
    if not parts:
        return _MISSING
    return "；".join(parts)


def _reminder_lookthrough(metrics_by_fund: dict[str, dict], context: dict) -> str:
    """申赎状态：结合 fund_profiles + cutoff_rules。"""
    rules = _cutoff_rules(context)
    if not metrics_by_fund:
        return _MISSING
    parts: list[str] = []
    for fund_id, bucket in metrics_by_fund.items():
        profile = bucket.get("profile") or {}
        purchase = str(profile.get("purchaseStatus") or "").upper()
        redemption = str(profile.get("redemptionStatus") or "").upper()
        fund_rules = [
            rule for rule in rules if str(rule.get("instrumentId") or "") == fund_id
        ]
        earliest = _earliest_cutoff(fund_rules)
        cutoff_text = earliest or "15:00"
        if purchase == "SUSPENDED" and redemption == "SUSPENDED":
            parts.append(f"{_fund_label(bucket)}今日暂停申购与赎回，请以基金公告为准")
            continue
        if purchase == "SUSPENDED":
            parts.append(
                f"{_fund_label(bucket)}今日暂停申购（赎回按 {cutoff_text} 前提交受理），"
                "请以基金公告为准"
            )
            continue
        if redemption == "SUSPENDED":
            parts.append(
                f"{_fund_label(bucket)}今日暂停赎回（申购按 {cutoff_text} 前提交受理），"
                "请以基金公告为准"
            )
            continue
        if purchase == "" and redemption == "":
            parts.append(f"{_fund_label(bucket)}申赎状态未确认，请以基金公告为准")
            continue
        parts.append(
            f"{_fund_label(bucket)}今日正常申购赎回，{cutoff_text} 前提交按今日净值成交"
        )
    if not parts:
        return _MISSING
    return "；".join(parts)


def _triggered_metrics(metrics: list[dict]) -> list[dict]:
    return sorted(
        [metric for metric in metrics if abs(metric["changePct"]) > _ACTION_THRESHOLD],
        key=lambda metric: abs(metric["changePct"]),
        reverse=True,
    )


def _cutoff_rules(context: dict) -> list[dict]:
    fund_context = context.get("fund1430") or {}
    return [
        item
        for item in (fund_context.get("fundCutoffRules") or [])
        if isinstance(item, dict)
    ]


def _earliest_cutoff(rules: list[dict]) -> str:
    """Earliest cutoff time ``HH:MM`` across an instrument's rules rules, or ``"``."""
    cutoffs: list[str] = []
    for rule in rules:
        cutoff = str(rule.get("cutoffTime") or "")[:5]
        if cutoff:
            cutoffs.append(cutoff)
    return min(cutoffs) if cutoffs else ""


def _summary(metrics_by_fund: dict[str, dict], context: dict) -> str:
    """基于真实加权估值 + 申赎状态的 14:30 综合判断（不臆造）。

    规则（与需求文档一致）：
    - 申赎受限 → "今日申赎受限，请留意具体规则"
    - 估值 > +0.5% 且申赎正常 → "今日估值偏乐观，可正常申赎"
    - 估值 < -0.5% 且申赎正常 → "今日估值偏弱，可观望"
    - 估值在 ±0.5% 区间 → "今日估值波动较小，按原计划执行"
    - 数据全空 → "今日估值与申赎均暂无数据..."
    """
    has_metrics = bool(metrics_by_fund)
    rules = _cutoff_rules(context)
    if not has_metrics and not rules:
        return f"今日估值与申赎均暂无数据，请以基金公告与渠道状态为准。{_ESTIMATION_SOURCE}"
    # Aggregate valuation across all held funds, weighted by coverage.
    weighted_total = Decimal(0)
    coverage_total = Decimal(0)
    paused_funds: list[str] = []
    for fund_id, bucket in metrics_by_fund.items():
        change = bucket.get("changePct")
        coverage = bucket.get("coveragePct") or Decimal(0)
        if change is None or coverage <= 0:
            continue
        weighted_total += change * coverage
        coverage_total += coverage
        profile = bucket.get("profile") or {}
        if (
            str(profile.get("purchaseStatus") or "").upper() == "SUSPENDED"
            or str(profile.get("redemptionStatus") or "").upper() == "SUSPENDED"
        ):
            paused_funds.append(_fund_label(bucket))
    aggregated = (weighted_total / coverage_total) if coverage_total > 0 else None
    paused = bool(paused_funds)
    if paused:
        return (
            "今日申赎受限（"
            + "、".join(paused_funds)
            + "），请留意具体规则。"
            + _RULE_SOURCE
        )
    if aggregated is None:
        # No weighted data but have cutoff rules: stay conservative.
        return (
            f"今日估值数据不足（无加权持仓），申赎以 {rules and '15:00'} 截止为准。"
            f"{_RULE_SOURCE}"
        )
    threshold = Decimal("0.5")
    if aggregated > threshold:
        return f"今日估值偏乐观（加权 {aggregated:+.2f}%），可正常申赎。{_RULE_SOURCE}"
    if aggregated < -threshold:
        return f"今日估值偏弱（加权 {aggregated:+.2f}%），可观望。{_RULE_SOURCE}"
    return f"今日估值波动较小（加权 {aggregated:+.2f}%），按原计划执行。{_RULE_SOURCE}"


def _quality(value: object) -> str:
    normalized = str(value or "UNAVAILABLE").upper()
    return (
        normalized if normalized in {"FRESH", "ESTIMATED", "STALE"} else "UNAVAILABLE"
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
                f"区间{_range_text(metric)}。{_ESTIMATION_SOURCE}"
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
    """Build the complete four-section FUND_1430 report without model I/O.

    Sections are driven by the lookthrough + live-quote snapshot that the
    service layer preloads into ``context['fund1430']``.  When no
    lookthrough rows score against live ticks, each section degrades to a
    single 「暂无」 line rather than fabricating data.
    """
    metrics_by_fund = _fund_metrics_by_id(context)
    overview = _overview_lookthrough(metrics_by_fund)
    confidence = _confidence_lookthrough(context)
    reminder = _reminder_lookthrough(metrics_by_fund, context)
    summary = _summary(metrics_by_fund, context)
    cutoff_rules = _cutoff_rules(context)
    sections = (
        (
            "今日估值概览",
            overview,
            "FACT",
            "ESTIMATED" if metrics_by_fund else "UNAVAILABLE",
        ),
        (
            "估值可信度",
            confidence,
            "FACT",
            "ESTIMATED" if metrics_by_fund else "UNAVAILABLE",
        ),
        (
            "申赎提醒",
            reminder,
            "TODO",
            "FRESH" if cutoff_rules else "UNAVAILABLE",
        ),
        (
            "总结",
            summary,
            "ACTION_PLAN",
            "ESTIMATED" if (metrics_by_fund or cutoff_rules) else "UNAVAILABLE",
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
        + _lookthrough_detail_items(metrics_by_fund, context)
        + _cutoff_detail_items(context),
        "quality": "ESTIMATED" if metrics_by_fund else "UNAVAILABLE",
        # Held-fund profiles + lookthrough are authoritatively sourced, so
        # we no longer mark the brief as "PARTIAL" simply because fund_1430
        # wasn't tracked before.  Coverage gaps still flow into the summary.
        "status": "PARTIAL",
        "policy_check_status": "WARNING",
    }


def _lookthrough_detail_items(
    metrics_by_fund: dict[str, dict], context: dict
) -> list[dict]:
    """每个被加权过的持仓基金追加一条 detail item（含权重与快照来源）。"""
    items: list[dict] = []
    for fund_id, bucket in metrics_by_fund.items():
        body_parts: list[str] = []
        change = bucket.get("changePct")
        coverage = bucket.get("coveragePct")
        if change is not None and coverage:
            body_parts.append(
                f"今日预估 {change:+.2f}%；覆盖 {coverage:.2f}% 权重（"
                f"{bucket.get('holdingCount', 0)} 只持仓）"
            )
        period_text = bucket.get("reportPeriod") or _MISSING
        body_parts.append(f"报告期 {period_text}")
        items.append(
            {
                "item_type": "FUND_ESTIMATE",
                "subject_id": fund_id,
                "severity": "INFO",
                "title": f"{_fund_label(bucket)}持仓加权明细",
                "body": "；".join(body_parts) + f"。{_ESTIMATION_SOURCE}",
                "quality": "ESTIMATED",
            }
        )
    return items


def _sourced_line(label: str, body: str, source: str, limit: int) -> str:
    prefix = f"{label}："
    available = limit - len(prefix) - len(source)
    if len(body) > available:
        body = body[: available - 1].rstrip("；，。") + "…"
    return f"{prefix}{body}{source}"


def format_push_content(context: dict) -> str:
    """Build a four-line WeCom FUND_1430 summary capped at 200 characters."""
    metrics_by_fund = _fund_metrics_by_id(context)
    rules = _cutoff_rules(context)
    # 估值行：第一只持仓基金的预估 + 加权 holdings 数；其余省略以保总长 ≤200。
    first_fund_id = next(iter(metrics_by_fund), None)
    overview = _MISSING
    confidence = _MISSING
    if first_fund_id:
        bucket = metrics_by_fund[first_fund_id]
        change = bucket.get("changePct")
        if change is not None:
            overview = (
                f"{_fund_label(bucket)}{change:+.2f}%"
                f"（{bucket.get('holdingCount', 0)}只加权）"
            )
        coverage = bucket.get("coveragePct")
        period = bucket.get("reportPeriod") or ""
        if coverage is not None and period:
            confidence = (
                f"{period[5:10] if len(period) >= 10 else period}覆盖{coverage:.0f}%"
            )
    # 申赎行：第一只截止规则 + 渠道状态。
    if rules:
        rule = next(
            (r for r in rules if str(r.get("salesChannel") or "DEFAULT") == "DEFAULT"),
            rules[0],
        )
        cutoff = str(rule.get("cutoffTime") or "")[:5] or "15:00"
        reminder = (
            f"{_compact_symbol(rule.get('symbol'))}{cutoff}截止参考，能否申赎以渠道为准"
        )
    else:
        reminder = _MISSING
    # 总结行：复用 _summary。
    summary = _summary(metrics_by_fund, context)
    if summary.startswith("今日估值与申赎均暂无数据") or summary.startswith(
        "今日估值数据不足"
    ):
        summary = "暂无"

    lines = (
        _sourced_line("估值", overview, _ESTIMATION_SOURCE, 56),
        _sourced_line("可信", confidence, _ESTIMATION_SOURCE, 48),
        _sourced_line("申赎", reminder, _RULE_SOURCE, 52),
        _sourced_line("总结", summary, _RULE_SOURCE, 40),
    )
    return "\n".join(lines)
