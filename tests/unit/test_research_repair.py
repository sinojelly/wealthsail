"""Bounded deterministic quarantine never invents reviews or weakens risk."""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module

import pytest

from backend.macro.daily_research import validate_report
from tests.unit.test_daily_research import NOW
from tests.unit.test_industrial_metals_research_regression import case

RUN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def engine():
    return import_module("backend.macro.research_repair")


def repair(raw, packet, checks):
    return engine().repair_report(
        raw, packet, checks, research_id=RUN, validated_at=NOW
    )


def fixture(*, narrative=True):
    packet, raw, checks = case()
    raw["macroCycle"] = "宏观周期证据待核查。"
    if narrative:
        raw["narrativeDirectionIds"] = {"summary": [], "macroCycle": [], "coverage": []}
    orphan = deepcopy(raw["valueDirections"][0])
    orphan.update(
        key="orphan-watch",
        title="额外观察",
        thesis="后续需要明确行业审核。",
        plans=[],
        quantitativeFundCodes=[],
        reviewIndustryIds=[],
        risks=[],
        catalysts=[],
    )
    raw["valueDirections"].append(orphan)
    return packet, raw, checks


def event(raw, checks, *, importance="LOW", summary="公开资料已发布。"):
    source = {
        "id": "s3",
        "title": "公开资料发布",
        "url": "https://example.org/release",
        "quote": summary,
        "publishedDate": (NOW - timedelta(days=1)).date().isoformat(),
        "publishedAt": None,
    }
    raw["sources"].append(source)
    checks["s3"] = {
        **source,
        "verification": "SOURCE_CHECKED",
        "quoteMatched": True,
        "retrievedAt": NOW.isoformat(),
        "contentHash": "c" * 64,
    }
    return {
        "title": "公开资料发布",
        "summary": summary,
        "phase": "ANNOUNCED",
        "importance": importance,
        "sourceIds": ["s3"],
        "channels": [],
        "affectedDirectionIds": ["orphan-watch"],
        "scenarios": [],
        "watchFor": [],
        "unknowns": [],
        "marketReaction": "UNVERIFIED",
    }


def test_orphan_watch_is_quarantined_and_full_strict_prices_risk_and_time_are_preserved():
    packet, raw, checks = fixture()
    original = deepcopy((packet, raw, checks))
    baseline = deepcopy(raw)
    baseline["valueDirections"].pop()
    expected = validate_report(baseline, packet, checks, completed_at=NOW)
    result = repair(raw, packet, checks)
    assert result["decision"] == "REPAIRED" and result["strictPass"] is True
    assert result["autoPublishAllowed"] and result["historicalPublishAllowed"]
    assert result["originalOutcome"]["decision"] == "PARTIAL"
    assert result["formalReport"] == expected
    assert result["candidateRaw"]["industryCycleReviews"] == raw["industryCycleReviews"]
    assert result["candidateRaw"]["sources"] == raw["sources"]
    assert result["quarantined"][0]["itemIndex"] == 1
    assert result["quarantined"][0]["businessKey"] == "orphan-watch"
    assert result["quarantined"][0]["originalItem"] == raw["valueDirections"][1]
    assert result["retained"]["counts"]["valueDirections"] == 1
    assert original == (packet, raw, checks)


def test_valid_original_report_is_not_rewritten():
    packet, raw, checks = case()
    result = repair(raw, packet, checks)
    assert result["decision"] == "NO_CHANGE"
    assert result["strictPass"] and not result["quarantined"]


def test_unsafe_legacy_source_keeps_original_rejection_and_cannot_reach_repair():
    from tests.unit.test_daily_research import report

    raw = report()
    raw["sources"][0]["url"] = "http://127.0.0.1/private"
    result = repair(raw, {"asOf": NOW.isoformat()}, {})
    assert not result["strictPass"] and not result["historicalPublishAllowed"]
    assert (
        result["originalOutcome"]["issues"][0]["reasonCode"]
        == "SOURCE_REFERENCE_INVALID"
    )
    assert result["hardBlockers"][0]["code"] == "UNSAFE_INPUT"


def test_quarantined_unknown_cycle_explicitly_retains_unverified_check_gap():
    packet, raw, checks = fixture()
    for check in raw["valueDirections"][-1]["industryCycle"]["riskChecks"]:
        check["status"] = "UNVERIFIED"
    before = deepcopy(raw)
    value = repair(raw, packet, checks)
    cycle = value["quarantined"][0]["industryCycle"]
    assert any("5项" in gap and "UNVERIFIED" in gap for gap in cycle["evidenceGaps"])
    assert any("行业审核关联" in gap for gap in cycle["evidenceGaps"])
    assert (
        value["quarantined"][0]["originalItem"]["industryCycle"]
        == before["valueDirections"][-1]["industryCycle"]
    )
    assert raw == before


