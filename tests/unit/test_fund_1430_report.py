"""Deterministic FUND_1430 report and push contracts (real-data driven)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from backend.reports import service as report_service


def _generator():
    module = getattr(report_service, "fund_1430", None)
    assert module is not None, "FUND_1430 deterministic generator is not implemented"
    return module


# Fund A is widely held with healthy data; Fund B carries an old report_period
# (staleness) and a limited-coverage snapshot to exercise all the data shapes.
FUND_A_ID = "11111111-1111-1111-1111-111111111111"
FUND_B_ID = "22222222-2222-2222-2222-222222222222"
HOLD_1 = "30000000-0000-0000-0000-000000000001"
HOLD_2 = "30000000-0000-0000-0000-000000000002"
HOLD_3 = "30000000-0000-0000-0000-000000000003"
SYM_1 = "600519"
SYM_2 = "000858"
SYM_3 = "000333"


def _quote(symbol: str, return_rate: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        return_rate=return_rate,
    )


def _context(
    *,
    include_quotes: bool = True,
    fund_a_profile: dict | None = None,
    fund_b_profile: dict | None = None,
    fund_a_cutoff: dict | None = None,
    fund_b_cutoff: dict | None = None,
) -> dict:
    lookthrough = [
        {
            "fundInstrumentId": FUND_A_ID,
            "fundSymbol": "110011",
            "fundName": "易方达消费中",
            "holdingInstrumentId": HOLD_1,
            "holdingSymbol": SYM_1,
            "holdingName": "贵州茅台",
            "weightPct": "60.00",
            "reportPeriod": "2026-08-31",
            "reportPeriodDate": date(2026, 8, 31),
            "asOf": datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc),
        },
        {
            "fundInstrumentId": FUND_A_ID,
            "fundSymbol": "110011",
            "fundName": "易方达消费中",
            "holdingInstrumentId": HOLD_2,
            "holdingSymbol": SYM_2,
            "holdingName": "五粮液",
            "weightPct": "30.00",
            "reportPeriod": "2026-08-31",
            "reportPeriodDate": date(2026, 8, 31),
            "asOf": datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc),
        },
        {
            "fundInstrumentId": FUND_B_ID,
            "fundSymbol": "161725",
            "fundName": "招商中证白酒",
            "holdingInstrumentId": HOLD_3,
            "holdingSymbol": SYM_3,
            "holdingName": "美的集团",
            "weightPct": "40.00",
            # Stale: > 90 days from today (2026-09-03).
            "reportPeriod": "2026-05-01",
            "reportPeriodDate": date(2026, 5, 1),
            "asOf": datetime(2026, 5, 5, 14, 30, tzinfo=timezone.utc),
        },
    ]
    profiles: list[dict] = []
    if fund_a_profile is not None:
        profiles.append({"instrumentId": FUND_A_ID, **fund_a_profile})
    if fund_b_profile is not None:
        profiles.append({"instrumentId": FUND_B_ID, **fund_b_profile})
    cutoff_rules: list[dict] = []
    if fund_a_cutoff is not None:
        cutoff_rules.append({"instrumentId": FUND_A_ID, **fund_a_cutoff})
    if fund_b_cutoff is not None:
        cutoff_rules.append({"instrumentId": FUND_B_ID, **fund_b_cutoff})
    quotes: dict[str, SimpleNamespace] = {}
    if include_quotes:
        quotes[SYM_1] = _quote(SYM_1, "1.50")  # +1.5%
        quotes[SYM_2] = _quote(SYM_2, "0.80")  # +0.8%
        quotes[SYM_3] = _quote(SYM_3, "-0.30")  # -0.3%
    return {
        "session": "PRE_MARKET",
        "summary": {},
        "policy": None,
        "fundEstimates": [],
        "watchlist": [],
        "marketSentiment": {},
        "fund1430": {
            "fundLookthrough": lookthrough,
            "fundProfiles": profiles,
            "fundCutoffRules": cutoff_rules,
            "holdingQuoteBySymbol": quotes,
        },
    }


def test_report_renders_four_sections_with_real_data() -> None:
    """全量渲染：覆盖加权估值、覆盖率分级、申赎正常、综合判断（轻度偏乐观）。"""
    draft = _generator().build_report(_context())

    assert [item["title"] for item in draft["items"][:4]] == [
        "今日估值概览",
        "估值可信度",
        "申赎提醒",
        "总结",
    ]
    overview = next(item for item in draft["items"] if item["title"] == "今日估值概览")
    confidence = next(item for item in draft["items"] if item["title"] == "估值可信度")
    reminder = next(item for item in draft["items"] if item["title"] == "申赎提醒")
    summary = next(item for item in draft["items"] if item["title"] == "总结")

    # 基金 A 加权：60%*1.5 + 30%*0.8 / (60+30) = 1.30  → ≈+1.30% (data-quality FRESH/ESTIMATED)
    assert "易方达消费中(110011)" in overview["body"]
    assert "今日预估" in overview["body"]
    assert "基于 2 只持仓加权" in overview["body"]

    # 可信度：基金 B 超 90 天 → STALE 提示；基金 A 健康
    assert "参考价值 较高" in confidence["body"]
    assert "招商中证白酒" in confidence["body"]
    assert "持仓数据已超过 3 个月" in confidence["body"]

    # 申赎提醒：无 profile 行 → "申赎状态未确认"，绝不臆造。
    assert "申赎状态未确认" in reminder["body"]

    # 总结：估值 > 0.5% → "今日估值偏乐观，可正常申赎"。
    assert "今日估值偏乐观" in summary["body"]
    assert "可正常申赎" in summary["body"]


def test_overview_omits_funds_with_no_live_quotes() -> None:
    """行情缺失的基金不进入估值节（避免估值偏差），但仍出现在可信度。"""
    # 提供部分 quotes：基金 A 两只 holdings 有行情、基金 B 无行情。
    context = _context()
    context["fund1430"]["holdingQuoteBySymbol"] = {
        SYM_1: _quote(SYM_1, "1.50"),
        SYM_2: _quote(SYM_2, "0.80"),
        # SYM_3 intentionally absent.
    }

    draft = _generator().build_report(context)
    overview = next(item for item in draft["items"] if item["title"] == "今日估值概览")
    confidence = next(item for item in draft["items"] if item["title"] == "估值可信度")
    # 估值节只展示基金 A。
    assert "易方达消费中(110011)" in overview["body"]
    assert "招商中证白酒" not in overview["body"]
    # 可信度节保留两基金（含基金 B 不展示变化率，但展示其持仓数据日期与参考价值）。
    assert "易方达消费中" in confidence["body"]
    assert "招商中证白酒" in confidence["body"]


def test_reminder_lists_open_normal_and_suspended_statuses() -> None:
    """申赎提醒：OPEN/OPEN → 正常 + 15:00；SUSPENDED 申购/赎回 → 分别说明限制。"""
    context = _context(
        fund_a_profile={
            "purchaseStatus": "OPEN",
            "redemptionStatus": "OPEN",
            "effectiveFrom": "2026-01-01",
        },
        fund_b_profile={
            "purchaseStatus": "SUSPENDED",
            "redemptionStatus": "OPEN",
            "effectiveFrom": "2026-01-01",
        },
        fund_a_cutoff={
            "symbol": "110011",
            "name": "易方达消费中",
            "salesChannel": "DEFAULT",
            "cutoffTime": "15:00:00",
            "timezone": "Asia/Shanghai",
        },
    )
    draft = _generator().build_report(context)
    reminder = next(item for item in draft["items"] if item["title"] == "申赎提醒")
    assert "易方达消费中(110011)" in reminder["body"]
    assert "今日正常申购赎回" in reminder["body"]
    assert "15:00 前提交按今日净值成交" in reminder["body"]
    assert "招商中证白酒(161725)" in reminder["body"]
    assert "今日暂停申购" in reminder["body"]


def test_summary_paused_or_weak_strong_with_threshold() -> None:
    """总结：估值 ≤ -0.5% → "偏弱"；估值 ≥ 0.5% → "偏乐观"；±0.5 内 → "波动较小"。"""
    # Case 1: 弱（基金 A 全部跌 1.5%）。
    ctx_weak = _context()
    ctx_weak["fund1430"]["holdingQuoteBySymbol"] = {
        SYM_1: _quote(SYM_1, "-1.50"),
        SYM_2: _quote(SYM_2, "-1.50"),
        SYM_3: _quote(SYM_3, "-1.50"),
    }
    weak = _generator().build_report(ctx_weak)
    weak_summary = next(item for item in weak["items"] if item["title"] == "总结")
    assert "今日估值偏弱" in weak_summary["body"]
    assert "可观望" in weak_summary["body"]

    # Case 2: 区间内（≈0%）。
    ctx_flat = _context()
    ctx_flat["fund1430"]["holdingQuoteBySymbol"] = {
        SYM_1: _quote(SYM_1, "0.10"),
        SYM_2: _quote(SYM_2, "-0.10"),
        SYM_3: _quote(SYM_3, "0.00"),
    }
    flat = _generator().build_report(ctx_flat)
    flat_summary = next(item for item in flat["items"] if item["title"] == "总结")
    assert "今日估值波动较小" in flat_summary["body"]
    assert "按原计划执行" in flat_summary["body"]


def test_summary_flags_reports_when_subscription_is_suspended() -> None:
    """申赎受限优先于估值阈值判断。"""
    context = _context(
        fund_a_profile={
            "purchaseStatus": "SUSPENDED",
            "redemptionStatus": "SUSPENDED",
            "effectiveFrom": "2026-01-01",
        },
        # 给基金 A 很强行情也不应触发乐观结论。
        fund_b_profile={
            "purchaseStatus": "OPEN",
            "redemptionStatus": "OPEN",
            "effectiveFrom": "2026-01-01",
        },
    )
    draft = _generator().build_report(context)
    summary = next(item for item in draft["items"] if item["title"] == "总结")
    assert "今日申赎受限" in summary["body"]
    assert "易方达消费中" in summary["body"]
    assert "请留意具体规则" in summary["body"]


def test_report_with_no_data_renders_missing_placeholders() -> None:
    """完全无数据：四节 body 均为「暂无」，整篇 quality 仍正常生成（推送仍发但内容标记）。"""
    context = _context(include_quotes=False)
    context["fund1430"]["fundLookthrough"] = []
    context["fund1430"]["fundCutoffRules"] = []
    context["fund1430"]["fundProfiles"] = []
    draft = _generator().build_report(context)
    for title in ("今日估值概览", "估值可信度", "申赎提醒"):
        item = next(section for section in draft["items"] if section["title"] == title)
        assert item["body"] == "暂无"
        assert item["quality"] == "UNAVAILABLE"


def test_push_keeps_four_lines_under_200_characters_with_real_data() -> None:
    push = _generator().format_push_content(_context())
    lines = push.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("估值：")
    assert lines[3].startswith("总结：")
    # 加权估值结果符号（两 holdings 60%×1.5 + 30%×0.8 ≈ +1.27%）
    assert "1.27%" in push
    # 总结方向（估值 > +0.5% → 偏乐观）。
    assert "偏乐观" in push
    assert len(push) <= 200
