"""Discovered directions select public evidence and server price coordinates."""

from copy import deepcopy
from importlib import import_module, util

import pytest

from test_phase2_research import NOW, anchor


def module():
    assert util.find_spec("backend.macro.value_directions"), (
        "value direction contract missing"
    )
    return import_module("backend.macro.value_directions")


def packet():
    plan = anchor()
    plan.pop("themeCode")
    plan.update(subjectId="005112", label="测试医药联接")
    return {
        "asOf": NOW.isoformat(),
        "discoveryActionableZones": [plan],
        "macros": [
            {"code": "PMI", "value": 50.1, "state": "READY", "asOf": "2026-09-01"}
        ],
    }


def direction(**updates):
    return {
        "key": "healthcare-recovery",
        "title": "医药景气修复",
        "thesis": "行业消息与宏观观察共同支持研究。",
        "signal": "LEFT_VALUE",
        "sourceIds": ["s1"],
        "macroCodes": ["PMI"],
        "watchFor": ["核对订单与盈利"],
        "plans": [
            {
                "subjectKind": "nav",
                "subjectId": "005112",
                "track": "LEFT_VALUE",
                "priceRevisionStatus": "INITIAL",
                "priceRevisionNote": "初次研究",
                "calibrationRuleKeys": [],
                "calibrationNote": "",
            }
        ],
        **updates,
    }


def validate(rows=None, public=None, aliases=None):
    return module().validate_value_directions(
        rows or [direction()],
        public or packet(),
        NOW,
        aliases or {"s1": "source:one", "s2": "source:two"},
    )


def test_discovers_non_seed_direction_and_copies_exact_server_fund_price():
    actual = validate()[0]
    assert actual["key"] == "healthcare-recovery"
    assert actual["sourceIds"] == ["source:one"]
    assert actual["plans"][0]["currentPrice"] == "1.2500"
    assert actual["plans"][0]["valueDirectionKey"] == actual["key"]
    assert actual["plans"][0]["selectedTrack"] == "LEFT_VALUE"
    assert actual["macroEvidence"][0]["value"] == 50.1
    assert actual["macroEvidence"][0]["asOf"] == "2026-09-01"


def test_unavailable_or_old_macro_cannot_be_the_only_second_evidence():
    public = packet()
    public["macros"][0].update(state="UNAVAILABLE")
    with pytest.raises(ValueError):
        validate(public=public)
    public["macros"][0].update(state="READY", asOf="2020-01-01")
    with pytest.raises(ValueError):
        validate(public=public)


@pytest.mark.parametrize(
    "updates",
    [
        {"sourceIds": ["missing"]},
        {"macroCodes": ["PRIVATE"]},
        {"sourceIds": ["s1"], "macroCodes": []},
        {"key": "../../private"},
        {"signal": "RIGHT_TREND"},
    ],
)
def test_unknown_references_insufficient_evidence_and_unproven_signal_rejected(updates):
    with pytest.raises(ValueError):
        validate([direction(**updates)])


def test_two_aliases_of_same_source_do_not_count_as_two_facts():
    with pytest.raises(ValueError):
        validate(
            [direction(sourceIds=["s1", "s2"], macroCodes=[])],
            aliases={"s1": "same", "s2": "same"},
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"subjectId": "999999"},
        {"entryZoneMin": "0.01"},
        {"currentPrice": "999"},
        {"calibrationRuleKeys": ["PRIVATE:TIMING"]},
    ],
)
def test_model_cannot_invent_product_prices_or_memory(changes):
    raw = direction()
    raw["plans"][0].update(changes)
    with pytest.raises(ValueError):
        validate([raw])


def test_direction_may_observe_without_plan_but_not_claim_a_buy_signal():
    assert validate([direction(signal="WATCH", plans=[])])[0]["plans"] == []
    with pytest.raises(ValueError):
        validate([direction(plans=[])])


