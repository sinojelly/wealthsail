"""Quotes must support the stated financial account and current industry risk."""

from copy import deepcopy
from datetime import timedelta
import re

import pytest

from backend.macro import industry_cycle as industry
from test_daily_research import NOW
from test_industry_cycle import cycle


def source(
    quote, *, checked=True, day="2026-10-15", host="original.test", title="行业经营数据"
):
    return {
        "url": f"https://{host}/article",
        "quote": quote,
        "title": title,
        "publishedDate": day,
        "verification": "SOURCE_CHECKED" if checked else "UNVERIFIED",
    }


def normalize(raw, *proofs):
    return industry.normalize_industry_cycle(
        raw,
        source_aliases={f"s{i}": f"source:{i}" for i in range(len(proofs))},
        sources={f"source:{i}": row for i, row in enumerate(proofs)},
        now=NOW,
    )


def earnings(quote):
    quote = "。".join(
        "甲公司2026年第三季度" + part
        for part in re.split(r"。|(?<=\.)\s+", quote)
        if part
    )
    return normalize(
        cycle(
            stage="EARNINGS_DELIVERY",
            sourceIds=["s0"],
            evidence=[
                {
                    "kind": "PROFIT_GROWTH",
                    "summary": "公司利润观察。",
                    "sourceIds": ["s0"],
                    "companyName": "甲公司",
                    "reportPeriod": "2026Q3",
                },
                {
                    "kind": "CASH_COLLECTION",
                    "summary": "公司现金回款。",
                    "sourceIds": ["s0"],
                    "companyName": "甲公司",
                    "reportPeriod": "2026Q3",
                },
            ],
        ),
        source(quote),
    )


def risk(kind, *proofs):
    refs = [f"s{i}" for i in range(len(proofs))]
    raw = cycle(stage="UNKNOWN", sourceIds=refs, evidence=[])
    next(row for row in raw["riskChecks"] if row["kind"] == kind).update(
        status="FOUND", sourceIds=refs
    )
    return normalize(raw, *proofs)


@pytest.mark.parametrize(
    "quote",
    [
        "本季度公司净利润下降30%但营业收入增长5%。客户订单已实际回款2亿元。",
        "本季度公司净利润亏损3亿元且营业收入增长5%。客户订单已实际回款2亿元。",
        "本季度净利润为2亿元营业收入同比增长5%。客户订单已实际回款2亿元。",
        "本季度净利润维持不变总资产规模增长30%。客户订单已实际回款2亿元。",
        "Net profit fell 30% but revenue grew 5%. Cash collected from customers reached CNY 2 billion.",
        "本季度净利润增长-30%。客户订单已实际回款2亿元。",
        "公司澄清净利润增长的说法不属实。客户订单已实际回款2亿元。",
        "公司否认净利润增长50%的传闻。客户订单已实际回款2亿元。",
        "本季度净利润并非增长而是营业收入增长5%。客户订单已实际回款2亿元。",
        "Net profit keeps falling though growth was 5%. Cash collected from customers reached CNY 2 billion.",
        "公司称净利润增长50%，这一说法不属实。客户订单已实际回款2亿元。",
        "净利润增长50%的传闻，公司予以否认。客户订单已实际回款2亿元。",
    ],
)
def test_profit_decline_or_growth_of_a_different_account_cannot_become_green(quote):
    result = earnings(quote)
    assert result["earningsVerified"] is False
    assert result["stage"] == "UNKNOWN"


def test_explicit_profit_growth_still_accepts_a_separate_paid_cash_fact():
    assert (
        earnings("本季度归母净利润同比增长25%。客户订单已实际回款2亿元。")[
            "earningsVerified"
        ]
        is True
    )
    assert (
        earnings("本季度净利同比增长25%。客户订单已实际回款2亿元。")["earningsVerified"]
        is True
    )


@pytest.mark.parametrize(
    "kind,quote",
    [
        ("FINANCING_COOLING", "机器人行业一级市场融资笔数同比下降30%。"),
        ("LAYOFFS_CLOSURES", "机器人行业多家企业已出现欠薪并宣布裁员。"),
        ("ORDER_CUTS", "机器人行业下游客户已经砍单并取消采购订单。"),
        ("OVERCAPACITY_PRICE_WAR", "机器人行业产能过剩，行业企业已经展开价格战。"),
        ("PRODUCTION_DELAY", "机器人行业商业化量产已延期至明年。"),
    ],
)
def test_recent_checked_quote_supports_only_its_specific_negative_claim(kind, quote):
    result = risk(kind, source("近期" + quote))
    check = next(row for row in result["riskChecks"] if row["kind"] == kind)
    assert check["claimSupported"] is True
    assert check["strength"] == "CONFIRMED"
    assert result["riskLevel"] == "CONFIRMED"


