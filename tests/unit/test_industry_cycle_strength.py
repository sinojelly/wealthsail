"""Risk intensity follows checked independent evidence and survives missed searches."""

from copy import deepcopy
from datetime import timedelta

import pytest

from backend.macro import industry_cycle as industry
from test_daily_research import NOW
from test_industry_cycle import cycle


def normalized(*proofs, stage="UNKNOWN", evidence=None):
    refs = [f"s{index}" for index in range(len(proofs))]
    raw = cycle(stage=stage, evidence=evidence or [], sourceIds=refs)
    if refs and evidence is None:
        raw["riskChecks"][0].update(
            status="FOUND", sourceIds=refs, summary="行业一级市场融资笔数减少。"
        )
    return industry.normalize_industry_cycle(
        raw,
        source_aliases={key: "source:" + key for key in refs},
        sources={"source:" + key: proof for key, proof in zip(refs, proofs)},
        now=NOW,
    )


def media(host="publisher-one.test", title="行业融资笔数减少", **updates):
    return {
        "url": f"https://{host}/story",
        "title": title,
        "publishedDate": "2026-10-15",
        "verification": "UNVERIFIED",
        **updates,
    }


def test_single_unchecked_media_is_half_discount_suspected_without_changing_stage():
    actual = normalized(media())
    assert actual["stage"] == "UNKNOWN"
    assert actual["riskChecks"][0]["independentSourceCount"] == 1
    assert actual["riskChecks"][0]["strength"] == "SUSPECTED"
    assert actual["confirmedRiskFlags"] == []
    assert actual["suspectedRiskFlags"] == ["FINANCING_COOLING"]
    assert actual["riskLevel"] == "SUSPECTED"
    assert actual["leftDiscountScale"] == 0.5
    assert "一级市场融资降温" in actual["industryRiskWarning"]
    assert "待核实" in actual["industryRiskWarning"]


def test_two_hosts_with_different_original_titles_confirm_the_signal():
    actual = normalized(media(), media("publisher-two.test", "创投季度融资统计回落"))
    assert actual["riskChecks"][0]["independentSourceCount"] == 2
    assert actual["confirmedRiskFlags"] == ["FINANCING_COOLING"]
    assert actual["riskLevel"] == "CONFIRMED"
    assert actual["leftDiscountScale"] == 1.0


@pytest.mark.parametrize(
    "second",
    [
        media("www.publisher-one.test", "另一标题的融资观察"),
        media("publisher-two.test", "转载： 行业融资笔数减少！"),
    ],
)
def test_same_host_or_reprinted_title_is_not_an_independent_confirmation(second):
    actual = normalized(media(), second)
    assert actual["riskChecks"][0]["independentSourceCount"] == 1
    assert actual["riskLevel"] == "SUSPECTED"
    assert actual["leftDiscountScale"] == 0.5


def test_one_checked_original_can_confirm_without_requiring_two_media():
    actual = normalized(media(verification="SOURCE_CHECKED"))
    assert actual["riskChecks"][0]["strength"] == "CONFIRMED"
    assert actual["leftDiscountScale"] == 1.0


@pytest.mark.parametrize("evidence_kind", ["EXPANSION_PLAN", "FRAMEWORK_AGREEMENT"])
def test_forward_plans_are_unproven_half_discount_even_if_model_says_unknown(
    evidence_kind,
):
    actual = normalized(
        media(),
        evidence=[
            {"kind": evidence_kind, "summary": "拟建厂或签框架。", "sourceIds": ["s0"]}
        ],
    )
    assert actual["forwardPlansOnly"] is True
    assert "FORWARD_PLANS_ONLY" in actual["riskFlags"]
    assert actual["riskLevel"] == "UNPROVEN"
    assert actual["leftDiscountScale"] == 0.5
    assert "仅见远期扩产/框架意向" in actual["industryRiskWarning"]


def test_unverified_earnings_claim_adds_server_gap_flag_and_half_discount():
    actual = normalized(
        media(),
        stage="EARNINGS_DELIVERY",
        evidence=[
            {
                "kind": "PROFIT_GROWTH",
                "summary": "模型声称已兑现。",
                "sourceIds": ["s0"],
            }
        ],
    )
    assert actual["stage"] == "UNKNOWN"
    assert "NO_CURRENT_EARNINGS_PROOF" in actual["riskFlags"]
    assert actual["riskLevel"] == "UNPROVEN"
    assert actual["leftDiscountScale"] == 0.5


@pytest.mark.parametrize(
    "query", ["无", "未找到", "机器人", "2026 融资降温", "robotics no results"]
)
def test_empty_search_claim_without_industry_and_risk_terms_is_unverified(query):
    raw = cycle(stage="UNKNOWN", evidence=[], sourceIds=[])
    raw["riskChecks"][0]["query"] = query
    actual = industry.normalize_industry_cycle(raw, now=NOW)
    assert actual["riskChecks"][0]["status"] == "UNVERIFIED"
    assert actual["riskChecks"][0]["queryValidation"] == "INSUFFICIENT_QUERY"
    assert actual["searchExecutionVerified"] is False