def test_real_broad_index_anchor_is_not_limited_to_seed_themes():
    public = packet()
    plan = public["discoveryActionableZones"][0]
    plan.update(
        subjectKind="index", subjectId="hs300", priceBasis="INDEX_POINTS", unit="points"
    )
    raw = direction()
    raw["plans"][0].update(subjectKind="index", subjectId="hs300")
    assert validate([raw], public)[0]["plans"][0]["subjectId"] == "hs300"


def test_previous_same_direction_requires_revision_and_preserves_day_and_targets():
    public, raw = packet(), direction()
    previous = deepcopy(public["discoveryActionableZones"][0])
    previous.update(valueDirectionKey=raw["key"], selectedTrack="LEFT_VALUE")
    public["previousValueDirections"] = [{"key": raw["key"], "plans": [previous]}]
    with pytest.raises(ValueError):
        validate([raw], public)
    raw["plans"][0].update(priceRevisionStatus="DOWNGRADED")
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "DOWNGRADED"
    raw["plans"][0]["priceRevisionStatus"] = "TAKE_PROFIT_ALERT"
    with pytest.raises(ValueError):
        validate([raw], public)


@pytest.mark.parametrize(
    "updates",
    [
        {"historyQuality": "STALE"},
        {"historyQuality": "CONFLICT"},
        {"historyQuality": "UNAVAILABLE"},
        {"asOf": "2020-01-01"},
    ],
)
def test_current_unusable_price_anchor_cannot_become_a_new_direction(updates):
    public = packet()
    public["discoveryActionableZones"][0].update(updates)
    with pytest.raises(ValueError):
        validate(public=public)


def test_old_frozen_previous_price_is_retained_for_revision_comparison():
    public, raw = packet(), direction()
    old = deepcopy(public["discoveryActionableZones"][0])
    old.update(asOf="2020-01-01", selectedTrack="LEFT_VALUE")
    public["previousValueDirections"] = [{"key": raw["key"], "plans": [old]}]
    raw["plans"][0]["priceRevisionStatus"] = "UPGRADED"
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "UPGRADED"


def test_duplicate_direction_and_product_track_rejected():
    with pytest.raises(ValueError):
        validate([direction(), direction()])
    raw = direction()
    raw["plans"] *= 2
    with pytest.raises(ValueError):
        validate([raw])


def test_daily_report_uses_new_dynamic_directions_and_flattens_verified_prices():
    from backend.macro.daily_research import validate_report
    from test_daily_research import report

    raw = {
        **report(),
        "macroCycle": "增长观察。",
        "actionablePlans": [],
        "valueDirections": [direction()],
    }
    public = {
        **packet(),
        "directions": [{"id": "a-share:broad"}],
        "discoveryUniverse": [],
    }
    actual = validate_report(raw, public, {}, completed_at=NOW)
    assert actual["valueDirections"][0]["plans"][0]["currentPrice"] == "1.2500"
    assert actual["actionablePlans"][0]["valueDirectionKey"] == "healthcare-recovery"
    assert actual["valueDirections"][0]["sourceIds"][0].startswith("source:")
    raw.pop("valueDirections")
    with pytest.raises(ValueError):
        validate_report(raw, public, {}, completed_at=NOW)


def test_eight_distinct_directions_survive_report_validation_without_price_anchors():
    from backend.macro.daily_research import validate_report
    from test_daily_research import report

    rows = [
        direction(key=f"opportunity-{number}", signal="WATCH", plans=[])
        for number in range(8)
    ]
    raw = {
        **report(),
        "macroCycle": "信用与行业催化提供多方向研究线索。",
        "actionablePlans": [],
        "valueDirections": rows,
    }
    public = {
        **packet(),
        "directions": [{"id": "a-share:broad"}],
        "discoveryUniverse": [],
        "discoveryActionableZones": [],
    }
    actual = validate_report(raw, public, {}, completed_at=NOW)
    assert [row["key"] for row in actual["valueDirections"]] == [
        "opportunity-0",
        "opportunity-1",
        "opportunity-2",
        "opportunity-3",
        "opportunity-4",
        "opportunity-5",
        "opportunity-6",
        "opportunity-7",
    ]
    assert all(row["plans"] == [] for row in actual["valueDirections"])
    raw["valueDirections"].append(
        direction(key="opportunity-8", signal="WATCH", plans=[])
    )
    with pytest.raises(ValueError):
        validate_report(raw, public, {}, completed_at=NOW)


