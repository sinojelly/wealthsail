"""Deterministic POST_MARKET content built from persisted structured facts."""

from __future__ import annotations

from decimal import Decimal

_MISSING = "暂不可用"
_MAX_PUSH_CHARS = 300


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _signed(value: Decimal, places: int = 2) -> str:
    return f"{value:+,.{places}f}"


def _performance(context: dict) -> tuple[str, str]:
    performance = (context.get("postMarket") or {}).get("performance") or {}
    quality = str(performance.get("quality") or "UNAVAILABLE").upper()
    daily_pnl = _decimal(performance.get("dailyPnl"))
    daily_rate = _decimal(performance.get("dailyPnlRate"))
    if quality not in {"FRESH", "ESTIMATED"}:
        daily_pnl = None
        daily_rate = None
    estimated = quality == "ESTIMATED"
    rate_text = (
        f"组合{'预估' if estimated else ''}{_signed(daily_rate)}%"
        if daily_rate is not None
        else "组合涨跌幅暂不可用"
    )
    pnl_text = (
        f"{'预估' if estimated else ''}盈亏{_signed(daily_pnl)}元"
        if daily_pnl is not None
        else "盈亏金额暂不可用"
    )
    return f"{rate_text}，{pnl_text}", quality


def _contribution_data(context: dict) -> tuple[list[dict], bool, int, int]:
    raw = (context.get("postMarket") or {}).get("contributions") or {}
    if isinstance(raw, dict):
        candidates = raw.get("items") or []
        complete = raw.get("complete") is True
        position_count = int(raw.get("positionCount") or 0)
        attributed_count = int(raw.get("attributedCount") or 0)
    else:
        # Compatibility for an already persisted pre-upgrade source_context.
        candidates = raw if isinstance(raw, list) else []
        complete = bool(candidates)
        position_count = attributed_count = len(candidates)
    rows = [
        row
        for row in candidates
        if isinstance(row, dict) and _decimal(row.get("contributionAmount")) is not None
    ]
    return rows, complete, position_count, attributed_count


def _contributions(context: dict) -> str:
    rows, complete, position_count, attributed_count = _contribution_data(context)
    positive = sorted(
        (row for row in rows if _decimal(row.get("contributionAmount")) > 0),
        key=lambda row: _decimal(row.get("contributionAmount")),
        reverse=True,
    )
    negative = sorted(
        (row for row in rows if _decimal(row.get("contributionAmount")) < 0),
        key=lambda row: _decimal(row.get("contributionAmount")),
    )

    # Show at most three instruments in total.  Preserve one item from each
    # sign when both exist, then fill the remaining slot by absolute impact.
    selected: list[dict] = []
    if positive and negative:
        selected.extend((positive[0], negative[0]))
        remaining = sorted(
            positive[1:] + negative[1:],
            key=lambda row: abs(_decimal(row.get("contributionAmount"))),
            reverse=True,
        )
        selected.extend(remaining[:1])
    else:
        selected = sorted(
            rows,
            key=lambda row: abs(_decimal(row.get("contributionAmount"))),
            reverse=True,
        )[:3]
    positive = [row for row in selected if _decimal(row.get("contributionAmount")) > 0]
    negative = [row for row in selected if _decimal(row.get("contributionAmount")) < 0]

    def label(row: dict) -> str:
        name = str(row.get("name") or row.get("symbol") or "标的")
        if str(row.get("quality") or "").upper() == "ESTIMATED":
            name += "(估)"
        amount = _decimal(row.get("contributionAmount"))
        return f"{name}{_signed(amount)}元"

    incomplete = "暂不可用（归因不完整）"
    missing_positive = incomplete if not complete else "暂无正贡献"
    missing_negative = incomplete if not complete else "暂无负贡献"
    positive_text = "、".join(label(row) for row in positive) or missing_positive
    negative_text = "、".join(label(row) for row in negative) or missing_negative
    missing_count = max(position_count - attributed_count, 0)
    coverage_note = (
        f"；另有{missing_count}只持仓暂不可归因"
        if not complete and missing_count
        else ""
    )
    return f"正贡献：{positive_text}；负贡献：{negative_text}{coverage_note}"


def _market_signal(context: dict) -> str:
    sentiment = context.get("marketSentiment") or {}
    performance = (context.get("postMarket") or {}).get("performance") or {}
    report_date = str(performance.get("asOf") or "")
    sentiment_date = str(sentiment.get("tradeDate") or "")
    if (
        str(sentiment.get("qualityStatus") or "").upper() != "FRESH"
        or not report_date
        or sentiment_date != report_date
    ):
        return (
            f"市场信号截至{sentiment_date}（非今日）"
            if sentiment_date
            else "市场信号暂不可用"
        )
    up = _decimal(sentiment.get("totalUp"))
    down = _decimal(sentiment.get("totalDown"))
    if up is None or down is None:
        return "市场信号暂不可用"
    if up > down * Decimal("1.2"):
        return "市场广度偏强"
    if down > up * Decimal("1.2"):
        return "市场广度偏弱"
    return "市场广度均衡"


