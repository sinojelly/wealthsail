"""Future and historical V2 validation share a fixed, network-free core."""

from copy import deepcopy

import pytest

from backend.macro.research_acceptance_replay import run_offline_repair
from tests.unit.test_research_repair import NOW, RUN, fixture


@pytest.mark.asyncio
async def test_v2_replay_retains_twenty_discoveries_without_another_model_or_clock():
    from backend.macro.research_topics import freeze_discovery_packet

    packet, raw, checks = fixture()
    raw["valueDirections"].pop()
    raw["topicCatalog"] = [
        {
            "key": f"discovery-{index}",
            "title": f"新研究主题{index}",
            "thesis": "原时点线索待独立核查。",
            "industryClaims": [f"待注册产业范围{index}"],
            "relatedIndustryIds": [],
            "sourceIds": ["s2"],
            "unknowns": [],
            "pendingTasks": [],
        }
        for index in range(20)
    ]
    packet = freeze_discovery_packet(packet, NOW)
    from tests.unit.test_research_cost_review75 import executed_checks
    checks = executed_checks(packet, checks, run_id=RUN)
    before = deepcopy((packet, raw, checks))
    result = await run_offline_repair(
        {
            "researchId": RUN,
            "validatedAt": NOW.isoformat(),
            "raw": raw,
            "packet": packet,
            "sourceChecks": checks,
        }
    )
    assert result["strictPass"] and result["historicalPublishAllowed"]
    assert result["validatedAt"] == NOW.isoformat()
    assert result["repairVersion"] == "research-batch-v2"
    topics = result["topicRegistry"]["items"]
    assert sum(row["key"].startswith("discovery-") for row in topics) == 20
    assert all(
        not row["investmentEligible"]
        for row in topics
        if row["key"].startswith("discovery-")
    )
    assert result["topicRegistry"]["coverage"]["searchExecutionVerified"] is True
    assert result["formalReport"]["valueDirections"]
    assert before == (packet, raw, checks)