@pytest.mark.parametrize(
    "kind,quote",
    [
        ("FINANCING_COOLING", "机器人行业融资仍在增长，但个别公司融资困难。"),
        ("FINANCING_COOLING", "机器人行业未出现融资降温。"),
        ("FINANCING_COOLING", "预计明年机器人行业融资笔数可能下降。"),
        ("FINANCING_COOLING", "机器人行业融资降温，相关传闻不实。"),
        ("FINANCING_COOLING", "机器人行业融资降温，该说法不属实。"),
        ("FINANCING_COOLING", "机器人行业融资降温，企业予以否认。"),
        ("LAYOFFS_CLOSURES", "企业否认欠薪和裁员，行业经营平稳。"),
        ("LAYOFFS_CLOSURES", "机器人行业欠薪裁员的说法不属实。"),
        ("ORDER_CUTS", "机器人行业订单持续增长，没有出现砍单。"),
        ("ORDER_CUTS", "机器人行业下游砍单消息不属实。"),
        ("OVERCAPACITY_PRICE_WAR", "该行业不存在产能过剩，也未出现价格战。"),
        ("OVERCAPACITY_PRICE_WAR", "机器人行业产能过剩与价格战消息不属实。"),
        ("PRODUCTION_DELAY", "机器人量产未推迟，客户验收按期完成。"),
        ("PRODUCTION_DELAY", "机器人行业量产延期的报道不属实。"),
    ],
)
def test_checked_quote_does_not_confirm_negated_forecast_or_historical_risk(
    kind, quote
):
    result = risk(kind, source(quote))
    check = next(row for row in result["riskChecks"] if row["kind"] == kind)
    assert check["claimSupported"] is False
    assert kind not in result["confirmedRiskFlags"]
    assert result["riskLevel"] != "CONFIRMED"


def test_unknown_publication_day_and_unrelated_second_host_cannot_confirm():
    missing_day = risk(
        "FINANCING_COOLING", source("行业融资笔数同比下降30%。", day=None)
    )
    assert missing_day["riskLevel"] != "CONFIRMED"
    assert missing_day["riskChecks"][0]["claimSupported"] is False
    assert missing_day["riskChecks"][0]["strength"] == "SUSPECTED"
    mixed = risk(
        "FINANCING_COOLING",
        source("行业融资笔数同比下降30%。", checked=False),
        source(
            "行业新产品发布，订单稳步增长。",
            checked=False,
            host="unrelated.test",
            title="新产品发布",
        ),
    )
    assert mixed["riskChecks"][0]["independentSourceCount"] == 1
    assert mixed["riskLevel"] == "SUSPECTED"
    undated = risk(
        "FINANCING_COOLING",
        source("行业融资笔数同比下降30%。"),
        source(
            "行业融资笔数同比下降30%。",
            day=None,
            host="undated.test",
            title="未标日期的融资情况",
        ),
    )
    assert undated["riskChecks"][0]["timeVerification"] == "UNVERIFIED"
    assert undated["riskChecks"][0]["claimSupported"] is False
    assert undated["riskChecks"][0]["strength"] == "SUSPECTED"


def test_checked_but_unrelated_quote_is_only_a_suspected_model_claim():
    actual = risk(
        "FINANCING_COOLING", source("机器人行业新产品发布，融资和订单均继续增长。")
    )
    assert actual["riskChecks"][0]["claimSupported"] is False
    assert actual["riskChecks"][0]["strength"] == "SUSPECTED"
    assert actual["suspectedRiskFlags"] == ["FINANCING_COOLING"]


def test_merging_earnings_clears_only_earnings_watch_metadata_and_does_not_resurrect_it():
    current = earnings("本季度净利润同比增长25%。客户订单已实际回款2亿元。")
    old = {
        "stage": "UNKNOWN",
        "riskLevel": "SUSPECTED",
        "riskFlags": ["PERSISTED_RISK_WATCH", "FORWARD_PLANS_ONLY"],
        "persistedRiskFlags": ["EARLY_COMMERCIALIZATION", "FINANCING_COOLING"],
        "confirmedRiskAt": {
            "EARLY_COMMERCIALIZATION": NOW.isoformat(),
            "FINANCING_COOLING": NOW.isoformat(),
        },
        "persistedEvidence": [
            {"flag": "EARLY_COMMERCIALIZATION"},
            {"flag": "FINANCING_COOLING"},
        ],
        "forwardPlansOnly": True,
        "evidenceGaps": ["CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING"],
    }
    result = industry.merge_industry_cycles(current, old)
    assert result["persistedRiskFlags"] == ["FINANCING_COOLING"]
    assert "FORWARD_PLANS_ONLY" not in result["riskFlags"]
    assert "EARLY_COMMERCIALIZATION" not in result["confirmedRiskAt"]
    assert (
        "个别企业当期盈利已核实，但近14日行业级风险观察尚未到期解除"
        in result["evidenceSummary"]
    )
    result = industry.expire_industry_risk(result, NOW + timedelta(days=15))
    assert result["leftDiscountScale"] == 0


