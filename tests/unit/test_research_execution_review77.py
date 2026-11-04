"""Exact-query mismatch explanations and bounded transport degradation."""

from importlib import import_module
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.integrations import codex_client, codex_research
from tests.unit.test_research_execution import event, observe


def execution():
    return import_module("backend.integrations.research_execution")


MANIFEST = {
    "tasks": [
        {"taskId": "task-a", "query": "机器人 融资降温 最近90天"},
        {"taskId": "task-b", "query": "机器人 下游砍单 最近90天"},
    ]
}


def test_unmatched_actual_queries_are_observed_without_fuzzy_authorization():
    receipt = observe(
        event(
            "exact", action={"type": "search", "query": MANIFEST["tasks"][0]["query"]}
        ),
        event(
            "variant",
            action={"type": "search", "query": "2026年人形机器人融资减少 企业倒闭"},
        ),
    )
    stats = execution().execution_match_stats(receipt, MANIFEST)
    assert stats == {
        "version": "research-execution-match-v1",
        "classification": "EXECUTION_UNMATCHED",
        "plannedTaskCount": 2,
        "matchedTaskCount": 1,
        "observedQueryCount": 2,
        "matchedQueryHashCount": 1,
        "unmatchedQueryHashCount": 1,
    }
    assert execution().executed_task_ids(receipt, MANIFEST) == {"task-a"}
    assert "企业倒闭" not in json.dumps(stats, ensure_ascii=False)


def test_matched_unique_hash_count_does_not_duplicate_actual_searches():
    query = MANIFEST["tasks"][0]["query"]
    receipt = observe(
        event("one", action={"type": "search", "query": query}),
        event("two", action={"type": "search", "query": query}),
    )
    stats = execution().execution_match_stats(receipt, MANIFEST)
    assert stats["classification"] == "EXECUTION_MATCHED"
    assert stats["observedQueryCount"] == 2 and stats["matchedQueryHashCount"] == 1
    assert stats["matchedTaskCount"] == 1 and stats["unmatchedQueryHashCount"] == 0


@pytest.mark.parametrize("receipt", [None, {}, observe(event(kind="item.started"))])
def test_unknown_or_partial_receipt_does_not_claim_task_matching(receipt):
    stats = execution().execution_match_stats(receipt, MANIFEST)
    assert stats["classification"] == "EXECUTION_UNVERIFIED"
    assert stats["plannedTaskCount"] == 2 and stats["matchedTaskCount"] == 0
    assert (
        stats["matchedQueryHashCount"] is None
        and stats["unmatchedQueryHashCount"] is None
    )


def test_complete_no_search_is_distinct_from_unavailable_execution():
    stats = execution().execution_match_stats(observe(), MANIFEST)
    assert stats["classification"] == "EXECUTION_NOT_OBSERVED"
    assert stats["observedQueryCount"] == 0 and stats["matchedTaskCount"] == 0


@pytest.mark.parametrize(
    "manifest", [None, {"tasks": "PRIVATE"}, {"tasks": [{"taskId": "x", "query": []}]}]
)
def test_invalid_manifest_is_not_a_matching_claim(manifest):
    stats = execution().execution_match_stats(observe(event()), manifest)
    assert stats["classification"] == "EXECUTION_UNVERIFIED"
    assert stats["plannedTaskCount"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "space,code",
    [(1000, "RECEIPT_SUMMARIZED_SIZE_LIMIT"), (30, "RECEIPT_OMITTED_SIZE_LIMIT")],
)
async def test_runner_size_fallback_reason_reaches_client_without_growing_body(
    monkeypatch, space, code
):
    from backend import codex_runner

    receipt = observe(
        *(
            event(str(index), action={"type": "search", "query": str(index)})
            for index in range(256)
        )
    )
    report = {"summary": ""}
    base = {"report": report, "usage": {}, "model": "fixture"}
    report["summary"] = "x" * (
        codex_runner.MAX_OUTPUT_BYTES - space - len(codex_runner._json_bytes(base))
    )
    execute = AsyncMock(
        return_value=codex_research.ResearchResult(
            report, model="fixture", execution_receipt=receipt
        )
    )
    monkeypatch.setattr(codex_runner, "run_codex_research", execute)
    app = codex_runner.create_app(
        token="t" * 32, executable="native", daily_model="fixture", environment={}
    )
    original = httpx.AsyncClient
    responses = []

    async def record(response):
        await response.aread()
        responses.append(response)

    def client(**kwargs):
        kwargs.pop("trust_env", None)
        return original(
            transport=httpx.ASGITransport(app=app),
            event_hooks={"response": [record]},
            **kwargs,
        )

    monkeypatch.setattr(codex_client.httpx, "AsyncClient", client)
    settings = SimpleNamespace(
        codex_runner_url="http://runner",
        codex_runner_token="t" * 32,
        codex_model="fixture",
    )
    result = await codex_client._research(settings, "public", {}, model="fixture")
    assert result == report and result.execution_receipt_transport_reason == code
    assert responses[0].headers["X-WealthSail-Execution-Receipt-Reason"] == code
    assert len(responses[0].content) <= codex_runner.MAX_OUTPUT_BYTES
    assert set(responses[0].json()) == (
        {"report", "usage", "model", "executionReceipt"}
        if space == 1000
        else {"report", "usage", "model"}
    )
    execute.assert_awaited_once()


