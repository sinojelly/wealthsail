"""Offline protocol-shaped events; no provider or recorded-user transcript."""

import hashlib
from importlib import import_module, util
import json

import pytest


def module():
    assert util.find_spec("backend.integrations.research_execution"), (
        "execution observer missing"
    )
    return import_module("backend.integrations.research_execution")


def event(item_id="tool-1", *, action=None, kind="item.completed"):
    return {
        "type": kind,
        "item": {
            "id": item_id,
            "type": "web_search",
            "query": "PRIVATE descriptive text",
            "action": action or {"type": "search", "query": "行业 融资降温"},
        },
    }


def stream(*events):
    return (
        "\n".join(json.dumps(value, ensure_ascii=False) for value in events) + "\n"
    ).encode()


def observe(*events):
    observer = module().ExecutionObserver()
    observer.feed(
        stream(
            {"type": "thread.started", "thread_id": "thread"},
            {"type": "turn.started"},
            *events,
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 12, "output_tokens": 2},
            },
        )
    )
    return observer.finish()


def test_completed_search_receipt_hashes_exact_query_without_saving_text():
    receipt = observe(event())
    assert receipt["version"] == "research-execution-v1"
    assert receipt["coverage"] == "COMPLETE" and receipt["turnCompleted"] is True
    assert receipt["counts"]["webToolCalls"] == 1
    assert receipt["counts"]["searchQueries"] == 1
    assert (
        receipt["queryReceipts"][0]["queryHash"]
        == hashlib.sha256("行业 融资降温".encode()).hexdigest()
    )
    assert "PRIVATE" not in json.dumps(receipt) and "融资降温" not in json.dumps(
        receipt, ensure_ascii=False
    )
    assert receipt["capabilities"]["preDispatchEnforcement"] is False
    assert receipt["counts"]["providerRetries"] is None


def test_started_updated_completed_web_item_is_counted_once_but_only_completed_binds():
    receipt = observe(event(kind="item.started"), event(kind="item.updated"), event())
    assert receipt["counts"]["webToolCalls"] == 1
    assert receipt["counts"]["searchActions"] == 1
    assert len(receipt["queryReceipts"]) == 1
    partial = observe(event(kind="item.started"))
    assert partial["coverage"] == "PARTIAL" and not partial["queryReceipts"]


def test_multiquery_search_and_page_actions_are_separate_observed_units():
    receipt = observe(
        event(action={"type": "search", "queries": ["query-a", "query-b"]}),
        event("open", action={"type": "openPage", "url": "https://private.invalid"}),
        event(
            "find",
            action={
                "type": "findInPage",
                "url": "https://private.invalid",
                "pattern": "PRIVATE",
            },
        ),
    )
    assert receipt["counts"] == {
        "webToolCalls": 3,
        "searchActions": 1,
        "searchQueries": 2,
        "pageOpens": 1,
        "pageFinds": 1,
        "otherWebActions": 0,
        "providerRetries": None,
        "clientRetries": 0,
    }
    assert "private.invalid" not in json.dumps(receipt)


def test_unknown_action_is_partial_and_does_not_claim_unobserved_zero_counters():
    receipt = observe(event(action={"type": "futureRead"}))
    assert receipt["coverage"] == "PARTIAL" and not receipt["queryReceipts"]
    assert receipt["counts"]["otherWebActions"] == 1
    assert receipt["counts"]["searchQueries"] is None


def test_chunked_utf8_stream_and_repeated_completion_keep_same_receipt():
    payload = stream(
        {"type": "thread.started", "thread_id": "thread"},
        event(),
        event(),
        {"type": "turn.completed"},
    )
    observer = module().ExecutionObserver()
    for byte in payload:
        observer.feed(bytes([byte]))
    receipt = observer.finish()
    assert receipt["coverage"] == "COMPLETE" and receipt["counts"]["webToolCalls"] == 1
    assert len(receipt["queryReceipts"]) == 1


