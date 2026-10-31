"""Industry labels require current cited evidence, never an industry name."""

from importlib import import_module, util

import pytest
from test_daily_research import NOW as RESEARCH_NOW


def current_stage_sources():
    return {
        "source:one": {
            "verification": "SOURCE_CHECKED",
            "publishedDate": "2026-10-15",
            "quote": "行业仍处试制验证阶段。",
        }
    }


def validate_current_stage(rows, public=None):
    from backend.macro.value_directions import validate_value_directions
    from test_value_directions import NOW, packet

    return validate_value_directions(
        rows,
        public or packet(),
        NOW,
        {"s1": "source:one", "s2": "source:two"},
        sources=current_stage_sources(),
    )


def engine():
    assert util.find_spec("backend.macro.industry_cycle"), (
        "industry cycle risk contract missing"
    )
    return import_module("backend.macro.industry_cycle")


def checks():
    return [
        {
            "kind": kind,
            "status": "NOT_FOUND",
            "query": f"测试行业 {query}",
            "summary": "本轮检索未找到可确认材料，不等于不存在风险。",
            "sourceIds": [],
        }
        for kind, query in [
            ("FINANCING_COOLING", "一级市场融资降温 断代"),
            ("LAYOFFS_CLOSURES", "欠薪 裁员 倒闭"),
            ("ORDER_CUTS", "下游砍单"),
            ("OVERCAPACITY_PRICE_WAR", "产能过剩 价格战"),
            ("PRODUCTION_DELAY", "商业化量产推迟"),
        ]
    ]


def cycle(**updates):
    return {
        "stage": "EARLY_COMMERCIALIZATION",
        "evidenceSummary": "商业化仍在早期，等待实际订单回款。",
        "sourceIds": ["s1"],
        "evidence": [
            {
                "kind": "COMMERCIALIZATION_EARLY",
                "summary": "披露仍处试制验证阶段。",
                "sourceIds": ["s1"],
            }
        ],
        "riskChecks": checks(),
        **updates,
    }


def test_legacy_missing_cycle_remains_unknown_without_inventing_robotics_risk():
    actual = engine().normalize_industry_cycle(None)
    assert actual["stage"] == "UNKNOWN"
    assert actual["riskFlags"] == []
    assert actual["researchStatus"] == "INCOMPLETE"
    assert len(actual["evidenceGaps"]) == 5
    assert engine().requires_left_discount(actual) is False


def test_cited_early_commercialization_propagates_precaution_without_keyword_guessing():
    actual = engine().normalize_industry_cycle(
        cycle(),
        source_aliases={"s1": "source:one"},
        sources=current_stage_sources(),
        now=RESEARCH_NOW,
    )
    assert actual["stage"] == "EARLY_COMMERCIALIZATION"
    assert actual["label"] == "🟠 商业化早期·去伪存真期"
    assert actual["evidence"][0]["sourceIds"] == ["source:one"]
    assert engine().industry_cycle_requires_discount(actual) is True
    assert actual["researchStatus"] == "COMPLETE"
    assert len(actual["evidenceGaps"]) == 5


@pytest.mark.parametrize(
    "kind", ["EXPANSION_PLAN", "FRAMEWORK_AGREEMENT", "PAID_ORDERS", "PROFIT_GROWTH"]
)
def test_single_positive_claim_and_remote_plans_cannot_become_green_delivery(kind):
    actual = engine().normalize_industry_cycle(
        cycle(
            stage="EARNINGS_DELIVERY",
            evidence=[{"kind": kind, "summary": "公司公告材料。", "sourceIds": ["s1"]}],
        ),
        source_aliases={"s1": "source:one"},
        sources={"source:one": {"verification": "SOURCE_CHECKED"}},
    )
    assert actual["stage"] == "UNKNOWN"


