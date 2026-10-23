"""Private-network Codex broker with no database or application session authority.

Run with ``python -m backend.codex_runner``. Only the runner and its one-shot
login helper mount Codex authentication. Requests are never retried here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from decimal import Decimal, InvalidOperation
import hmac
import json
import math
import os
import tempfile
from typing import Annotated, Any

import anyio
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from backend.integrations.codex_research import (
    CodexUnavailableError,
    preflight_codex,
    public_environment,
    run_codex_research,
)

MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_PROMPT_BYTES = 3 * 1024 * 1024
MAX_SCHEMA_BYTES = 512 * 1024


async def complete_private(*args, **kwargs):
    from backend.integrations.codex_private import complete

    return await complete(*args, **kwargs)


async def stream_private(*args, **kwargs):
    from backend.integrations.codex_private import stream_chat

    async with aclosing(stream_chat(*args, **kwargs)) as stream:
        async for delta in stream:
            yield delta


class RunnerBusy(Exception):
    """The bounded local execution queue cannot admit another request."""


class Capacity:
    def __init__(self, concurrent: int, *, max_queued: int = 4, queue_timeout: float = 5):
        if not 1 <= concurrent <= 4 or not 0 <= max_queued <= 16 or queue_timeout <= 0:
            raise ValueError("invalid Codex runner capacity")
        self._slots = asyncio.Semaphore(concurrent)
        self.max_queued = max_queued
        self.queue_timeout = queue_timeout
        self.active = 0
        self.queued = 0

    async def acquire(self):
        if self._slots.locked():
            if self.queued >= self.max_queued:
                raise RunnerBusy
            self.queued += 1
            try:
                await asyncio.wait_for(self._slots.acquire(), self.queue_timeout)
            except TimeoutError as exc:
                raise RunnerBusy from exc
            finally:
                self.queued -= 1
        else:
            await self._slots.acquire()
        self.active += 1

    def release(self):
        self.active -= 1
        self._slots.release()

    @asynccontextmanager
    async def slot(self):
        await self.acquire()
        try:
            yield
        finally:
            self.release()


class BodyLimit:
    """Bound actual bytes, including chunked requests without Content-Length."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        content_length = next(
            (value for name, value in scope["headers"] if name.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                size = int(content_length)
                if size < 0:
                    raise ValueError
            except ValueError:
                return await JSONResponse({"detail": "Invalid request"}, 400)(scope, receive, send)
            if size > MAX_REQUEST_BYTES:
                return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _bounded_output(value: Any) -> None:
    if len(_json_bytes(value)) > MAX_OUTPUT_BYTES:
        raise ValueError("Codex output limit")


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_prompt: str = Field(min_length=1, max_length=MAX_PROMPT_BYTES)
    user_prompt: str = Field(min_length=1, max_length=MAX_PROMPT_BYTES)
    json_mode: StrictBool = True
    model: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("system_prompt", "user_prompt")
    @classmethod
    def bound_prompt(cls, value):
        if len(value.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("prompt limit")
        return value


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_BYTES)
    output_schema: dict[str, Any] = Field(alias="schema")
    model: str | None = Field(default=None, min_length=1, max_length=120)
    timeout: float = Field(default=900, ge=30, le=1200)

    @field_validator("prompt")
    @classmethod
    def bound_prompt(cls, value):
        if len(value.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("prompt limit")
        return value

    @field_validator("output_schema")
    @classmethod
    def bound_schema(cls, value):
        if len(_json_bytes(value)) > MAX_SCHEMA_BYTES:
            raise ValueError("schema limit")
        return value


def _safe_completion(result):
    if not isinstance(result, dict) or not isinstance(result.get("content"), str):
        raise ValueError("invalid Codex completion")
    model = result.get("model")
    if not isinstance(model, str) or not 1 <= len(model) <= 120:
        raise ValueError("invalid Codex completion")
    usage = result.get("usage", {})
    if not isinstance(usage, dict):
        raise ValueError("invalid Codex usage")
    usage = {
        name: int(value)
        for name, value in usage.items()
        if name in {"prompt_tokens", "completion_tokens"}
        and isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 1_000_000_000
    }
    try:
        cost = Decimal(str(result.get("cost_cny", "0")))
        if not cost.is_finite() or cost < 0:
            raise ValueError
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("invalid Codex cost") from exc
    safe = {"content": result["content"], "model": model, "usage": usage, "cost_cny": str(cost)}
    _bounded_output(safe)
    return safe


class ManagedStream(StreamingResponse):
    def __init__(self, content, *, capacity):
        super().__init__(content, media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        self._capacity = capacity

    async def __call__(self, scope, receive, send):
        try:
            return await super().__call__(scope, receive, send)
        finally:
            try:
                # A socket send can fail while the generator is suspended at
                # yield; async-for alone does not close that generator.
                with anyio.CancelScope(shield=True):
                    close = getattr(self.body_iterator, "aclose", None)
                    if close is not None:
                        await close()
            finally:
                self._capacity.release()


def create_app(
    *,
    token: str | None = None,
    executable: str | None = None,
    model: str | None = None,
    daily_model: str | None = None,
    completion_timeout: float | None = None,
    chat_timeout: float | None = None,
    max_concurrent: int | None = None,
    max_queued: int = 4,
    queue_timeout: float = 5,
    heartbeat_seconds: float = 15,
    environment: dict[str, str] | None = None,
) -> FastAPI:
    """Build an isolated broker; imports never load the application's dotenv."""
    env = os.environ if environment is None else environment
    token = env.get("CODEX_RUNNER_TOKEN", "") if token is None else token
    if len(token.encode("utf-8")) < 32 or len(token.encode("utf-8")) > 512 or any(char.isspace() for char in token):
        raise ValueError("CODEX_RUNNER_TOKEN must contain 32-512 non-whitespace bytes")
    expected_token = token.encode("utf-8")
    executable = executable or env.get("CODEX_PATH", "/usr/local/bin/codex")
    model = (model or env.get("CODEX_MODEL") or "").strip() or None
    daily_model = (daily_model or env.get("DAILY_RESEARCH_MODEL") or "").strip() or model
    completion_timeout = float(env.get("CODEX_TIMEOUT_SECONDS", "90")) if completion_timeout is None else completion_timeout
    chat_timeout = float(env.get("CODEX_CHAT_TIMEOUT_SECONDS", "900")) if chat_timeout is None else chat_timeout
    max_concurrent = int(env.get("CODEX_RUNNER_MAX_CONCURRENT", "2")) if max_concurrent is None else max_concurrent
    if not all(math.isfinite(value) and 0 < value <= 1200 for value in (completion_timeout, chat_timeout, heartbeat_seconds)):
        raise ValueError("invalid Codex runner timeout")
    child_environment = public_environment(env, tempfile.gettempdir())
    capacity = Capacity(max_concurrent, max_queued=max_queued, queue_timeout=queue_timeout)
    app = FastAPI(title="WealthSail internal Codex runner", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    app.state.capacity = capacity

    async def authorize(authorization: Annotated[str | None, Header()] = None):
        supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if len(supplied.encode("utf-8")) > 512 or not hmac.compare_digest(supplied.encode("utf-8"), expected_token):
            raise HTTPException(401, "Runner authentication required", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        # Pydantic's normal details include input values, potentially prompts.
        return JSONResponse({"detail": "Invalid Codex request"}, status_code=422)

    @app.exception_handler(RunnerBusy)
    async def busy(_request, _exc):
        return JSONResponse({"detail": "Codex runner is busy"}, status_code=503)

    def approved_model(requested, allowed):
        if requested is not None and requested != allowed:
            raise HTTPException(400, "Requested Codex model is not allowed")
        return allowed

    async def invoke(operation: Callable[[], Awaitable], timeout: float, connection: Request):
        async def execute():
            async with capacity.slot():
                async with asyncio.timeout(timeout):
                    result = await operation()
                    _bounded_output(result)
                    return result

        async def disconnected():
            # ASGI does not automatically cancel a non-streaming route when
            # its peer closes the socket. Stop queued or active work as well.
            while (await connection.receive())["type"] != "http.disconnect":
                pass

        execution = asyncio.create_task(execute())
        disconnect = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait({execution, disconnect}, return_when=asyncio.FIRST_COMPLETED)
            if execution in done:
                return execution.result()
            raise HTTPException(499, "Client disconnected")
        except RunnerBusy:
            raise
        except HTTPException:
            raise
        except TimeoutError as exc:
            raise HTTPException(504, "Codex execution timed out") from exc
        except CodexUnavailableError as exc:
            raise HTTPException(503, "Codex authentication or runtime unavailable") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise HTTPException(502, "Codex execution failed") from exc
        finally:
            with anyio.CancelScope(shield=True):
                for task in (execution, disconnect):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(execution, disconnect, return_exceptions=True)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/preflight", dependencies=[Depends(authorize)])
    async def preflight(connection: Request):
        result = await invoke(lambda: preflight_codex(executable, environment=child_environment), 75, connection)
        return {name: result.get(name) for name in ("executable", "version", "selectedModel")}

    @app.post("/v1/completions", dependencies=[Depends(authorize)])
    async def completion(request: PromptRequest, connection: Request):
        selected = approved_model(request.model, model)
        result = await invoke(
            lambda: complete_private(executable, request.system_prompt, request.user_prompt, json_mode=request.json_mode, timeout=completion_timeout, environment=child_environment, model=selected),
            completion_timeout + 5,
            connection,
        )
        try:
            return _safe_completion(result)
        except Exception as exc:
            raise HTTPException(502, "Invalid Codex response") from exc

    @app.post("/v1/research", dependencies=[Depends(authorize)])
    async def research(request: ResearchRequest, connection: Request):
        selected = approved_model(request.model, daily_model)
        result = await invoke(
            lambda: run_codex_research(executable, request.prompt, request.output_schema, timeout=request.timeout, environment=child_environment, model=selected),
            request.timeout + 5,
            connection,
        )
        if not isinstance(result, dict):
            raise HTTPException(502, "Invalid Codex research response")
        return result

    @app.post("/v1/chat/stream", dependencies=[Depends(authorize)])
    async def stream(request: PromptRequest):
        selected = approved_model(request.model, model)
        await capacity.acquire()

        async def events() -> AsyncIterator[str]:
            iterator = stream_private(executable, request.system_prompt, request.user_prompt, timeout=chat_timeout, environment=child_environment, model=selected).__aiter__()
            pending = None
            size = 0
            try:
                async with asyncio.timeout(chat_timeout + 5):
                    while True:
                        if pending is None:
                            pending = asyncio.create_task(iterator.__anext__())
                        done, _ = await asyncio.wait({pending}, timeout=heartbeat_seconds)
                        if not done:
                            yield ": keep-alive\n\n"
                            continue
                        try:
                            delta = pending.result()
                        except StopAsyncIteration:
                            pending = None
                            break
                        pending = None
                        if not isinstance(delta, str):
                            raise ValueError("invalid assistant delta")
                        size += len(delta.encode("utf-8"))
                        if size > MAX_OUTPUT_BYTES:
                            raise ValueError("assistant output limit")
                        if delta:
                            yield "data: " + _json_bytes({"delta": delta}).decode("utf-8") + "\n\n"
                yield 'data: {"done":true}\n\n'
            except asyncio.CancelledError:
                raise
            except Exception:
                yield 'data: {"error":"Codex conversation unavailable"}\n\n'
                yield 'data: {"done":true}\n\n'
            finally:
                with anyio.CancelScope(shield=True):
                    if pending is not None:
                        if not pending.done():
                            pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                    await iterator.aclose()

        return ManagedStream(events(), capacity=capacity)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8090, access_log=False)


if __name__ == "__main__":
    main()