def quantitative_packet():
    public = packet()
    plan = public["discoveryActionableZones"][0]
    plan.update(fundCode="005112", historyQuality="READY")
    public["quantitativeScan"] = {
        "methodVersion": "fund-quant-scan-v1",
        "asOf": NOW.isoformat(),
        "minimumHistorySamples": 220,
        "coverage": {},
        "items": [
            {
                **deepcopy(plan),
                "discoverySector": "HEALTHCARE",
                "signals": [
                    {
                        "track": "LEFT_VALUE",
                        "triggerState": "CANDIDATE",
                        "inEntryZone": False,
                        "distanceToEntryPct": "0.04",
                    }
                ],
            }
        ],
    }
    return public


@pytest.mark.parametrize("sources", [["s1"], []])
def test_real_nav_quantitative_scan_can_supply_independent_direction_evidence(sources):
    public = quantitative_packet()
    actual = validate(
        [direction(sourceIds=sources, macroCodes=[], quantitativeFundCodes=["005112"])],
        public,
    )[0]
    assert actual["quantitativeFundCodes"] == ["005112"]
    assert actual["quantitativeEvidence"][0]["currentPrice"] == "1.2500"
    assert actual["quantitativeEvidence"][0]["fundCode"] == "005112"
    public["quantitativeScan"]["items"][0]["currentPrice"] = "99"
    assert actual["quantitativeEvidence"][0]["currentPrice"] == "1.2500"


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "unknown",
        "stale",
        "future",
        "wrong_identity",
        "wrong_price",
        "unrelated",
        "duplicate",
        "no_scan_method",
    ],
)
def test_quantitative_evidence_rejects_unavailable_forged_or_unrelated_funds(problem):
    public = quantitative_packet()
    raw = direction(sourceIds=["s1"], macroCodes=[], quantitativeFundCodes=["005112"])
    scan = public["quantitativeScan"]
    if problem == "missing":
        public.pop("quantitativeScan")
    elif problem == "unknown":
        raw["quantitativeFundCodes"] = ["999999"]
    elif problem == "stale":
        scan["items"][0]["historyQuality"] = "STALE"
    elif problem == "future":
        scan["items"][0]["asOf"] = "2026-10-19"
    elif problem == "wrong_identity":
        scan["items"][0]["subjectId"] = "another"
    elif problem == "wrong_price":
        scan["items"][0]["currentPrice"] = "3"
    elif problem == "unrelated":
        raw.update(signal="WATCH", plans=[])
    elif problem == "duplicate":
        raw["quantitativeFundCodes"] = ["005112", "005112"]
    else:
        scan.pop("methodVersion")
    with pytest.raises(ValueError):
        validate([raw], public)


def test_pure_quantitative_direction_needs_a_triggered_selected_track():
    public = quantitative_packet()
    public["discoveryActionableZones"][0]["tracks"][0]["state"] = "WATCH"
    public["quantitativeScan"]["items"][0]["tracks"][0]["state"] = "WATCH"
    with pytest.raises(ValueError):
        validate(
            [
                direction(
                    sourceIds=[],
                    macroCodes=[],
                    signal="WATCH",
                    quantitativeFundCodes=["005112"],
                )
            ],
            public,
        )


def test_dual_fund_direction_keeps_both_real_plans_and_quantitative_facts():
    public = quantitative_packet()
    second = deepcopy(public["discoveryActionableZones"][0])
    second.update(subjectId="320007", fundCode="320007", label="真实主动基金")
    public["discoveryActionableZones"].append(second)
    public["quantitativeScan"]["items"].append(deepcopy(second))
    raw = direction(macroCodes=[], quantitativeFundCodes=["005112", "320007"])
    second_selection = deepcopy(raw["plans"][0])
    second_selection["subjectId"] = "320007"
    raw["plans"].append(second_selection)
    actual = validate([raw], public)[0]
    assert [row["fundCode"] for row in actual["plans"]] == ["005112", "320007"]
    assert [row["fundCode"] for row in actual["quantitativeEvidence"]] == [
        "005112",
        "320007",
    ]


