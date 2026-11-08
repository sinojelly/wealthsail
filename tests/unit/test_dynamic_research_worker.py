"""A discovery catalog is persisted once, independent of the formal projection."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.unit.test_research_acceptance_worker import boundary
from tests.unit.test_research_repair import NOW, fixture
from tests.unit.test_daily_research_job import settings


@pytest.mark.asyncio
async def test_future_batch_keeps_thirty_topics_and_calls_model_once(monkeypatch):
    from backend.macro.research_topics import freeze_discovery_packet
    from backend.macro import research_batch

    mod, state = boundary(monkeypatch)
    batch = Mock(wraps=research_batch.evaluate_research_batch)
    monkeypatch.setattr(research_batch, "evaluate_research_batch", batch)
    original_claim = mod.claim_daily_run

    async def claim(*args, **kwargs):
        value = await original_claim(*args, **kwargs)
        return (
            {**value, "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
            if value
            else None
        )

    monkeypatch.setattr(mod, "claim_daily_run", claim)
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
    assert packet["researchDiscovery"]["version"] == "research-discovery-v3"
    manifest = packet["researchDiscovery"]["auditManifest"]
    original = deepcopy((packet, raw, checks))
    monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
    mod.run_codex_research.side_effect = None
    from backend.integrations.codex_research import ResearchResult
    from tests.unit.test_research_cost_review75 import executed_checks, KEY

    receipt = executed_checks(packet, checks)[KEY]["receipt"]
    mod.run_codex_research.return_value = ResearchResult(
        raw,
        usage={
            "prompt_tokens": 5000,
            "cached_input_tokens": 4000,
            "completion_tokens": 300,
        },
        execution_receipt=receipt,
    )
    mod.verify_research_sources.side_effect = None
    mod.verify_research_sources.return_value = checks
    result = await mod._execute_daily_research(object(), settings(), NOW, packet=packet)
    assert result["status"] == "READY"
    mod.run_codex_research.assert_awaited_once()
    mod.verify_research_sources.assert_awaited_once()
    batch.assert_called_once()
    assert (
        batch.call_args.kwargs["research_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )
    queued = mod.archive_repository.enqueue_research_archive.await_args
    registry = queued.kwargs["topic_registry"]
    keys = {row["key"] for row in registry["items"]}
    assert all(f"new-topic-{index}" in keys for index in range(30))
    assert all(
        not row["investmentEligible"]
        for row in registry["items"]
        if row["key"].startswith("new-topic-")
    )
    # v3 freezes targeted searches before dispatch. New model topics remain
    # explicit observations, without pretending their searches were executed.
    for row in registry["items"]:
        if row["key"].startswith("new-topic-"):
            assert row["status"] == "PENDING_REVIEW"
            assert row["reasonCode"] == "INDUSTRY_IDENTITY_PENDING"
    coverage = state["terminal"][0]["payload"]["researchDiscovery"]["coverage"]
    assert coverage["pendingScopeCount"] == 0
    assert coverage["observationScopeCount"] >= 30
    assert coverage["executionPlannedTaskCount"] == len(manifest["tasks"])
    assert coverage["executionMatchedTaskCount"] == len(manifest["tasks"])
    assert coverage["additionalModelCalls"] == 0
    assert state["artifacts"][2] == original[1]
    assert original == (packet, raw, checks)
    assert state["terminal"][0]["payload"]["valueDirections"]
    bound_checks = queued.args[4]
    assert bound_checks[KEY]["researchId"] == str(queued.args[1])
    assert bound_checks[KEY]["receipt"] == receipt
    workload = queued.args[6]["researchWorkload"]
    assert workload["promptBytes"] > 0 and workload["modelElapsedMs"] >= 0
    assert workload["sourceVerificationElapsedMs"] >= 0
    assert workload["observedSearchQueries"] == len(receipt["queryReceipts"])
    assert workload["inputTokens"] == 5000 and workload["cachedInputTokens"] == 4000
    assert workload["outputTokens"] == 300
    assert workload["preDispatchEnforcement"] is False


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


@pytest.mark.asyncio
async def test_zero_formal_delivers_news_topics_with_one_model_and_original_usage(
    monkeypatch,
):
    from backend.integrations.codex_research import ResearchResult
    from backend.macro import research_batch
    from tests.unit.test_research_independent_contracts import combined_case, RUN

    mod, state = boundary(monkeypatch)
    batch = Mock(wraps=research_batch.evaluate_research_batch)
    monkeypatch.setattr(research_batch, "evaluate_research_batch", batch)
    original_claim = mod.claim_daily_run

    async def claim(*args, **kwargs):
        value = await original_claim(*args, **kwargs)
        return {**value, "run_id": RUN} if value else None

    monkeypatch.setattr(mod, "claim_daily_run", claim)
    packet, raw, checks = combined_case()
    original = deepcopy((packet, raw, checks))
    receipt = checks.pop("__wealthsail_execution_v1")["receipt"]
    monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
    mod.run_codex_research.side_effect = None
    mod.run_codex_research.return_value = ResearchResult(
        raw,
        usage={
            "prompt_tokens": 5000,
            "cached_input_tokens": 4000,
            "completion_tokens": 300,
        },
        execution_receipt=receipt,
    )
    mod.verify_research_sources.side_effect = None
    mod.verify_research_sources.return_value = checks
    result = await mod._execute_daily_research(object(), settings(), NOW, packet=packet)
    assert result["status"] == "READY"
    mod.run_codex_research.assert_awaited_once()
    mod.verify_research_sources.assert_awaited_once()
    batch.assert_called_once()
    assert batch.call_args.kwargs["research_id"] == RUN
    terminal = state["terminal"][0]["payload"]
    assert terminal["valueDirections"] == [] and terminal["actionablePlans"] == []
    assert len(terminal["events"]) == 6 and len(terminal["upcoming"]) == 3
    queued = mod.archive_repository.enqueue_research_archive.await_args
    coverage = queued.kwargs["topic_registry"]["coverage"]
    assert coverage["discoveredTopicCount"] == 64
    assert coverage["executionMatchedTaskCount"] == 50
    assert coverage["researchDeliveryStatus"] == "VALIDATED_READ_ONLY"
    assert coverage["qualifiedFormalDirectionCount"] == 0
    assert coverage["additionalModelCalls"] == 0
    assert queued.args[6]["researchWorkload"]["inputTokens"] == 5000
    assert queued.args[6]["researchWorkload"]["cachedInputTokens"] == 4000
    assert queued.args[6]["researchWorkload"]["outputTokens"] == 300
    assert state["artifacts"][2] == original[1]