def test_profit_and_cash_collection_both_need_independently_checked_originals_for_green():
    raw = cycle(
        stage="EARNINGS_DELIVERY",
        evidence=[
            {"kind": "PROFIT_GROWTH", "summary": "季报利润增长。", "sourceIds": ["s1"]},
            {
                "kind": "CASH_COLLECTION",
                "summary": "财报披露真实订单回款。",
                "sourceIds": ["s2"],
            },
        ],
        sourceIds=["s1", "s2"],
    )
    aliases = {"s1": "source:one", "s2": "source:two"}
    assert (
        engine().normalize_industry_cycle(raw, source_aliases=aliases)["stage"]
        == "UNKNOWN"
    )
    actual = engine().normalize_industry_cycle(
        raw,
        source_aliases=aliases,
        sources={
            "source:one": {
                "verification": "SOURCE_CHECKED",
                "publishedDate": "2026-10-15",
                "quote": "本报告期净利润同比增长25%。",
            },
            "source:two": {
                "verification": "SOURCE_CHECKED",
                "publishedDate": "2026-10-15",
                "quote": "本季度客户订单已实际回款2亿元。",
            },
        },
    )
    assert actual["stage"] == "EARNINGS_DELIVERY"
    assert actual["label"] == "🟢 业绩兑现期"
    assert engine().requires_left_discount(actual) is False


def test_found_negative_signal_requires_current_source_and_preserves_verification_state():
    raw = cycle(stage="UNKNOWN", evidence=[])
    raw["riskChecks"][0].update(
        status="FOUND", sourceIds=["s1"], summary="媒体报道行业融资笔数减少。"
    )
    actual = engine().normalize_industry_cycle(raw, source_aliases={"s1": "source:one"})
    assert "FINANCING_COOLING" in actual["riskFlags"]
    assert actual["riskChecks"][0]["verification"] == "UNVERIFIED"
    assert engine().requires_left_discount(actual) is True
    raw["riskChecks"][0]["sourceIds"] = []
    with pytest.raises(ValueError):
        engine().normalize_industry_cycle(raw, source_aliases={"s1": "source:one"})


def test_new_reports_require_all_five_checks_and_reject_foreign_sources():
    raw = cycle()
    raw["riskChecks"].pop()
    with pytest.raises(ValueError):
        engine().normalize_industry_cycle(
            raw, source_aliases={"s1": "source:one"}, require_checks=True
        )
    raw = cycle()
    raw["evidence"][0]["sourceIds"] = ["private"]
    with pytest.raises(ValueError):
        engine().normalize_industry_cycle(raw, source_aliases={"s1": "source:one"})


def test_new_model_schema_requires_cycle_but_legacy_direction_remains_readable():
    from backend.macro.daily_research import REPORT_SCHEMA
    from test_value_directions import validate

    assert (
        "industryCycle" in REPORT_SCHEMA["$defs"]["ValueDirectionProposal"]["required"]
    )
    assert validate()[0]["industryCycle"]["stage"] == "UNKNOWN"


def test_validated_direction_attaches_industry_cycle_to_its_original_plan():
    from test_value_directions import direction

    raw = direction(industryCycle=cycle())
    actual = validate_current_stage([raw])[0]
    assert actual["industryCycle"]["stage"] == "EARLY_COMMERCIALIZATION"
    assert actual["plans"][0]["industryCycle"] == actual["industryCycle"]
    assert "左侧接飞刀" in actual["plans"][0]["industryRiskWarning"]


def test_collected_research_builds_five_queries_per_real_dynamic_candidate():
    from backend.integrations import daily_sources
    from test_daily_research import NOW

    tasks = daily_sources.industry_cycle_research_tasks(
        {
            "quantitativeScan": {
                "items": [
                    {"discoverySector": "光模块"},
                    {"discoverySector": "光模块"},
                    {"discoverySector": "创新药"},
                ]
            },
            "alphaPublic": {"sectorCatalysts": {"动态新增赛道": ["news:1"]}},
        },
        NOW,
    )
    candidates = {row["industry"]: row for row in tasks["candidates"]}
    assert set(candidates) == {"光模块", "创新药", "动态新增赛道"}
    assert all(len(row["queries"]) == 5 for row in candidates.values())
    assert "机器人" not in str(tasks)
    assert all(
        "2026" in query["query"]
        for row in candidates.values()
        for query in row["queries"]
    )