def _tomorrow_signals(context: dict) -> str:
    positions = [
        row
        for row in (context.get("positions") or [])
        if isinstance(row, dict) and (_decimal(row.get("quantity")) or Decimal("0")) > 0
    ]
    signals = [_market_signal(context)]
    stale = [
        row
        for row in positions
        if str(row.get("quality") or "").upper() in {"STALE", "MISSING"}
    ]
    if stale:
        symbols = "、".join(
            str(row.get("symbol") or row.get("name") or "标的") for row in stale[:2]
        )
        signals.append(f"{symbols}估值过期")
    limit = _decimal((context.get("policy") or {}).get("single_position_limit"))
    if limit is not None:
        overweight = sorted(
            (
                row
                for row in positions
                if (_decimal(row.get("weight")) or Decimal("0")) > limit
            ),
            key=lambda row: _decimal(row.get("weight")) or Decimal("0"),
            reverse=True,
        )
        if overweight:
            row = overweight[0]
            symbol = str(row.get("symbol") or row.get("name") or "标的")
            weight = _decimal(row.get("weight"))
            signals.append(f"{symbol}仓位{weight:.2f}%超{limit:.2f}%")
    return "；".join(signals[:3])


def _tomorrow_quality(context: dict) -> str:
    sentiment = context.get("marketSentiment") or {}
    performance = (context.get("postMarket") or {}).get("performance") or {}
    if str(sentiment.get("qualityStatus") or "").upper() != "FRESH" or str(
        sentiment.get("tradeDate") or ""
    ) != str(performance.get("asOf") or ""):
        return "UNAVAILABLE"
    position_qualities = {
        str(row.get("quality") or "UNAVAILABLE").upper()
        for row in (context.get("positions") or [])
        if isinstance(row, dict)
    }
    if "MISSING" in position_qualities or "UNAVAILABLE" in position_qualities:
        return "UNAVAILABLE"
    if "STALE" in position_qualities:
        return "STALE"
    if "ESTIMATED" in position_qualities:
        return "ESTIMATED"
    return "FRESH"


def _worst_quality(*qualities: str) -> str:
    priority = {"FRESH": 0, "ESTIMATED": 1, "STALE": 2, "UNAVAILABLE": 3}
    return max(
        (quality if quality in priority else "UNAVAILABLE" for quality in qualities),
        key=priority.__getitem__,
    )


def format_push_content(context: dict) -> str:
    """Answer result, attribution and next-day signals in at most 300 chars."""
    lines = [
        f"1. 今日结果：{_performance(context)[0]}",
        f"2. 盈亏原因：{_contributions(context)}",
        f"3. 明日关注：{_tomorrow_signals(context)}",
    ]
    content = "\n".join(lines)
    if len(content) <= _MAX_PUSH_CHARS:
        return content
    return content[: _MAX_PUSH_CHARS - 1].rstrip("；，。") + "…"


def build_report(context: dict) -> dict:
    """Build the audit-page report without model I/O or inferred market facts."""
    result_text, quality = _performance(context)
    cause_text = _contributions(context)
    signal_text = _tomorrow_signals(context)
    contribution_rows, contribution_complete, _position_count, _attributed = (
        _contribution_data(context)
    )
    contribution_quality = "UNAVAILABLE"
    if contribution_complete and contribution_rows:
        qualities = {
            str(row.get("quality") or "UNAVAILABLE").upper()
            for row in contribution_rows
        }
        contribution_quality = (
            "STALE"
            if "STALE" in qualities
            else "ESTIMATED"
            if "ESTIMATED" in qualities
            else "FRESH"
        )
    tomorrow_quality = _tomorrow_quality(context)
    sections = (
        ("今日结果", result_text, "FACT", quality),
        (
            "盈亏原因",
            cause_text,
            "FACT",
            contribution_quality,
        ),
        ("明日关注", signal_text, "ACTION_PLAN", tomorrow_quality),
    )
    overall_quality = _worst_quality(quality, contribution_quality, tomorrow_quality)
    return {
        "summary": "\n".join(
            f"{index}. {title}：{body}"
            for index, (title, body, _item_type, _quality) in enumerate(
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
        "quality": overall_quality,
        "status": "PUBLISHED" if overall_quality != "UNAVAILABLE" else "PARTIAL",
        "policy_check_status": (
            "WARNING"
            if overall_quality != "FRESH"
            or "过期" in signal_text
            or "超" in signal_text
            else "PASSED"
        ),
    }