@pytest.mark.parametrize("header", ["missing", "title", "explicit"])
def test_unproven_header_is_only_a_historical_draft(header):
    packet, raw, checks = fixture(narrative=header != "missing")
    if header == "title":
        raw["summary"] = "额外观察需要等待后续行业资料。"
    elif header == "explicit":
        raw["narrativeDirectionIds"]["summary"] = ["orphan-watch"]
    result = repair(raw, packet, checks)
    assert result["strictPass"] and result["historicalPublishAllowed"]
    assert result["autoPublishAllowed"] is False and result["autoBlockers"]
    assert result["candidateRaw"]["summary"] == raw["summary"]
    assert result["candidateRaw"]["macroCycle"] == raw["macroCycle"]


def test_unknown_narrative_reference_is_a_hard_blocker():
    packet, raw, checks = fixture()
    raw["narrativeDirectionIds"]["summary"] = ["invented-direction"]
    result = repair(raw, packet, checks)
    assert not result["historicalPublishAllowed"] and result["safetyBlocked"]
    assert result["hardBlockers"][0]["code"] == "HEADER_REFERENCE_UNKNOWN"


def test_repair_never_guesses_composite_as_fund_sector_or_removes_plans():
    packet, raw, checks = fixture()
    raw["industryCycleReviews"] = raw["industryCycleReviews"][:2]
    result = repair(raw, packet, checks)
    assert not result["historicalPublishAllowed"] and not result["strictPass"]
    assert any(row["code"] == "PLAN_REVIEW_REQUIRED" for row in result["hardBlockers"])


def test_more_than_two_watch_quarantines_are_not_allowed():
    packet, raw, checks = fixture()
    raw["valueDirections"].extend(
        [
            {**deepcopy(raw["valueDirections"][-1]), "key": key}
            for key in ("orphan-two", "orphan-three")
        ]
    )
    result = repair(raw, packet, checks)
    assert not result["historicalPublishAllowed"]
    assert any(
        row["code"] == "QUARANTINE_BUDGET_EXCEEDED" for row in result["hardBlockers"]
    )


def test_low_clearly_nonnegative_event_is_quarantined_with_its_evidence():
    packet, raw, checks = fixture()
    raw["events"] = [event(raw, checks)]
    result = repair(raw, packet, checks)
    assert result["strictPass"] and result["autoPublishAllowed"]
    item = next(row for row in result["quarantined"] if row["itemType"] == "EVENT")
    assert item["importance"] == "LOW" and item["sourceIds"] == ["s3"]
    assert item["sourceEvidence"] and item["originalItem"] == raw["events"][0]
    assert result["candidateRaw"]["events"] == []


@pytest.mark.parametrize("kind", ["high", "negative", "historical"])
def test_important_or_negative_or_historical_risk_cannot_be_removed(kind):
    packet, raw, checks = fixture()
    if kind == "historical":
        packet["previousIndustryRiskStates"] = [
            {
                "key": "orphan-watch",
                "researchedAt": NOW.isoformat(),
                "industryCycle": {
                    "stage": "UNKNOWN",
                    "riskFlags": ["FINANCING_COOLING"],
                    "confirmedRiskFlags": ["FINANCING_COOLING"],
                    "riskLevel": "CONFIRMED",
                },
            }
        ]
    else:
        raw["events"] = [
            event(
                raw,
                checks,
                importance="HIGH" if kind == "high" else "LOW",
                summary="近期行业融资下降30%。"
                if kind == "negative"
                else "公开资料已发布。",
            )
        ]
        # A seed alias must not make the dependency disappear after removal.
        packet["directions"] = [{"id": "orphan-watch"}]
    result = repair(raw, packet, checks)
    assert result["strictPass"] and result["candidateRaw"] is not None
    assert not result["autoPublishAllowed"] and result["quarantined"]
    if kind == "historical":
        assert result["safetyBlocked"] and not result["historicalPublishAllowed"]
    else:
        assert not result["safetyBlocked"] and result["historicalPublishAllowed"]
        item = next(row for row in result["quarantined"] if row["itemType"] == "EVENT")
        assert item["mandatory"] is True and item["originalItem"] == raw["events"][0]


@pytest.mark.parametrize("kind", ["event", "upcoming"])
def test_ambiguous_dependency_can_only_be_a_historical_draft(kind):
    packet, raw, checks = fixture()
    dependency = event(raw, checks, importance="MEDIUM", summary="后续安排仍需观察。")
    if kind == "event":
        raw["events"] = [dependency]
    else:
        raw["upcoming"] = [
            {
                "title": dependency["title"],
                "phase": "PLANNED",
                "scheduledAt": None,
                "scheduledDate": (NOW + timedelta(days=1)).date().isoformat(),
                "sourceIds": ["s3"],
                "affectedDirectionIds": ["orphan-watch"],
                "watchFor": [],
            }
        ]
    result = repair(raw, packet, checks)
    assert result["strictPass"] and result["historicalPublishAllowed"]
    assert not result["autoPublishAllowed"] and result["autoBlockers"]


