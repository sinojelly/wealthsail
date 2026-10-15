"""Research coverage updates are separate from verified business notifications."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock

import pytest

from backend.investment import decision_repository as repo

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def result():
    return {
        "decision": {
            "state": "WAIT",
            "marketState": "WAIT",
            "personalState": "UNKNOWN",
            "gates": [{"id": "macro", "state": "UNKNOWN"}],
        },
        "portfolio": {
            "state": "UNKNOWN",
            "heldSelected": None,
            "noticeBudget": 3,
            "silenceMinutes": 0,
        },
        "directionModel": {"missingRequiredEvidence": ["M1_YOY"]},
        "researchBoard": {
            "methodVersion": "research-board-v2",
            "items": [
                {
                    "id": "scope:trend:hs300",
                    "key": "trend:hs300",
                    "category": "OPPORTUNITY",
                    "severity": "MEDIUM",
                    "confidence": "VERIFIED",
                    "factVerification": "VERIFIED",
                    "evidenceIds": ["hs300"],
                }
            ],
        },
    }


async def record(before, after):
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW - timedelta(days=1),
    }
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        monitor,
        {
            "id": "prior",
            "condition_fingerprint": repo.condition_fingerprint(before),
            "result": before,
            "as_of": NOW - timedelta(minutes=15),
        },
        {"notice_count": 0, "last_notice": None},
    ]
    conn.fetchval.side_effect = ["snapshot", "notice"]
    return await repo.record_monitor_result(conn, monitor, after, NOW), conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["missing", "gates", "stale_removal", "unverified", "ordinary"]
)
async def test_coverage_or_unverified_changes_save_baseline_without_user_notice(change):
    before, after = result(), result()
    if change == "missing":
        after["directionModel"]["missingRequiredEvidence"] = []
    elif change == "gates":
        after["decision"]["gates"][0]["state"] = "PASS"
    elif change == "stale_removal":
        after["researchBoard"]["items"] = []
    elif change == "unverified":
        after["researchBoard"]["items"].append(
            {
                "id": "unknown",
                "category": "RISK",
                "severity": "HIGH",
                "confidence": "UNKNOWN",
                "factVerification": "UNKNOWN",
                "evidenceIds": ["UNKNOWN"],
            }
        )
    else:
        after["researchBoard"]["items"].append(
            {
                "id": "ordinary",
                "category": "CHANGE",
                "severity": "LOW",
                "confidence": "VERIFIED",
                "factVerification": "VERIFIED",
                "evidenceIds": ["PMI"],
            }
        )
    output, conn = await record(before, after)
    assert output["notice"] is False
    assert not any(
        "opportunity_condition_notices" in call.args[0]
        for call in conn.fetchval.await_args_list
    )
    if change != "ordinary":
        assert any(
            "opportunity_decision_snapshots" in call.args[0]
            for call in conn.fetchval.await_args_list
        )
        assert conn.fetchrow.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["reversal", "new_verified", "fact_verified", "partial_impact"]
)
async def test_current_new_or_changed_verified_facts_notify_and_survive_persistence(
    change,
):
    before, after = result(), result()
    item = after["researchBoard"]["items"][0]
    if change == "reversal":
        item.update(category="CHANGE", key="trend-failed:hs300")
    elif change == "new_verified":
        item["id"] = "scope:trend:zz500"
        item["evidenceIds"] = ["zz500"]
    elif change == "fact_verified":
        before["researchBoard"]["items"][0]["factVerification"] = "UNKNOWN"
    else:
        item.update(category="RISK", confidence="PARTIAL", evidenceIds=["US_T10_REAL"])
    output, conn = await record(before, after)
    assert output["notice"] is True
    saved = json.loads(conn.fetchval.await_args_list[0].args[-1])
    assert (
        saved["researchConditions"]["importantItems"][0]["factVerification"]
        == "VERIFIED"
    )
    assert repo.condition_fingerprint(saved) == repo.condition_fingerprint(after)
    unchanged, _ = await record(saved, deepcopy(after))
    assert unchanged["notice"] is False


@pytest.mark.asyncio
async def test_v1_to_v2_is_silent_baseline_before_verified_changes():
    before, after = result(), result()
    before["researchBoard"]["methodVersion"] = "research-board-v1"
    output, conn = await record(before, after)
    assert output["baseline"] is True and output["notice"] is False
    assert conn.fetchrow.await_count == 2


def saved_snapshot(conn):
    call = next(
        call
        for call in conn.fetchval.await_args_list
        if "opportunity_decision_snapshots" in call.args[0]
    )
    return json.loads(call.args[-1])


def crossing(direction):
    current = result()
    current["researchBoard"]["items"] = [
        {
            "id": "scope:macro:PMI:BENCHMARK_CROSSING_" + direction,
            "key": "macro:PMI:BENCHMARK_CROSSING_" + direction,
            "category": "CHANGE",
            "severity": "MEDIUM",
            "confidence": "VERIFIED",
            "factVerification": "VERIFIED",
            "evidenceIds": ["PMI"],
        }
    ]
    return current


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["removed", "unverified"])
async def test_same_verified_fact_after_a_coverage_gap_does_not_notify_again(
    unavailable,
):
    original, gap = result(), result()
    if unavailable == "removed":
        gap["researchBoard"]["items"] = []
    else:
        gap["researchBoard"]["items"][0]["factVerification"] = "UNKNOWN"
    gap["directionModel"]["missingRequiredEvidence"] = ["INDEX_TREND"]
    lost, conn = await record(original, gap)
    assert not lost["notice"]
    restored, _ = await record(saved_snapshot(conn), deepcopy(original))
    assert not restored["notice"]


@pytest.mark.asyncio
async def test_gap_snapshot_retains_a_compact_verified_baseline_separate_from_current_conditions():
    original, gap = result(), result()
    original["researchBoard"]["items"][0]["privateHistory"] = "must not persist"
    gap["researchBoard"]["items"] = []
    _, conn = await record(original, gap)
    saved = saved_snapshot(conn)
    assert saved["researchConditions"]["importantItems"] == []
    assert saved["verifiedBusinessBaseline"] == {
        "methodVersion": "research-board-v2",
        "items": repo.research_conditions(original)["importantItems"],
    }
    assert "privateHistory" not in json.dumps(saved)
    changed_memory = deepcopy(saved)
    changed_memory["verifiedBusinessBaseline"]["items"] = []
    assert repo.conditions(changed_memory) == repo.conditions(saved)
    assert repo.condition_fingerprint(changed_memory) == repo.condition_fingerprint(
        saved
    )


@pytest.mark.asyncio
async def test_a_different_verified_trend_after_the_gap_updates_the_same_business_baseline():
    original, gap = result(), result()
    gap["researchBoard"]["items"] = []
    _, gap_conn = await record(original, gap)
    opposite = result()
    opposite["researchBoard"]["items"][0]["category"] = "CHANGE"
    output, changed = await record(saved_snapshot(gap_conn), opposite)
    assert output["notice"]
    saved = saved_snapshot(changed)
    assert len(saved["verifiedBusinessBaseline"]["items"]) == 1
    assert saved["verifiedBusinessBaseline"]["items"][0]["category"] == "CHANGE"
    repeated, _ = await record(saved, deepcopy(opposite))
    assert not repeated["notice"]


@pytest.mark.asyncio
async def test_crossing_up_down_up_replaces_one_family_instead_of_remembering_seen_ids():
    previous = crossing("UP")
    for direction in ("DOWN", "UP"):
        gap = crossing("UP")
        gap["researchBoard"]["items"] = []
        output, conn = await record(previous, gap)
        assert not output["notice"]
        output, conn = await record(saved_snapshot(conn), crossing(direction))
        assert output["notice"]
        previous = saved_snapshot(conn)
        assert len(previous["verifiedBusinessBaseline"]["items"]) == 1
        assert previous["verifiedBusinessBaseline"]["items"][0]["key"].endswith(
            "_" + direction
        )
    repeated, _ = await record(previous, crossing("UP"))
    assert not repeated["notice"]


@pytest.mark.asyncio
async def test_semantic_version_change_discards_old_business_memory_and_stays_silent():
    before = crossing("DOWN")
    before["researchBoard"]["methodVersion"] = "research-board-v1"
    before["verifiedBusinessBaseline"] = {
        "methodVersion": "research-board-v1",
        "items": crossing("DOWN")["researchBoard"]["items"],
    }
    output, conn = await record(before, crossing("UP"))
    assert output["baseline"] and not output["notice"]
    saved = saved_snapshot(conn)
    assert saved["verifiedBusinessBaseline"]["methodVersion"] == "research-board-v2"
    assert len(saved["verifiedBusinessBaseline"]["items"]) == 1
    assert saved["verifiedBusinessBaseline"]["items"][0]["key"].endswith("_UP")
