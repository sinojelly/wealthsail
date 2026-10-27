"""Codex transports never use old provider keys or execute a real model."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.integrations import codex_client


def settings(**overrides):
    values = {
        "codex_runner_url": "http://codex-runner:8001",
        "codex_runner_token": "runner-test-token",
        "codex_model": None,
        "codex_path": None,
        "codex_timeout_seconds": 90,
        "codex_chat_timeout_seconds": 900,
        "daily_research_codex_path": None,
        "model_api_key": "old-key-must-not-be-used",
        "model_base_url": "https://old-provider.invalid/v1",
        "model_name": "old-model-must-not-be-used",
    }
    return SimpleNamespace(**{**values, **overrides})


def transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        codex_client.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.asyncio
async def test_completion_uses_internal_codex_auth_without_legacy_provider(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        assert str(request.url) == "http://codex-runner:8001/v1/completions"
        assert request.headers["authorization"] == "Bearer runner-test-token"
        payload = json.loads(request.content)
        assert payload == {
            "system_prompt": "tenant-scoped facts",
            "user_prompt": "please summarize",
            "json_mode": True,
            "model": None,
        }
        assert b"old-key" not in request.content
        return httpx.Response(
            200,
            json={"content": '{"summary":"ok"}', "model": "codex-default", "usage": {}, "cost_cny": "0.0000"},
        )

    transport(monkeypatch, handler)
    result = await codex_client.completion(
        settings(), system_prompt="tenant-scoped facts", user_prompt="please summarize"
    )
    assert result["content"] == '{"summary":"ok"}'
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_missing_codex_configuration_never_activates_old_key_or_local_cli(monkeypatch):
    preflight = AsyncMock()
    monkeypatch.setattr(codex_client, "_preflight_local", preflight)
    with pytest.raises(codex_client.CodexNotConfiguredError):
        await codex_client.completion(
            settings(codex_runner_url="", codex_runner_token=""),
            system_prompt="scope", user_prompt="text",
        )
    preflight.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_local_path_calls_private_codex_with_default_model_none(monkeypatch):
    complete = AsyncMock(return_value={"content": "local", "model": "codex", "usage": {}, "cost_cny": "0"})
    monkeypatch.setattr(codex_client, "_private", lambda: SimpleNamespace(complete=complete))
    monkeypatch.setattr(codex_client, "_preflight_local", AsyncMock(return_value={"executable": "native-codex"}))
    result = await codex_client.completion(
        settings(codex_runner_url="", codex_path="explicit-native"),
        system_prompt="scope", user_prompt="text", json_mode=False,
    )
    assert result["content"] == "local"
    complete.assert_awaited_once_with(
        "native-codex", "scope", "text", json_mode=False, model=None, timeout=90,
    )


@pytest.mark.asyncio
async def test_http_failures_are_not_retried_or_exposed(monkeypatch):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="private stderr: runner-test-token")

    transport(monkeypatch, handler)
    with pytest.raises(codex_client.CodexCallError) as failure:
        await codex_client.completion(settings(), system_prompt="scope", user_prompt="text")
    assert calls == 1
    assert "private stderr" not in str(failure.value)
    assert "runner-test-token" not in str(failure.value)


@pytest.mark.asyncio
async def test_remote_stream_only_yields_agent_text_and_done(monkeypatch):
    async def handler(request):
        assert request.url.path == "/v1/chat/stream"
        return httpx.Response(
            200, text=': heartbeat\n\ndata: {"delta":"你"}\n\ndata: {"delta":"好"}\n\ndata: {"done":true}\n\n'
        )

    transport(monkeypatch, handler)
    rows = [event async for event in codex_client.stream_chat(settings(), system_prompt="scope", user_prompt="text")]
    assert rows == [{"delta": "你"}, {"delta": "好"}, {"done": True}]


@pytest.mark.asyncio
async def test_remote_stream_error_is_sanitized(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, text='data: {"error":"secret stderr"}\n\n'))
    with pytest.raises(codex_client.CodexCallError) as failure:
        _ = [event async for event in codex_client.stream_chat(settings(), system_prompt="scope", user_prompt="text")]
    assert "secret stderr" not in str(failure.value)


@pytest.mark.asyncio
async def test_oversized_response_is_rejected(monkeypatch):
    monkeypatch.setattr(codex_client, "_MAX_OUTPUT_BYTES", 32)
    transport(monkeypatch, lambda request: httpx.Response(200, content=b"x" * 33))
    with pytest.raises(codex_client.CodexCallError):
        await codex_client.completion(settings(), system_prompt="scope", user_prompt="text")


@pytest.mark.asyncio
async def test_preflight_and_research_use_separate_public_endpoints(monkeypatch):
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, json={"executable": "server-native", "version": "codex-cli test"})
        assert json.loads(request.content) == {"prompt": "public packet", "schema": {"type": "object"}, "model": None, "timeout": 900}
        return httpx.Response(200, json={"summary": "public"})

    transport(monkeypatch, handler)
    assert (await codex_client.preflight(settings()))["version"] == "codex-cli test"
    assert await codex_client.research(settings(), "public packet", {"type": "object"}, timeout=900) == {"summary": "public"}
    assert paths == ["/v1/preflight", "/v1/research"]


@pytest.mark.asyncio
async def test_remote_research_envelope_preserves_strict_report_and_usage(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json={
        "report": {"summary": "public"}, "usage": {"prompt_tokens": 80, "cached_input_tokens": 60, "completion_tokens": 9, "total_tokens": 89}, "model": "fixture",
    }))
    result = await codex_client.research(settings(), "public", {"type": "object"})
    assert result == {"summary": "public"}
    assert result.usage == {"prompt_tokens": 80, "cached_input_tokens": 60, "completion_tokens": 9, "total_tokens": 89}
    assert result.model == "fixture"


@pytest.mark.asyncio
async def test_remote_stream_retains_observed_usage_on_terminal_event(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, text='data: {"delta":"answer"}\n\ndata: {"done":true,"usage":{"prompt_tokens":20,"cached_input_tokens":10,"completion_tokens":4,"total_tokens":24},"model":"fixture"}\n\n'))
    result = [event async for event in codex_client.stream_chat(settings(), system_prompt="scope", user_prompt="chat")]
    assert result[-1] == {"done": True, "usage": {"prompt_tokens": 20, "cached_input_tokens": 10, "completion_tokens": 4, "total_tokens": 24}, "model": "fixture"}


@pytest.mark.asyncio
async def test_account_reads_runner_metadata_and_degrades_safely_when_unavailable(monkeypatch):
    assert callable(getattr(codex_client, "account", None)), "client account telemetry missing"
    transport(monkeypatch, lambda request: httpx.Response(503, text="private runner error"))
    result = await codex_client.account(settings())
    assert result["status"] == "UNAVAILABLE" and result["windows"] == []
    assert "private runner error" not in json.dumps(result)


@pytest.mark.asyncio
async def test_remote_output_failure_retains_successful_inference_usage(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(502, json={"detail": {
        "message": "safe output failure", "usage": {"prompt_tokens": 18, "cached_input_tokens": 9, "completion_tokens": 2, "total_tokens": 20},
        "model": "fixture", "inferenceSucceeded": True, "private": "must-not-propagate",
    }}))
    with pytest.raises(codex_client.CodexCallError) as failure:
        await codex_client.completion(settings(), system_prompt="facts", user_prompt="report")
    assert getattr(failure.value, "usage", None) == {"prompt_tokens": 18, "cached_input_tokens": 9, "completion_tokens": 2, "total_tokens": 20}
    assert failure.value.inference_succeeded is True
    assert failure.value.model == "fixture"
    assert "must-not-propagate" not in str(failure.value)


@pytest.mark.asyncio
async def test_failed_local_stream_keeps_usage_already_observed(monkeypatch):
    from backend.integrations.codex_research import CodexExecutionError

    async def stream(*args, **kwargs):
        kwargs["usage_callback"]({"prompt_tokens": 40, "cached_input_tokens": 20, "completion_tokens": 3, "total_tokens": 43})
        yield "partial"
        raise CodexExecutionError("private response detail")

    monkeypatch.setattr(codex_client, "_private", lambda: SimpleNamespace(stream_chat=stream))
    monkeypatch.setattr(codex_client, "_preflight_local", AsyncMock(return_value={"executable": "fixture"}))
    with pytest.raises(codex_client.CodexCallError) as failure:
        _ = [event async for event in codex_client.stream_chat(settings(codex_runner_url="", codex_path="fixture"), system_prompt="scope", user_prompt="chat")]
    assert getattr(failure.value, "usage", None) == {"prompt_tokens": 40, "cached_input_tokens": 20, "completion_tokens": 3, "total_tokens": 43}
    assert failure.value.inference_succeeded is False


@pytest.mark.asyncio
async def test_account_success_strips_nested_unexpected_metadata(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json={
        "status": "READY", "provider": "codex", "authType": "chatgpt", "email": "operator@example.test", "planType": "pro", "observedAt": "2026-10-27T00:00:00+00:00",
        "windows": [{"key": "codex:primary", "label": "5h", "usedPercent": 12, "remainingPercent": 88, "windowMinutes": 300, "resetsAt": 1800000000, "accessToken": "must-not-return"}],
        "credits": {"balance": "20", "hasCredits": True, "unlimited": False, "accessToken": "must-not-return"},
        "limitations": [], "accessToken": "must-not-return",
    }))
    result = await codex_client.account(settings())
    assert result["email"] == "operator@example.test"
    assert result["windows"][0]["remainingPercent"] == 88
    assert "must-not-return" not in json.dumps(result)


@pytest.mark.asyncio
async def test_cancellation_closes_remote_stream(monkeypatch):
    started = asyncio.Event()
    closed = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Future()
            yield b""

        async def aclose(self):
            closed.set()

    transport(monkeypatch, lambda request: httpx.Response(200, stream=Stream()))

    async def consume():
        return [row async for row in codex_client.stream_chat(settings(), system_prompt="scope", user_prompt="text")]

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("private"), httpx.ReadError("private"), httpx.WriteTimeout("private")])
async def test_dispatched_research_transport_failure_is_uncertain(monkeypatch, failure):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        raise failure

    transport(monkeypatch, handler)
    with pytest.raises(TimeoutError, match="uncertain"):
        await codex_client.research(settings(), "public", {}, timeout=900)
    assert calls == 1


@pytest.mark.asyncio
async def test_research_server_timeout_is_uncertain_without_retry(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(504, text="private stderr"))
    with pytest.raises(TimeoutError, match="uncertain"):
        await codex_client.research(settings(), "public", {}, timeout=900)


@pytest.mark.asyncio
async def test_research_connection_failure_before_dispatch_is_not_uncertain(monkeypatch):
    transport(monkeypatch, lambda request: (_ for _ in ()).throw(httpx.ConnectError("private")))
    with pytest.raises(codex_client.CodexCallError):
        await codex_client.research(settings(), "public", {}, timeout=900)


@pytest.mark.asyncio
async def test_request_input_limit_prevents_dispatch(monkeypatch):
    monkeypatch.setattr(codex_client, "_MAX_INPUT_BYTES", 32)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    transport(monkeypatch, handler)
    with pytest.raises(codex_client.CodexCallError):
        await codex_client.completion(settings(), system_prompt="x" * 33, user_prompt="text")
    assert called is False


@pytest.mark.asyncio
async def test_sse_byte_limit_applies_before_a_complete_line_is_buffered(monkeypatch):
    monkeypatch.setattr(codex_client, "_MAX_OUTPUT_BYTES", 32)

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 33
            raise AssertionError("client must stop before reading more unbroken data")

    transport(monkeypatch, lambda request: httpx.Response(200, stream=Stream()))
    with pytest.raises(codex_client.CodexCallError, match="exceeds"):
        _ = [row async for row in codex_client.stream_chat(settings(), system_prompt="scope", user_prompt="text")]


@pytest.mark.asyncio
async def test_client_contract_matches_real_runner_without_model_execution(monkeypatch):
    from backend import codex_runner

    token = "runner-fixture-" + "a" * 32
    monkeypatch.setattr(codex_runner, "preflight_codex", AsyncMock(return_value={
        "executable": "native", "version": "codex-cli fixture", "selectedModel": None,
    }))
    completion = AsyncMock(return_value={"content": '{"summary":"ok"}', "model": "codex-fixture", "usage": {}, "cost_cny": "0"})
    public = AsyncMock(return_value={"summary": "public"})

    async def stream(*_args, **_kwargs):
        yield "private answer"

    monkeypatch.setattr(codex_runner, "complete_private", completion)
    monkeypatch.setattr(codex_runner, "stream_private", stream)
    monkeypatch.setattr(codex_runner, "run_codex_research", public)
    app = codex_runner.create_app(token=token, environment={"CODEX_HOME": "fixture-home"})
    client = httpx.AsyncClient
    monkeypatch.setattr(codex_client.httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.ASGITransport(app=app), **kwargs,
    ))
    config = settings(codex_runner_token=token)
    assert (await codex_client.preflight(config))["version"] == "codex-cli fixture"
    assert (await codex_client.completion(config, system_prompt="tenant facts", user_prompt="report"))["content"] == '{"summary":"ok"}'
    assert [row async for row in codex_client.stream_chat(config, system_prompt="tenant facts", user_prompt="chat")] == [{"delta": "private answer"}, {"done": True}]
    assert await codex_client.research(config, "public facts", {"type": "object"}) == {"summary": "public"}
    assert completion.await_count == public.await_count == 1
    assert app.state.capacity.active == app.state.capacity.queued == 0
