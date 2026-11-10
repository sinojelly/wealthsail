"""Model revisions must select real same-subject program anchors."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta

import pytest

from backend.macro import alpha_research as alpha

NOW = datetime(2026, 10, 18, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_public_packet_keeps_broad_news_after_fortieth_and_partial_coverage():
    rows = [
        {
            "id": f"cls:{number}",
            "title": f"消费与医药资讯{number}",
            "asOf": "2026-10-18",
            "retrievedAt": NOW.isoformat(),
            "sourceUri": "https://www.cls.cn/telegraph",
        }
        for number in range(151)
    ]
    bundle = {
        "catalysts": rows,
        "sourceResults": {
            "CLS_TELEGRAPH": {
                "status": "PARTIAL",
                "coverage": {
                    "scannedCount": 180,
                    "retainedCount": 151,
                    "pages": 9,
                    "stopReason": "HTTP_ERROR",
                    "truncated": True,
                    "private": "omit",
                },
            }
        },
    }
    public = alpha.extend_public_packet(bundle, [], [], [], {}, NOW)["alphaPublic"]
    assert len(public["catalysts"]) == 151
    assert public["sourceResults"]["CLS_TELEGRAPH"]["coverage"]["scannedCount"] == 180
    assert "private" not in public["sourceResults"]["CLS_TELEGRAPH"]["coverage"]


def test_public_packet_projects_industry_catalysts_without_upgrading_media_facts():
    bundle = {
        "catalysts": [
            {
                "id": "cls:123",
                "title": "盛和资源整合",
                "content": "媒体报道整合计划。",
                "asOf": "2026-10-18",
                "retrievedAt": NOW.isoformat(),
                "sourceUri": "https://www.cls.cn/detail/123",
                "factVerification": "UNVERIFIED",
                "sectorBuckets": ["RARE_EARTH_METALS", "PRIVATE"],
                "researchPriority": "CORE_CATALYST",
                "private": "omit",
            }
        ]
    }
    public = alpha.extend_public_packet(bundle, [], [], [], {}, NOW)["alphaPublic"]
    assert public["catalysts"][0]["sectorBuckets"] == ["RARE_EARTH_METALS"]
    assert public["catalysts"][0]["researchPriority"] == "CORE_CATALYST"
    assert public["catalysts"][0]["factVerification"] == "UNVERIFIED"
    assert public["sectorCatalysts"]["RARE_EARTH_METALS"] == ["cls:123"]
    assert "PRIVATE" not in str(public)


def test_public_packet_keeps_later_catalysts_in_bounded_multi_industry_calendar_text():
    content = (
        "本周行业日程："
        + "已公布活动说明。" * 90
        + "AI技术大会与具身智能比赛将举行，金融会议将发布政策安排。"
    )
    assert content.index("AI技术大会") > 400
    bundle = {
        "catalysts": [
            {
                "id": "cls:calendar",
                "title": "本周资本市场大事提醒",
                "content": content,
                "asOf": "2026-10-18",
                "retrievedAt": NOW.isoformat(),
                "sourceUri": "https://www.cls.cn/detail/123",
                "sectorBuckets": [
                    "AI_COMPUTE_SEMICONDUCTOR",
                    "ROBOTICS_MOBILITY",
                    "FINANCE_POLICY",
                ],
                "factVerification": "UNVERIFIED",
            }
        ]
    }
    public = alpha.extend_public_packet(bundle, [], [], [], {}, NOW)["alphaPublic"]
    assert "AI技术大会与具身智能比赛" in public["catalysts"][0]["content"]
    assert len(public["catalysts"][0]["content"]) <= 1000
    assert all(
        public["sectorCatalysts"][sector] == ["cls:calendar"]
        for sector in bundle["catalysts"][0]["sectorBuckets"]
    )


def anchor():
    return {
        "methodVersion": "actionable-zones-v2",
        "themeCode": "AI_MEMORY_HBM",
        "subjectKind": "nav",
        "subjectId": "008281",
        "label": "芯片联接",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "asOf": "2026-10-16",
        "sourceUri": "https://example.org/nav",
        "currentPrice": "1.2500",
        "indicators": {"ma20": 1.20},
        "tracks": [
            {
                "track": "LEFT_VALUE",
                "state": "CANDIDATE",
                "entryZoneMin": "1.1000",
                "entryZoneMax": "1.2000",
                "stopLossPrice": None,
                "takeProfitT1": "1.4000",
                "takeProfitT2": None,
                "priceState": "PARTIAL",
                "reasons": [],
                "missingEvidence": ["ATR14"],
            }
        ],
    }


def proposal(**changes):
    return {
        "themeCode": "AI_MEMORY_HBM",
        "subjectKind": "nav",
        "subjectId": "008281",
        "track": "LEFT_VALUE",
        "priceRevisionStatus": "INITIAL",
        "priceRevisionNote": "依据实际净值变化观察。",
        "calibrationRuleKeys": [],
        "calibrationNote": "暂无历史样本。",
        **changes,
    }


def test_revision_freezes_real_anchor_and_does_not_accept_client_prices():
    packet = {
        "themeActionableZones": [anchor()],
        "previousActionablePlans": [],
        "strategyMemoryRules": [],
    }
    original = deepcopy(packet)
    result = alpha.validate_actionable_plans([proposal()], packet, NOW)
    assert result[0]["tracks"][0]["entryZoneMin"] == "1.1000"
    assert result[0]["selectedTrack"] == "LEFT_VALUE"
    assert result[0]["priceBasis"] == "UNIT_NAV"
    assert packet == original
    with pytest.raises(ValueError):
        alpha.validate_actionable_plans([proposal(entryZoneMin="0.01")], packet, NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"subjectId": "another"},
        {"themeCode": "PRIVATE_TOPIC"},
        {"track": "NOT_A_TRACK"},
        {"calibrationRuleKeys": ["unknown"]},
        {"priceRevisionStatus": "TAKE_PROFIT_ALERT"},
    ],
)
def test_unknown_identity_calibration_or_unproven_take_profit_is_rejected(changes):
    with pytest.raises(ValueError):
        alpha.validate_actionable_plans(
            [proposal(**changes)], {"themeActionableZones": [anchor()]}, NOW
        )


def test_take_profit_needs_same_basis_previous_target_and_observed_price():
    old = anchor()
    old.update(selectedTrack="LEFT_VALUE")
    current = anchor()
    packet = {"themeActionableZones": [current], "previousActionablePlans": [old]}
    with pytest.raises(ValueError):
        alpha.validate_actionable_plans(
            [proposal(priceRevisionStatus="TAKE_PROFIT_ALERT")], packet, NOW
        )
    current["currentPrice"] = "1.4500"
    actual = alpha.validate_actionable_plans(
        [proposal(priceRevisionStatus="TAKE_PROFIT_ALERT")], packet, NOW
    )
    assert actual[0]["priceRevisionStatus"] == "TAKE_PROFIT_ALERT"
    old["priceBasis"] = "INDEX_POINTS"
    with pytest.raises(ValueError):
        alpha.validate_actionable_plans(
            [proposal(priceRevisionStatus="TAKE_PROFIT_ALERT")], packet, NOW
        )


def test_daily_validator_accepts_enriched_structured_plan_and_macro_cycle():
    from test_daily_research import report
    from backend.macro.daily_research import validate_report

    raw = report()
    raw.update(macroCycle="增长分化，信用传导待核查。", actionablePlans=[proposal()])
    packet = {
        "asOf": NOW.isoformat(),
        "directions": [{"id": "a-share:broad"}],
        "themeActionableZones": [anchor()],
    }
    actual = validate_report(raw, packet, {}, completed_at=NOW)
    assert actual["actionablePlans"][0]["currentPrice"] == "1.2500"
    assert actual["macroCycle"] == "增长分化，信用传导待核查。"


def test_public_packet_retains_latest_history_for_every_code_and_definition_proof():
    rows = []
    for code in (
        "CN_T10",
        "HOUSE_HAIKOU_NEW_MOM",
        "HOUSE_SHANGHAI_NEW_MOM",
        "M1_M2_GAP",
        "M1_YOY",
        "M2_YOY",
        "SHIBOR_7D",
        "TSF_STOCK_YOY",
    ):
        for offset in range(120):
            rows.append(
                {
                    "code": code,
                    "referenceDate": (
                        NOW.date() - timedelta(days=120 - offset)
                    ).isoformat(),
                    "value": "1",
                    "sourceUri": "https://data.eastmoney.com/data",
                    "retrievedAt": NOW.isoformat(),
                    "availableAt": NOW.isoformat(),
                    "metadata": {
                        "definitionVersion": "EASTMONEY_REPORTED:2025_ONWARD",
                        "definitionNote": "M1统计口径修订。",
                        "components": {"M1_YOY": "1", "M2_YOY": "2"},
                        "calculation": "M1_YOY-M2_YOY",
                        "originalIndex": "101",
                        "indexBasis": "PREVIOUS_MONTH_100",
                        "transformation": "originalIndex-100",
                        "privateNote": "PRIVATE",
                    },
                }
            )
    rows.append({"code": "M1_YOY", "referenceDate": "2026-10-20", "value": "999"})
    packet = alpha.extend_public_packet({"observations": rows}, [], [], [], {}, NOW)
    actual = packet["alphaPublic"]["observations"]
    assert {row["code"] for row in actual} == {row["code"] for row in rows}
    assert all(
        sum(row["code"] == code for row in actual) <= 24
        for code in {row["code"] for row in rows}
    )
    assert all(row["referenceDate"] <= "2026-10-18" for row in actual)
    assert actual[0]["metadata"]["definitionNote"] == "M1统计口径修订。"
    assert actual[0]["metadata"]["components"] == {"M1_YOY": "1", "M2_YOY": "2"}
    assert actual[0]["metadata"]["originalIndex"] == "101"
    assert "PRIVATE" not in str(packet)


@pytest.mark.parametrize(
    "changes",
    [
        {"currentPrice": "NaN"},
        {"currentPrice": True},
        {"asOf": "2026-10-19"},
        {"retrievedAt": "2026-10-20T08:30:00+08:00"},
        {"unit": "USD"},
        {"sourceUri": "https://private.local/nav"},
    ],
)
def test_invalid_program_anchor_cannot_become_a_model_plan(changes):
    current = {**anchor(), **changes}
    with pytest.raises(ValueError):
        alpha.validate_actionable_plans(
            [proposal()], {"themeActionableZones": [current]}, NOW
        )


@pytest.mark.parametrize(
    "change",
    [
        {"entryZoneMin": "NaN"},
        {"entryZoneMin": "2"},
        {"stopLossPrice": "1.5"},
        {"takeProfitT1": "-1"},
    ],
)
def test_invalid_program_track_cannot_be_selected(change):
    current = anchor()
    current["tracks"][0].update(change)
    with pytest.raises(ValueError):
        alpha.validate_actionable_plans(
            [proposal()], {"themeActionableZones": [current]}, NOW
        )


def test_take_profit_requires_nonfuture_previous_date_and_finite_original_target():
    old = anchor()
    old.update(selectedTrack="LEFT_VALUE", asOf="2026-10-17")
    current = anchor()
    current["currentPrice"] = "1.45"
    packet = {"themeActionableZones": [current], "previousActionablePlans": [old]}
    with pytest.raises(ValueError):
        alpha.validate_actionable_plans(
            [proposal(priceRevisionStatus="TAKE_PROFIT_ALERT")], packet, NOW
        )
    old["asOf"] = "2026-10-16"
    old["tracks"][0]["takeProfitT1"] = "-Infinity"
    with pytest.raises(ValueError):
        alpha.validate_actionable_plans(
            [proposal(priceRevisionStatus="TAKE_PROFIT_ALERT")], packet, NOW
        )


def test_phase2_missing_noncore_fields_are_defaulted_without_losing_research():
    from test_daily_research import report
    from backend.macro.daily_research import validate_report

    result = validate_report(
            report(),
            {
                "asOf": NOW.isoformat(),
                "directions": [{"id": "a-share:broad"}],
                "themeActionableZones": [],
            },
            {},
            completed_at=NOW,
        )
    assert result["executionStatus"] == "COMPLETED"
    assert result["summary"] == report()["summary"]
    assert result["actionablePlans"] == []
