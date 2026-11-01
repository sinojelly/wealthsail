"""Public daily research never delegates fact authority to model output."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from importlib import import_module, util
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 16, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai"))


def engine():
    assert util.find_spec("backend.macro.daily_research"), (
        "daily research contract missing"
    )
    return import_module("backend.macro.daily_research")


def report():
    return {
        "summary": "本次公开研究保留条件与反方。",
        "coverage": [
            {
                "topic": "ENERGY_SHIPPING",
                "status": "UNVERIFIED",
                "summary": "实际交付仍待核查。",
            }
        ],
        "sources": [
            {
                "id": "s1",
                "url": "https://ofac.treasury.gov/recent-actions/example",
                "title": "政策公告",
                "quote": "Transactions are authorized.",
                "publishedDate": "2026-10-15",
                "publishedAt": None,
            }
        ],
        "events": [
            {
                "title": "政策许可已公布",
                "summary": "许可不等于已交付。",
                "phase": "ANNOUNCED",
                "importance": "HIGH",
                "sourceIds": ["s1"],
                "channels": ["供应与运输成本可能变化。"],
                "affectedDirectionIds": ["a-share:broad"],
                "scenarios": [
                    {
                        "condition": "实际供给增加",
                        "impact": "成本压力可能缓解。",
                        "counter": "运输瓶颈可能抵消。",
                    }
                ],
                "watchFor": ["实际交付"],
                "unknowns": ["实际数量"],
                "marketReaction": "UNVERIFIED",
            }
        ],
        "upcoming": [],
        "energy": [],
    }


def test_strict_report_contract_rejects_unrecognized_execution_and_private_fields():
    module = engine()
    assert module.REPORT_SCHEMA["type"] == "object"
    for key in ("executionOrder", "tenantId"):
        raw = deepcopy(report())
        raw["events"][0][key] = "buy"
        with pytest.raises(ValueError):
            module.validate_report(
                raw,
                {"asOf": NOW.isoformat(), "directions": [{"id": "a-share:broad"}]},
                {},
                completed_at=NOW,
            )


def packet():
    return engine().make_research_packet(
        {
            "items": [
                {
                    "code": "PMI",
                    "label": "制造业PMI",
                    "state": "READY",
                    "value": Decimal("50.2"),
                    "unit": "",
                    "referencePeriod": "2026-09",
                    "sourceUri": "https://www.stats.gov.cn/release",
                    "retrievedAt": NOW.isoformat(),
                    "valueVerification": "VERIFIED",
                    "tenantId": "PRIVATE",
                    "nextRelease": {
                        "status": "SCHEDULED",
                        "referencePeriod": "2026-10",
                        "scheduledAt": (NOW + timedelta(days=15)).isoformat(),
                        "sourceUri": "https://www.stats.gov.cn/calendar",
                    },
                }
            ],
            "portfolio": {"secret": "PRIVATE"},
        },
        [
            {
                "code": "US_T10_REAL",
                "state": "READY",
                "value": 1.7,
                "frequency": "DAILY",
                "unit": "%",
                "asOf": "2026-10-15",
                "retrievedAt": NOW.isoformat(),
                "metadata": {"password": "PRIVATE"},
            }
        ],
        [
            {
                "indexId": "a500",
                "label": "中证A500",
                "state": "READY",
                "asOf": "2026-10-15",
                "retrievedAt": NOW.isoformat(),
                "sourceUri": "https://www.csindex.com.cn/index",
                "historyQuality": "CONFLICT",
                "trend": {
                    "state": "READY",
                    "ma200": 100,
                    "distanceMA200": -0.05,
                    "momentum63": -0.02,
                },
                "trendWindow": {
                    "start": "2025-10-01",
                    "end": "2026-10-15",
                    "sampleCount": 250,
                },
                "valuation": {
                    "state": "PARTIAL",
                    "pe": {
                        "state": "INSUFFICIENT",
                        "latest": 12.8,
                        "percentile": None,
                        "sampleCount": 500,
                        "private": "PRIVATE",
                    },
                },
                "risk": {"state": "PARTIAL", "maxDrawdown": None},
                "instrumentId": "PRIVATE",
            }
        ],
        [
            {
                "id": "a-share:broad",
                "label": "A股宽基",
                "market_id": "a-share",
                "userId": "PRIVATE",
            }
        ],
        now=NOW,
        previous={
            "report": {
                "events": [
                    {
                        "watchFor": ["后续订单"],
                        "unknowns": ["盈利变化"],
                        "private": "PRIVATE",
                    }
                ]
            }
        },
        external={
            "documents": [
                {
                    "id": "official:1",
                    "url": "https://www.stats.gov.cn/facts",
                    "title": "公开公告",
                    "quote": "short",
                    "retrievedAt": NOW.isoformat(),
                    "publishedDate": "2026-10-15",
                    "verification": "SOURCE_CHECKED",
                    "private": "PRIVATE",
                }
            ],
            "energy": [],
            "sourceResults": {
                "x": {
                    "status": "FAILED",
                    "errorClass": "HTTPStatusError",
                    "error": "PRIVATE",
                }
            },
        },
    )


def test_packet_whitelists_public_facts_without_discarding_current_partial_research():
    result = packet()
    import json

    assert "PRIVATE" not in json.dumps(result)
    assert result["macros"][0]["value"] == 50.2
    assert result["indices"][0]["trend"]["ma200"] == 100
    assert result["indices"][0]["historyQuality"] == "CONFLICT"
    assert result["indices"][0]["valuation"]["pe"]["latest"] == 12.8
    assert result["schedules"][0]["status"] == "SCHEDULED"
    assert result["previousQuestions"] == ["后续订单", "盈利变化"]
    prompt = engine().build_prompt(result)
    assert "PRIVATE" not in prompt
    assert "最近24小时" in prompt and "14天" in prompt
    assert "SOURCE_CHECKED" in prompt and "08:30" not in prompt


def test_prompt_prioritizes_media_and_quantitative_dual_engine_without_active_fund_veto():
    prompt = engine().build_prompt(packet())
    assert "6至8" in prompt
    assert "quantitativeScan" in prompt and "quantitativeFundCodes" in prompt
    assert "核心一等催化线索" in prompt
    assert "严禁因未二次抓取官网原文而丢弃或降权" in prompt
    assert "sourceIds=[]" in prompt
    assert "主动基金最新高仓位未核验时只作候选" not in prompt
    assert "自身真实净值" in prompt


def test_prompt_selects_available_fund_tracks_without_dropping_watch_candidates():
    prompt = engine().build_prompt(packet())
    assert "可选价格轨道取quantitativeScan.items[].signals" in prompt
    assert "WATCH也选择其中已有完整价格的轨道" in prompt
    assert "某轨因PB等依据不足不可用时，保留该基金并选择另一有效轨道" in prompt
    assert "技术state与当前inEntryZone分开表述" in prompt


def test_prompt_prioritizes_current_cross_industry_catalysts_before_extra_technical_slots():
    prompt = engine().build_prompt(packet())
    assert engine().PROMPT_VERSION == "daily-research-industry-risk-strength-v15"
    assert "sources总预算最多24条" in prompt
    assert "先跨行业覆盖本次输入中真实存在的核心催化" in prompt
    assert "再分配同链补充来源和纯量化方向" in prompt
    assert "金融重磅会议" in prompt and "券商与银行" in prompt
    assert "黄金与油气" in prompt and "互补的1至2只基金" in prompt
    assert "纯量化与消费补充不挤掉已有核心产业催化" in prompt
    assert "逐主题说明未找到或未核实" not in prompt
    assert "优先综合可用事实与价格发现机会" in prompt


def test_report_source_budget_accepts_24_distinct_sources_and_rejects_a_25th():
    raw = report()
    original = raw["sources"][0]
    raw["sources"] = [
        {
            **original,
            "id": "s1" if i == 0 else f"extra{i}",
            "url": f"https://www.stats.gov.cn/source-{i}",
        }
        for i in range(24)
    ]
    assert len(validate(raw, source_checks={})["sources"]) == 24
    assert engine().REPORT_SCHEMA["properties"]["sources"]["maxItems"] == 24
    raw["sources"].append(
        {**original, "id": "extra24", "url": "https://www.stats.gov.cn/source-24"}
    )
    with pytest.raises(ValueError):
        validate(raw, source_checks={})


def test_continuation_preserves_an_event_relying_on_the_last_of_24_sources():
    old = report()
    original = old["sources"][0]
    old["sources"] = [
        {**original, "id": f"source{i}", "url": f"https://www.stats.gov.cn/source-{i}"}
        for i in range(24)
    ]
    old["events"][0].update(id="retained-last-source", sourceIds=["source23"])
    public = engine().make_research_packet(
        {},
        [],
        [],
        [],
        now=NOW,
        previous={
            "history": [
                {
                    "status": "READY",
                    "runId": "previous-run",
                    "completedAt": (NOW - timedelta(minutes=5)).isoformat(),
                    "report": old,
                }
            ]
        },
    )
    assert public["previousResearch"][0]["eventId"] == "retained-last-source"
    assert public["previousResearch"][0]["sources"][0]["id"] == "source23"


def test_public_price_projection_keeps_verified_nav_time_and_quality_metadata():
    result = engine()._public_actionable_plan(
        {
            "subjectKind": "nav",
            "subjectId": "320007",
            "asOf": "2026-10-16",
            "priceTimeBasis": "CONFIRMED_UNIT_NAV",
            "historyQuality": "READY",
            "currentPrice": "1.25",
            "private": "omit",
            "tracks": [],
        },
        "320007",
        NOW,
        subject_kind="nav",
    )
    assert result["priceTimeBasis"] == "CONFIRMED_UNIT_NAV"
    assert result["historyQuality"] == "READY"
    assert "private" not in result


@pytest.mark.parametrize("field", ["retrievedAt", "availableAt", "publishedAt"])
def test_packet_cannot_present_future_collected_facts_as_current(field):
    future = {
        "code": "PMI",
        "value": 55,
        "state": "READY",
        "unit": "",
        "referencePeriod": "2026-09",
        field: (NOW + timedelta(seconds=1)).isoformat(),
    }
    result = engine().make_research_packet({"items": [future]}, [], [], [], now=NOW)
    assert result["macros"] == []


def test_packet_does_not_forward_raw_prior_report_or_unknown_source_payload():
    result = engine().make_research_packet(
        {},
        [],
        [],
        [],
        now=NOW,
        previous={
            "report": {
                "events": [{"unknowns": ["short question"], "summary": "secret"}]
            }
        },
        external={
            "documents": [
                {
                    "id": "bad",
                    "url": "http://127.0.0.1/secrets",
                    "retrievedAt": NOW.isoformat(),
                }
            ]
        },
    )
    assert result["documents"] == []
    assert result["previousQuestions"] == ["short question"]


def checks():
    return {
        "s1": {
            "verification": "SOURCE_CHECKED",
            "quoteMatched": True,
            "url": report()["sources"][0]["url"],
            "title": "政策公告",
            "publishedDate": "2026-10-15",
            "publishedAt": None,
            "retrievedAt": NOW.isoformat(),
            "contentHash": "a" * 64,
        }
    }


def validate(raw=None, source_checks=None, public_packet=None):
    return engine().validate_report(
        raw or report(),
        public_packet or packet(),
        checks() if source_checks is None else source_checks,
        completed_at=NOW,
    )


def test_source_check_certifies_quote_only_and_never_implementation_or_market_causality():
    result = validate()
    assert result["sources"][0]["verification"] == "SOURCE_CHECKED"
    assert result["sources"][0]["retrievedAt"] == NOW.isoformat()
    event = result["events"][0]
    assert event["phase"] == "ANNOUNCED"
    assert event["factVerification"] == "UNVERIFIED"
    assert event["reasoningType"] == "MODEL_CONDITIONAL"
    assert event["marketReaction"] == "UNVERIFIED"
    assert event["id"].startswith("event:")
    assert result["asOf"] == NOW.isoformat()


@pytest.mark.parametrize("field", ["events", "upcoming"])
def test_event_references_accept_current_validated_value_direction_keys(field):
    from test_value_directions import (
        quantitative_packet,
        direction,
        NOW as direction_now,
    )

    raw = {
        **report(),
        "macroCycle": "行业与真实净值共同研究。",
        "actionablePlans": [],
        "valueDirections": [direction(macroCodes=[], quantitativeFundCodes=["005112"])],
    }
    public = {
        **quantitative_packet(),
        "directions": [{"id": "a-share:broad"}],
        "discoveryUniverse": [],
    }
    if field == "upcoming":
        raw[field] = [
            {
                "title": "未来行业会议",
                "phase": "PLANNED",
                "scheduledDate": "2026-10-20",
                "scheduledAt": None,
                "sourceIds": ["s1"],
                "affectedDirectionIds": ["healthcare-recovery"],
                "watchFor": [],
            }
        ]
    else:
        raw[field][0]["affectedDirectionIds"] = ["healthcare-recovery"]
    actual = engine().validate_report(raw, public, {}, completed_at=direction_now)
    assert actual[field][0]["affectedDirectionIds"] == ["healthcare-recovery"]
    raw[field][0]["affectedDirectionIds"] = ["nonexistent-current-direction"]
    with pytest.raises(ValueError, match="unknown research direction reference"):
        engine().validate_report(raw, public, {}, completed_at=direction_now)


def test_invalid_current_directions_are_validated_before_event_key_references():
    from test_value_directions import (
        quantitative_packet,
        direction,
        NOW as direction_now,
    )

    raw = {
        **report(),
        "macroCycle": "行业研究",
        "actionablePlans": [],
        "valueDirections": [direction(macroCodes=[], quantitativeFundCodes=["005112"])]
        * 2,
    }
    raw["events"][0]["affectedDirectionIds"] = ["healthcare-recovery"]
    public = {
        **quantitative_packet(),
        "directions": [{"id": "a-share:broad"}],
        "discoveryUniverse": [],
    }
    with pytest.raises(ValueError, match="duplicate value direction"):
        engine().validate_report(raw, public, {}, completed_at=direction_now)


@pytest.mark.parametrize(
    "text",
    [
        "RSI14<38且净值>20日均线时观察趋势。",
        "跌幅<=8%，且净值>MA20后观察右侧。",
        "净值<MA20且目标收益>10%时等待确认。",
    ],
)
def test_plain_financial_comparators_are_not_misread_as_html(text):
    raw = report()
    raw["events"][0]["summary"] = text
    assert validate(raw)["events"][0]["summary"] == text


@pytest.mark.parametrize(
    "text",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<a href='https://example.org'>链接</a>",
        "<!-- injected -->",
        "OFFICIAL_FACT",
    ],
)
def test_actual_html_and_unsupported_claim_markers_remain_rejected(text):
    raw = report()
    raw["events"][0]["summary"] = text
    with pytest.raises(ValueError, match="unsupported research instruction or claim"):
        validate(raw)


@pytest.mark.parametrize(
    "change",
    [
        "unknown_source",
        "unknown_direction",
        "future_pubdate",
        "future_pubtime",
        "private_url",
        "unsafe_html",
        "verified_reaction",
    ],
)
def test_invalid_or_overclaimed_reports_are_rejected(change):
    raw = report()
    if change == "unknown_source":
        raw["events"][0]["sourceIds"] = ["absent"]
    elif change == "unknown_direction":
        raw["events"][0]["affectedDirectionIds"] = ["made-up:energy"]
    elif change == "future_pubdate":
        raw["sources"][0]["publishedDate"] = "2026-10-17"
    elif change == "future_pubtime":
        raw["sources"][0]["publishedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif change == "private_url":
        raw["sources"][0]["url"] = "https://127.0.0.1/secrets"
    elif change == "unsafe_html":
        raw["events"][0]["summary"] = "<script>alert(1)</script>"
    else:
        raw["events"][0]["marketReaction"] = "VERIFIED"
    with pytest.raises(ValueError):
        validate(raw)


@pytest.mark.parametrize(
    "change", ["missing", "unmatched", "future", "url_mismatch", "date_mismatch"]
)
def test_bad_or_missing_source_check_cannot_create_source_checked(change):
    evidence = checks()
    if change == "missing":
        evidence = {}
    elif change == "unmatched":
        evidence["s1"]["quoteMatched"] = False
    elif change == "future":
        evidence["s1"]["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif change == "url_mismatch":
        evidence["s1"]["url"] = "https://www.stats.gov.cn/different"
    else:
        evidence["s1"]["publishedDate"] = "2026-10-14"
    result = validate(source_checks=evidence)
    assert result["sources"][0]["verification"] != "SOURCE_CHECKED"
    assert result["events"][0]["factVerification"] == "UNVERIFIED"


def test_source_and_phase_dedup_and_event_identity_ignore_titles_source_aliases_and_order():
    raw = report()
    raw["sources"].append({**raw["sources"][0], "id": "duplicate"})
    raw["events"].append(
        {
            **deepcopy(raw["events"][0]),
            "title": "同一公告的改写",
            "sourceIds": ["duplicate"],
        }
    )
    first = validate(raw)
    assert len(first["sources"]) == len(first["events"]) == 1
    raw["sources"].reverse()
    raw["events"].reverse()
    second = validate(raw, {**checks(), "duplicate": checks()["s1"]})
    assert first["events"][0]["id"] == second["events"][0]["id"]


def test_energy_values_are_program_observations_not_model_authored_numbers():
    public = packet()
    public["energy"] = [
        {
            "code": "GASDESW",
            "state": "READY",
            "value": 3.7,
            "unit": "美元/加仑",
            "frequency": "WEEKLY",
            "asOf": "2026-10-12",
            "timeBasis": "OBSERVATION_ONLY",
        }
    ]
    raw = report()
    raw["energy"] = ["GASDESW"]
    assert validate(raw, public_packet=public)["energy"] == public["energy"]
    raw["energy"] = ["DCOILBRENTEU"]
    with pytest.raises(ValueError):
        validate(raw, public_packet=public)


def test_planned_event_keeps_future_schedule_separate_from_source_publication():
    raw = report()
    raw["upcoming"] = [
        {
            "title": "官方未来发布计划",
            "phase": "PLANNED",
            "scheduledDate": "2026-10-20",
            "scheduledAt": "2026-10-20T10:00:00+08:00",
            "sourceIds": ["s1"],
            "affectedDirectionIds": ["a-share:broad"],
            "watchFor": ["实际公布后核对"],
        }
    ]
    result = validate(raw)
    assert result["upcoming"][0]["phase"] == "PLANNED"
    assert result["upcoming"][0]["scheduledAt"] == "2026-10-20T10:00:00+08:00"


def test_future_reference_period_and_private_nested_provenance_are_not_public_facts():
    result = engine().make_research_packet(
        {
            "items": [
                {
                    "code": "PMI",
                    "state": "READY",
                    "value": 55,
                    "referencePeriod": "2026-11",
                }
            ]
        },
        [],
        [
            {
                "indexId": "hs300",
                "asOf": "2026-10-15",
                "valuation": {
                    "pe": {
                        "latest": 12,
                        "sourceUri": "postgresql://user:PRIVATE@127.0.0.1/private",
                    }
                },
            }
        ],
        [],
        now=NOW,
    )
    assert result["macros"] == []
    assert "PRIVATE" not in str(result)


def test_boolean_measurement_and_nonfinite_numbers_are_never_presented_as_values():
    result = engine().make_research_packet(
        {
            "items": [
                {
                    "code": "PMI",
                    "state": "READY",
                    "value": True,
                    "statistics": {"previousValue": float("inf")},
                }
            ]
        },
        [],
        [],
        [],
        now=NOW,
    )
    assert result["macros"][0]["value"] is None
    assert result["macros"][0]["statistics"]["previousValue"] is None


def test_duplicate_json_keys_and_naive_or_backdated_completion_are_rejected():
    with pytest.raises(ValueError):
        engine().validate_report(
            '{"summary":"a","summary":"b"}', packet(), {}, completed_at=NOW
        )
    for stamp in (NOW.replace(tzinfo=None), NOW - timedelta(seconds=1)):
        with pytest.raises(ValueError):
            engine().validate_report(report(), packet(), checks(), completed_at=stamp)


def test_first_not_run_dto_with_null_report_can_build_public_packet():
    result = engine().make_research_packet(
        {},
        [],
        [],
        [],
        now=NOW,
        previous={
            "methodVersion": "daily-research-v1",
            "state": "NOT_RUN",
            "report": None,
        },
    )
    assert result["previousQuestions"] == []


def test_checked_source_title_comes_from_document_not_model_and_does_not_certify_event():
    raw = report()
    raw["sources"][0]["title"] = "模型把标题改写为已实际交付"
    result = validate(raw)
    assert result["sources"][0]["title"] == "政策公告"
    assert result["events"][0]["factVerification"] == "UNVERIFIED"


def test_external_failed_sources_keep_only_public_status_not_raw_error():
    result = engine().make_research_packet(
        {},
        [],
        [],
        [],
        now=NOW,
        external={
            "sourceResults": {
                "OFAC": {
                    "status": "FAILED",
                    "errorClass": "HTTPStatusError",
                    "error": "PRIVATE",
                }
            }
        },
    )
    assert result["sourceResults"] == {
        "OFAC": {"status": "FAILED", "errorClass": "HTTPStatusError"}
    }
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize(
    "day,stamp", [("2026-10-15", None), ("2026-10-16", "2026-10-16T08:34:00+08:00")]
)
def test_past_plans_cannot_be_returned_as_upcoming(day, stamp):
    raw = report()
    raw["upcoming"] = [
        {
            "title": "旧发布计划",
            "phase": "PLANNED",
            "scheduledDate": day,
            "scheduledAt": stamp,
            "sourceIds": ["s1"],
            "affectedDirectionIds": [],
            "watchFor": [],
        }
    ]
    with pytest.raises(ValueError):
        validate(raw)


def test_future_reference_month_is_allowed_for_known_schedule_not_for_observation():
    result = engine().make_research_packet(
        {
            "items": [
                {
                    "code": "PMI",
                    "state": "READY",
                    "value": 50.2,
                    "referencePeriod": "2026-09",
                    "nextRelease": {
                        "status": "SCHEDULED",
                        "referencePeriod": "2026-11",
                        "scheduledAt": "2026-11-30T09:30:00+08:00",
                        "sourceUri": "https://www.stats.gov.cn/calendar",
                        "retrievedAt": NOW.isoformat(),
                    },
                }
            ]
        },
        [],
        [],
        [],
        now=NOW,
    )
    assert result["schedules"][0]["referencePeriod"] == "2026-11"
