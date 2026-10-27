"""Bounded, non-retrying Codex transport shared by API and Worker.

Private calls require an explicitly configured runner or native CLI path. Public
research retains its existing local discovery and single-dispatch policy.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import json
from typing import AsyncIterator
from urllib.parse import urlsplit

import httpx

from backend.integrations import codex_research

_MAX_INPUT_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024


class CodexClientError(Exception):
    """A safe error that never contains a prompt, credential or CLI stderr."""


class CodexNotConfiguredError(CodexClientError):
    pass


class CodexCallError(CodexClientError):
    pass


def is_configured(settings) -> bool:
    return bool(
        getattr(settings, "codex_runner_url", "")
        or getattr(settings, "codex_path", None)
    )


def _private():
    from backend.integrations import codex_private

    return codex_private


def _model(settings, selected=None):
    value = selected if selected is not None else getattr(settings, "codex_model", None)
    if value is not None and (
        not isinstance(value, str) or not value.strip() or len(value) > 120
    ):
        raise CodexNotConfiguredError("Codex model configuration is invalid")
    return value


def _remote(settings):
    base = str(getattr(settings, "codex_runner_url", "") or "").rstrip("/")
    token = str(getattr(settings, "codex_runner_token", "") or "")
    try:
        parsed = urlsplit(base)
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
        )
        parsed.port
    except ValueError:
        valid = False
    if not valid or not token:
        raise CodexNotConfiguredError("Codex runner configuration is incomplete")
    return base, {"Authorization": f"Bearer {token}"}


def _check_input(payload):
    try:
        size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise CodexCallError("Codex input is invalid") from exc
    if size > _MAX_INPUT_BYTES:
        raise CodexCallError("Codex input exceeds the configured limit")


def _failure_metadata(error, source):
    if isinstance(source, dict):
        succeeded = source.get("inferenceSucceeded")
        usage, model = source.get("usage"), source.get("model")
    else:
        succeeded = getattr(source, "inference_succeeded", None)
        usage, model = getattr(source, "usage", None), getattr(source, "model", None)
    if type(succeeded) is bool:
        codex_research.with_telemetry(error, usage=usage, model=model if isinstance(model, str) and 1 <= len(model) <= 120 else None, inference_succeeded=succeeded)
    return error


async def _sse_lines(response):
    """Apply the byte budget before an unterminated SSE line can accumulate."""
    pending, size = b"", 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > _MAX_OUTPUT_BYTES:
            raise CodexCallError("Codex output exceeds the configured limit")
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            yield line.rstrip(b"\r").decode("utf-8")
    if pending:
        yield pending.rstrip(b"\r").decode("utf-8")


async def _request_json(settings, method, endpoint, *, payload=None, timeout=90, uncertain=False):
    base, headers = _remote(settings)
    if payload is not None:
        _check_input(payload)
    try:
        async with asyncio.timeout(timeout):
            # Internal prompts and runner authentication must not be sent to an
            # inherited HTTP proxy. Only the runner itself contacts Codex.
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream(
                    method, base + endpoint, json=payload, headers=headers
                ) as response:
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_OUTPUT_BYTES:
                            raise CodexCallError("Codex output exceeds the configured limit")
                        chunks.append(chunk)
                    if response.status_code != 200:
                        try:
                            body = json.loads(b"".join(chunks))
                        except (ValueError, UnicodeError):
                            body = {}
                        error = TimeoutError("Codex research outcome is uncertain") if uncertain and response.status_code == 504 else CodexCallError(f"Codex runner unavailable (HTTP {response.status_code})")
                        raise _failure_metadata(error, body.get("detail") if isinstance(body, dict) else None)
                    result = json.loads(b"".join(chunks))
                    if not isinstance(result, dict):
                        raise CodexCallError("Codex runner returned an invalid response")
                    return result
    except CodexClientError:
        raise
    except (httpx.ReadError, httpx.WriteError, httpx.ReadTimeout,
            httpx.WriteTimeout, httpx.RemoteProtocolError, TimeoutError) as exc:
        if uncertain:
            raise _failure_metadata(TimeoutError("Codex research outcome is uncertain"), exc) from exc
        raise CodexCallError("Codex runner request failed") from exc
    except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError) as exc:
        raise CodexCallError("Codex runner request failed") from exc


async def _preflight_local(settings, *, public=False):
    path = getattr(settings, "codex_path", None)
    if public:
        path = getattr(settings, "daily_research_codex_path", None) or path
    elif not path:
        raise CodexNotConfiguredError("Codex has not been configured")
    try:
        return await codex_research.preflight_codex(path)
    except codex_research.CodexUnavailableError as exc:
        raise CodexNotConfiguredError("Codex installation or authentication is unavailable") from exc
    except Exception as exc:
        raise CodexCallError("Codex preflight failed") from exc


async def preflight(settings) -> dict:
    """Check CLI/version/login/capabilities without executing a model or a slot."""
    if getattr(settings, "codex_runner_url", ""):
        result = await _request_json(
            settings, "GET", "/v1/preflight",
            timeout=getattr(settings, "codex_timeout_seconds", 90),
        )
        if not isinstance(result.get("version"), str) or not result["version"].startswith("codex-cli "):
            raise CodexCallError("Codex runner preflight returned an invalid version")
        return result
    return await _preflight_local(settings, public=True)


async def account(settings) -> dict:
    """Read metadata at the actual runtime; failures never block AI business."""
    private = _private()
    try:
        async with asyncio.timeout(10):
            if getattr(settings, "codex_runner_url", ""):
                result = await _request_json(settings, "GET", "/v1/account", timeout=9)
                return private.safe_account_snapshot(result)
            path = getattr(settings, "daily_research_codex_path", None) or getattr(settings, "codex_path", None)
            executable = codex_research.resolve_codex_executable(path)
            if executable is None:
                return private.unavailable_account("CODEX_NOT_CONFIGURED")
            return await private.read_account(executable, timeout=8)
    except Exception:
        return private.unavailable_account()


async def completion(
    settings, *, system_prompt: str, user_prompt: str,
    json_mode: bool = True, model=None,
) -> dict:
    from backend.ai_usage.tracker import trace_inference

    return await trace_inference(
        settings, model=model or getattr(settings, "codex_model", None), call_type="completion",
        operation=lambda: _completion(settings, system_prompt=system_prompt, user_prompt=user_prompt, json_mode=json_mode, model=model),
    )


async def _completion(
    settings, *, system_prompt: str, user_prompt: str,
    json_mode: bool = True, model=None,
) -> dict:
    if not is_configured(settings):
        raise CodexNotConfiguredError("Codex has not been configured")
    selected = _model(settings, model)
    payload = {
        "system_prompt": system_prompt, "user_prompt": user_prompt,
        "json_mode": json_mode, "model": selected,
    }
    _check_input(payload)
    timeout = getattr(settings, "codex_timeout_seconds", 90)
    if getattr(settings, "codex_runner_url", ""):
        result = await _request_json(
            settings, "POST", "/v1/completions", payload=payload, timeout=timeout
        )
    else:
        try:
            async with asyncio.timeout(timeout):
                cli = await _preflight_local(settings)
                result = await _private().complete(
                    cli["executable"], system_prompt, user_prompt,
                    json_mode=json_mode, model=selected, timeout=timeout,
                )
        except CodexClientError:
            raise
        except Exception as exc:
            raise _failure_metadata(CodexCallError("Codex completion failed"), exc) from exc
    if (
        not isinstance(result, dict) or not isinstance(result.get("content"), str)
        or len(result["content"].encode("utf-8")) > _MAX_OUTPUT_BYTES
    ):
        raise CodexCallError("Codex returned an invalid completion")
    return result


async def stream_chat(
    settings, *, system_prompt: str, user_prompt: str, model=None,
) -> AsyncIterator[dict]:
    from backend.ai_usage.tracker import trace_stream

    operation = _stream_chat(settings, system_prompt=system_prompt, user_prompt=user_prompt, model=model)
    stream = trace_stream(settings, model=model or getattr(settings, "codex_model", None), operation_iterator=operation)
    async with aclosing(operation), aclosing(stream):
        async for event in stream:
            yield event


async def _stream_chat(
    settings, *, system_prompt: str, user_prompt: str, model=None,
) -> AsyncIterator[dict]:
    if not is_configured(settings):
        raise CodexNotConfiguredError("Codex has not been configured")
    selected = _model(settings, model)
    payload = {"system_prompt": system_prompt, "user_prompt": user_prompt, "model": selected}
    _check_input(payload)
    timeout = getattr(settings, "codex_chat_timeout_seconds", 900)
    usage = {}
    try:
        async with asyncio.timeout(timeout):
            if not getattr(settings, "codex_runner_url", ""):
                cli = await _preflight_local(settings)
                def record_usage(value):
                    usage.clear()
                    usage.update(codex_research.normalize_usage(value))

                stream = _private().stream_chat(
                    cli["executable"], system_prompt, user_prompt,
                    model=selected, timeout=timeout, usage_callback=record_usage,
                )
                size = 0
                try:
                    async for text in stream:
                        if not isinstance(text, str):
                            raise CodexCallError("Codex returned an invalid chat event")
                        size += len(text.encode("utf-8"))
                        if size > _MAX_OUTPUT_BYTES:
                            raise CodexCallError("Codex output exceeds the configured limit")
                        if text:
                            yield {"delta": text}
                finally:
                    await stream.aclose()
                terminal = {"done": True}
                if usage:
                    terminal.update(usage=usage, model=selected or "codex")
                yield terminal
                return
            base, headers = _remote(settings)
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream(
                    "POST", base + "/v1/chat/stream", json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        raise CodexCallError(f"Codex runner unavailable (HTTP {response.status_code})")
                    async for line in _sse_lines(response):
                        if not line.startswith("data:"):
                            continue
                        event = json.loads(line[5:].strip())
                        if not isinstance(event, dict):
                            raise CodexCallError("Codex chat failed")
                        if "error" in event:
                            raise _failure_metadata(CodexCallError("Codex chat failed"), event)
                        if event.get("done") is True:
                            terminal = {"done": True}
                            usage = codex_research.normalize_usage(event.get("usage"))
                            if usage:
                                terminal.update(usage=usage, model=_model(settings, event.get("model")) or "codex")
                            yield terminal
                            return
                        if isinstance(event.get("delta"), str) and event["delta"]:
                            yield {"delta": event["delta"]}
                    raise CodexCallError("Codex chat ended before completion")
    except CodexClientError:
        raise
    except Exception as exc:
        error = _failure_metadata(CodexCallError("Codex chat request failed"), exc)
        if usage and not hasattr(error, "inference_succeeded"):
            codex_research.with_telemetry(error, usage=usage, model=selected, inference_succeeded=False)
        raise error from exc


async def research(settings, prompt, schema, *, timeout=900, model=None) -> dict:
    from backend.ai_usage.tracker import trace_inference

    return await trace_inference(
        settings, model=model or getattr(settings, "daily_research_model", None) or getattr(settings, "codex_model", None),
        call_type="research", operation=lambda: _research(settings, prompt, schema, timeout=timeout, model=model),
    )


async def _research(settings, prompt, schema, *, timeout=900, model=None) -> dict:
    """Run exactly one public research invocation; this transport never retries."""
    payload = {"prompt": prompt, "schema": schema, "model": model, "timeout": timeout}
    _check_input(payload)
    if getattr(settings, "codex_runner_url", ""):
        result = await _request_json(
            settings, "POST", "/v1/research", payload=payload,
            timeout=timeout + 30, uncertain=True,
        )
        if set(result) == {"report", "usage", "model"} and isinstance(result.get("report"), dict):
            return codex_research.ResearchResult(result["report"], usage=result["usage"], model=_model(settings, result["model"]))
        return codex_research.ResearchResult(result, model=model)
    path = getattr(settings, "daily_research_codex_path", None) or getattr(settings, "codex_path", None)
    executable = codex_research.resolve_codex_executable(path)
    if executable is None:
        raise CodexNotConfiguredError("Codex installation is unavailable")
    try:
        return await codex_research.run_codex_research(
            executable, prompt, schema, timeout=timeout, model=model,
        )
    except TimeoutError:
        raise
    except Exception as exc:
        raise _failure_metadata(CodexCallError("Codex public research failed"), exc) from exc
