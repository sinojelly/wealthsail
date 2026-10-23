"""The internal Codex broker never needs DB authority or a real model call."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from importlib import import_module
import json
from unittest.mock import AsyncMock

import httpx
import pytest


TOKEN = "runner-test-only-token-" + "x" * 40


def runner():
    return import_module("backend.codex_runner")


@asynccontextmanager
async def client_for(**kwargs):
    app = runner().create_app(token=TOKEN, **kwargs)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runner"
    ) as client:
        yield client, app


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.asyncio
async def test_health_is_process_only_and_other_routes_require_token(monkeypatch):
    mod = runner()
    preflight = AsyncMock()
    monkeypatch.setattr(mod, "preflight_codex", preflight)
    async with client_for() as (client, _):
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        assert (await client.get("/v1/preflight")).status_code == 401
        assert (await client.get("/v1/preflight", headers={"Authorization": "Bearer bad"})).status_code == 401
        response = await client.post("/v1/completions", json={"system_prompt": "safe", "user_prompt": "data"})
        assert response.status_code == 401
    preflight.assert_not_awaited()


@pytest.mark.parametrize("token", ["", "short", " " * 40, "x" * 32 + "\n"])
def test_missing_or_invalid_runner_token_fails_before_serving(token):
    with pytest.raises(ValueError, match="CODEX_RUNNER_TOKEN"):
        runner().create_app(token=token)


@pytest.mark.asyncio
async def test_preflight_returns_only_safe_metadata_and_sanitized_environment(monkeypatch):
    mod = runner()
    preflight = AsyncMock(return_value={"executable": "/bin/codex", "version": "codex-cli test", "selectedModel": None})
    monkeypatch.setattr(mod, "preflight_codex", preflight)
    async with client_for(environment={"PATH": "/bin", "CODEX_HOME": "/auth", "CODEX_RUNNER_TOKEN": TOKEN, "DATABASE_URL": "private", "JWT_SECRET": "private"}) as (client, _):
        response = await client.get("/v1/preflight", headers=auth())
    assert response.status_code == 200
    env = preflight.await_args.kwargs["environment"]
    assert env["CODEX_HOME"] == "/auth"
    assert not {"CODEX_RUNNER_TOKEN", "DATABASE_URL", "JWT_SECRET"} & set(env)


@pytest.mark.asyncio
async def test_completion_forwards_one_private_call_and_bounds_the_model(monkeypatch):
    mod = runner()
    execute = AsyncMock(return_value={"content": "answer", "model": "allowed", "usage": {}, "cost_cny": "0.0000", "debug": "must-not-return"})
    monkeypatch.setattr(mod, "complete_private", execute)
    async with client_for(model="allowed") as (client, _):
        rejected = await client.post("/v1/completions", headers=auth(), json={"system_prompt": "rules", "user_prompt": "private facts", "model": "costly-other"})
        response = await client.post("/v1/completions", headers=auth(), json={"system_prompt": "rules", "user_prompt": "private facts", "model": "allowed"})
    assert rejected.status_code == 400
    assert response.status_code == 200
    assert "debug" not in response.json()
    execute.assert_awaited_once()
    assert execute.await_args.args[1:3] == ("rules", "private facts")


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,daily,expected,expected_daily", [
    (" \t ", " \t ", None, None),
    (" allowed ", " daily ", "allowed", "daily"),
    (" allowed ", " \t ", "allowed", "allowed"),
])
async def test_environment_models_match_normalized_template_settings(
    monkeypatch, configured, daily, expected, expected_daily
):
    mod = runner()
    execute = AsyncMock(return_value={"content": "answer", "model": expected or "codex", "usage": {}, "cost_cny": "0"})
    research = AsyncMock(return_value={"summary": "public research"})
    monkeypatch.setattr(mod, "complete_private", execute)
    monkeypatch.setattr(mod, "run_codex_research", research)
    async with client_for(environment={"CODEX_MODEL": configured, "DAILY_RESEARCH_MODEL": daily}) as (client, _):
        completion_response = await client.post("/v1/completions", headers=auth(), json={"system_prompt": "rules", "user_prompt": "private facts", "model": expected})
        research_response = await client.post("/v1/research", headers=auth(), json={"prompt": "public packet", "schema": {"type": "object"}, "model": expected_daily})
    assert completion_response.status_code == 200
    assert research_response.status_code == 200
    assert execute.await_args.kwargs["model"] == expected
    assert research.await_args.kwargs["model"] == expected_daily


@pytest.mark.asyncio
async def test_errors_never_echo_prompt_token_or_raw_stderr_and_are_not_retried(monkeypatch):
    mod = runner()
    execute = AsyncMock(side_effect=RuntimeError("private prompt " + TOKEN))
    monkeypatch.setattr(mod, "complete_private", execute)
    async with client_for() as (client, _):
        response = await client.post("/v1/completions", headers=auth(), json={"system_prompt": "rules", "user_prompt": "private prompt"})
    assert response.status_code == 502
    assert TOKEN not in response.text and "private prompt" not in response.text
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_timeout_is_safe_and_does_not_retry(monkeypatch):
    mod = runner()
    execute = AsyncMock(side_effect=TimeoutError(TOKEN))
    monkeypatch.setattr(mod, "complete_private", execute)
    async with client_for() as (client, app):
        response = await client.post("/v1/completions", headers=auth(), json={"system_prompt": "rules", "user_prompt": "data"})
    assert response.status_code == 504 and TOKEN not in response.text
    assert app.state.capacity.active == 0
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_disconnect_cancels_runtime_and_releases_capacity(monkeypatch):
    mod = runner()
    started, closed = asyncio.Event(), asyncio.Event()

    async def execute(*_args, **_kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(mod, "complete_private", execute)
    async with client_for() as (client, app):
        request = asyncio.create_task(client.post("/v1/completions", headers=auth(), json={"system_prompt": "rules", "user_prompt": "data"}))
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert closed.is_set() and app.state.capacity.active == 0


@pytest.mark.asyncio
async def test_socket_disconnect_without_task_cancellation_stops_runtime(monkeypatch):
    mod = runner()
    started, closed = asyncio.Event(), asyncio.Event()

    async def execute(*_args, **_kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(mod, "complete_private", execute)
    app = mod.create_app(token=TOKEN)
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": json.dumps({"system_prompt": "rules", "user_prompt": "private"}).encode(), "more_body": False})
    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/completions", "raw_path": b"/v1/completions", "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json"), (b"authorization", f"Bearer {TOKEN}".encode())], "client": ("127.0.0.1", 1234), "server": ("runner", 8090)}
    task = asyncio.create_task(app(scope, incoming.get, send))
    await started.wait()
    incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(task, 1)
    assert closed.is_set() and app.state.capacity.active == 0
    assert sent[0]["status"] == 499


@pytest.mark.asyncio
async def test_cancelled_queued_request_never_starts_a_model(monkeypatch):
    mod = runner()
    started, finish = asyncio.Event(), asyncio.Event()

    async def execute(*_args, **_kwargs):
        started.set()
        await finish.wait()
        return {"content": "answer", "model": "codex", "usage": {}, "cost_cny": "0"}

    dispatch = AsyncMock(side_effect=execute)
    monkeypatch.setattr(mod, "complete_private", dispatch)
    async with client_for(max_concurrent=1) as (client, app):
        payload = {"system_prompt": "rules", "user_prompt": "data"}
        active = asyncio.create_task(client.post("/v1/completions", headers=auth(), json=payload))
        await started.wait()
        queued = asyncio.create_task(client.post("/v1/completions", headers=auth(), json=payload))
        while app.state.capacity.queued == 0:
            await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert dispatch.await_count == 1
        finish.set()
        assert (await active).status_code == 200
        assert app.state.capacity.active == 0 and app.state.capacity.queued == 0


@pytest.mark.asyncio
async def test_public_research_uses_separate_runtime_and_configured_daily_model(monkeypatch):
    mod = runner()
    execute = AsyncMock(return_value={"summary": "public"})
    private = AsyncMock()
    monkeypatch.setattr(mod, "run_codex_research", execute)
    monkeypatch.setattr(mod, "complete_private", private)
    async with client_for(model="general", daily_model="daily") as (client, _):
        response = await client.post("/v1/research", headers=auth(), json={"prompt": "public input", "schema": {"type": "object"}, "model": "daily", "timeout": 900})
    assert response.status_code == 200 and response.json() == {"summary": "public"}
    execute.assert_awaited_once()
    assert execute.await_args.kwargs["model"] == "daily"
    private.assert_not_awaited()


@pytest.mark.asyncio
async def test_payload_limit_rejects_actual_bytes_and_validation_does_not_echo_input(monkeypatch):
    mod = runner()
    execute = AsyncMock()
    monkeypatch.setattr(mod, "complete_private", execute)
    async with client_for() as (client, _):
        response = await client.post("/v1/completions", headers=auth(), content=b"x" * (mod.MAX_REQUEST_BYTES + 1))
        invalid = await client.post("/v1/completions", headers=auth(), json={"system_prompt": {"private": TOKEN}, "user_prompt": "text"})
    assert response.status_code == 413
    assert invalid.status_code == 422 and TOKEN not in invalid.text
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_output_limit_rejects_oversized_result(monkeypatch):
    mod = runner()
    execute = AsyncMock(return_value={"content": "x" * (mod.MAX_OUTPUT_BYTES + 1), "model": "codex", "usage": {}, "cost_cny": "0"})
    monkeypatch.setattr(mod, "complete_private", execute)
    async with client_for() as (client, _):
        response = await client.post("/v1/completions", headers=auth(), json={"system_prompt": "rules", "user_prompt": "data"})
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_capacity_rejects_full_queue_and_cancellation_releases_slot():
    gate = runner().Capacity(1, max_queued=1, queue_timeout=0.05)
    await gate.acquire()
    waiting = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0)
    with pytest.raises(runner().RunnerBusy):
        await gate.acquire()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    gate.release()
    await gate.acquire()
    gate.release()
    assert gate.active == 0 and gate.queued == 0


@pytest.mark.asyncio
async def test_full_broker_rejects_extra_request_without_dispatch(monkeypatch):
    mod = runner()
    started, finish = asyncio.Event(), asyncio.Event()

    async def execute(*_args, **_kwargs):
        started.set()
        await finish.wait()
        return {"content": "answer", "model": "codex", "usage": {}, "cost_cny": "0"}

    dispatch = AsyncMock(side_effect=execute)
    monkeypatch.setattr(mod, "complete_private", dispatch)
    async with client_for(max_concurrent=1, max_queued=0) as (client, app):
        payload = {"system_prompt": "rules", "user_prompt": "data"}
        active = asyncio.create_task(client.post("/v1/completions", headers=auth(), json=payload))
        await started.wait()
        rejected = await client.post("/v1/completions", headers=auth(), json=payload)
        assert rejected.status_code == 503
        assert dispatch.await_count == 1
        finish.set()
        assert (await active).status_code == 200
        assert app.state.capacity.active == 0


@pytest.mark.asyncio
async def test_stream_only_forwards_assistant_deltas_and_finishes(monkeypatch):
    mod = runner()

    async def stream(*_args, **_kwargs):
        yield "first"
        yield " second"

    monkeypatch.setattr(mod, "stream_private", stream)
    async with client_for() as (client, app):
        response = await client.post("/v1/chat/stream", headers=auth(), json={"system_prompt": "rules", "user_prompt": "private"})
    assert response.status_code == 200
    assert '"delta":"first"' in response.text and '"delta":" second"' in response.text
    assert '"done":true' in response.text
    assert app.state.capacity.active == 0


@pytest.mark.asyncio
async def test_stream_heartbeats_without_fabricating_assistant_text(monkeypatch):
    mod = runner()

    async def stream(*_args, **_kwargs):
        await asyncio.sleep(0.025)
        yield "actual assistant answer"

    monkeypatch.setattr(mod, "stream_private", stream)
    async with client_for(heartbeat_seconds=0.005) as (client, _):
        response = await client.post("/v1/chat/stream", headers=auth(), json={"system_prompt": "rules", "user_prompt": "private"})
    assert ": keep-alive\n\n" in response.text
    assert response.text.count('"delta"') == 1


@pytest.mark.asyncio
async def test_stream_disconnect_closes_runtime_before_releasing_capacity(monkeypatch):
    mod = runner()
    started, closed = asyncio.Event(), asyncio.Event()

    async def stream(*_args, **_kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            closed.set()

    monkeypatch.setattr(mod, "stream_private", stream)
    async with client_for() as (client, app):
        request = asyncio.create_task(client.post("/v1/chat/stream", headers=auth(), json={"system_prompt": "rules", "user_prompt": "private"}))
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert closed.is_set()
        assert app.state.capacity.active == 0


@pytest.mark.asyncio
async def test_stream_errors_are_redacted_and_output_limit_closes_runtime(monkeypatch):
    mod = runner()
    closed = asyncio.Event()

    async def stream(*_args, **_kwargs):
        try:
            yield "x" * (mod.MAX_OUTPUT_BYTES + 1)
        finally:
            closed.set()

    monkeypatch.setattr(mod, "stream_private", stream)
    async with client_for() as (client, app):
        response = await client.post("/v1/chat/stream", headers=auth(), json={"system_prompt": "rules", "user_prompt": TOKEN})
    assert '"error"' in response.text and '"delta"' not in response.text
    assert TOKEN not in response.text
    assert closed.is_set() and app.state.capacity.active == 0


@pytest.mark.asyncio
async def test_stream_wrapper_explicitly_closes_the_inner_runtime(monkeypatch):
    mod = runner()
    private = import_module("backend.integrations.codex_private")
    closed = asyncio.Event()

    async def stream(*_args, **_kwargs):
        try:
            yield "first"
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(private, "stream_chat", stream)
    wrapper = mod.stream_private("codex", "rules", "private")
    assert await wrapper.__anext__() == "first"
    await wrapper.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_stream_send_failure_closes_generator_suspended_at_a_delta(monkeypatch):
    mod = runner()
    closed = asyncio.Event()

    async def stream(*_args, **_kwargs):
        try:
            yield "first"
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(mod, "stream_private", stream)
    app = mod.create_app(token=TOKEN)
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": json.dumps({"system_prompt": "rules", "user_prompt": "private"}).encode(), "more_body": False})

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("closed socket")

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/chat/stream", "raw_path": b"/v1/chat/stream", "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json"), (b"authorization", f"Bearer {TOKEN}".encode())], "client": ("127.0.0.1", 1234), "server": ("runner", 8090)}
    with pytest.raises(Exception):
        await app(scope, incoming.get, send)
    assert closed.is_set() and app.state.capacity.active == 0


@pytest.mark.asyncio
async def test_chunked_payload_limit_does_not_trust_content_length(monkeypatch):
    mod = runner()
    execute = AsyncMock()
    monkeypatch.setattr(mod, "complete_private", execute)

    async def body():
        yield b"x" * mod.MAX_REQUEST_BYTES
        yield b"extra"

    async with client_for() as (client, _):
        response = await client.post("/v1/completions", headers=auth(), content=body())
    assert response.status_code == 413
    execute.assert_not_awaited()
