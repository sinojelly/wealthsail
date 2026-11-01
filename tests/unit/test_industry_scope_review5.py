"""Company facts remain true without establishing another industry's stage."""

from copy import deepcopy
from datetime import timedelta

import pytest

from backend.macro import industry_cycle as industry
from test_daily_research import NOW
from test_industry_cycle import cycle


def proof(quote, title="机器人产业投资观察"):
    return {
        "quote": quote,
        "title": title,
        "publishedDate": "2026-10-15",
        "verification": "SOURCE_CHECKED",
        "url": "https://original.test/report",
    }


def normalize(raw, source, expected=("ROBOT",)):
    return industry.normalize_industry_cycle(
        raw,
        source_aliases={"s0": "source:0"},
        sources={"source:0": source},
        expected_industries=expected,
        now=NOW,
    )


def risk(quote):
    raw = cycle(stage="UNKNOWN", sourceIds=["s0"], evidence=[])
    raw["riskChecks"][0].update(status="FOUND", sourceIds=["s0"])
    return normalize(raw, proof(quote))


def earnings(*, company="甲半导体股份有限公司", expected=("ROBOT",)):
    raw = cycle(
        stage="EARNINGS_DELIVERY",
        sourceIds=["s0"],
        evidence=[
            {
                "kind": kind,
                "summary": "公司真实财务事实。",
                "sourceIds": ["s0"],
                "companyName": company,
                "reportPeriod": "2026Q3",
            }
            for kind in ("PROFIT_GROWTH", "CASH_COLLECTION")
        ],
    )
    return normalize(
        raw,
        proof(
            f"{company}2026年第三季度净利润同比增长25%。"
            f"{company}2026年第三季度客户订单已实际回款2亿元。"
        ),
        expected,
    )