@pytest.mark.parametrize("suffix", [b'{"type":', b"not-json\n", b"\xff\n"])
def test_incomplete_or_invalid_stream_never_produces_complete_receipt(suffix):
    observer = module().ExecutionObserver()
    observer.feed(stream(event(), {"type": "turn.completed"}) + suffix)
    assert observer.finish()["coverage"] == "PARTIAL"


def test_no_events_are_unavailable_rather_than_zero_searches():
    receipt = module().ExecutionObserver().finish()
    assert receipt["coverage"] == "UNAVAILABLE"
    assert receipt["counts"]["webToolCalls"] is None


def test_event_identity_conflict_and_receipt_limit_fail_closed():
    conflict = observe(event(), event(action={"type": "search", "query": "different"}))
    assert conflict["coverage"] == "PARTIAL"
    limited = observe(
        *(
            event(str(index), action={"type": "search", "query": str(index)})
            for index in range(257)
        )
    )
    assert limited["coverage"] == "PARTIAL" and len(limited["queryReceipts"]) <= 256


def test_sanitizer_rejects_forged_shapes_and_binds_only_complete_exact_hash():
    mod = module()
    receipt = observe(event())
    assert mod.sanitize_execution_receipt({**receipt, "raw": "PRIVATE"}) is None
    assert mod.sanitize_execution_receipt({**receipt, "turnCompleted": 1}) is None
    tasks = [
        {"taskId": "task-1", "query": "行业 融资降温"},
        {"taskId": "task-2", "query": "行业 融资降温 "},
    ]
    assert mod.executed_task_ids(receipt, {"tasks": tasks}) == {"task-1"}
    assert not mod.executed_task_ids(
        {**receipt, "coverage": "PARTIAL"}, {"tasks": tasks}
    )


def test_soft_query_budget_is_reported_without_claiming_pre_dispatch_control():
    receipt = observe(
        *(
            event(str(index), action={"type": "search", "query": str(index)})
            for index in range(51)
        )
    )
    assert receipt["softQueryLimit"] == 50 and receipt["softQueryLimitExceeded"] is True
    assert receipt["coverage"] == "COMPLETE"
    assert receipt["capabilities"]["preDispatchEnforcement"] is False


def test_malformed_event_type_and_surrogate_queries_are_partial_without_crashing():
    for value in ({"type": []}, event(action={"type": "search", "query": "\ud800"})):
        observer = module().ExecutionObserver()
        observer.feed(json.dumps(value).encode() + b"\n")
        assert observer.finish()["coverage"] == "PARTIAL"
    receipt = observe(event())
    assert module().sanitize_execution_receipt({**receipt, "coverage": []}) is None