def test_two_independent_unchecked_quotes_remain_suspected():
    result = risk(
        "FINANCING_COOLING",
        source("本季度行业融资笔数同比下降30%。", checked=False),
        source(
            "近期行业融资规模显著收缩。",
            checked=False,
            host="second.test",
            title="创投市场季度数据",
        ),
    )
    assert result["riskChecks"][0]["independentSourceCount"] == 2
    assert result["riskLevel"] == "SUSPECTED"
    assert result["riskChecks"][0]["strength"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5


def test_early_stage_has_half_protection_and_unsupported_stage_stays_unknown():
    raw = cycle(
        sourceIds=["s0"],
        evidence=[
            {
                "kind": "COMMERCIALIZATION_EARLY",
                "summary": "尚在试制。",
                "sourceIds": ["s0"],
            }
        ],
    )
    actual = normalize(raw, source("机器人行业仍处试制验证阶段，尚未实现规模量产。"))
    assert actual["stage"] == "EARLY_COMMERCIALIZATION"
    assert actual["riskLevel"] == "UNPROVEN"
    assert actual["leftDiscountScale"] == 0.5
    missing = normalize(raw, source("一家企业宣布拟扩产计划。", day=None))
    assert missing["stage"] == "UNKNOWN"
    assert missing["riskLevel"] != "CONFIRMED"


def test_early_stage_evidence_with_one_undated_reference_does_not_count_as_a_recent_row():
    raw = cycle(
        sourceIds=["s0", "s1"],
        evidence=[
            {
                "kind": "COMMERCIALIZATION_EARLY",
                "summary": "早期阶段待核验。",
                "sourceIds": ["s0", "s1"],
            }
        ],
    )
    actual = normalize(
        raw, source("仍处试制阶段。"), source("仍处试制阶段。", day=None)
    )
    assert actual["evidence"][0]["timeVerification"] == "UNVERIFIED"
    assert actual["stage"] == "UNKNOWN"


def test_stage_alone_changes_risk_intensity_without_inventing_an_evidence_flag():
    actual = industry.industry_risk_policy({"stage": "EARLY_COMMERCIALIZATION"})
    assert actual["riskLevel"] == "UNPROVEN"
    assert actual["leftDiscountScale"] == 0.5
    assert actual["riskFlags"] == []


def test_legacy_early_only_confirmation_is_downgraded_without_erasing_concrete_risk():
    result = industry.industry_risk_policy(
        {
            "stage": "EARLY_COMMERCIALIZATION",
            "riskLevel": "CONFIRMED",
            "riskFlags": ["EARLY_COMMERCIALIZATION"],
            "confirmedRiskFlags": ["EARLY_COMMERCIALIZATION"],
        }
    )
    assert result["riskLevel"] == "UNPROVEN"
    assert result["leftDiscountScale"] == 0.5
    assert result["confirmedRiskFlags"] == []
    assert (
        industry.industry_risk_policy({"riskFlags": ["FINANCING_COOLING"]})["riskLevel"]
        == "CONFIRMED"
    )


@pytest.mark.parametrize("kind", list(industry.RISK_CHECKS))
def test_company_earnings_do_not_clear_the_fourteen_day_specific_industry_observation(
    kind,
):
    previous = {
        "stage": "UNKNOWN",
        "riskFlags": [kind],
        "riskLevel": "CONFIRMED",
        "confirmedRiskFlags": [kind],
        "confirmedRiskAt": {kind: NOW.isoformat()},
    }
    current = earnings("本季度净利润同比增长25%。客户订单已实际回款2亿元。")
    result = industry.persist_industry_risk(
        current, previous, confirmed_at=NOW, now=NOW + timedelta(days=1)
    )
    assert result["earningsVerified"] is True
    assert result["persistedRiskFlags"] == [kind]
    assert result["leftDiscountScale"] == 0.5
    assert (
        "个别企业当期盈利已核实，但近14日行业级风险观察尚未到期解除"
        in result["evidenceSummary"]
    )
    assert industry.expire_industry_risk(result, NOW + timedelta(days=2))[
        "persistedRiskFlags"
    ] == [kind]
    assert industry.merge_industry_cycles(current, result)["persistedRiskFlags"] == [
        kind
    ]


def test_verified_earnings_remove_only_earnings_related_flags_from_current_and_previous():
    removable = {
        "EARLY_COMMERCIALIZATION",
        "NO_EARNINGS_DELIVERY",
        "NO_CURRENT_EARNINGS_PROOF",
        "FORWARD_PLANS_ONLY",
    }
    current = earnings("本季度净利润同比增长25%。客户订单已实际回款2亿元。")
    current.update(
        riskFlags=sorted(removable),
        confirmedRiskFlags=sorted(removable),
        riskLevel="CONFIRMED",
        forwardPlansOnly=True,
    )
    previous = deepcopy(current)
    previous["earningsVerified"] = False
    previous["confirmedRiskAt"] = {flag: NOW.isoformat() for flag in removable}
    actual = industry.persist_industry_risk(
        current, previous, confirmed_at=NOW, now=NOW + timedelta(days=1)
    )
    assert not removable.intersection(actual["riskFlags"])
    assert actual["leftDiscountScale"] == 0
