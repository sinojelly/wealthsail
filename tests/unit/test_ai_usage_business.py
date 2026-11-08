"""Business entry points retain inference evidence without creating phantom calls."""

import asyncio
from builtins import BaseExceptionGroup
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.ai_usage import repository as usage_repository
from backend.chat import api as chat_api
from backend.chat.service import ChatService
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.integrations import codex_client
from backend.integrations.codex_research import ResearchResult, with_telemetry
from backend.jobs import daily_research
from backend.reports import service as report_service


@pytest.fixture
def observed(monkeypatch):
    events = []

    async def persist(_pool, tenant, user, event, data):
        events.append((event, deepcopy(data), tenant, user))

    monkeypatch.setattr(usage_repository, "persist", persist)
    monkeypatch.setattr(
        codex_client,
        "account",
        AsyncMock(
            return_value={"status": "UNAVAILABLE", "windows": [], "credits": None}
        ),
    )
    return events


@asynccontextmanager
async def transaction(*args, **kwargs):
    class Connection:
        @asynccontextmanager
        async def transaction(self):
            yield self

        async def fetchval(self, sql, request_id, stamp, stage, stages):
            assert sql.startswith(
                "UPDATE research.daily_research_refresh_requests SET execution_stage=$3,stage_updated_at=$2"
            )
            assert "status='RUNNING'" in sql and "RETURNING id" in sql
            assert isinstance(stamp, datetime) and stamp.utcoffset() is not None
            assert stage in stages
            return request_id

    yield Connection()


def finished(events, event):
    return [data for name, data, _tenant, _user in events if name == event]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["completion", "chat"])
@pytest.mark.parametrize("failure", ["timeout", "disconnect", "gateway_timeout"])
async def test_remote_dispatched_inference_transport_loss_remains_uncertain(
    monkeypatch, observed, mode, failure
):
    import httpx
    from backend.ai_usage.tracker import usage_task

    async def respond(request):
        if failure == "gateway_timeout":
            return httpx.Response(504, json={"detail": "timeout"})
        if failure == "timeout":
            raise httpx.ReadTimeout("private transport message")
        raise httpx.ReadError("private transport message")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        codex_client.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    settings = Settings(
        _env_file=None, codex_runner_url="http://fixture", codex_runner_token="fixture"
    )
    async with usage_task(
        object(),
        settings,
        tenant_id="tenant",
        user_id="user",
        task_type="REPORT",
        task_name="推理连接中断",
    ):
        with pytest.raises(codex_client.CodexCallError):
            if mode == "completion":
                await codex_client.completion(
                    settings, system_prompt="facts", user_prompt="request"
                )
            else:
                async for _event in codex_client.stream_chat(
                    settings, system_prompt="facts", user_prompt="request"
                ):
                    pass
    call = finished(observed, "finish_call")[0]
    assert call["status"] == "UNCERTAIN"
    assert call["tokens"]["totalTokens"] is None
    assert finished(observed, "finish_task")[0]["businessStatus"] == "FAILED"
    assert "private transport message" not in str(observed)


def report_boundary(monkeypatch):
    monkeypatch.setattr(report_service, "tenant_transaction", transaction)
    monkeypatch.setattr(
        report_service.repository, "latest_report", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        report_service, "_persist_generation", AsyncMock(return_value={"id": "report"})
    )
    monkeypatch.setattr(
        report_service, "_render_prompts", lambda *args: ("facts", "request")
    )
    monkeypatch.setattr(
        report_service,
        "_format_personalized_push_content",
        lambda *args, **kwargs: "push",
    )
    monkeypatch.setattr(
        report_service,
        "_fallback_draft",
        lambda *args: {"summary": "fallback", "items": []},
    )
    return Settings(
        _env_file=None, codex_runner_url="http://fixture", codex_runner_token="fixture"
    ), TenantContext("user", "tenant", tenant_role="OWNER")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_output", [False, True])