def test_current_packet_rejects_missing_cycle_even_if_legacy_parser_accepts_it():
    from backend.macro.value_directions import validate_value_directions
    from test_value_directions import NOW
    from test_value_directions import direction, packet

    public = {**packet(), "industryCycleResearch": {"required": True}}
    with pytest.raises(ValueError):
        validate_value_directions([direction()], public, NOW, {"s1": "source:one"})


def test_report_keeps_rejected_candidate_reviews_and_refuses_unreviewed_dynamic_sector():
    from backend.macro.daily_research import validate_report
    from test_daily_research import NOW, report

    public = {
        "asOf": NOW.isoformat(),
        "directions": [{"id": "a-share:broad"}],
        "industryCycleResearch": {"required": True},
        "discoveryUniverse": [{"discoverySector": "动态待观察行业"}],
    }
    with pytest.raises(ValueError, match="unreviewed industry"):
        validate_report(
            {**report(), "valueDirections": []}, public, {}, completed_at=NOW
        )
    raw = {
        **report(),
        "valueDirections": [],
        "industryCycleReviews": [
            {"industry": "动态待观察行业", "sourceIds": [], "riskChecks": checks()}
        ],
    }
    actual = validate_report(raw, public, {}, completed_at=NOW)
    assert actual["industryCycleReviews"][0]["industry"] == "动态待观察行业"
    assert actual["industryCycleReviews"][0]["riskChecks"][0]["status"] == "NOT_FOUND"
    assert actual["valueDirections"] == []


def test_unchanged_discounted_plan_is_not_misreported_as_a_price_upgrade():
    from datetime import timedelta
    from backend.indicators.actionable_zones import compute_actionable_price_plan
    from test_value_directions import NOW, direction, packet

    rows = [
        {
            "date": (NOW.date() - timedelta(days=201 - index)).isoformat(),
            "close": 1.0 if index < 200 else 0.9,
        }
        for index in range(201)
    ]
    anchor = compute_actionable_price_plan(
        rows,
        subject_kind="nav",
        subject_id="005112",
        price_basis="UNIT_NAV",
        source_uri="https://fund.eastmoney.com/005112.html",
        pe_percentile=35,
        unit="CNY/unit",
    )
    anchor["historyQuality"] = "READY"
    public = packet()
    public["discoveryActionableZones"] = [anchor]
    raw = direction(industryCycle=cycle())
    first = validate_current_stage([raw], public)[0]
    public["previousValueDirections"] = [first]
    second = validate_current_stage([raw], public)[0]
    assert second["plans"][0]["tracks"] == first["plans"][0]["tracks"]
    assert second["plans"][0]["priceRevisionStatus"] == "MAINTAINED"


def test_found_sector_review_is_a_risk_floor_for_an_optimistic_direction():
    from backend.macro.value_directions import validate_value_directions
    from test_value_directions import NOW, direction, packet

    aliases = {"s1": "source:one", "s2": "source:two"}
    review_raw = cycle(stage="UNKNOWN", evidence=[], sourceIds=["s2"])
    review_raw["riskChecks"][0].update(
        status="FOUND", sourceIds=["s2"], summary="行业融资笔数回落。"
    )
    review = {
        "industry": "ROBOT",
        **engine().normalize_industry_cycle(review_raw, source_aliases=aliases),
    }
    optimistic = engine().normalize_industry_cycle(
        cycle(stage="UNKNOWN", evidence=[]), source_aliases=aliases
    )
    merged = engine().merge_industry_cycles(optimistic, review)
    assert "FINANCING_COOLING" in merged["riskFlags"]
    assert merged["riskChecks"][0]["status"] == "FOUND"
    assert merged["sourceIds"] == ["source:one", "source:two"]
    public = packet()
    public["discoveryActionableZones"][0]["discoverySector"] = "ROBOT"
    actual = validate_value_directions(
        [direction(industryCycle=cycle(stage="UNKNOWN", evidence=[]))],
        public,
        NOW,
        aliases,
        industry_reviews=[review],
    )[0]
    assert "FINANCING_COOLING" in actual["industryCycle"]["riskFlags"]
    assert actual["sourceIds"] == ["source:one", "source:two"]
    assert "左侧接飞刀" in actual["plans"][0]["industryRiskWarning"]