def test_found_risk_on_orphan_is_never_quarantined():
    packet, raw, checks = fixture()
    orphan = raw["valueDirections"][-1]
    orphan["industryCycle"]["riskChecks"][0].update(status="FOUND", sourceIds=["s1"])
    orphan["industryCycle"]["sourceIds"] = ["s1"]
    result = repair(raw, packet, checks)
    assert not result["historicalPublishAllowed"] and result["safetyBlocked"]
    assert result["strictPass"] and result["candidateRaw"] is not None
    assert result["decision"] == "BLOCKED" and result["quarantined"][0]["mandatory"]


def test_retained_protection_regression_blocks_publication(monkeypatch):
    packet, raw, checks = fixture()
    mod = engine()
    actual = mod.validate_report

    def weakened(*args, **kwargs):
        value = actual(*args, **kwargs)
        value["valueDirections"][0]["industryCycle"]["riskFlags"] = []
        return value

    monkeypatch.setattr(mod, "validate_report", weakened)
    result = repair(raw, packet, checks)
    assert not result["historicalPublishAllowed"]
    assert any(
        row["code"] == "RETAINED_PROTECTION_CHANGED" for row in result["hardBlockers"]
    )


@pytest.mark.parametrize(
    "field,value",
    [("sourceIds", ["unknown-source"]), ("macroCodes", ["UNKNOWN_MACRO"])],
)
def test_watch_with_other_invalid_evidence_is_not_a_repair_candidate(field, value):
    packet, raw, checks = fixture()
    raw["valueDirections"][-1][field] = value
    result = repair(raw, packet, checks)
    assert not result["strictPass"] and result["safetyBlocked"]


def test_unsafe_text_cannot_be_silently_deleted_by_watch_quarantine():
    packet, raw, checks = fixture()
    raw["valueDirections"][-1]["thesis"] = "<script>PRIVATE</script>"
    result = repair(raw, packet, checks)
    assert not result["strictPass"] and result["safetyBlocked"]


def test_negative_supporting_source_cannot_be_hidden_behind_benign_event_summary():
    packet, raw, checks = fixture()
    raw["events"] = [event(raw, checks)]
    raw["sources"][-1]["quote"] = "近期行业融资下降30%。"
    checks["s3"]["quote"] = raw["sources"][-1]["quote"]
    result = repair(raw, packet, checks)
    assert result["historicalPublishAllowed"] and not result["safetyBlocked"]
    assert not result["autoPublishAllowed"] and result["quarantined"][-1]["mandatory"]


def test_negative_source_alias_uses_the_same_canonical_url_as_strict_validation():
    packet, raw, checks = fixture()
    raw["events"] = [event(raw, checks)]
    raw["sources"][-1]["url"] = "https://EXAMPLE.org/release#details"
    raw["sources"][-1]["quote"] = "近期行业融资下降30%。"
    checks["s3"].update(
        url=raw["sources"][-1]["url"], quote=raw["sources"][-1]["quote"]
    )
    result = repair(raw, packet, checks)
    assert not result["safetyBlocked"] and result["historicalPublishAllowed"]
    assert not result["autoPublishAllowed"]
    assert result["quarantined"][-1]["sourceEvidence"][0]["id"] == "s3"
    assert (
        result["quarantined"][-1]["sourceEvidence"][0]["url"]
        == "https://example.org/release"
    )
    assert any(
        row["code"] == "NARRATIVE_RISK_REVIEW_REQUIRED"
        for row in result["autoBlockers"]
    )


@pytest.mark.parametrize("kind", ["generic-risk", "negative-phrase", "denial"])
def test_narrative_risk_is_retained_for_mandatory_historical_review_without_becoming_a_fact(
    kind,
):
    packet, raw, checks = fixture()
    orphan = raw["valueDirections"][-1]
    summary = {
        "generic-risk": "公司客户结构集中和黄金价格波动需要观察。",
        "negative-phrase": "融资下降风险尚需核实。",
        "denial": "公司声明近期未砍单。",
    }[kind]
    orphan["risks"] = [{"summary": summary, "sourceIds": ["s1"]}]
    orphan["watchFor"] = ["等待后续公开资料。"]
    result = repair(raw, packet, checks)
    assert result["strictPass"] and result["historicalPublishAllowed"]
    assert not result["autoPublishAllowed"] and not result["safetyBlocked"]
    annex = result["quarantined"][0]
    assert annex["mandatory"] is True and annex["risks"] == orphan["risks"]
    assert annex["watchFor"] == orphan["watchFor"]
    assert annex["sourceEvidence"] and annex["originalItem"] == orphan


