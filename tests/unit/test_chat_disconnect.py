"""Real ASGI disconnect/send failures must close the authenticated HTTP stream."""

import asyncio
from builtins import BaseExceptionGroup
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import ClientDisconnect

from backend.chat import api
from backend.chat.service import ChatService
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.integrations import codex_client


async def response_with_open_transport(monkeypatch):
    closed = asyncio.Event()
    received = []
    # Keep this transport test scoped to chat; account inspection has its own
    # protocol tests and must not share the deliberately never-ending SSE body.
    monkeypatch.setattr(codex_client, "account", AsyncMock(return_value={"status": "UNAVAILABLE", "windows": [], "credits": None}))

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"delta":"answer"}\n\n'
            await asyncio.Future()

        async def aclose(self):
            await asyncio.sleep(0)
            closed.set()

    async def handler(request):
        received.append(request)
        return httpx.Response(200, stream=Stream())

    original = httpx.AsyncClient
    monkeypatch.setattr(codex_client.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    monkeypatch.setattr(ChatService, "read_database_context", AsyncMock(return_value={}))
    monkeypatch.setattr(ChatService, "build_system_context", AsyncMock(return_value="tenant facts"))
    settings = Settings(
        _env_file=None, codex_runner_url="http://codex-runner:8001",
        codex_runner_token="private-runner-token",
    )
    response = await api.chat_stream(
        api.ChatMessage(message="hello"), pool=object(),
        ctx=TenantContext("user-1", "tenant-1", tenant_role="OWNER"), settings=settings,
    )
    return response, closed, received


def scope(version):
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": version},
        "method": "POST", "path": "/api/v1/chat/stream", "headers": [],
    }


@pytest.mark.asyncio
async def test_asgi_socket_send_failure_closes_codex_transport(monkeypatch):
    response, closed, received = await response_with_open_transport(monkeypatch)

    async def receive():
        await asyncio.Future()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            assert b"private-runner-token" not in message["body"]
            raise OSError("socket closed")

    with pytest.raises((ClientDisconnect, OSError, BaseExceptionGroup)):
        await response(scope("2.4"), receive, send)
    assert len(received) == 1
    assert received[0].headers["authorization"] == "Bearer private-runner-token"
    assert closed.is_set(), "closing a response suspended at yield must close the runner HTTP stream"


@pytest.mark.asyncio
async def test_asgi_disconnect_cancellation_closes_codex_transport(monkeypatch):
    response, closed, received = await response_with_open_transport(monkeypatch)
    sent = asyncio.Event()

    async def receive():
        await sent.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent.set()

    await asyncio.wait_for(response(scope("2.0"), receive, send), timeout=1)
    assert len(received) == 1
    assert closed.is_set(), "active request cancellation must finish closing the runner HTTP stream"
