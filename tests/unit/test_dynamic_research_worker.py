"""A discovery catalog is persisted once, independent of the formal projection."""

from copy import deepcopy

import pytest

from tests.unit.test_research_acceptance_worker import boundary
from tests.unit.test_research_repair import NOW, fixture
from tests.unit.test_daily_research_job import settings


@pytest.mark.asyncio
async def test_future_batch_keeps_thirty_topics_and_calls_model_once(monkeypatch):
    from backend.macro.research_topics import freeze_discovery_packet

    mod, state = boundary(monkeypatch)
    packet, raw, checks = fixture()
    raw["valueDirections"].pop()
    raw["topicCatalog"] = [
        {
            "key": f"new-topic-{index}",
            "title": f"动态观察{index}",
            "thesis": "公开线索待补证。",
            "industryClaims": [f"新研究范围{index}"],
            "relatedIndustryIds": [],
            "sourceIds": ["s2"],
            "unknowns": ["等待独立风险核查"],
            "pendingTasks": [],
        }
        for index in range(30)
    ]
    packet = freeze_discovery_packet(packet, NOW)
    original = deepcopy((packet, raw, checks))
    monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
    mod.run_codex_research.side_effect = None
    mod.run_codex_research.return_value = raw
    mod.verify_research_sources.side_effect = None
    mod.verify_research_sources.return_value = checks
    result = await mod._execute_daily_research(object(), settings(), NOW, packet=packet)
    assert result["status"] == "READY"
    mod.run_codex_research.assert_awaited_once()
    mod.verify_research_sources.assert_awaited_once()
    queued = mod.archive_repository.enqueue_research_archive.await_args
    registry = queued.kwargs["topic_registry"]
    keys = {row["key"] for row in registry["items"]}
    assert all(f"new-topic-{index}" in keys for index in range(30))
    assert all(
        not row["investmentEligible"]
        for row in registry["items"]
        if row["key"].startswith("new-topic-")
    )
    assert (
        state["terminal"][0]["payload"]["researchDiscovery"]["coverage"][
            "pendingScopeCount"
        ]
        >= 30
    )
    assert state["artifacts"][2] == original[1]
    assert original == (packet, raw, checks)


@pytest.mark.asyncio
async def test_oversize_discovery_prompt_never_dispatches_or_consumes_slot(monkeypatch):
    from backend.macro.research_topics import freeze_discovery_packet
    from backend.macro.daily_research import ResearchValidationError

    mod, state = boundary(monkeypatch)
    packet, _, _ = fixture()
    packet = freeze_discovery_packet(packet, NOW)
    monkeypatch.setattr(
        mod, "build_prompt", lambda _packet: "x" * (4 * 1024 * 1024 + 1)
    )
    with pytest.raises(ResearchValidationError) as error:
        await mod._execute_daily_research(object(), settings(), NOW, packet=packet)
    assert error.value.rule_id == "RESEARCH_INPUT_BUDGET_EXCEEDED"
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()