def test_unknown_but_explicit_shipbuilding_industry_cannot_borrow_robot_title():
    result = risk("近期造船行业融资规模下降30%。")
    assert result["riskChecks"][0]["industryScopeVerified"] is False
    assert result["riskChecks"][0]["strength"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5


@pytest.mark.parametrize(
    "quote",
    [
        "近期机器人行业景气向好，同时造船行业融资规模下降30%。",
        "近期机器人行业景气向好同时半导体行业融资规模下降30%。",
        "相比机器人行业，造船行业近期融资规模下降30%。",
    ],
)
def test_parallel_or_compared_foreign_risk_cannot_confirm_robot_cooling(quote):
    result = risk(quote)
    check = result["riskChecks"][0]
    assert check["industryScopeVerified"] is False
    assert check["strength"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5


def test_parallel_context_reference_keeps_robot_subject_and_current_event_time():
    result = risk("近期机器人行业订单放缓，同时该行业一级市场融资规模下降30%。")
    check = result["riskChecks"][0]
    assert check["industryScopeVerified"] is True
    assert check["eventTimeVerification"] == "RECENT"
    assert check["strength"] == "CONFIRMED"


@pytest.mark.parametrize(
    "quote",
    [
        "相比机器人行业，造船行业近期融资规模下降30%。",
        "机器人与半导体行业近期融资规模下降30%。",
    ],
)
def test_mixed_subjects_in_one_scope_statement_cannot_verify_robot(quote):
    assert industry._industry_scope_verified(proof(quote), ("ROBOT",)) is False


@pytest.mark.parametrize("period", ["近期", "2026年9月"])
def test_parallel_split_preserves_immediately_preceding_date_only_context(period):
    result = risk(f"{period}，同时机器人行业融资规模下降30%。")
    assert result["riskChecks"][0]["eventTimeVerification"] == "RECENT"
    assert result["riskChecks"][0]["strength"] == "CONFIRMED"


@pytest.mark.parametrize(
    "quote",
    [
        "近期半导体行业订单放缓，同时该行业一级市场融资规模下降30%。",
        "近期机器人行业景气向好及造船行业订单放缓，同时该行业一级市场融资规模下降30%。",
    ],
)
def test_context_reference_keeps_its_foreign_antecedent_despite_robot_title(quote):
    result = risk(quote)
    assert result["riskChecks"][0]["industryScopeVerified"] is False
    assert result["riskChecks"][0]["strength"] == "SUSPECTED"


def test_explicit_context_reference_can_still_use_the_specific_checked_title():
    result = risk("近期该行业融资规模下降30%。")
    assert result["riskChecks"][0]["industryScopeVerified"] is True
    assert result["riskChecks"][0]["strength"] == "CONFIRMED"


def test_semiconductor_trial_fact_does_not_establish_robot_early_stage():
    raw = cycle(
        sourceIds=["s0"],
        evidence=[
            {
                "kind": "COMMERCIALIZATION_EARLY",
                "summary": "试制阶段。",
                "sourceIds": ["s0"],
            }
        ],
    )
    result = normalize(
        raw, proof("近期半导体行业仍处于试制验证阶段，尚未实现规模量产。")
    )
    assert result["stage"] == "UNKNOWN"
    assert result["evidence"][0]["industryScopeVerified"] is False
    assert "STAGE_INDUSTRY_SCOPE_UNVERIFIED" in result["evidenceGaps"]


def test_corresponding_robot_trial_fact_still_supports_early_stage():
    raw = cycle(
        sourceIds=["s0"],
        evidence=[
            {
                "kind": "COMMERCIALIZATION_EARLY",
                "summary": "试制阶段。",
                "sourceIds": ["s0"],
            }
        ],
    )
    result = normalize(raw, proof("近期机器人行业仍处于试制验证阶段。"))
    assert result["stage"] == "EARLY_COMMERCIALIZATION"
    assert result["evidence"][0]["industryScopeVerified"] is True


@pytest.mark.parametrize(
    ("quote", "expected", "stage"),
    [
        ("近期半导体行业获专项政策支持。", ("ROBOT",), "UNKNOWN"),
        ("近期机器人行业获专项政策支持。", ("ROBOT",), "POLICY_CATALYST"),
        ("近期半导体行业获专项政策支持。", None, "POLICY_CATALYST"),
    ],
)
def test_policy_stage_also_requires_applicable_industry_evidence(
    quote, expected, stage
):
    raw = cycle(
        stage="POLICY_CATALYST",
        sourceIds=["s0"],
        evidence=[
            {"kind": "POLICY_EVENT", "summary": "专项支持政策。", "sourceIds": ["s0"]}
        ],
    )
    result = normalize(raw, proof(quote), expected)
    assert result["stage"] == stage
    assert ("STAGE_INDUSTRY_SCOPE_UNVERIFIED" in result["evidenceGaps"]) is (
        stage == "UNKNOWN"
    )


def test_other_industry_financial_pair_preserves_company_facts_without_robot_delivery():
    result = earnings()
    assert result["earningsVerified"] is True
    assert result["earningsEntity"] == "甲半导体股份有限公司"
    assert result["earningsReportPeriod"] == "2026Q3"
    assert result["earningsEvidenceScope"] == "COMPANY_LEVEL"
    assert result["earningsIndustryScopeVerified"] is False
    assert all(row["factIdentityVerified"] for row in result["evidence"])
    assert all(row["industryScopeVerified"] is False for row in result["evidence"])
    assert result["stage"] == "UNKNOWN"
    assert "EARNINGS_INDUSTRY_SCOPE_UNVERIFIED" in result["evidenceGaps"]
    assert "NO_CURRENT_EARNINGS_PROOF" in result["riskFlags"]
    assert industry.earnings_recovery_verified(result) is False


def test_related_company_pair_still_supports_industry_stage_and_recovery():
    result = earnings(company="甲机器人股份有限公司")
    assert result["stage"] == "EARNINGS_DELIVERY"
    assert result["earningsVerified"] is True
    assert result["earningsIndustryScopeVerified"] is True
    assert all(row["industryScopeVerified"] for row in result["evidence"])
    assert industry.earnings_recovery_verified(result) is True


def test_legacy_earnings_pair_keeps_unknown_scope_and_compatible_recovery():
    result = earnings(expected=None)
    assert result["earningsVerified"] is True
    assert result["earningsIndustryScopeVerified"] is None
    assert result["stage"] == "EARNINGS_DELIVERY"
    assert industry.earnings_recovery_verified(result) is True
    assert industry.earnings_recovery_verified({"earningsVerified": True}) is True


@pytest.mark.parametrize("operation", ["policy", "merge", "persist", "expire"])
def test_foreign_company_facts_cannot_clear_target_industry_earnings_watch(operation):
    current = earnings()
    flag = "NO_CURRENT_EARNINGS_PROOF"
    current.update(
        riskFlags=[flag, "PERSISTED_RISK_WATCH"],
        riskLevel="SUSPECTED",
        persistedRiskFlags=[flag],
        confirmedRiskAt={flag: NOW.isoformat()},
        forwardPlansOnly=True,
    )
    if operation == "policy":
        result = industry.industry_risk_policy(current)
    elif operation == "merge":
        result = industry.merge_industry_cycles(current, deepcopy(current))
    elif operation == "persist":
        result = industry.persist_industry_risk(
            current, deepcopy(current), confirmed_at=NOW, now=NOW + timedelta(days=1)
        )
    else:
        result = industry.expire_industry_risk(current, NOW + timedelta(days=1))
    assert flag in result["riskFlags"]
    assert result["leftDiscountScale"] == 0.5
    assert industry.COMPANY_EARNINGS_INDUSTRY_WATCH_NOTE not in result.get(
        "evidenceSummary", ""
    )