def test_transport_reason_has_closed_schema_and_survives_safe_error_metadata():
    from backend import codex_runner

    code = "RECEIPT_OMITTED_SIZE_LIMIT"
    result = codex_research.ResearchResult({}, execution_receipt_transport_reason=code)
    assert result.execution_receipt_transport_reason == code and result == {}
    invalid = codex_research.ResearchResult(
        {}, execution_receipt_transport_reason="PRIVATE reason"
    )
    assert invalid.execution_receipt_transport_reason is None
    error = codex_research.with_telemetry(
        ValueError("PRIVATE error"),
        usage={},
        model="fixture",
        inference_succeeded=True,
        execution_receipt_transport_reason=code,
    )
    detail = codex_runner._error_detail(error, "safe", capture_output=True)
    assert detail[
        "executionReceiptTransportReason"
    ] == code and "PRIVATE" not in json.dumps(detail)
    returned = codex_client._failure_metadata(
        ValueError("safe"), detail, capture_raw=True
    )
    assert returned.execution_receipt_transport_reason == code
    unsafe = codex_client._failure_metadata(
        ValueError("safe"),
        {**detail, "executionReceiptTransportReason": "PRIVATE"},
        capture_raw=True,
    )
    assert not getattr(unsafe, "execution_receipt_transport_reason", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,expected",
    [
        (None, None),
        ("PRIVATE untrusted reason", None),
        ("RECEIPT_OMITTED_SIZE_LIMIT", "RECEIPT_OMITTED_SIZE_LIMIT"),
    ],
)
async def test_old_envelope_and_reason_header_remain_independently_compatible(
    monkeypatch, header, expected
):
    original = httpx.AsyncClient
    response_headers = (
        {"X-WealthSail-Execution-Receipt-Reason": header} if header else {}
    )

    def client(**kwargs):
        return original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "report": {"summary": "public"},
                        "usage": {},
                        "model": "fixture",
                    },
                    headers=response_headers,
                )
            ),
            **kwargs,
        )

    monkeypatch.setattr(codex_client.httpx, "AsyncClient", client)
    settings = SimpleNamespace(
        codex_runner_url="http://runner",
        codex_runner_token="t" * 32,
        codex_model="fixture",
    )
    result = await codex_client._research(settings, "public", {}, model="fixture")
    assert result == {"summary": "public"} and result.execution_receipt is None
    assert result.execution_receipt_transport_reason == expected


@pytest.mark.asyncio
async def test_timeout_reason_header_survives_uncertain_error_rewrapping(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(
            504,
            json={"detail": {"message": "safe", "inferenceSucceeded": False}},
            headers={
                "X-WealthSail-Execution-Receipt-Reason": "RECEIPT_OMITTED_SIZE_LIMIT"
            },
        )

    monkeypatch.setattr(
        codex_client.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs),
    )
    settings = SimpleNamespace(
        codex_runner_url="http://runner",
        codex_runner_token="t" * 32,
        codex_model="fixture",
    )
    with pytest.raises(TimeoutError) as caught:
        await codex_client._research(settings, "public", {}, model="fixture")
    assert (
        caught.value.execution_receipt_transport_reason == "RECEIPT_OMITTED_SIZE_LIMIT"
    )
    assert caught.value.inference_uncertain is True and len(requests) == 1