def test_syntactically_valid_query_does_not_invent_a_search_execution_receipt():
    actual = normalized()
    assert actual["riskChecks"][0]["status"] == "NOT_FOUND"
    assert actual["riskChecks"][0]["queryValidation"] == "BASIC_SYNTAX_ONLY"
    assert actual["searchExecutionVerified"] is False


def earnings_same_source(quote):
    return normalized(
        media(verification="SOURCE_CHECKED", quote=quote),
        stage="EARNINGS_DELIVERY",
        evidence=[
            {
                "kind": "PROFIT_GROWTH",
                "summary": "甲公司本期利润增长。",
                "sourceIds": ["s0"],
            },
            {
                "kind": "CASH_COLLECTION",
                "summary": "甲公司实际回款。",
                "sourceIds": ["s0"],
            },
        ],
    )


def test_one_clause_cannot_be_reused_as_two_independent_earnings_facts():
    actual = earnings_same_source("甲公司净利润同比增长25%并实际回款2亿元。")
    assert actual["stage"] == "UNKNOWN"
    assert actual["earningsVerified"] is False
    assert actual["leftDiscountScale"] == 0.5


def test_one_checked_financial_source_with_separate_factual_clauses_keeps_company_scope():
    actual = earnings_same_source(
        "甲公司本期净利润同比增长25%。本季度客户订单已实际回款2亿元。"
    )
    assert actual["stage"] == "EARNINGS_DELIVERY"
    assert actual["earningsVerified"] is True
    assert actual["earningsEvidenceScope"] == "COMPANY_LEVEL"
    assert "公司级" in actual["evidenceSummary"]
    assert "不代表全行业" in actual["evidenceSummary"]
    assert actual["riskLevel"] == "NONE"


def test_policy_splits_stage_from_intensity_and_keeps_legacy_protection():
    assert (
        industry.industry_risk_policy({"stage": "EARLY_COMMERCIALIZATION"})[
            "leftDiscountScale"
        ]
        == 1
    )
    assert (
        industry.industry_risk_policy({"riskFlags": ["FINANCING_COOLING"]})["riskLevel"]
        == "CONFIRMED"
    )
    assert (
        industry.industry_risk_policy(
            {"stage": "UNKNOWN", "riskFlags": [], "riskLevel": "NONE"}
        )["leftDiscountScale"]
        == 0
    )
    suspect = {
        "stage": "POLICY_CATALYST",
        "riskFlags": ["FINANCING_COOLING"],
        "suspectedRiskFlags": ["FINANCING_COOLING"],
        "riskLevel": "SUSPECTED",
        "leftDiscountScale": 0,
    }
    assert industry.industry_risk_policy(suspect)["leftDiscountScale"] == 0.5
    assert industry.requires_left_discount(suspect) is True