def test_legacy_missing_discount_anchors_do_not_break_the_following_research_run():
    from test_value_directions import direction, packet

    public = packet()
    raw = direction(industryCycle=cycle())
    first = validate_current_stage([raw], public)[0]
    assert first["plans"][0]["industryRiskAdjustment"]["state"] == "INSUFFICIENT"
    public["previousValueDirections"] = [first]
    second = validate_current_stage([raw], public)[0]
    assert second["plans"][0]["tracks"][0]["entryZoneMin"] is None
    assert second["plans"][0]["entryReady"] is False


def test_new_watch_only_direction_must_link_its_review_without_using_title_keywords():
    from backend.macro.value_directions import validate_value_directions
    from test_value_directions import NOW, direction, packet

    public = packet()
    public["industryCycleResearch"] = {"required": True}
    review = {
        "industry": "已审查观察行业",
        **engine().normalize_industry_cycle(
            cycle(),
            source_aliases={"s1": "source:one"},
            sources=current_stage_sources(),
            now=NOW,
        ),
    }
    raw = direction(industryCycle=cycle(), plans=[], signal="WATCH")
    with pytest.raises(ValueError, match="review link"):
        validate_value_directions(
            [raw],
            public,
            NOW,
            {"s1": "source:one"},
            industry_reviews=[review],
            sources=current_stage_sources(),
        )
    raw["reviewIndustryIds"] = ["已审查观察行业"]
    actual = validate_value_directions(
        [raw],
        public,
        NOW,
        {"s1": "source:one"},
        industry_reviews=[review],
        sources=current_stage_sources(),
    )[0]
    assert actual["industryCycle"]["riskFlags"] == ["EARLY_COMMERCIALIZATION"]


@pytest.mark.parametrize(
    "quote,day",
    [
        ("公司预计明年投资建厂，计划实现大批量生产。", "2026-10-15"),
        ("本季度净利润同比增长25%，但客户尚无实际回款。", "2026-10-15"),
        ("本季度净利润同比增长25%，客户订单已实际回款2亿元。", "2020-10-15"),
        ("本季度净利润同比增长25%，客户订单已实际回款2亿元。", None),
    ],
)
def test_mislabelled_forward_plan_unpaid_orders_and_old_disclosures_never_become_green(
    quote, day
):
    from test_daily_research import NOW

    raw = cycle(
        stage="EARNINGS_DELIVERY",
        evidence=[
            {
                "kind": "PROFIT_GROWTH",
                "summary": "模型声称利润增长。",
                "sourceIds": ["s1"],
            },
            {
                "kind": "CASH_COLLECTION",
                "summary": "模型声称已经回款。",
                "sourceIds": ["s1"],
            },
        ],
    )
    actual = engine().normalize_industry_cycle(
        raw,
        source_aliases={"s1": "source:one"},
        sources={
            "source:one": {
                "verification": "SOURCE_CHECKED",
                "publishedDate": day,
                "quote": quote,
            },
        },
        now=NOW,
    )
    assert actual["stage"] == "UNKNOWN"
    assert actual["earningsVerified"] is False
    assert "CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING" in actual["evidenceGaps"]


