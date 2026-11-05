"""Keep distinct news facts and risk details without semantic/fuzzy merging."""

from copy import deepcopy
from datetime import timedelta

from backend.macro import daily_research as strict
from tests.unit.test_daily_research import NOW
from tests.unit.test_research_cost_review75 import RUN
from tests.unit.test_research_event_links import observation_case, event, validate


def test_two_different_events_sharing_one_aggregation_url_and_phase_survive():
    packet, raw, checks = observation_case()
    raw["events"] = [
        event(title="央行公布流动性安排", summary="流动性安排已公布，传导效果待观察。", importance="LOW"),
        event(title="企业订单明显下降", summary="企业披露订单下降，行业影响仍待核验。", importance="HIGH"),
    ]
    result = validate(raw, packet, checks)
    assert len(result["events"]) == 2
    assert len({row["id"] for row in result["events"]}) == 2
    assert any(row["importance"] == "HIGH" and "下降" in row["summary"] for row in result["events"])


def test_exact_fact_with_multiple_sources_merges_evidence_and_keeps_highest_risk():
    packet, raw, checks = observation_case()
    raw["events"] = [
        event(sourceIds=["s1"], importance="LOW", unknowns=["原材料变化"]),
        event(sourceIds=["s2"], importance="HIGH", unknowns=["利润下降风险"]),
    ]
    result = validate(raw, packet, checks)
    assert len(result["events"]) == 1
    merged = result["events"][0]
    assert len(merged["sourceIds"]) == 2
    assert merged["importance"] == "HIGH"
    assert set(merged["unknowns"]) == {"原材料变化", "利润下降风险"}


def test_same_headline_with_conflicting_facts_is_not_a_duplicate():
    packet, raw, checks = observation_case()
    raw["events"] = [
        event(summary="订单同比增加，实际回款待核实。"),
        event(summary="订单同比下降，现金回款承压。"),
    ]
    result = validate(raw, packet, checks)
    assert len(result["events"]) == 2


def test_overfull_risk_union_stays_separate_instead_of_truncating_adverse_details():
    packet, raw, checks = observation_case()
    first = [f"待核实风险{index}" for index in range(5)]
    second = [f"负面风险{index}" for index in range(5)]
    raw["events"] = [event(unknowns=first), event(unknowns=second)]
    result = validate(raw, packet, checks)
    assert len(result["events"]) == 2
    assert {text for row in result["events"] for text in row["unknowns"]} == set(first + second)


def test_repeated_fact_has_stable_identity_across_days_but_new_facts_are_separate():
    packet, raw, checks = observation_case()
    before = deepcopy((packet, raw, checks))
    first = validate(raw, packet, checks)["events"][0]
    second = strict.validate_report(
        raw, packet, checks, completed_at=NOW + timedelta(days=1), research_id=RUN,
    )["events"][0]
    assert first["id"] == second["id"]
    raw["events"][0]["summary"] = "新增披露显示订单继续下降，影响待核实。"
    updated = strict.validate_report(
        raw, packet, checks, completed_at=NOW + timedelta(days=1), research_id=RUN,
    )["events"][0]
    assert updated["id"] != first["id"]
    assert before[0] == packet and before[2] == checks


def test_same_source_two_future_events_at_one_time_are_not_collapsed():
    packet, raw, checks = observation_case()
    date = (NOW + timedelta(days=2)).date().isoformat()
    base = {"phase": "PLANNED", "scheduledDate": date, "scheduledAt": None,
            "sourceIds": ["s2"], "affectedDirectionIds": [], "watchFor": []}
    raw["upcoming"] = [
        {**base, "title": "经济增长数据公布", "importance": "LOW"},
        {**base, "title": "重大政策会议", "importance": "HIGH"},
    ]
    assert len(validate(raw, packet, checks)["upcoming"]) == 2
