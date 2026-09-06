"""Deterministic FUND_1430 report contracts for persisted real-data estimates."""

from __future__ import annotations

from types import SimpleNamespace

from backend.reports import service as report_service


FUND_A_ID = "11111111-1111-1111-1111-111111111111"
FUND_B_ID = "22222222-2222-2222-2222-222222222222"


def _generator():
    module = getattr(report_service, "fund_1430", None)
    assert module is not None, "FUND_1430 deterministic generator is not implemented"
    return module


def _context() -> dict:
    return {
        "positions": [
            {
                "instrumentId": FUND_A_ID,
                "symbol": "110011",
                "name": "易方达消费中",
                "type": "场外基金",
                "quantity": "1000",
            },
            {
                "instrumentId": FUND_B_ID,
                "symbol": "161725",
                "name": "招商中证白酒",
                "type": "场外基金",
                "quantity": "800",
            },
        ],
        # These are Worker-persisted ESTIMATED_NAV observations. Their metadata
        # comes from the real disclosure and quote snapshot used by the estimator.
        "fundEstimates": [
            {
                "instrumentId": FUND_A_ID,
                "symbol": "110011",
                "name": "易方达消费中",
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
                    "confirmedNavAsOf": "2026-08-31T00:00:00+08:00",
                },
            },
            {
                "instrumentId": FUND_B_ID,
                "symbol": "161725",
                "name": "招商中证白酒",
                "value": "0.9600",
                "lowerValue": "0.9550",
                "upperValue": "0.9660",
                "quality": "STALE",
                "asOf": "2026-09-01T14:29:00+08:00",
                "snapshotId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "metadata": {
                    "coveragePct": "68.00",
                    "disclosedOn": "2026-03-31",
                    "confirmedNav": "1.0000",
                    "confirmedNavAsOf": "2026-08-31T00:00:00+08:00",
                },
            },
        ],
        "fund1430": {
            "fundCutoffRules": [
                {
                    "instrumentId": FUND_A_ID,
                    "symbol": "110011",
                    "name": "易方达消费中",
                    "salesChannel": "DEFAULT",
                    "cutoffTime": "14:45:00",
                    "timezone": "Asia/Shanghai",
                }
            ],
            "fundProfiles": [],
        },
    }


def test_external_provider_estimate_explains_source_without_fake_coverage() -> None:
    generator = _generator()
    context = _context()
    context["fundEstimates"] = [
        {
            "instrumentId": FUND_A_ID,
            "symbol": "110011",
            "name": "易方达消费中",
            "value": "1.0200",
            "lowerValue": None,
            "upperValue": None,
            "quality": "ESTIMATED",
            "asOf": "2026-09-07T14:30:00+08:00",
            "snapshotId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "methodVersion": "sina/fund-estimate/v1",
            "metadata": {
                "estimateSourceKind": "EXTERNAL_PROVIDER",
                "estimateSource": "Sina-Fund-Estimate",
                "confirmedNav": "1.0000",
                "confirmedNavAsOf": "2026-09-04T00:00:00+00:00",
            },
        }
    ]

    report = generator.build_report(context)

    confidence = next(
        item["body"] for item in report["items"] if item["title"] == "估值可信度"
    )
    assert "Sina-Fund-Estimate外部盘中估值" in confidence
    assert "不提供持仓覆盖率" in confidence
    assert "基于暂无披露持仓" not in confidence
    assert "今日估值整体温和（未触发±3%阈值）" in report["summary"]
    assert "来源不提供区间" in report["summary"]
    assert "区间：暂无" not in report["summary"]


def test_non_trading_day_report_labels_latest_trading_day_as_historical() -> None:
    generator = _generator()
    context = _context()
    estimate = context["fundEstimates"][0]
    estimate["asOf"] = "2026-09-04T16:04:00+08:00"
    estimate["quality"] = "ESTIMATED"
    estimate["metadata"].update(
        {
            "valuationDate": "2026-09-04",
            "latestTradingDate": "2026-09-04",
            "valuationDateKind": "LATEST_TRADING_DAY",
            "nonTradingDayReference": True,
        }
    )
    context["fundEstimates"] = [estimate]

    report = generator.build_report(context)
    push = generator.format_push_content(context)

    assert report["items"][0]["title"] == "最近交易日估值概览"
    assert "2026-09-04" in report["summary"]
    assert "非交易日历史参考" in report["summary"]
    assert "今日预估" not in report["summary"]
    assert "09-04" in push


def test_report_uses_persisted_estimates_and_preserves_evidence() -> None:
    draft = _generator().build_report(_context())

    assert [item["title"] for item in draft["items"][:4]] == [
        "今日估值概览",
        "估值可信度",
        "申赎提醒",
        "总结",
    ]
    assert "易方达消费中(110011)预估+4.10%" in draft["summary"]
    assert "区间+3.80%～+4.50%" in draft["summary"]
    assert "覆盖率72.50%" in draft["summary"]
    assert "基于2026-06-30披露持仓" in draft["summary"]
    assert "14:45" in draft["summary"]
    assert "15:00" not in draft["summary"]
    assert "正常申赎" not in draft["summary"]
    evidence = [item for item in draft["items"] if item["item_type"] == "FUND_ESTIMATE"]
    assert len(evidence) == 2
    assert evidence[0]["_evidence"]["source_snapshot_id"] == (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )


def test_report_ignores_legacy_approximate_lookthrough_seed() -> None:
    context = _context()
    context["fund1430"].update(
        {
            "fundLookthrough": [
                {
                    "fundInstrumentId": FUND_A_ID,
                    "fundSymbol": "110011",
                    "fundName": "易方达消费中",
                    "holdingSymbol": "600519",
                    "weightPct": "100",
                    "reportPeriod": "2026-06-30",
                }
            ],
            "holdingQuoteBySymbol": {
                "600519": SimpleNamespace(symbol="600519", return_rate="99")
            },
        }
    )

    draft = _generator().build_report(context)

    assert "预估+4.10%" in draft["summary"]
    assert "99.00%" not in draft["summary"]


def test_quality_reflects_persisted_estimate_quality() -> None:
    draft = _generator().build_report(_context())
    confidence = next(item for item in draft["items"] if item["title"] == "估值可信度")

    assert "质量ESTIMATED" in confidence["body"]
    assert "质量STALE" in confidence["body"]
    assert confidence["quality"] == "STALE"


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

    assert "未触发±3%阈值" in action["body"]
    assert "按原计划操作" in action["body"]


def test_missing_data_is_explicit_and_never_fabricated() -> None:
    context = {"positions": [], "fundEstimates": [], "fund1430": {}}

    draft = _generator().build_report(context)
    push = _generator().format_push_content(context)
    rendered = f"{draft['summary']}\n{push}"

    assert draft["summary"].count("暂无") >= 5
    assert "None" not in rendered
    assert "null" not in rendered
    assert "--" not in rendered
    assert len(push) <= 200


def test_push_uses_persisted_estimate_and_remains_compact() -> None:
    push = _generator().format_push_content(_context())

    lines = push.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("估值：") and lines[0].endswith("【基于估值系统】")
    assert lines[1].startswith("可信：") and lines[1].endswith("【基于估值系统】")
    assert "+4.10%" in push
    assert "72.50%" in push
    assert "14:45" in push
    assert "15:00" not in push
    assert "正常申赎" not in push
    assert len(push) <= 200
