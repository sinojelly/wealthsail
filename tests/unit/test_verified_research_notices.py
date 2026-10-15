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
