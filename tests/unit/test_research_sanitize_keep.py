"""Usable model research survives local content defects and retains fund prices."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.macro.daily_research import validate_report, report_schema_for_packet
from backend.macro.research_acceptance import validate_report_outcome
from test_phase2_research import anchor

NOW = datetime(2026, 11, 10, 7, 8, tzinfo=ZoneInfo("Asia/Shanghai"))


def packet():
    price = anchor()
    price.update(
        asOf="2026-11-09",
        fundCode="008281",
        requiredReviewIndustryIds=["SEMICONDUCTOR_AI", "MEMORY"],
    )
    return {
        "asOf": "2026-11-10T07:01:08+08:00",
        "discoveryActionableZones": [price],
        "industryCycleResearch": {
            "required": True,
            "allowedReviewIndustryIds": ["BROAD"],
        },
    }


def report():
    return {
        "summary": "AI硬件需求改善，芯片与存储值得跟踪。",
        "sources": [
            {
                "id": "news",
                "title": "硬件资讯",
                "quote": "产业资讯",
                "url": "https://example.org/news",
                "publishedDate": "日期写错了",
            }
        ],
        "events": [
            {
                "title": "存储景气改善",
                "summary": "保持模型完整观点。",
                "sourceIds": ["news", "missing"],
            }
        ],
        "upcoming": [
            {"title": "产业活动", "scheduledDate": "坏日期", "sourceIds": ["missing"]}
        ],
        "valueDirections": [
            {"key": "first", "title": "已有方向", "thesis": "完整保留已有观点。"},
            {"key": "second", "title": "第二方向", "thesis": "完整保留已有观点。"},
            {"key": "third", "title": "第三方向", "thesis": "完整保留已有观点。"},
            {
                "key": "ai-hardware",
                "title": "AI硬件",
                "thesis": "算力芯片、存储景气改善。",
                "signal": "LEFT_VALUE",
                "reviewIndustryIds": ["SEMICONDUCTOR_AI", "MEMORY"],
                "sourceIds": ["news", "unknown"],
                "plans": [
                    {
                        "subjectKind": "nav",
                        "subjectId": "008281",
                        "track": "LEFT_VALUE",
                        "currentPrice": "999",
                        "entryZoneMin": "998",
                    },
                    "broken",
                ],
            },
        ],
        "topicCatalog": [
            {
                "key": f"topic-{number}",
                "title": f"机会{number}",
                "thesis": "完整主题观点",
                "industryClaims": ["MEMORY"],
            }
            for number in range(12)
        ],
    }


def test_fourth_ai_direction_and_its_real_price_plan_survive_local_defects():
    original = report()
    actual = validate_report(
        original, packet(), {}, completed_at=NOW, research_id="run"
    )
    assert original == report()
    assert actual["executionStatus"] == "COMPLETED"
    assert len(actual["valueDirections"]) == 4
    direction = actual["valueDirections"][3]
    assert direction["reviewIndustryIds"] == ["SEMICONDUCTOR_AI", "MEMORY"]
    assert direction["auditStatus"] == "UNAUDITED_SCOPE"
    assert direction["signal"] == "LEFT_VALUE"
    assert len(direction["plans"]) == 1
    assert direction["plans"][0]["currentPrice"] == "1.2500"
    assert direction["plans"][0]["tracks"][0]["entryZoneMin"] == "1.1000"
    assert len(actual["topicCatalog"]) == 12
    assert actual["events"][0]["summary"] == "保持模型完整观点。"
    assert actual["upcoming"][0]["scheduledDate"] is None
    assert "missing" not in str(actual["events"][0]["sourceIds"])
    assert actual["validationWarnings"]


def test_event_questions_keep_only_current_subject_and_preserve_raw_report():
    raw = report()
    event = raw["events"][0]
    event.update(
        title="中东运输风险与黄金、油气价格分化",
        unknowns=["英国对俄石油制裁范围未知；法国网络攻击仍在调查"],
        openQuestions=["商船能否恢复通航；原油供应是否持续受压；黄金需求能否维持"],
    )
    actual = validate_report(raw, packet(), {}, completed_at=NOW)
    assert actual["events"][0]["displayQuestions"] == ["商船能否恢复通航", "原油供应是否持续受压"]
    assert "法国" in event["unknowns"][0]


def test_unknown_industry_maps_to_broad_and_keeps_original_insight():
    raw = report()
    raw["valueDirections"][3]["reviewIndustryIds"] = ["NEW_QUANTUM_MEMORY"]
    actual = validate_report(raw, packet(), {}, completed_at=NOW)
    direction = actual["valueDirections"][3]
    assert "BROAD" in direction["reviewIndustryIds"]
    assert direction["originalReviewIndustryIds"] == ["NEW_QUANTUM_MEMORY"]
    assert direction["plans"]
    assert any(
        row["code"] == "INDUSTRY_NORMALIZED" for row in actual["validationWarnings"]
    )


def test_parseable_object_always_has_publishable_acceptance_payload():
    for raw in ({}, {"events": [None, "broken"]}, report()):
        outcome = validate_report_outcome(
            raw, packet(), {}, research_id="run", completed_at=NOW
        )
        assert outcome.decision == "COMPLETE"
        assert outcome.formal_payload is not None
        assert outcome.formal_payload["executionStatus"] == "COMPLETED"


def test_outbound_schema_does_not_limit_industries_to_frozen_subset():
    schema = report_schema_for_packet(packet())
    field = schema["$defs"]["ValueDirectionProposal"]["properties"]["reviewIndustryIds"]
    assert "enum" not in field["items"]


@pytest.mark.parametrize("bad", [None, [], {}, 0, True])
def test_broken_optional_scalar_fields_never_reject_healthy_siblings(bad):
    raw = report()
    raw["valueDirections"][3]["signal"] = bad
    raw["valueDirections"][3]["plans"][0]["track"] = bad
    raw["valueDirections"][3]["plans"][0]["priceRevisionStatus"] = bad
    raw["events"][0].update(phase=bad, importance=bad)
    raw["coverage"] = [{"status": bad, "summary": "仍保留研究"}]
    result = validate_report(raw, packet(), {}, completed_at=NOW)
    assert len(result["valueDirections"]) == 4
    assert result["valueDirections"][3]["plans"]


def test_json_array_is_retained_as_discovery_items():
    result = validate_report(
        '[{"title":"新发现","thesis":"完整保留"}]', packet(), {}, completed_at=NOW
    )
    assert result["executionStatus"] == "COMPLETED"
    assert result["topicCatalog"][0]["thesis"] == "完整保留"


def test_batch_publishes_complete_fourth_direction_and_all_twelve_topics():
    from backend.macro.research_batch import evaluate_research_batch
    from backend.macro.research_topic_repository import prepare_topic_registry

    result = evaluate_research_batch(
        report(), packet(), {}, research_id="run", validated_at=NOW
    )
    assert result["autoPublishAllowed"] is True
    assert result["formalReport"]["valueDirections"][3]["plans"]
    assert len(result["formalReport"]["researchTopics"]) == 12
    assert len(result["registry"]["items"]) == 16
    assert prepare_topic_registry(result["registry"], "run", NOW)


def test_preserved_fund_plans_still_carry_existing_industry_risk():
    from tests.unit.test_research_cost_review75 import NOW as FROZEN_NOW, prepared

    public, raw, checks = prepared()
    review = next(
        row for row in raw["industryCycleReviews"] if row["industry"] == "NONFERROUS"
    )
    review["riskChecks"][0].update(
        status="FOUND", summary="存在融资降温风险，观点保留。"
    )
    actual = validate_report(raw, public, checks, completed_at=FROZEN_NOW)
    direction = actual["valueDirections"][0]
    assert direction["plans"]
    assert any(
        row["status"] == "FOUND" for row in direction["industryCycle"]["riskChecks"]
    )


def test_verified_source_requires_a_current_matching_proof():
    raw = report()
    checks = {
        "news": {
            "verification": "SOURCE_CHECKED",
            "quoteMatched": True,
            "url": raw["sources"][0]["url"],
            "contentHash": "a" * 64,
            "retrievedAt": "2099-01-01T00:00:00+08:00",
        }
    }
    actual = validate_report(raw, packet(), checks, completed_at=NOW)
    assert actual["sources"][0]["verification"] == "UNVERIFIED"


@pytest.mark.parametrize(
    "summary,query", [("", ""), ("   ", "  "), ([], {}), (None, None)]
)
def test_empty_or_malformed_cycle_notes_are_repaired_locally(summary, query):
    raw = report()
    raw["valueDirections"][3]["industryCycle"] = {
        "riskChecks": [
            {
                "kind": "FINANCING_COOLING",
                "status": "FOUND",
                "query": query,
                "summary": summary,
                "sourceIds": ["news"] * 25,
            }
        ]
    }
    actual = validate_report(raw, packet(), {}, completed_at=NOW)
    assert actual["executionStatus"] == "COMPLETED"
    assert actual["valueDirections"][3]["plans"]
    assert actual["valueDirections"][3]["industryCycle"]["riskChecks"]


def test_direction_industry_alias_fields_and_structured_fund_mentions_survive():
    raw = report()
    raw["valueDirections"][3].pop("reviewIndustryIds")
    raw["valueDirections"][3].update(
        relatedIndustryIds=["MEMORY"], industryCodes=["SEMICONDUCTOR_AI"]
    )
    raw["mentionedFunds"] = [
        {"fundCode": "008281", "name": "芯片联接"},
        {"fundCode": "012345", "name": "新提及基金"},
    ]
    actual = validate_report(raw, packet(), {}, completed_at=NOW)
    assert actual["valueDirections"][3]["reviewIndustryIds"] == [
        "MEMORY",
        "SEMICONDUCTOR_AI",
    ]
    assert {row["fundCode"] for row in actual["mentionedFunds"]} == {"008281", "012345"}
    assert (
        next(row for row in actual["mentionedFunds"] if row["fundCode"] == "012345")[
            "label"
        ]
        == "新提及基金"
    )


def test_invalid_unicode_and_non_finite_numbers_are_repaired_in_every_published_field():
    import json

    raw = report()
    unsafe = "完整观点" + chr(0) + chr(0xD800)
    raw["summary"] = unsafe
    raw["valueDirections"][3]["reviewIndustryIds"] = [unsafe]
    raw["valueDirections"][3]["industryCycle"] = {
        "stage": "not-a-stage",
        "evidence": [{"summary": unsafe, "numericDetail": float("nan")}],
        "riskChecks": [],
    }
    actual = validate_report(raw, packet(), {}, completed_at=NOW)
    encoded = json.dumps(actual, ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert encoded
    assert chr(0) not in actual["summary"]
    assert chr(0xFFFD) in actual["summary"]
    assert raw["summary"] == unsafe
    assert any(
        row["code"] == "TEXT_ENCODING_REPAIRED" for row in actual["validationWarnings"]
    )
    assert any(
        row["code"] == "NON_FINITE_NUMBER_CLEARED"
        for row in actual["validationWarnings"]
    )


def test_explicit_fund_code_wins_identity_conflict_without_borrowing_another_funds_price():
    raw = report()
    raw["valueDirections"][3]["plans"] = [
        {
            "subjectKind": "nav",
            "subjectId": "008281",
            "fundCode": "012345",
            "track": "LEFT_VALUE",
        }
    ]
    actual = validate_report(raw, packet(), {}, completed_at=NOW)
    plan = actual["valueDirections"][3]["plans"][0]
    assert plan["fundCode"] == plan["subjectId"] == "012345"
    assert plan["currentPrice"] is None
    assert any(
        row["code"] == "FUND_IDENTITY_RECONCILED"
        for row in actual["validationWarnings"]
    )
