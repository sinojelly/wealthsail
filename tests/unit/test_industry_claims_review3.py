"""Checked quotations must bind the claim, company, period and event clock."""

import pytest

from backend.macro import industry_cycle as industry
from test_daily_research import NOW
from test_industry_cycle import cycle


def source(quote, *, checked=True, host="original.test", title="行业经营披露"):
    return {
        "url": f"https://{host}/report",
        "title": title,
        "publishedDate": "2026-10-15",
        "quote": quote,
        "verification": "SOURCE_CHECKED" if checked else "UNVERIFIED",
    }


def normalize(raw, *proofs):
    return industry.normalize_industry_cycle(
        raw,
        source_aliases={f"s{i}": f"source:{i}" for i in range(len(proofs))},
        sources={f"source:{i}": proof for i, proof in enumerate(proofs)},
        now=NOW,
    )


def risk(*proofs):
    refs = [f"s{i}" for i in range(len(proofs))]
    raw = cycle(stage="UNKNOWN", sourceIds=refs, evidence=[])
    raw["riskChecks"][0].update(status="FOUND", sourceIds=refs)
    return normalize(raw, *proofs)


def earnings(
    profit,
    cash,
    *,
    profit_entity="甲机器人股份有限公司",
    cash_entity="甲机器人股份有限公司",
    profit_period="2026Q3",
    cash_period="2026Q3",
    same_source=False,
    identity=True,
):
    refs = ["s0"] if same_source else ["s0", "s1"]
    evidence = []
    for kind, ref, company, period in (
        ("PROFIT_GROWTH", "s0", profit_entity, profit_period),
        ("CASH_COLLECTION", refs[-1], cash_entity, cash_period),
    ):
        row = {"kind": kind, "sourceIds": [ref], "summary": "公司当期已披露事实。"}
        if identity:
            row.update(companyName=company, reportPeriod=period)
        evidence.append(row)
    proofs = (
        [source(profit + "。" + cash)]
        if same_source
        else [source(profit), source(cash)]
    )
    return normalize(
        cycle(stage="EARNINGS_DELIVERY", sourceIds=refs, evidence=evidence), *proofs
    )


PROFIT = "甲机器人股份有限公司2026年第三季度净利润同比增长25%"
CASH = "甲机器人股份有限公司2026年三季度客户订单已实际回款2亿元"