async def test_report_task_records_successful_inference_even_if_report_falls_back(
    monkeypatch, observed, invalid_output
):
    settings, ctx = report_boundary(monkeypatch)
    content = (
        "not-json"
        if invalid_output
        else '{"summary":"report","items":[{"item_type":"FACT","severity":"INFO","title":"fact","body":"known"}]}'
    )
    monkeypatch.setattr(
        codex_client,
        "_request_json",
        AsyncMock(
            return_value={
                "content": content,
                "model": "fixture",
                "usage": {
                    "prompt_tokens": 40,
                    "cached_input_tokens": 20,
                    "completion_tokens": 5,
                },
            }
        ),
    )
    result = await report_service.generate_report(
        object(), ctx, settings, "INTRADAY", context={"positions": [{"symbol": "test"}]}
    )
    assert result["id"] == "report"
    calls, tasks = finished(observed, "finish_call"), finished(observed, "finish_task")
    assert len(calls) == len(tasks) == 1
    assert (
        calls[0]["status"] == "SUCCEEDED" and calls[0]["tokens"]["cachedTokens"] == 20
    )
    assert calls[0]["tokens"]["totalTokens"] == 45
    assert tasks[0]["businessStatus"] == ("DEGRADED" if invalid_output else "SUCCEEDED")
    assert calls[0]["taskId"] == tasks[0]["id"]
    assert [data["taskType"] for data in finished(observed, "start_task")] == ["REPORT"]
    assert {(tenant, user) for _name, _data, tenant, user in observed} == {
        ("tenant", "user")
    }


@pytest.mark.asyncio
async def test_deterministic_report_does_not_create_ai_usage_rows(
    monkeypatch, observed
):
    settings, ctx = report_boundary(monkeypatch)
    monkeypatch.setattr(
        report_service.pre_market,
        "build_report",
        lambda context: {"summary": "deterministic", "items": []},
    )
    monkeypatch.setattr(
        report_service.pre_market, "format_push_content", lambda context: "push"
    )
    await report_service.generate_report(
        object(), ctx, settings, "PRE_MARKET", context={"positions": []}
    )
    assert observed == []