def test_merging_suspected_checks_does_not_upgrade_them_or_keep_an_old_zero_scale():
    first = normalized(media())
    second = normalized(media("publisher-two.test", "另一篇独立观察"))
    second["leftDiscountScale"] = 0
    result = industry.merge_industry_cycles(first, second)
    assert result["riskLevel"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5
    assert result["confirmedRiskFlags"] == []
    assert result["riskChecks"][0]["strength"] == "SUSPECTED"
    strong = normalized(media(verification="SOURCE_CHECKED"))
    assert industry.merge_industry_cycles(first, strong)["riskLevel"] == "CONFIRMED"


def test_missed_search_retains_recent_confirmed_risk_as_half_discount_observation():
    original = normalized(media(verification="SOURCE_CHECKED"))
    today = normalized()
    result = industry.persist_industry_risk(
        today, original, confirmed_at=NOW, now=NOW + timedelta(days=3)
    )
    assert result["riskLevel"] == "SUSPECTED"
    assert result["leftDiscountScale"] == 0.5
    assert result["confirmedRiskFlags"] == []
    assert result["persistedRiskFlags"] == ["FINANCING_COOLING"]
    assert "PERSISTED_RISK_WATCH" in result["riskFlags"]
    assert "延续近14日已确认的行业降温观察" in result["evidenceSummary"]
    assert result["confirmedRiskAt"]["FINANCING_COOLING"] == NOW.isoformat()
    repeated = industry.persist_industry_risk(
        today,
        result,
        confirmed_at=NOW + timedelta(days=3),
        now=NOW + timedelta(days=12),
    )
    assert repeated["confirmedRiskAt"]["FINANCING_COOLING"] == NOW.isoformat()
    assert repeated["leftDiscountScale"] == 0.5
    expired = industry.persist_industry_risk(
        today,
        repeated,
        confirmed_at=NOW + timedelta(days=12),
        now=NOW + timedelta(days=15),
    )
    assert expired["leftDiscountScale"] == 0


def test_search_failure_retains_watch_but_verified_earnings_releases_the_observation():
    previous = normalized(media(verification="SOURCE_CHECKED"))
    today = normalized()
    today["riskChecks"][0]["status"] = "UNVERIFIED"
    assert (
        industry.persist_industry_risk(
            today, previous, confirmed_at=NOW, now=NOW + timedelta(days=1)
        )["leftDiscountScale"]
        == 0.5
    )
    recovered = earnings_same_source(
        "甲公司本期净利润同比增长25%。本季度客户订单已实际回款2亿元。"
    )
    assert (
        industry.persist_industry_risk(
            recovered, previous, confirmed_at=NOW, now=NOW + timedelta(days=1)
        )["leftDiscountScale"]
        == 0
    )
    assert (
        industry.persist_industry_risk(previous, today, confirmed_at=NOW, now=NOW)[
            "riskLevel"
        ]
        == "CONFIRMED"
    )


def test_persisted_watch_merge_never_reconfirms_the_original_risk():
    previous = normalized(media(verification="SOURCE_CHECKED"))
    today = normalized()
    watch = industry.persist_industry_risk(
        today, previous, confirmed_at=NOW, now=NOW + timedelta(days=1)
    )
    combined = industry.merge_industry_cycles(deepcopy(today), watch)
    assert combined["riskLevel"] == "SUSPECTED"
    assert combined["leftDiscountScale"] == 0.5
    assert combined["confirmedRiskFlags"] == []


def test_persistence_never_relabels_today_search_or_adds_historical_source_aliases():
    previous = normalized(media(verification="SOURCE_CHECKED"))
    previous["sources"] = [media(verification="SOURCE_CHECKED")]
    current = normalized()
    original = deepcopy(current)
    result = industry.persist_industry_risk(
        current, previous, confirmed_at=NOW, now=NOW + timedelta(days=2)
    )
    assert current == original
    assert result["sourceIds"] == []
    assert result["riskChecks"] == current["riskChecks"]
    assert result["persistedEvidence"][0]["sourceIds"] == ["source:s0"]
    assert (
        result["persistedEvidence"][0]["sources"][0]["url"]
        == "https://publisher-one.test/story"
    )


def test_reusing_a_cached_persisted_state_after_day_fourteen_cannot_extend_it():
    previous = normalized(media(verification="SOURCE_CHECKED"))
    watch = industry.persist_industry_risk(
        normalized(), previous, confirmed_at=NOW, now=NOW + timedelta(days=1)
    )
    expired = industry.persist_industry_risk(
        watch,
        watch,
        confirmed_at=NOW + timedelta(days=14),
        now=NOW + timedelta(days=15),
    )
    assert expired["riskLevel"] == "NONE"
    assert expired["leftDiscountScale"] == 0
    assert "PERSISTED_RISK_WATCH" not in expired["riskFlags"]


def test_a_later_real_confirmation_is_not_overwritten_by_older_history_order():
    old = normalized(media(verification="SOURCE_CHECKED"))
    recent = deepcopy(old)
    recent["confirmedRiskAt"] = {
        "FINANCING_COOLING": (NOW + timedelta(days=5)).isoformat()
    }
    recent["confirmedAt"] = (NOW + timedelta(days=5)).isoformat()
    first = industry.persist_industry_risk(
        normalized(),
        recent,
        confirmed_at=NOW + timedelta(days=5),
        now=NOW + timedelta(days=6),
    )
    result = industry.persist_industry_risk(
        first, old, confirmed_at=NOW, now=NOW + timedelta(days=6)
    )
    assert (
        result["confirmedRiskAt"]["FINANCING_COOLING"]
        == (NOW + timedelta(days=5)).isoformat()
    )


def test_source_count_finds_independent_host_title_pairs_without_greedy_order_bias():
    actual = normalized(
        media("publisher-one.test", "a共同标题"),
        media("publisher-one.test", "z独立调查"),
        media("publisher-two.test", "a共同标题"),
    )
    assert actual["riskChecks"][0]["independentSourceCount"] == 2
    assert actual["riskLevel"] == "CONFIRMED"


def test_full_early_stage_discount_does_not_present_a_suspected_media_claim_as_confirmed():
    raw = cycle(
        sourceIds=["s0"],
        evidence=[
            {
                "kind": "COMMERCIALIZATION_EARLY",
                "summary": "仍处试制阶段。",
                "sourceIds": ["s0"],
            }
        ],
    )
    raw["riskChecks"][0].update(status="FOUND", sourceIds=["s0"])
    actual = industry.normalize_industry_cycle(
        raw,
        source_aliases={"s0": "source:zero"},
        sources={"source:zero": media()},
        now=NOW,
    )
    assert actual["riskLevel"] == "CONFIRMED"
    assert actual["riskChecks"][0]["strength"] == "SUSPECTED"
    assert "一级市场融资降温（待核实）" in actual["industryRiskWarning"]