def test_report_keeps_cls_media_as_unverified_while_using_its_catalyst_with_nav():
    from backend.macro.daily_research import validate_report
    from test_daily_research import report

    raw = {
        **report(),
        "macroCycle": "行业催化与量化共同研究。",
        "actionablePlans": [],
        "valueDirections": [direction(macroCodes=[], quantitativeFundCodes=["005112"])],
    }
    raw["sources"][0]["url"] = "https://www.cls.cn/detail/123"
    public = {
        **quantitative_packet(),
        "directions": [{"id": "a-share:broad"}],
        "discoveryUniverse": [],
    }
    actual = validate_report(raw, public, {}, completed_at=NOW)
    assert actual["sources"][0]["verification"] == "UNVERIFIED"
    assert actual["valueDirections"][0]["plans"][0]["currentPrice"] == "1.2500"


def test_pure_quantitative_candidate_must_be_one_of_the_referenced_scan_funds():
    public = quantitative_packet()
    first = public["discoveryActionableZones"][0]
    first["tracks"][0]["state"] = "WATCH"
    public["quantitativeScan"]["items"][0]["tracks"][0]["state"] = "WATCH"
    second = deepcopy(first)
    second.update(subjectId="320007", fundCode="320007")
    second["tracks"][0]["state"] = "CANDIDATE"
    public["discoveryActionableZones"].append(second)
    raw = direction(
        sourceIds=[], macroCodes=[], signal="WATCH", quantitativeFundCodes=["005112"]
    )
    second_selection = deepcopy(raw["plans"][0])
    second_selection["subjectId"] = "320007"
    raw["plans"].append(second_selection)
    with pytest.raises(ValueError):
        validate([raw], public)


def test_direction_catalysts_and_risks_keep_related_sources_and_normalize_aliases():
    actual = validate(
        [
            direction(
                sourceIds=["s1", "s2"],
                catalysts=[
                    {
                        "summary": "同链企业回购意向与经营改善构成联合催化。",
                        "sourceIds": ["s2", "s1", "s1"],
                    }
                ],
                risks=[
                    {"summary": "相关企业解禁可能增加短期供给。", "sourceIds": ["s2"]}
                ],
            )
        ]
    )[0]
    assert actual["catalysts"] == [
        {
            "summary": "同链企业回购意向与经营改善构成联合催化。",
            "sourceIds": ["source:one", "source:two"],
        }
    ]
    assert actual["risks"] == [
        {"summary": "相关企业解禁可能增加短期供给。", "sourceIds": ["source:two"]}
    ]


def test_existing_direction_without_structured_notes_keeps_empty_compatible_fields():
    actual = validate()[0]
    assert actual["catalysts"] == []
    assert actual["risks"] == []


@pytest.mark.parametrize("field", ["catalysts", "risks"])
@pytest.mark.parametrize("source", ["s2", "unknown"])
def test_direction_notes_cannot_attach_unrelated_or_unknown_sources(field, source):
    raw = direction(
        **{field: [{"summary": "来源须与该方向相关。", "sourceIds": [source]}]}
    )
    with pytest.raises(ValueError):
        validate([raw])


@pytest.mark.parametrize("field", ["catalysts", "risks"])
@pytest.mark.parametrize(
    "notes",
    [
        [{"summary": "过长" * 121, "sourceIds": ["s1"]}],
        [{"summary": "没有来源", "sourceIds": []}],
        [{"summary": "简短来源说明", "sourceIds": ["s1"]}] * 3,
    ],
)
def test_direction_structured_notes_are_bounded(field, notes):
    with pytest.raises(ValueError):
        validate([direction(**{field: notes})])
