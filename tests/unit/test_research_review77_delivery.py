"""Historical execution diagnosis never infers evidence from query gaps."""

from copy import deepcopy
import hashlib

from backend.macro.research_batch import diagnose_research_batch, evaluate_research_batch
from backend.macro.research_assurance import bind_execution
from tests.unit.test_research_cost_review75 import prepared, executed_checks, RUN, KEY
from tests.unit.test_daily_research import NOW


def run(packet, raw, checks):
    return evaluate_research_batch(raw, packet, checks, research_id=RUN, validated_at=NOW)


def diagnose(packet, raw, checks):
    """Keep strict audit diagnostics separate from the current delivery policy."""
    return diagnose_research_batch(raw, packet, checks, research_id=RUN, validated_at=NOW)


def changed_query(packet, checks):
    checks = executed_checks(packet, checks)
    manifest = packet["researchDiscovery"]["auditManifest"]
    target = next(t for t in manifest["tasks"] if t["industryId"] == "NONFERROUS")
    old = hashlib.sha256(target["query"].encode()).hexdigest()
    row = next(r for r in checks[KEY]["receipt"]["queryReceipts"] if r["queryHash"] == old)
    row["queryHash"] = hashlib.sha256("近期铜铝产业融资变化与订单观察".encode()).hexdigest()
    return checks, target


def test_rephrased_search_is_unmatched_and_keeps_exact_task_pending():
    packet, raw, checks = prepared()
    checks, target = changed_query(packet, checks)
    before = deepcopy((packet, raw, checks))
    result = diagnose(packet, raw, checks)
    coverage = result["registry"]["coverage"]
    assert coverage["executionMatchStatus"] == "EXECUTION_UNMATCHED"
    assert coverage["executionMatchedTaskCount"] == 14
    assert coverage["executionPlannedTaskCount"] == 15
    assert coverage["executionUnmatchedQueryHashCount"] == 1
    assert coverage["executionPendingDirectionCount"] == 1
    assert any(t["taskId"] == target["taskId"] for t in result["registry"]["auditTasks"])
    assert not any(t["investmentEligible"] for t in result["registry"]["items"])
    assert any(i["reasonCode"] == "EXECUTION_UNMATCHED" for i in result["registry"]["issues"])
    assert before == (packet, raw, checks)


def test_unrelated_macro_query_does_not_invalidate_complete_risk_audits():
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    receipt = checks[KEY]["receipt"]
    receipt["queryReceipts"].append({
        "queryHash": hashlib.sha256("美国利率公开数据".encode()).hexdigest(),
        "completedItemId": "f" * 64,
    })
    for field in ("webToolCalls", "searchActions", "searchQueries"):
        receipt["counts"][field] += 1
    result = diagnose(packet, raw, checks)
    coverage = result["registry"]["coverage"]
    assert coverage["executionUnmatchedQueryHashCount"] == 1
    assert coverage["executionMatchedTaskCount"] == coverage["executionPlannedTaskCount"] == 15
    assert coverage["executionPendingDirectionCount"] == 0
    assert result["formalReport"]["valueDirections"]


def test_four_of_five_tasks_retains_one_missing_check_without_fuzzy_repair():
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    manifest = packet["researchDiscovery"]["auditManifest"]
    target = next(t for t in manifest["tasks"] if t["industryId"] == "NONFERROUS")
    query_hash = hashlib.sha256(target["query"].encode()).hexdigest()
    receipt = checks[KEY]["receipt"]
    receipt["queryReceipts"] = [r for r in receipt["queryReceipts"] if r["queryHash"] != query_hash]
    for field in ("webToolCalls", "searchActions", "searchQueries"):
        receipt["counts"][field] -= 1
    result = diagnose(packet, raw, checks)
    pending = [t for t in result["registry"]["auditTasks"] if t["industryId"] == "NONFERROUS"]
    assert [t["taskId"] for t in pending] == [target["taskId"]]
    assert result["registry"]["coverage"]["executionMatchedTaskCount"] == 14
    assert result["registry"]["coverage"]["executionPendingDirectionCount"] == 1


def test_transport_loss_is_explained_without_sacrificing_observations():
    packet, raw, checks = prepared()
    receipt = executed_checks(packet, checks)[KEY]["receipt"]
    receipt.update(coverage="PARTIAL", eventsComplete=False, queryReceipts=[])
    reason = "RECEIPT_SUMMARIZED_SIZE_LIMIT"
    checks = bind_execution(checks, receipt, packet["researchDiscovery"]["auditManifest"], RUN, NOW, transport_reason=reason)
    result = diagnose(packet, raw, checks)
    assert result["registry"]["coverage"]["executionReceiptTransportReason"] == reason
    assert any(i["reasonCode"] == reason for i in result["registry"]["issues"])
    assert result["registry"]["items"] and result["originalOutcome"].accepted["sources"]
    assert not any(item["investmentEligible"] for item in result["registry"]["items"])


def test_invalid_direction_review_ids_do_not_crash_optional_gap_metrics():
    packet, raw, checks = prepared()
    checks = executed_checks(packet, checks)
    raw["valueDirections"][0]["reviewIndustryIds"] = None
    result = diagnose(packet, raw, checks)
    assert not any(item["investmentEligible"] for item in result["registry"]["items"])


def test_transport_reason_survives_safe_acceptance_workload():
    from backend.macro.research_workload import measured_workload
    from backend.macro.research_acceptance import rejected_outcome
    from backend.macro.research_acceptance_repository import safe_outcome_summary

    reason = "RECEIPT_OMITTED_SIZE_LIMIT"
    workload = measured_workload(12000, None, {}, transport_reason=reason)
    summary = safe_outcome_summary({**rejected_outcome(RUN, NOW).to_dict(), "researchWorkload": workload})
    assert summary["researchWorkload"]["executionReceiptTransportReason"] == reason
    assert summary["researchWorkload"]["observedSearchQueries"] is None


def test_registry_projection_keeps_closed_delivery_diagnostics():
    from backend.macro.research_topic_repository import prepare_topic_registry

    packet, raw, checks = prepared()
    checks, _ = changed_query(packet, checks)
    result = diagnose(packet, raw, checks)
    projected = prepare_topic_registry(result["registry"], RUN, NOW)
    for field in ("executionMatchStatus", "executionPlannedTaskCount", "executionMatchedTaskCount", "executionUnmatchedQueryHashCount", "executionPendingDirectionCount"):
        assert projected["coverage"][field] == result["registry"]["coverage"][field]
