"""Deterministic FUND_1430 report and push contracts."""

from __future__ import annotations

from backend.reports import service as report_service


def _generator():
    module = getattr(report_service, "fund_1430", None)
    assert module is not None, "FUND_1430 deterministic generator is not implemented"
    return module


def _context() -> dict:
    return {
        "positions": [
            {
                "instrumentId": "11111111-1111-1111-1111-111111111111",
                "symbol": "110011",
                "name": "易方达中小盘",
                "type": "场外基金",
                "quantity": "1000",
            },
            {
                "instrumentId": "22222222-2222-2222-2222-222222222222",
                "symbol": "161725",
                "name": "招商中证白酒",
                "type": "场外基金",
                "quantity": "800",
            },
        ],
        "fundEstimates": [
            {
                "instrumentId": "11111111-1111-1111-1111-111111111111",
                "symbol": "110011",
                "name": "易方达中小盘",
                "value": "1.0410",
                "lowerValue": "1.0380",
                "upperValue": "1.0450",
                "quality": "ESTIMATED",
                "asOf": "2026-09-01T14:30:00+08:00",
                "snapshotId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "metadata": {
                    "coveragePct": "72.50",
                    "disclosedOn": "2026-06-30",
                    "confirmedNav": "1.0000",
                    "confirmedNavAsOf": "2026-08-31",
                },
            },
            {
                "instrumentId": "22222222-2222-2222-2222-222222222222",
                "symbol": "161725",
                "name": "招商中证白酒",
                "value": "0.9600",
                "lowerValue": "0.9550",
                "upperValue": "0.9660",
                "quality": "ESTIMATED",
                "asOf": "2026-09-01T14:29:00+08:00",
                "snapshotId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "metadata": {
                    "coveragePct": "68.00",
                    "disclosedOn": "2026-06-30",
                    "confirmedNav": "1.0000",
                    "confirmedNavAsOf": "2026-08-31",
                },
            },
        ],
        "fund1430": {
            "fundCutoffRules": [
                {
                    "instrumentId": "11111111-1111-1111-1111-111111111111",
                    "symbol": "110011",
                    "name": "易方达中小盘",
                    "salesChannel": "DEFAULT",
                    "cutoffTime": "15:00:00",
                    "timezone": "Asia/Shanghai",
                },
                {
                    "instrumentId": "22222222-2222-2222-2222-222222222222",
                    "symbol": "161725",
                    "name": "招商中证白酒",
                    "salesChannel": "DEFAULT",
                    "cutoffTime": "15:00:00",
                    "timezone": "Asia/Shanghai",
                },
            ]
        },
    }


def test_full_report_has_four_sections_sources_and_estimate_evidence() -> None:
    draft = _generator().build_report(_context())

    assert [item["title"] for item in draft["items"][:4]] == [
        "今日估值概览",
        "估值可信度",
        "申赎提醒",
        "总结",
    ]
    assert (
        draft["summary"]
        .splitlines()[0]
        .startswith(
            "1. 今日估值概览：易方达中小盘(110011)预估+4.10%，区间+3.80%～+4.50%"
        )
    )
    assert "覆盖率72.50%" in draft["summary"]
    assert "最近正式净值截至2026-08-31" in draft["summary"]
    assert "状态：暂无" in draft["summary"]
    assert "实际能否申赎以销售渠道为准" in draft["summary"]
    assert "超过+3%" in draft["summary"]
    assert "低于-3%" in draft["summary"]
    assert "【基于估值系统】" in draft["summary"]
    assert "【基于规则引擎】" in draft["summary"]
    evidence_items = [
        item for item in draft["items"] if item["item_type"] == "FUND_ESTIMATE"
    ]
    assert len(evidence_items) == 2
    assert evidence_items[0]["_evidence"]["source_snapshot_id"] == (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )


def test_exact_three_percent_does_not_trigger_action_threshold() -> None:
    context = _context()
    context["fundEstimates"] = [
        {
            **context["fundEstimates"][0],
            "value": "1.0300",
            "lowerValue": "1.0200",
            "upperValue": "1.0400",
        },
        {
            **context["fundEstimates"][1],
            "value": "0.9700",
            "lowerValue": "0.9600",
            "upperValue": "0.9800",
        },
    ]

    draft = _generator().build_report(context)
    action = next(item for item in draft["items"] if item["title"] == "总结")

    # 未触发 ±3% 阈值时，总结节应描述整体温和并给出按原计划操作的建议。
    assert "未触发±3%阈值" in action["body"]
    assert "可正常申赎" in action["body"]
    assert "按原计划操作" in action["body"]
    assert "避免追涨" not in action["body"]