def test_two_unchecked_independent_claims_remain_suspected():
    result = risk(
        source("近期机器人行业融资笔数下降30%。", checked=False),
        source(
            "近期机器人行业融资轮次下降25%。",
            checked=False,
            host="second.test",
            title="创投融资轮次统计",
        ),
    )
    check = result["riskChecks"][0]
    assert check["independentSourceCount"] == 2
    assert check["claimSupported"] is True
    assert check["strength"] == "SUSPECTED"
    assert result["riskLevel"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5


@pytest.mark.parametrize(
    "quote,event",
    [
        ("2024年第三季度机器人行业融资笔数下降30%。", "HISTORICAL"),
        ("2025年第四季度机器人行业融资笔数下降30%。", "HISTORICAL"),
        ("机器人行业融资笔数下降30%。", "UNVERIFIED"),
    ],
)
def test_recent_article_does_not_make_old_or_undated_event_confirmed(quote, event):
    result = risk(source(quote))
    check = result["riskChecks"][0]
    assert check["claimSupported"] is True
    assert check["eventTimeVerification"] == event
    assert check["strength"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5


def test_checked_current_event_can_confirm_and_unlisted_is_not_a_negation():
    result = risk(source("未上市企业近期融资笔数下降30%。"))
    check = result["riskChecks"][0]
    assert check["claimSupported"] is True
    assert check["eventTimeVerification"] == "RECENT"
    assert check["strength"] == "CONFIRMED"
    assert result["leftDiscountScale"] == 1


def test_unrelated_recent_clause_cannot_supply_risk_event_clock():
    check = risk(source("2025年第四季度行业融资下降30%。近期机器人产品发布。"))[
        "riskChecks"
    ][0]
    assert check["eventTimeVerification"] == "HISTORICAL"
    assert check["strength"] == "SUSPECTED"


def test_event_date_before_a_comma_still_exposes_the_old_event_period():
    check = risk(source("2025年1月，机器人行业融资下降30%。"))["riskChecks"][0]
    assert check["eventTimeVerification"] == "HISTORICAL"
    assert check["eventPeriodStart"] == "2025-01-01"
    assert check["strength"] == "SUSPECTED"


def test_current_delay_announcement_is_not_dated_by_its_future_deadline():
    raw = cycle(stage="UNKNOWN", sourceIds=["s0"], evidence=[])
    raw["riskChecks"][4].update(status="FOUND", sourceIds=["s0"])
    result = normalize(raw, source("近期机器人行业宣布量产延期至2028年1月。"))
    check = result["riskChecks"][4]
    assert check["claimSupported"] is True
    assert check["eventTimeVerification"] == "RECENT"
    assert check["strength"] == "CONFIRMED"


def test_entity_identifier_must_also_be_checked_in_both_supporting_facts():
    def make(identifier, quoted_identifier):
        raw = cycle(
            stage="EARNINGS_DELIVERY",
            sourceIds=["s0"],
            evidence=[
                {
                    "kind": kind,
                    "summary": "已披露的公司事实。",
                    "sourceIds": ["s0"],
                    "companyName": "甲机器人股份有限公司",
                    "entityId": identifier,
                    "reportPeriod": "2026Q3",
                }
                for kind in ("PROFIT_GROWTH", "CASH_COLLECTION")
            ],
        )
        return normalize(
            raw,
            source(
                PROFIT.replace("2026", f"（{quoted_identifier}）2026")
                + "。"
                + CASH.replace("2026", f"（{quoted_identifier}）2026")
            ),
        )

    assert make("600001", "600001")["earningsVerified"] is True
    assert make("600999", "600001")["earningsVerified"] is False


@pytest.mark.parametrize(
    "quote,expected",
    [
        ("近期机器人行业尚未盈利，未实现实际回款。", "EARLY_COMMERCIALIZATION"),
        ("近期机器人行业已实现盈利和实际回款。", "UNKNOWN"),
    ],
)
def test_non_delivery_evidence_must_quote_actual_unmet_earnings(quote, expected):
    raw = cycle(
        sourceIds=["s0"],
        evidence=[
            {
                "kind": "EARNINGS_NOT_DELIVERED",
                "summary": "盈利兑现状态。",
                "sourceIds": ["s0"],
            }
        ],
    )
    assert normalize(raw, source(quote))["stage"] == expected


def test_checked_matching_company_period_facts_keep_company_scope():
    result = earnings(PROFIT, CASH)
    assert result["stage"] == "EARNINGS_DELIVERY"
    assert result["earningsVerified"] is True
    assert result["earningsEntity"] == "甲机器人股份有限公司"
    assert result["earningsReportPeriod"] == "2026Q3"
    assert all(row["factIdentityVerified"] for row in result["evidence"])
    assert result["earningsEvidenceScope"] == "COMPANY_LEVEL"


def test_old_payload_without_company_period_is_readable_but_not_green():
    result = earnings(PROFIT, CASH, identity=False)
    assert result["stage"] == "UNKNOWN"
    assert result["earningsVerified"] is False
    assert "EARNINGS_ENTITY_OR_PERIOD_UNVERIFIED" in result["evidenceGaps"]


@pytest.mark.parametrize(
    "profit,cash,kwargs",
    [
        (
            PROFIT,
            CASH.replace("甲机器人", "乙机器人"),
            {"cash_entity": "乙机器人股份有限公司"},
        ),
        (
            PROFIT,
            CASH.replace("2026年三季度", "2026年二季度"),
            {"cash_period": "2026Q2"},
        ),
        (PROFIT, CASH.replace("甲机器人", "乙机器人"), {}),
        (PROFIT, CASH.replace("2026年三季度", "2026年二季度"), {}),
        (PROFIT, CASH, {"profit_entity": "公司", "cash_entity": "公司"}),
        (PROFIT, CASH, {"profit_entity": "该公司", "cash_entity": "该公司"}),
    ],
)
def test_cross_company_period_and_unchecked_identity_cannot_be_green(
    profit, cash, kwargs
):
    result = earnings(profit, cash, **kwargs)
    assert result["stage"] == "UNKNOWN"
    assert result["earningsVerified"] is False


def test_one_source_does_not_allow_cross_company_or_period_stitching():
    result = earnings(PROFIT, CASH.replace("甲机器人", "乙机器人"), same_source=True)
    assert result["earningsVerified"] is False
    assert result["stage"] == "UNKNOWN"


def test_checked_one_source_with_two_explicit_matching_facts_can_be_green():
    assert earnings(PROFIT, CASH, same_source=True)["earningsVerified"] is True


def test_another_company_in_the_same_fact_clause_cannot_borrow_the_first_identity():
    result = earnings(
        "甲机器人股份有限公司2026年第三季度对比乙机器人股份有限公司净利润增长25%",
        CASH,
    )
    assert result["earningsVerified"] is False
    assert result["stage"] == "UNKNOWN"


def test_company_mentioned_after_the_actual_profit_subject_is_not_its_identity():
    result = earnings(
        "乙机器人股份有限公司2026年第三季度净利润增长25%优于甲机器人股份有限公司",
        CASH,
    )
    assert result["earningsVerified"] is False


def test_shared_company_name_suffix_is_not_a_verified_entity():
    result = earnings(
        PROFIT,
        CASH.replace("甲机器人", "乙机器人"),
        profit_entity="机器人股份有限公司",
        cash_entity="机器人股份有限公司",
    )
    assert result["earningsVerified"] is False
    assert result["stage"] == "UNKNOWN"


def test_entity_id_after_another_company_profit_cannot_supply_its_subject():
    raw = cycle(
        stage="EARNINGS_DELIVERY",
        sourceIds=["s0", "s1"],
        evidence=[
            {
                "kind": kind,
                "sourceIds": [ref],
                "summary": "公司财报事实。",
                "entityId": "600001",
                "reportPeriod": "2026Q3",
            }
            for kind, ref in (("PROFIT_GROWTH", "s0"), ("CASH_COLLECTION", "s1"))
        ],
    )
    result = normalize(
        raw,
        source(
            "乙机器人股份有限公司2026Q3净利润增长25%优于甲机器人股份有限公司（600001）"
        ),
        source("甲机器人股份有限公司（600001）2026Q3客户订单已实际回款2亿元"),
    )
    assert result["earningsVerified"] is False
    assert result["stage"] == "UNKNOWN"


def test_later_product_announcement_cannot_supply_the_risk_clause_event_time():
    result = risk(source("行业融资下降30%，近期公司发布新产品。"))
    assert result["riskChecks"][0]["eventTimeVerification"] == "UNVERIFIED"
    assert result["riskChecks"][0]["strength"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5


@pytest.mark.parametrize(
    "quote,checked,expected",
    [
        (
            "近期机器人行业仍处试制验证阶段，尚未实现规模量产。",
            True,
            "EARLY_COMMERCIALIZATION",
        ),
        ("近期机器人行业仍处试制验证阶段，尚未实现规模量产。", False, "UNKNOWN"),
        ("近期机器人行业发布新产品。", True, "UNKNOWN"),
        ("近期机器人行业拟投资建设新工厂。", True, "UNKNOWN"),
        ("近期机器人行业已经完成规模量产。", True, "UNKNOWN"),
        ("近期机器人行业并非商业化早期，已经完成规模量产。", True, "UNKNOWN"),
    ],
)
def test_early_stage_requires_checked_corresponding_stage_facts(
    quote, checked, expected
):
    raw = cycle(sourceIds=["s0"])
    raw["evidence"][0]["sourceIds"] = ["s0"]
    result = normalize(raw, source(quote, checked=checked))
    assert result["stage"] == expected
    assert result["riskLevel"] == ("UNPROVEN" if expected != "UNKNOWN" else "NONE")