def test_high_event_annex_preserves_scenarios_unknowns_and_watch_in_readable_fields():
    packet, raw, checks = fixture()
    row = event(raw, checks, importance="HIGH", summary="公司声明近期未砍单。")
    row["scenarios"] = [
        {"condition": "后续变化", "impact": "进一步核实", "counter": "继续观察"}
    ]
    row["unknowns"] = ["后续影响尚无法判断。"]
    row["watchFor"] = ["等待公告。"]
    raw["events"] = [row]
    result = repair(raw, packet, checks)
    assert result["strictPass"] and result["historicalPublishAllowed"]
    assert not result["autoPublishAllowed"] and not result["safetyBlocked"]
    annex = next(item for item in result["quarantined"] if item["itemType"] == "EVENT")
    for name in ("scenarios", "unknowns", "watchFor"):
        assert annex[name] == row[name]
    assert annex["sourceEvidence"] and annex["mandatory"]


@pytest.mark.parametrize(
    "uncertainty", ["extra-sentence", "title", "unknowns", "scenarios"]
)
def test_benign_fragment_does_not_prove_a_whole_dependent_event_nonnegative(
    uncertainty,
):
    packet, raw, checks = fixture()
    row = event(raw, checks)
    if uncertainty == "extra-sentence":
        row["summary"] += "后续影响尚无法判断。"
    elif uncertainty == "title":
        row["title"] = "后续影响尚无法判断"
    elif uncertainty == "unknowns":
        row["unknowns"] = ["后续影响尚无法判断。"]
    else:
        row["scenarios"] = [
            {"condition": "情况变化", "impact": "尚无法判断", "counter": "继续观察"}
        ]
    raw["events"] = [row]
    result = repair(raw, packet, checks)
    assert result["strictPass"] and result["historicalPublishAllowed"]
    assert not result["autoPublishAllowed"]
    assert any(
        row["code"] == "DEPENDENT_NEGATIVE_UNPROVEN" for row in result["autoBlockers"]
    )


def test_new_model_contract_requires_header_provenance_and_calendar_importance():
    from backend.macro.daily_research import REPORT_SCHEMA, build_prompt

    packet, _, _ = fixture()
    assert "narrativeDirectionIds" in REPORT_SCHEMA["required"]
    assert set(REPORT_SCHEMA["$defs"]["_NarrativeDirectionIds"]["required"]) == {
        "summary",
        "macroCycle",
        "coverage",
    }
    assert "importance" in REPORT_SCHEMA["$defs"]["_Upcoming"]["properties"]
    prompt = build_prompt(packet)
    assert "narrativeDirectionIds" in prompt and "不猜测相邻行业" in prompt


def test_compact_repair_summary_never_copies_model_text_or_private_candidates():
    from backend.macro.research_repair import repair_summary

    packet, raw, checks = fixture()
    raw["valueDirections"][-1]["thesis"] = "PRIVATE_ORIGINAL_OBSERVATION"
    result = repair(raw, packet, checks)
    import json

    summary = repair_summary(result)
    encoded = json.dumps(summary, ensure_ascii=False)
    assert len(encoded.encode()) < 32 * 1024
    assert "PRIVATE_ORIGINAL_OBSERVATION" not in encoded
    assert not {"formalReport", "candidateRaw", "originalOutcome"} & summary.keys()
    assert summary["version"] == summary["repairVersion"]
    assert summary["repairRevision"] == summary["revision"]
    assert (
        summary["originalDecision"] == "PARTIAL"
        and summary["strictRevalidated"] is True
    )
    assert summary["counts"]["fundPlans"] == 2
    assert summary["quarantined"][0]["businessKey"] == "orphan-watch"
    assert summary["quarantined"][0]["reason"]


def test_two_orphan_watches_are_the_maximum_safe_attempt():
    packet, raw, checks = fixture()
    raw["valueDirections"].append(
        {**deepcopy(raw["valueDirections"][-1]), "key": "orphan-two"}
    )
    result = repair(raw, packet, checks)
    assert result["strictPass"] and result["autoPublishAllowed"]
    assert len(result["quarantined"]) == 2


@pytest.mark.parametrize("unsafe", ["missing-checks", "duplicate-json"])
def test_unreplayable_or_ambiguous_original_input_is_hard_blocked(unsafe):
    packet, raw, checks = fixture()
    if unsafe == "missing-checks":
        checks = None
    else:
        raw = '{"summary":"one","summary":"two"}'
    result = repair(raw, packet, checks)
    assert not result["strictPass"] and not result["historicalPublishAllowed"]
    assert result["hardBlockers"][0]["code"] == "UNSAFE_INPUT"