def test_confidence_renders_disclosure_date_and_coverage_value_label() -> None:
    """估值可信度须展示持仓披露日期与参考价值分级（覆盖率 60%~85% → 中等）。"""
    draft = _generator().build_report(_context())
    confidence = next(item for item in draft["items"] if item["title"] == "估值可信度")

    assert "覆盖率72.50%" in confidence["body"]
    assert "基于2026-06-30披露持仓" in confidence["body"]
    assert "参考价值中等" in confidence["body"]
    assert "最近正式净值截至2026-08-31" in confidence["body"]


def test_full_report_limits_details_but_discloses_additional_funds() -> None:
    context = _context()
    third_estimate = {
        **context["fundEstimates"][0],
        "instrumentId": "33333333-3333-3333-3333-333333333333",
        "symbol": "003095",
        "name": "中欧医疗健康",
        "value": "1.0500",
        "snapshotId": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    }
    third_rule = {
        **context["fund1430"]["fundCutoffRules"][0],
        "instrumentId": "33333333-3333-3333-3333-333333333333",
        "symbol": "003095",
        "name": "中欧医疗健康",
    }
    third_bank_rule = {**third_rule, "salesChannel": "BANK"}
    context["fundEstimates"].append(third_estimate)
    context["fund1430"]["fundCutoffRules"].extend([third_rule, third_bank_rule])

    draft = _generator().build_report(context)
    reminder = next(item for item in draft["items"] if item["title"] == "申赎提醒")
    summary = next(item for item in draft["items"] if item["title"] == "总结")

    assert "另有1只基金的截止规则，请在报告明细中核对" in reminder["body"]
    assert "另有2只基金" not in reminder["body"]
    # 总结节中触发基金数量应被整体反映（2 只触发 + 1 只未触发 = 触发 2 只）。
    assert "2只基金预估涨幅超过+3%" in summary["body"]
    cutoff_details = [
        item for item in draft["items"] if item["title"].endswith("申赎截止明细")
    ]
    assert len(cutoff_details) == 4
    third_details = [item for item in cutoff_details if "003095" in item["title"]]
    assert len(third_details) == 2
    assert all("能否申赎以销售渠道为准" in item["body"] for item in third_details)


def test_missing_data_is_explicit_and_never_fabricated() -> None:
    context = {"positions": [], "fundEstimates": [], "fund1430": {}}

    draft = _generator().build_report(context)
    push = _generator().format_push_content(context)
    rendered = f"{draft['summary']}\n{push}"

    assert draft["summary"].count("暂无") >= 5
    assert "None" not in rendered
    assert "null" not in rendered
    assert "--" not in rendered
    assert "美股" not in rendered
    assert "隔夜" not in rendered
    assert "明日" not in rendered
    assert len(push) <= 200


def test_push_has_four_compact_sourced_lines_under_200_characters() -> None:
    push = _generator().format_push_content(_context())

    lines = push.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("估值：") and lines[0].endswith("【基于估值系统】")
    assert lines[1].startswith("可信：") and lines[1].endswith("【基于估值系统】")
    assert lines[2].startswith("申赎：") and lines[2].endswith("【基于规则引擎】")
    assert lines[3].startswith("总结：") and lines[3].endswith("【基于规则引擎】")
    assert "+4.10%" in push
    assert "72.50%" in push
    assert "15:00" in push
    assert "能否申赎以渠道为准" in push
    assert len(push) <= 200


def test_summary_reports_split_count_when_mixed_thresholds_triggered() -> None:
    """估值方向同时出现正/负触发时，总结节应给出正/负各几只的具体计数。"""
    context = _context()
    # 第二个 fund 保持 -4% 触发负阈值，添加第三只 +5% 触发正阈值 → 分化。
    context["fundEstimates"].append(
        {
            **context["fundEstimates"][0],
            "instrumentId": "33333333-3333-3333-3333-333333333333",
            "symbol": "003095",
            "name": "中欧医疗健康",
            "value": "1.0500",
            "lowerValue": "1.0300",
            "upperValue": "1.0700",
            "snapshotId": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        }
    )
    draft = _generator().build_report(context)
    summary = next(item for item in draft["items"] if item["title"] == "总结")

    assert "估值分化" in summary["body"]
    # 110011 +4.10%、003095 +5.00% 触发 +3%；161725 -4.00% 触发 -3%。
    assert "2只基金预估涨幅超过+3%" in summary["body"]
    assert "1只基金预估跌幅低于-3%" in summary["body"]
    assert "按阈值控制" in summary["body"]


def test_summary_reports_no_fabrication_when_metrics_and_rules_empty() -> None:
    context = {"positions": [], "fundEstimates": [], "fund1430": {}}
    draft = _generator().build_report(context)
    summary = next(item for item in draft["items"] if item["title"] == "总结")
    assert "估值与申赎均暂无数据" in summary["body"]
    assert "不臆造" not in summary["body"]  # sanity: 避免错把"不臆造"写成内容