def test_old_negative_article_stays_historical_and_cannot_be_confirmed():
    from test_daily_research import NOW

    raw = cycle(stage="UNKNOWN", evidence=[])
    raw["riskChecks"][0].update(
        status="FOUND", sourceIds=["s1"], summary="六年前融资降温。"
    )
    actual = engine().normalize_industry_cycle(
        raw,
        source_aliases={"s1": "source:one"},
        sources={
            "source:one": {
                "verification": "SOURCE_CHECKED",
                "publishedDate": "2020-01-01",
                "quote": "当年融资减少。",
            },
        },
        now=NOW,
    )
    assert "FINANCING_COOLING" in actual["suspectedRiskFlags"]
    assert actual["riskChecks"][0]["status"] == "FOUND"
    assert actual["riskChecks"][0]["claimSupported"] is False
    assert actual["riskChecks"][0]["strength"] == "SUSPECTED"
    assert actual["riskChecks"][0]["timeVerification"] == "HISTORICAL"
    assert actual["leftDiscountScale"] == 0.5


def test_historical_early_commercialization_evidence_cannot_lock_the_industry_in_early_stage_forever():
    from test_daily_research import NOW

    actual = engine().normalize_industry_cycle(
        cycle(),
        source_aliases={"s1": "source:one"},
        sources={
            "source:one": {
                "verification": "SOURCE_CHECKED",
                "publishedDate": "2020-01-01",
                "quote": "该行业仍处试制验证阶段。",
            },
        },
        now=NOW,
    )
    assert actual["stage"] == "UNKNOWN"
    assert engine().requires_left_discount(actual) is False


def test_public_scan_projection_preserves_server_discount_base_relative_strength_and_cycle():
    from datetime import timedelta
    from backend.indicators.actionable_zones import (
        compute_actionable_price_plan,
        apply_industry_cycle,
    )
    from backend.macro.daily_research import _public_actionable_plan
    from backend.investment.quantitative_scan import public_quantitative_scan
    from test_value_directions import NOW

    closes = [1 + i / 500 for i in range(140)] + [1.12] * 80
    rows = [
        {"date": (NOW.date() - timedelta(days=220 - i)).isoformat(), "close": close}
        for i, close in enumerate(closes)
    ]
    benchmark = [{**row, "close": 1.0} for row in rows]
    original = compute_actionable_price_plan(
        rows,
        subject_kind="nav",
        subject_id="005112",
        price_basis="UNIT_NAV",
        unit="CNY/unit",
        source_uri="https://fund.eastmoney.com/005112.html",
        benchmark_points=benchmark,
        pe_percentile=35,
        market="FUND_CN",
    )
    original.update(
        fundCode="005112",
        historyQuality="READY",
        signals=[
            {
                "track": "LEFT_VALUE",
                "triggerState": "CANDIDATE",
                "candidate": True,
                "entryReady": False,
                "inEntryZone": False,
                "distanceToEntryPct": 2.0268,
            }
        ],
    )
    original["tracks"] = original["tracks"][:1]
    projected = _public_actionable_plan(original, "005112", NOW, subject_kind="nav")
    assert projected["industryAdjustmentBase"]["close"] == "1.12"
    assert projected["relativeStrength"]["excessReturn20d"] == 0
    cycle_value = engine().normalize_industry_cycle(
        cycle(),
        source_aliases={"s1": "source:one"},
        sources=current_stage_sources(),
        now=NOW,
    )
    scan = public_quantitative_scan(
        {
            "methodVersion": "fund-quant-scan-v2",
            "minimumHistorySamples": 120,
            "asOf": NOW.isoformat(),
            "coverage": {},
            "items": [apply_industry_cycle(original, cycle_value)],
        },
        NOW,
    )
    result = apply_industry_cycle(scan["items"][0], cycle_value)
    assert result["industryRiskAdjustment"]["state"] == "APPLIED"
    assert result["industryCycle"]["riskFlags"] == ["EARLY_COMMERCIALIZATION"]
    assert (
        result["tracks"][0]["entryZoneMin"]
        == scan["items"][0]["tracks"][0]["entryZoneMin"]
    )