def daily_boundary(monkeypatch, remote):
    stamp = datetime(2026, 10, 27, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    settings = SimpleNamespace(
        daily_research_enabled=True,
        daily_research_codex_path="fixture",
        daily_research_model=None,
        daily_research_timeout_seconds=900,
        codex_runner_url="http://fixture" if remote else "",
        codex_runner_token="fixture",
        app_env="test",
        daily_research_debug_enabled=True,
    )
    monkeypatch.setattr(daily_research, "system_connection", transaction)
    monkeypatch.setattr(daily_research, "consumed", AsyncMock(return_value=False))
    monkeypatch.setattr(
        daily_research,
        "claim_daily_run",
        AsyncMock(return_value={"run_id": str(uuid4())}),
    )
    monkeypatch.setattr(daily_research, "finish_daily_run", AsyncMock())
    monkeypatch.setattr(
        daily_research.archive_repository,
        "enqueue_research_archive",
        AsyncMock(),
        raising=False,
    )
    monkeypatch.setattr(
        daily_research,
        "archive_research_run",
        AsyncMock(return_value={"auditStatus": "ARCHIVED"}),
        raising=False,
    )
    monkeypatch.setattr(daily_research, "lock_model_dispatch", AsyncMock())
    monkeypatch.setattr(
        daily_research, "create_model_guard", AsyncMock(return_value=str(uuid4()))
    )
    monkeypatch.setattr(
        daily_research, "release_model_guard", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        daily_research, "collect_daily_sources", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        daily_research, "prepare_research_inputs", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        daily_research,
        "load_public_packet",
        AsyncMock(return_value={"asOf": stamp.isoformat()}),
    )
    monkeypatch.setattr(daily_research, "build_prompt", lambda *args: "public")
    monkeypatch.setattr(
        daily_research,
        "preflight_codex",
        AsyncMock(
            return_value={"executable": "fixture", "version": "codex-cli fixture"}
        ),
    )
    monkeypatch.setattr(
        codex_client,
        "preflight",
        AsyncMock(return_value={"version": "codex-cli fixture"}),
    )
    monkeypatch.setattr(
        daily_research, "verify_research_sources", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        daily_research, "write_research_audit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(daily_research, "now_shanghai", lambda: stamp)
    return settings, stamp


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_daily_public_task_keeps_inference_on_business_validation_failure_once(
    monkeypatch, observed, remote
):
    settings, stamp = daily_boundary(monkeypatch, remote)
    report = ResearchResult(
        {"summary": "public", "sources": []},
        usage={"prompt_tokens": 70, "cached_input_tokens": 30, "completion_tokens": 9},
        model="fixture",
    )

    async def local_response(*args, lifecycle, **kwargs):
        lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        return report

    async def remote_response(*args, payload, **kwargs):
        from backend.integrations.research_lifecycle import ResearchLifecycle

        lifecycle = ResearchLifecycle(payload["identity"])
        lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
        return {
            "report": dict(report),
            "usage": report.usage,
            "model": report.model,
            "lifecycle": lifecycle.snapshot(),
        }

    local_model = AsyncMock(side_effect=local_response)
    remote_model = AsyncMock(side_effect=remote_response)
    monkeypatch.setattr(daily_research, "run_codex_research", local_model)
    monkeypatch.setattr(
        codex_client,
        "_request_json",
        remote_model,
    )

    reject = Mock(side_effect=ValueError("source validation failed"))
    monkeypatch.setattr(daily_research, "validate_report", reject)
    result = await daily_research.sync_daily_research(
        object(), settings, stamp, debug_request_id="debug", source_batch_id="batch"
    )
    assert result["status"] == "FAILED"
    assert reject.called, "business failure must reach report validation"
    (remote_model if remote else local_model).assert_awaited_once()
    (local_model if remote else remote_model).assert_not_awaited()
    calls, tasks = finished(observed, "finish_call"), finished(observed, "finish_task")
    assert len(calls) == len(tasks) == 1
    assert calls[0]["status"] == "SUCCEEDED" and calls[0]["tokens"]["totalTokens"] == 79
    assert tasks[0]["businessStatus"] == "FAILED"
    assert {(tenant, user) for _name, _data, tenant, user in observed} == {(None, None)}
    started = finished(observed, "start_task")[0]
    assert started["taskType"] == "DAILY_RESEARCH" and "手动" in started["taskName"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_chat_entry_tracks_stream_result_when_service_handles_the_error(
    monkeypatch, observed, failure
):
    monkeypatch.setattr(chat_api, "tenant_transaction", transaction)
    monkeypatch.setattr(
        ChatService, "read_database_context", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        ChatService, "build_system_context", AsyncMock(return_value="facts")
    )

    async def stream(*args, **kwargs):
        yield {"delta": "answer"}
        usage = {"prompt_tokens": 50, "cached_input_tokens": 25, "completion_tokens": 5}
        if failure:
            raise with_telemetry(
                codex_client.CodexCallError("safe failure"),
                usage=usage,
                model="fixture",
                inference_succeeded=False,
            )
        yield {"done": True, "usage": usage, "model": "fixture"}

    monkeypatch.setattr(codex_client, "_stream_chat", stream)
    response = await chat_api.chat_stream(
        chat_api.ChatMessage(message="question"),
        pool=object(),
        ctx=TenantContext("user", "tenant", tenant_role="OWNER"),
        settings=Settings(
            _env_file=None,
            codex_runner_url="http://fixture",
            codex_runner_token="fixture",
        ),
    )
    chunks = [chunk async for chunk in response.body_iterator]
    assert "answer" in "".join(chunks)
    calls, tasks = finished(observed, "finish_call"), finished(observed, "finish_task")
    assert len(calls) == len(tasks) == 1
    assert calls[0]["tokens"]["totalTokens"] == 55
    assert calls[0]["status"] == ("FAILED" if failure else "SUCCEEDED")
    assert tasks[0]["businessStatus"] == ("FAILED" if failure else "SUCCEEDED")


@pytest.mark.asyncio
async def test_closing_chat_mid_reply_finishes_call_and_task_as_cancelled(
    monkeypatch, observed
):
    monkeypatch.setattr(chat_api, "tenant_transaction", transaction)
    monkeypatch.setattr(
        ChatService, "read_database_context", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        ChatService, "build_system_context", AsyncMock(return_value="facts")
    )
    closed = asyncio.Event()

    async def stream(*args, **kwargs):
        try:
            yield {"delta": "partial"}
            await asyncio.Future()
        finally:
            closed.set()

    monkeypatch.setattr(codex_client, "_stream_chat", stream)
    response = await chat_api.chat_stream(
        chat_api.ChatMessage(message="question"),
        pool=object(),
        ctx=TenantContext("user", "tenant", tenant_role="OWNER"),
        settings=Settings(
            _env_file=None,
            codex_runner_url="http://fixture",
            codex_runner_token="fixture",
        ),
    )
    assert "partial" in await response.body_iterator.__anext__()
    await response.body_iterator.aclose()
    assert closed.is_set()
    assert finished(observed, "finish_call")[0]["status"] == "UNCERTAIN"
    assert finished(observed, "finish_task")[0]["businessStatus"] == "CANCELLED"


@pytest.mark.asyncio
async def test_client_closing_after_done_does_not_turn_a_completed_chat_into_cancelled(
    monkeypatch, observed
):
    monkeypatch.setattr(chat_api, "tenant_transaction", transaction)
    monkeypatch.setattr(
        ChatService, "read_database_context", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        ChatService, "build_system_context", AsyncMock(return_value="facts")
    )

    async def stream(*args, **kwargs):
        yield {"delta": "answer"}
        yield {"done": True, "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    monkeypatch.setattr(codex_client, "_stream_chat", stream)
    response = await chat_api.chat_stream(
        chat_api.ChatMessage(message="question"),
        pool=object(),
        ctx=TenantContext("user", "tenant", tenant_role="OWNER"),
        settings=Settings(
            _env_file=None,
            codex_runner_url="http://fixture",
            codex_runner_token="fixture",
        ),
    )
    assert "answer" in await response.body_iterator.__anext__()
    assert '"done": true' in await response.body_iterator.__anext__()
    await response.body_iterator.aclose()
    assert finished(observed, "finish_call")[0]["status"] == "SUCCEEDED"
    assert finished(observed, "finish_task")[0]["businessStatus"] == "SUCCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2.0", "2.4"])
async def test_chat_asgi_send_failure_closes_usage_context_across_streaming_tasks(
    monkeypatch, observed, version
):
    from starlette.requests import ClientDisconnect

    monkeypatch.setattr(chat_api, "tenant_transaction", transaction)
    monkeypatch.setattr(
        ChatService, "read_database_context", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        ChatService, "build_system_context", AsyncMock(return_value="facts")
    )

    async def stream(*args, **kwargs):
        yield {"delta": "partial"}
        await asyncio.Future()

    monkeypatch.setattr(codex_client, "_stream_chat", stream)
    response = await chat_api.chat_stream(
        chat_api.ChatMessage(message="question"),
        pool=object(),
        ctx=TenantContext("user", "tenant", tenant_role="OWNER"),
        settings=Settings(
            _env_file=None,
            codex_runner_url="http://fixture",
            codex_runner_token="fixture",
        ),
    )

    async def receive():
        await asyncio.Future()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("socket closed")

    with pytest.raises((OSError, ClientDisconnect, BaseExceptionGroup)):
        await response(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": version},
                "method": "POST",
                "path": "/api/v1/chat/stream",
                "headers": [],
            },
            receive,
            send,
        )
    assert len(finished(observed, "finish_call")) == 1
    assert finished(observed, "finish_task")[0]["businessStatus"] == "CANCELLED"