@pytest.mark.asyncio
async def test_large_report_asgi_to_client_to_strict_core_keeps_verified_fund_risk(
    monkeypatch,
):
    """Offline sources and model output; strict business validation is real."""
    from backend import codex_runner
    from backend.macro.research_assurance import bind_execution
    from backend.macro.research_batch import evaluate_research_batch
    from backend.macro.research_workload import measured_workload
    from tests.unit.test_research_cost_review75 import prepared, RUN, NOW

    packet, raw, checks = prepared()
    direction = raw["valueDirections"][0]
    direction["plans"] = [
        plan for plan in direction["plans"] if plan["subjectId"] == "004432"
    ]
    direction.update(reviewIndustryIds=["NONFERROUS"], industryCycle=None)
    source = next(row for row in raw["sources"] if row["id"] == "s2")
    source.update(
        title="有色金属行业反方核查",
        quote="近期有色金属行业一级市场融资下降30%。近期有色金属行业企业欠薪、裁员和倒闭。近期有色金属行业下游砍单20%。近期有色金属行业产能过剩、价格战加剧。近期有色金属行业量产推迟。",
    )
    checks["s2"] = {
        **source,
        "verification": "SOURCE_CHECKED",
        "quoteMatched": True,
        "retrievedAt": NOW.isoformat(),
        "contentHash": "b" * 64,
    }
    review = next(
        row for row in raw["industryCycleReviews"] if row["industry"] == "NONFERROUS"
    )
    review["sourceIds"] = ["s2"]
    for check in review["riskChecks"]:
        check.update(status="FOUND", sourceIds=["s2"])
    # This oversized observation is quarantined; it cannot veto the independently
    # verified direction or bypass its industry, price and risk requirements.
    observation = {
        "key": "oversize-observation",
        "title": "公开观察",
        "thesis": "待审核。",
        "extra": [],
    }
    raw["topicCatalog"] = [observation]
    delta = (
        codex_runner.MAX_OUTPUT_BYTES
        - 500
        - len(json.dumps(raw, ensure_ascii=False).encode())
    )
    observation["extra"] = ["x" * 2000] * (delta // 2004)
    if delta % 2004 >= 4:
        observation["extra"].append("x" * (delta % 2004 - 4))
    receipt = observe(
        *(
            event(str(index), action={"type": "search", "query": str(index)})
            for index in range(256)
        )
    )
    execute = AsyncMock(
        return_value=codex_research.ResearchResult(
            raw, model="fixture", execution_receipt=receipt
        )
    )
    monkeypatch.setattr(codex_runner, "run_codex_research", execute)
    app = codex_runner.create_app(
        token="t" * 32, executable="native", daily_model="fixture", environment={}
    )
    original = httpx.AsyncClient

    def client(**kwargs):
        kwargs.pop("trust_env", None)
        return original(transport=httpx.ASGITransport(app=app), **kwargs)

    monkeypatch.setattr(codex_client.httpx, "AsyncClient", client)
    settings = SimpleNamespace(
        codex_runner_url="http://runner",
        codex_runner_token="t" * 32,
        codex_model="fixture",
    )
    result = await codex_client._research(settings, "public", {}, model="fixture")
    assert (
        result == raw
        and result.execution_receipt_transport_reason == "RECEIPT_SUMMARIZED_SIZE_LIMIT"
    )
    assert result.execution_receipt["coverage"] == "PARTIAL"
    bound = bind_execution(
        checks,
        result.execution_receipt,
        packet["researchDiscovery"]["auditManifest"],
        RUN,
        NOW,
        transport_reason=result.execution_receipt_transport_reason,
    )
    batch = evaluate_research_batch(
        result, packet, bound, research_id=RUN, validated_at=NOW
    )
    assert batch["strictPass"] and batch["autoPublishAllowed"]
    formal = batch["formalReport"]["valueDirections"][0]
    assert len(formal["plans"]) == 1 and formal["plans"][0]["subjectId"] == "004432"
    assert len(formal["industryCycle"]["confirmedRiskFlags"]) == 5
    assert formal["plans"][0]["industryCycle"]["leftDiscountScale"] == 1
    assert (
        batch["registry"]["coverage"]["executionReceiptTransportReason"]
        == "RECEIPT_SUMMARIZED_SIZE_LIMIT"
    )
    assert batch["registry"]["coverage"]["executionMatchedTaskCount"] == 0
    workload = measured_workload(
        100,
        result.execution_receipt,
        result.usage,
        transport_reason=result.execution_receipt_transport_reason,
    )
    assert (
        workload["executionReceiptTransportReason"] == "RECEIPT_SUMMARIZED_SIZE_LIMIT"
    )
    execute.assert_awaited_once()
