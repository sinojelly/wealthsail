"""Observed queries can bind a frozen industry/risk core without raw query text."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import UUID

import pytest

from backend.integrations import research_execution as execution
from tests.unit.test_research_execution import event, observe


MANIFEST = {
    "tasks": [
        {
            "taskId": "robot-financing",
            "industryId": "ROBOT",
            "kind": "FINANCING_COOLING",
            "query": "机器人 具身智能 一级市场融资降温/断代 2026 近期 近90天",
        },
        {
            "taskId": "robot-layoffs",
            "industryId": "ROBOT",
            "kind": "LAYOFFS_CLOSURES",
            "query": "机器人 具身智能 企业欠薪/裁员/倒闭 2026 近期 近90天",
        },
        {
            "taskId": "metals-financing",
            "industryId": "NONFERROUS",
            "kind": "FINANCING_COOLING",
            "query": "有色金属 工业金属 铜 铝 一级市场融资降温/断代 2026 近期 近90天",
        },
        {
            "taskId": "mobility-financing",
            "industryId": "ROBOTICS_MOBILITY",
            "kind": "FINANCING_COOLING",
            "query": "机器人 智能驾驶 一级市场融资降温/断代 2026 近期 近90天",
        },
    ]
}


def test_reordered_industry_and_risk_synonyms_bind_only_the_same_frozen_task():
    receipt = observe(
        event(
            "actual",
            action={
                "type": "search",
                "query": "２０２６年 人形机器人 行业 融资减少 最新",
            },
        )
    )
    assert execution.executed_task_ids(receipt, MANIFEST) == {"robot-financing"}
    stats = execution.execution_match_stats(receipt, MANIFEST)
    assert stats["matchedTaskCount"] == 1
    assert stats["matchedQueryHashCount"] == 1
    assert stats["unmatchedQueryHashCount"] == 0
    assert stats["classification"] == "EXECUTION_PARTIALLY_MATCHED"
    assert "融资减少" not in json.dumps(receipt, ensure_ascii=False)
    assert "人形机器人" not in json.dumps(receipt, ensure_ascii=False)


@pytest.mark.parametrize(
    "query",
    [
        "2026年 有色金属 融资减少",  # another industry
        "2026年 人形机器人 裁员",  # another risk category
        "2026年 人形机器人 融资减少 裁员",  # ambiguous categories
        "2026年 人形机器人 有色金属 融资减少",  # ambiguous industries
        "2025年 人形机器人 融资减少",  # a different explicit year
        "2026年 人形机器人 融资增加",  # no counter-evidence risk core
        "2026年 人形机器人 未来十年 融资减少",  # unsupported time scope
    ],
)
def test_normalization_cannot_authorize_another_industry_category_or_time(query):
    receipt = observe(event("actual", action={"type": "search", "query": query}))
    assert "robot-financing" not in execution.executed_task_ids(receipt, MANIFEST)


@pytest.mark.parametrize(
    "kind,planned,actual",
    [
        ("FINANCING_COOLING", "一级市场融资降温/断代", "融资下降"),
        ("LAYOFFS_CLOSURES", "企业欠薪/裁员/倒闭", "拖欠工资"),
        ("ORDER_CUTS", "下游砍单", "订单取消"),
        ("OVERCAPACITY_PRICE_WAR", "产能过剩/价格战", "降价竞争"),
        ("PRODUCTION_DELAY", "商业化量产推迟", "量产延期"),
    ],
)
def test_each_risk_category_accepts_its_observed_core_without_changing_budget(
    kind, planned, actual
):
    manifest = {
        "tasks": [
            {
                "taskId": "frozen",
                "industryId": "ROBOT",
                "kind": kind,
                "query": f"机器人 具身智能 {planned} 2026 近期 近90天",
            }
        ]
    }
    snapshot = deepcopy(manifest)
    receipt = observe(
        event("actual", action={"type": "search", "query": f"2026 人形机器人 {actual}"})
    )
    assert execution.executed_task_ids(receipt, manifest) == {"frozen"}
    assert manifest == snapshot


def test_core_proof_requires_matching_frozen_industry_and_kind_metadata():
    receipt = observe(
        event("actual", action={"type": "search", "query": "2026 机器人 融资下降"})
    )
    for change in ({"industryId": "NONFERROUS"}, {"kind": "ORDER_CUTS"}):
        task = {**MANIFEST["tasks"][0], **change}
        assert not execution.executed_task_ids(receipt, {"tasks": [task]})


def test_old_v2_receipt_without_core_hashes_stays_exact_and_summary_grants_no_core_proof():
    receipt = observe(
        event("actual", action={"type": "search", "query": "2026 机器人 融资下降"})
    )
    assert execution.executed_task_ids(receipt, MANIFEST) == {"robot-financing"}
    old = deepcopy(receipt)
    for row in old["actionProofs"]:
        row.pop("queryCoreHashes", None)
    assert execution.sanitize_execution_receipt(old) is not None
    assert not execution.executed_task_ids(old, MANIFEST)
    assert not execution.executed_task_ids(
        execution.summarize_execution_receipt(receipt), MANIFEST
    )


def test_conflicting_completed_identity_revokes_its_core_proof():
    receipt = observe(
        event("shared", action={"type": "search", "query": "2026 机器人 融资下降"}),
        event("shared", action={"type": "search", "query": "2026 有色金属 融资下降"}),
    )
    assert not execution.executed_task_ids(receipt, MANIFEST)


@pytest.mark.parametrize("change", ["orphan", "invalid", "nonsearch", "too_many"])
def test_malformed_core_proof_cannot_be_used_as_authorization(change):
    receipt = observe(
        event("actual", action={"type": "search", "query": "2026 机器人 融资下降"})
    )
    proof = receipt["actionProofs"][0]
    assert proof.get("queryCoreHashes"), (
        "actual completed search must retain its bounded core hash"
    )
    if change == "orphan":
        proof["queryCoreHashes"] = {"a" * 64: "b" * 64}
    elif change == "invalid":
        proof["queryCoreHashes"] = {proof["queryHashes"][0]: "PRIVATE"}
    elif change == "nonsearch":
        proof.update(status="UNKNOWN_ACTION", queryHashes=[])
        receipt["queryReceipts"] = []
        receipt.update(coverage="PARTIAL", eventsComplete=False)
    else:
        proof["queryCoreHashes"] = {
            format(index, "064x"): "b" * 64 for index in range(257)
        }
    assert execution.sanitize_execution_receipt(receipt) is None
    assert not execution.executed_task_ids(receipt, MANIFEST)


def test_not_found_assurance_uses_completed_cores_even_with_an_unrelated_unknown_event():
    from backend.macro.research_assurance import audit_assurance, bind_execution

    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    run_id = UUID("00000000-0000-4000-8000-000000000001")
    risks = [
        ("FINANCING_COOLING", "一级市场融资降温/断代", "融资下降"),
        ("LAYOFFS_CLOSURES", "企业欠薪/裁员/倒闭", "拖欠工资"),
        ("ORDER_CUTS", "下游砍单", "订单取消"),
        ("OVERCAPACITY_PRICE_WAR", "产能过剩/价格战", "降价竞争"),
        ("PRODUCTION_DELAY", "商业化量产推迟", "量产延期"),
    ]
    manifest = {
        "manifestHash": "a" * 64,
        "asOf": now.isoformat(),
        "selectedScopes": ["ROBOT"],
        "tasks": [
            {
                "taskId": kind,
                "industryId": "ROBOT",
                "kind": kind,
                "query": f"机器人 具身智能 {planned} 2026 近期 近90天",
            }
            for kind, planned, _actual in risks
        ],
    }
    receipt = observe(
        *(
            event(kind, action={"type": "search", "query": f"2026 人形机器人 {actual}"})
            for kind, _planned, actual in risks
        ),
        {"type": "future.privateEvent", "text": "PRIVATE"},
    )
    bound = bind_execution({}, receipt, manifest, run_id, now)
    assurance = audit_assurance(
        {
            "ROBOT": {
                "riskChecks": [
                    {"kind": kind, "status": "NOT_FOUND"}
                    for kind, _planned, _actual in risks
                ]
            }
        },
        manifest,
        bound,
        now,
        research_id=run_id,
    )
    assert assurance["completedKinds"] == {
        "ROBOT": {
            "FINANCING_COOLING",
            "LAYOFFS_CLOSURES",
            "ORDER_CUTS",
            "OVERCAPACITY_PRICE_WAR",
            "PRODUCTION_DELAY",
        }
    }
    assert assurance["executionVerifiedCheckCount"] == 5
    assert assurance["executionVerifiedScopeCount"] == 1
    assert assurance["searchExecutionVerified"] is True
    assert assurance["executionMatchStatus"] == "EXECUTION_MATCHED"
    assert assurance["executionObservedQueryCount"] is None
    # Existing research/time bindings remain mandatory for the same receipts.
    other_run = UUID("00000000-0000-4000-8000-000000000002")
    unbound = audit_assurance({}, manifest, bound, now, research_id=other_run)
    assert unbound["executionVerifiedCheckCount"] == 0