@pytest.mark.asyncio
async def test_public_adapter_attaches_observation_outside_formal_report(monkeypatch):
    from pathlib import Path
    from backend.integrations import codex_research

    async def execute(args, **kwargs):
        data = stream({"type": "thread.started"}, event(), {"type": "turn.completed"})
        kwargs["stdout_observer"](data)
        Path(args[args.index("--output-last-message") + 1]).write_text(
            '{"summary":"public"}'
        )
        return 0, data, b""

    monkeypatch.setattr(codex_research, "_run", execute)
    result = await codex_research.run_codex_research("native", "PRIVATE prompt", {})
    assert result.execution_receipt["coverage"] == "COMPLETE"
    assert json.dumps(result) == '{"summary": "public"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [TimeoutError, __import__("asyncio").CancelledError]
)
async def test_adapter_failure_keeps_partial_receipt_and_never_retries(
    monkeypatch, failure
):
    from backend.integrations import codex_research

    calls = []

    async def execute(args, **kwargs):
        calls.append(args)
        kwargs["stdout_observer"](
            stream({"type": "thread.started"}, event(kind="item.started"))
        )
        raise failure("PRIVATE failure")

    monkeypatch.setattr(codex_research, "_run", execute)
    with pytest.raises(failure) as caught:
        await codex_research.run_codex_research("native", "public", {})
    assert caught.value.execution_receipt["coverage"] == "PARTIAL"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_remote_runner_client_roundtrip_preserves_receipt_metadata_only(
    monkeypatch,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import httpx
    from backend import codex_runner
    from backend.integrations import codex_client, codex_research

    receipt = observe(event())
    result = codex_research.ResearchResult({"summary": "public"}, model="fixture")
    result.execution_receipt = receipt
    monkeypatch.setattr(
        codex_runner, "run_codex_research", AsyncMock(return_value=result)
    )
    app = codex_runner.create_app(
        token="t" * 32, executable="native", daily_model="fixture", environment={}
    )
    original = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("trust_env", None)
        return original(transport=httpx.ASGITransport(app=app), **kwargs)

    monkeypatch.setattr(codex_client.httpx, "AsyncClient", factory)
    settings = SimpleNamespace(
        codex_runner_url="http://runner",
        codex_runner_token="t" * 32,
        codex_model="fixture",
    )
    returned = await codex_client._research(settings, "public", {}, model="fixture")
    assert returned == {"summary": "public"}
    assert returned.execution_receipt == receipt


def test_failure_transport_whitelists_receipt_and_rejects_raw_event_fields():
    from backend import codex_runner
    from backend.integrations import codex_client, codex_research

    receipt = observe(event())
    error = codex_research.with_telemetry(
        ValueError("PRIVATE"), usage={}, model="fixture", inference_succeeded=True
    )
    error.execution_receipt = receipt
    detail = codex_runner._error_detail(error, "safe", capture_output=True)
    assert detail["executionReceipt"] == receipt
    restored = codex_client._failure_metadata(
        ValueError("safe"), detail, capture_raw=True
    )
    assert restored.execution_receipt == receipt
    forged = codex_client._failure_metadata(
        ValueError("safe"),
        {**detail, "executionReceipt": {**receipt, "raw": "PRIVATE"}},
        capture_raw=True,
    )
    assert not hasattr(forged, "execution_receipt")


@pytest.mark.asyncio
async def test_real_subprocess_reader_observes_before_process_exit(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from backend.integrations import codex_research

    observed, finish = asyncio.Event(), asyncio.Event()
    chunks = [stream({"type": "thread.started"}, event())]

    async def read(_size):
        if chunks:
            return chunks.pop()
        await finish.wait()
        return b""

    child = SimpleNamespace(
        returncode=None,
        stdout=SimpleNamespace(read=read),
        stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
        stdin=SimpleNamespace(
            write=lambda _: None, drain=AsyncMock(), close=lambda: None
        ),
    )

    async def wait():
        await finish.wait()
        child.returncode = 0
        return 0

    child.wait = wait
    observer = module().ExecutionObserver()

    def record(chunk):
        observer.feed(chunk)
        observed.set()

    monkeypatch.setattr(
        codex_research.asyncio, "create_subprocess_exec", AsyncMock(return_value=child)
    )
    task = asyncio.create_task(
        codex_research._run(["native"], cwd=".", env={}, stdout_observer=record)
    )
    await asyncio.wait_for(observed.wait(), 1)
    assert not task.done() and observer.finish()["counts"]["webToolCalls"] == 1
    finish.set()
    await task


@pytest.mark.parametrize(
    "field,value",
    [
        ("counts", {"webToolCalls": True}),
        ("capabilities", {"preDispatchEnforcement": True}),
        ("queryReceipts", [{"queryHash": "PRIVATE"}]),
        ("softQueryLimitExceeded", 1),
    ],
)
def test_receipt_metadata_rejects_invalid_or_unsafe_values(field, value):
    receipt = observe(event())
    assert module().sanitize_execution_receipt({**receipt, field: value}) is None


@pytest.mark.parametrize(
    "value",
    [
        {"type": "tool.started"},
        {"type": "item.completed", "item": {"id": "new", "type": "future_web_tool"}},
    ],
)
def test_unknown_protocol_event_never_claims_complete_observation(value):
    receipt = observe(value)
    assert receipt["coverage"] == "PARTIAL" and receipt["eventsComplete"] is False


@pytest.mark.parametrize(
    "change", ["duplicate", "missing", "count", "actions", "completion"]
)
def test_contradictory_complete_receipt_is_rejected(change):
    receipt = observe(event())
    if change == "duplicate":
        receipt["queryReceipts"] *= 2
        receipt["counts"]["searchQueries"] = 2
    elif change == "missing":
        receipt["queryReceipts"] = []
    elif change == "count":
        receipt["counts"]["webToolCalls"] = 0
    elif change == "actions":
        receipt["counts"]["searchActions"] = 2
    else:
        receipt["eventsComplete"] = False
    assert module().sanitize_execution_receipt(receipt) is None


@pytest.mark.asyncio
async def test_remote_timeout_preserves_observed_receipt_without_retry(monkeypatch):
    from tests.unit.test_codex_client import settings, transport
    from backend.integrations import codex_client
    import httpx

    receipt = observe(event(kind="item.started"))
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(
            504,
            json={
                "detail": {
                    "message": "safe",
                    "executionReceipt": receipt,
                    "inferenceSucceeded": False,
                }
            },
        )

    transport(monkeypatch, response)
    with pytest.raises(TimeoutError) as caught:
        await codex_client._research(settings(), "public", {})
    assert caught.value.execution_receipt == receipt
    assert caught.value.inference_uncertain is True and len(requests) == 1


def test_oversize_unparsed_event_marks_web_counts_unknown():
    observer = module().ExecutionObserver()
    observer.feed(
        stream({"type": "thread.started"})
        + b"x" * (module().MAX_LINE_BYTES + 1)
        + b"\n"
        + stream({"type": "turn.completed"})
    )
    receipt = observer.finish()
    assert receipt["coverage"] == "PARTIAL"
    assert receipt["counts"]["webToolCalls"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("space,include_summary", [(1000, True), (30, False)])
async def test_receipt_byte_budget_falls_back_without_discarding_model_report(
    monkeypatch, space, include_summary
):
    from unittest.mock import AsyncMock
    import httpx
    from backend import codex_runner
    from backend.integrations.codex_research import ResearchResult

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
    result = ResearchResult(report, model="fixture", execution_receipt=receipt)
    execute = AsyncMock(return_value=result)
    monkeypatch.setattr(codex_runner, "run_codex_research", execute)
    app = codex_runner.create_app(
        token="t" * 32, executable="native", daily_model="fixture", environment={}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://runner",
    ) as client:
        response = await client.post(
            "/v1/research",
            headers={"Authorization": "Bearer " + "t" * 32},
            json={"prompt": "public", "schema": {}, "model": "fixture"},
        )
    assert response.status_code == 200 and response.json()["report"] == report
    assert len(response.content) <= codex_runner.MAX_OUTPUT_BYTES
    if include_summary:
        compact = response.json()["executionReceipt"]
        assert compact["coverage"] == "PARTIAL" and compact["eventsComplete"] is False
        assert compact["queryReceipts"] == [] and compact["counts"] == receipt["counts"]
    else:
        assert set(response.json()) == {"report", "usage", "model"}
    assert result.execution_receipt == receipt
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_base_research_envelope_over_budget_is_safe_http_failure(monkeypatch):
    from unittest.mock import AsyncMock
    import httpx
    from backend import codex_runner
    from backend.integrations.codex_research import ResearchResult

    report = {"summary": ""}
    base = {"report": report, "usage": {}, "model": "fixture"}
    report["summary"] = "x" * (
        codex_runner.MAX_OUTPUT_BYTES + 1 - len(codex_runner._json_bytes(base))
    )
    execute = AsyncMock(return_value=ResearchResult(report, model="fixture"))
    monkeypatch.setattr(codex_runner, "run_codex_research", execute)
    app = codex_runner.create_app(
        token="t" * 32, executable="native", daily_model="fixture", environment={}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://runner",
    ) as client:
        response = await client.post(
            "/v1/research",
            headers={"Authorization": "Bearer " + "t" * 32},
            json={"prompt": "PRIVATE prompt", "schema": {}, "model": "fixture"},
        )
    assert response.status_code == 502
    assert response.json()["detail"]["inferenceSucceeded"] is True
    assert "PRIVATE" not in response.text and len(response.content) < 1024
    execute.assert_awaited_once()
