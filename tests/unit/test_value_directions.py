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
