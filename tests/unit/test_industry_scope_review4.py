"""Industry risk belongs to its checked claim, independently of maturity."""

import pytest

from backend.macro import industry_cycle as industry
from test_daily_research import NOW
from test_industry_cycle import cycle


def proof(quote, *, title="产业经营披露", checked=True):
    return {
        "quote": quote,
        "title": title,
        "publishedDate": "2026-10-15",
        "url": "https://original.test/report",
        "verification": "SOURCE_CHECKED" if checked else "UNVERIFIED",
    }


def normalize(*proofs, expected=("ROBOT",), stage="UNKNOWN", evidence=None):
    refs = [f"s{i}" for i in range(len(proofs))]
    raw = cycle(stage=stage, sourceIds=refs, evidence=evidence or [])
    if refs:
        raw["riskChecks"][0].update(status="FOUND", sourceIds=refs)
    return industry.normalize_industry_cycle(
        raw,
        source_aliases={ref: "source:" + ref for ref in refs},
        sources={"source:" + ref: row for ref, row in zip(refs, proofs)},
        expected_industries=expected,
        now=NOW,
    )


@pytest.mark.parametrize(
    "quote,title",
    [
        ("近期半导体行业融资笔数下降30%。", "机器人投融资观察"),
        ("近期光伏行业融资笔数下降30%。", "产业融资统计"),
        ("近期半导体行业融资笔数下降30%。机器人公司发布新产品。", "机器人观察"),
        ("近期半导体行业融资笔数下降30%，机器人公司发布新产品。", "机器人观察"),
        ("近期行业融资笔数下降30%。", "全行业宏观统计"),
    ],
)
def test_other_or_unknown_industry_claim_cannot_confirm_robot_risk(quote, title):
    result = normalize(proof(quote, title=title))
    check = result["riskChecks"][0]
    assert check["claimSupported"] is True
    assert check["industryScopeVerified"] is False
    assert check["strength"] == "SUSPECTED"
    assert result["riskLevel"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5
    assert "INDUSTRY_SCOPE_UNVERIFIED:FINANCING_COOLING" in result["evidenceGaps"]


@pytest.mark.parametrize(
    "quote,title",
    [
        ("近期具身智能与人形机器人融资笔数下降30%。", "产业观察"),
        ("近期丝杠与减速器产业链融资规模下降30%。", "产业观察"),
        ("2026-10-15 robot industry financing declined 30%.", "产业观察"),
        ("近期该行业融资笔数下降30%。", "人形机器人投融资观察"),
    ],
)
def test_corresponding_robot_claim_or_specific_title_can_confirm(quote, title):
    result = normalize(proof(quote, title=title))
    check = result["riskChecks"][0]
    assert check["industryScopeVerified"] is True
    assert check["strength"] == "CONFIRMED"
    assert result["leftDiscountScale"] == 1


def test_scope_and_current_checked_event_must_belong_to_the_same_support():
    result = normalize(
        proof("近期半导体行业融资规模下降30%。"),
        proof("2025年1月机器人行业融资规模下降30%。"),
    )
    assert result["riskChecks"][0]["industryScopeVerified"] is False
    assert result["riskChecks"][0]["strength"] == "SUSPECTED"


def test_unchecked_robot_reference_does_not_verify_industry_scope():
    result = normalize(proof("近期机器人行业融资规模下降30%。", checked=False))
    assert result["riskChecks"][0]["industryScopeVerified"] is False
    assert result["riskLevel"] == "SUSPECTED"


def test_legacy_call_does_not_invent_an_expected_industry():
    result = normalize(proof("近期半导体行业融资规模下降30%。"), expected=None)
    assert result["riskChecks"][0]["industryScopeVerified"] is False
    assert result["riskLevel"] == "CONFIRMED"


def test_dynamic_chinese_industry_name_supplies_its_core_root():
    result = normalize(
        proof("近期特种玻璃产业融资规模下降30%。"), expected=["特种玻璃产业链"]
    )
    assert result["riskChecks"][0]["industryScopeVerified"] is True
    assert result["riskLevel"] == "CONFIRMED"


def test_direction_title_does_not_broaden_its_explicit_sector_code():
    result = normalize(
        proof("近期智能驾驶行业融资规模下降30%。"),
        expected=["ROBOT", "机器人产业链"],
    )
    assert result["riskChecks"][0]["industryScopeVerified"] is False
    assert result["riskLevel"] == "SUSPECTED"


@pytest.mark.parametrize(
    "checked,level,scale", [(True, "CONFIRMED", 1), (False, "SUSPECTED", 0.5)]
)
def test_risk_alone_does_not_establish_early_commercialization(checked, level, scale):
    result = normalize(
        proof("近期机器人行业融资下降30%。", checked=checked),
        stage="EARLY_COMMERCIALIZATION",
    )
    assert result["stage"] == "UNKNOWN"
    assert result["riskLevel"] == level
    assert result["leftDiscountScale"] == scale


def test_checked_early_fact_still_establishes_the_stage():
    result = normalize(
        proof("近期机器人行业仍处试制验证阶段，尚未规模量产。"),
        evidence=[
            {
                "kind": "COMMERCIALIZATION_EARLY",
                "summary": "试制阶段。",
                "sourceIds": ["s0"],
            }
        ],
        stage="EARLY_COMMERCIALIZATION",
    )
    assert result["stage"] == "EARLY_COMMERCIALIZATION"


def test_audit_structure_completeness_does_not_claim_search_receipts():
    result = normalize()
    assert result["auditStructureStatus"] == "COMPLETE"
    assert result["researchStatus"] == "COMPLETE"
    assert result["searchExecutionVerified"] is False
    assert result["evidenceGaps"]
    assert (
        industry.normalize_industry_cycle(None)["auditStructureStatus"] == "INCOMPLETE"
    )


@pytest.mark.parametrize(
    "code,term",
    [
        ("ROBOT", "人形机器人"),
        ("ROBOTICS_MOBILITY", "智能驾驶"),
        ("SEMICONDUCTOR_AI", "半导体"),
        ("AI", "人工智能"),
        ("RARE_EARTH", "稀土"),
        ("FINANCIAL", "银行"),
        ("FINANCE_POLICY", "券商"),
        ("DRUG", "创新药"),
        ("HEALTHCARE_CLINICAL", "临床"),
        ("GOLD_ENERGY", "原油"),
        ("AEROSPACE", "航空航天"),
        ("CONSUMPTION", "白酒"),
        ("MEMORY", "存储芯片"),
        ("PCB", "印制电路板"),
        ("POWER", "电网"),
        ("SOLID_BATTERY", "固态电池"),
        ("SATELLITE", "卫星"),
        ("LOW_ALTITUDE", "低空"),
        ("HK_TECH", "港股科技"),
        ("DIVIDEND", "红利"),
        ("TRADITIONAL_MEDICINE", "中药"),
        ("COAL", "煤炭"),
        ("GAMING", "游戏"),
        ("CHEMICAL", "化工"),
    ],
)
def test_all_requested_industry_codes_have_matching_scope_terms(code, term):
    assert (
        industry._industry_scope_verified(proof(f"近期{term}行业融资下降30%。"), [code])
        is True
    )
