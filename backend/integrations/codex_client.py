"""Bounded, non-retrying Codex transport shared by API and Worker.

Private calls require an explicitly configured runner or native CLI path. Public
research retains its existing local discovery and single-dispatch policy.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import hashlib
import inspect
import json
import os
import time
from typing import AsyncIterator
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import httpx

from backend.integrations import codex_research
from backend.common.runtime_diagnostics import attach_diagnostic, make_diagnostic
from backend.integrations.research_execution import (
    EXECUTION_TRANSPORT_HEADER,
    sanitize_execution_receipt,
    sanitize_execution_transport_reason,
    sanitize_execution_progress,
    sanitize_execution_statistics,
)
from backend.integrations.research_lifecycle import (
    RECOVERY_SECONDS, ResearchLifecycle, TIMEOUT_ORIGINS, call_identity,
    research_deadlines, sanitize_lifecycle,
)

_MAX_INPUT_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_LOCAL_ACCOUNT_CACHES = WeakKeyDictionary()
_REMOTE_ACCOUNT_HISTORIES = WeakKeyDictionary()


class CodexClientError(Exception):
    """A safe error that never contains a prompt, credential or CLI stderr."""


class CodexNotConfiguredError(CodexClientError):
    pass


class CodexCallError(CodexClientError):
    pass


class _ResearchEnvelope(dict):
    """Retain bounded header metadata without modifying the JSON envelope."""

    def __init__(self, payload, reason):
        super().__init__(payload)
        self.execution_receipt_transport_reason = reason


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


def _failure_metadata(error, source, *, capture_raw=False, identity=None):
    attach_diagnostic(error, source.get("diagnostic") if isinstance(source, dict)
                      else getattr(source, "codex_diagnostic", None))
    if not isinstance(source, dict) and not hasattr(error, "codex_diagnostic"):
        reason = ("TRANSPORT_TIMEOUT" if isinstance(source, (TimeoutError, httpx.TimeoutException))
                  else "TRANSPORT_DISCONNECTED" if isinstance(source, (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError))
                  else "NETWORK_UNAVAILABLE" if isinstance(source, httpx.ConnectError)
                  else None)
        if reason is not None:
            attach_diagnostic(error, make_diagnostic("TRANSPORT", "TRANSPORT", reason))
    if isinstance(source, dict):
        uncertain = source.get("inferenceUncertain") is True
        succeeded = source.get("inferenceSucceeded")
        usage, model = source.get("usage"), source.get("model")
    else:
        uncertain = getattr(source, "inference_uncertain", False) or isinstance(source, (TimeoutError, httpx.ReadError, httpx.WriteError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError))
        succeeded = getattr(source, "inference_succeeded", None)
        usage, model = getattr(source, "usage", None), getattr(source, "model", None)
    if type(succeeded) is bool:
        codex_research.with_telemetry(error, usage=usage, model=model if isinstance(model, str) and 1 <= len(model) <= 120 else None, inference_succeeded=succeeded)
    if uncertain:
        error.inference_uncertain = True
    origin = source.get("timeoutOrigin") if isinstance(source, dict) else getattr(source, "timeout_origin", None)
    if isinstance(origin, str) and origin in TIMEOUT_ORIGINS:
        error.timeout_origin = origin
    if identity is not None:
        lifecycle = sanitize_lifecycle(source.get("lifecycle") if isinstance(source, dict) else getattr(source, "research_execution_state", None), identity)
        if lifecycle is not None:
            error.research_execution_state = lifecycle
    for field, key, sanitizer in (("execution_statistics", "executionStatistics", sanitize_execution_statistics),
                                  ("execution_progress", "executionProgress", sanitize_execution_progress)):
        safe = sanitizer(source.get(key) if isinstance(source, dict) else getattr(source, field, None))
        if safe is not None:
            setattr(error, field, safe)
    if capture_raw:
        reason = sanitize_execution_transport_reason(
            source.get("executionReceiptTransportReason") if isinstance(source, dict)
            else getattr(source, "execution_receipt_transport_reason", None)
        )
        if reason is not None:
            error.execution_receipt_transport_reason = reason
        receipt = sanitize_execution_receipt(
            source.get("executionReceipt") if isinstance(source, dict)
            else getattr(source, "execution_receipt", None)
        )
        if receipt is not None:
            error.execution_receipt = receipt
    captured = (
        source.get("rawOutput") if isinstance(source, dict)
        else codex_research.raw_output_metadata(source)
    ) if capture_raw else None
    if isinstance(captured, dict):
        content, size, digest = (captured.get(key) for key in ("content", "byteLength", "sha256"))
        valid = type(size) is int and size >= 0 and isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        if isinstance(content, str):
            try:
                encoded = content.encode("utf-8")
                valid = valid and len(encoded) <= _MAX_OUTPUT_BYTES and len(encoded) == size and hashlib.sha256(encoded).hexdigest() == digest
            except UnicodeError:
                valid = False
        else:
            valid = valid and content is None and captured.get("omissionReason") in {"SIZE_LIMIT", "INVALID_UTF8"}
        if valid:
            error.raw_output, error.raw_output_bytes, error.raw_output_sha256 = content, size, digest
            error.raw_output_omission = captured.get("omissionReason")
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


async def _request_json(settings, method, endpoint, *, payload=None, timeout=90, uncertain=False, identity=None):
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
                    transport_reason = sanitize_execution_transport_reason(response.headers.get(EXECUTION_TRANSPORT_HEADER)) if endpoint == "/v1/research" else None
                    if response.status_code != 200:
                        try:
                            body = json.loads(b"".join(chunks))
                        except (ValueError, UnicodeError):
                            body = {}
                        error = TimeoutError("Codex research outcome is uncertain") if uncertain and response.status_code == 504 else CodexCallError(f"Codex runner unavailable (HTTP {response.status_code})")
                        if response.status_code == 504:
                            error.inference_uncertain = True
                        error = _failure_metadata(error, body.get("detail") if isinstance(body, dict) else None, capture_raw=endpoint == "/v1/research", identity=identity)
                        if not hasattr(error, "codex_diagnostic"):
                            attach_diagnostic(error, make_diagnostic("RUNNER", "RUNNER_HTTP", "RUNNER_HTTP_ERROR", httpStatus=response.status_code))
                        if response.status_code == 504 and not hasattr(error, "timeout_origin"):
                            error.timeout_origin = "RUNNER_OPERATION_TIMEOUT"
                        if transport_reason is not None:
                            error.execution_receipt_transport_reason = transport_reason
                        raise error
                    result = json.loads(b"".join(chunks))
                    if not isinstance(result, dict):
                        raise CodexCallError("Codex runner returned an invalid response")
                    return _ResearchEnvelope(result, transport_reason) if transport_reason is not None else result
    except CodexClientError:
        raise
    except (httpx.ReadError, httpx.WriteError, httpx.ReadTimeout,
            httpx.WriteTimeout, httpx.RemoteProtocolError, TimeoutError) as exc:
        if uncertain:
            error = _failure_metadata(TimeoutError("Codex research outcome is uncertain"), exc, capture_raw=endpoint == "/v1/research", identity=identity)
            if not hasattr(error, "timeout_origin"):
                error.timeout_origin = "WORKER_RPC_TIMEOUT" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "RUNNER_DISCONNECTED"
            raise error from exc
        raise _failure_metadata(CodexCallError("Codex runner request failed"), exc) from exc
    except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError) as exc:
        error = _failure_metadata(CodexCallError("Codex runner request failed"), exc)
        if not hasattr(error, "codex_diagnostic"):
            attach_diagnostic(error, make_diagnostic("TRANSPORT", "TRANSPORT", "TRANSPORT_FAILED_UNCLASSIFIED"))
        raise error from exc


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


async def account(settings, *, fresh=False, refresh=False) -> dict:
    """Read metadata at the actual runtime; failures never block AI business."""
    private = _private()
    from backend.integrations.codex_account_cache import AccountMetadataCache, RemoteAccountHistory, account_diagnostic, auth_file_generation

    started = time.monotonic()
    loop = asyncio.get_running_loop()
    previous = _REMOTE_ACCOUNT_HISTORIES.get(loop)
    remote = str(getattr(settings, "codex_runner_url", "") or "").rstrip("/")
    history, sequence = None, 0
    if remote:
        key = (remote, hashlib.sha256(str(getattr(settings, "codex_runner_token", "") or "").encode()).digest())
        if previous is None or previous.key != key:
            if previous is not None:
                previous.invalidate()
            _REMOTE_ACCOUNT_HISTORIES[loop] = RemoteAccountHistory(key, invalidated=previous is not None)
        history = _REMOTE_ACCOUNT_HISTORIES[loop]
        sequence = history.begin()
    elif previous is not None:
        previous.invalidate()
        del _REMOTE_ACCOUNT_HISTORIES[loop]
    try:
        async with asyncio.timeout(25):
            if getattr(settings, "codex_runner_url", ""):
                endpoint = "/v1/account"
                if fresh:
                    endpoint += "?fresh=true"
                elif refresh:
                    endpoint += "?refresh=true"
                result = await _request_json(settings, "GET", endpoint, timeout=23)
                result = private.safe_account_snapshot(result)
                account_diagnostic("transport", started, status=result["status"], diagnostic=result.get("diagnostic"))
                result = history.accept(sequence, result)
                if fresh and result["status"] == "STALE":
                    return private.unavailable_account("ACCOUNT_STALE")
                return result
            path = getattr(settings, "daily_research_codex_path", None) or getattr(settings, "codex_path", None)
            executable = codex_research.resolve_codex_executable(path)
            if executable is None:
                return private.unavailable_account("CODEX_NOT_CONFIGURED")
            caches = _LOCAL_ACCOUNT_CACHES.setdefault(asyncio.get_running_loop(), {})
            key = (executable, os.environ.get("CODEX_HOME", ""))
            if key not in caches:
                async def inspect():
                    return await private.read_account(executable)

                if len(caches) >= 8:
                    caches.pop(next(iter(caches)))
                caches[key] = AccountMetadataCache(inspect, generation=auth_file_generation)
            return await caches[key].get(fresh=fresh, refresh=refresh)
    except Exception as error:
        if not hasattr(error, "codex_diagnostic"):
            attach_diagnostic(error, make_diagnostic("TRANSPORT", "TRANSPORT", "TRANSPORT_TIMEOUT" if isinstance(error, TimeoutError) else "TRANSPORT_FAILED_UNCLASSIFIED"))
        account_diagnostic("transport", started, error)
        if history is not None:
            fallback = history.failure(sequence)
            if not fresh:
                from backend.common.runtime_diagnostics import sanitize_diagnostic

                diagnostic = sanitize_diagnostic(getattr(error, "codex_diagnostic", None))
                if diagnostic is not None:
                    fallback["diagnostic"] = diagnostic
                return fallback
        return private.unavailable_account(diagnostic=getattr(error, "codex_diagnostic", None))


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
                        raise _failure_metadata(CodexCallError(f"Codex runner unavailable (HTTP {response.status_code})"), {"inferenceUncertain": response.status_code == 504})
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


async def research(settings, prompt, schema, *, timeout=900, model=None, on_return=None, on_output=None,
                   identity=None, on_exit=None, on_progress=None, trace_usage=True) -> dict:
    from backend.ai_usage.tracker import trace_inference

    async def operation():
        result = await _research(settings, prompt, schema, timeout=timeout, model=model,
                                 identity=identity, on_exit=on_exit, on_progress=on_progress)
        if on_output is not None:
            on_output(result)
        if on_return is not None:
            on_return()
        return result

    if not trace_usage:
        return await operation()
    return await trace_inference(
        settings, model=model or getattr(settings, "daily_research_model", None) or getattr(settings, "codex_model", None),
        call_type="research", operation=operation,
    )


async def _notify(callback, value):
    if callback is None or value is None:
        return
    try:
        result = callback(value)
        if inspect.isawaitable(result):
            async with asyncio.timeout(1):
                await result
    except Exception:
        # Diagnostic persistence never changes the research business result.
        pass


async def research_status(settings, identity, *, cancel=False):
    identity = call_identity(identity)
    result = await _request_json(settings, "POST", "/v1/research/cancel" if cancel else "/v1/research/status",
                                 payload={"identity": identity}, timeout=2)
    return sanitize_lifecycle(result.get("lifecycle"), identity)


async def _recover_research(settings, identity, *, on_exit=None, on_progress=None):
    try:
        async with asyncio.timeout(RECOVERY_SECONDS):
            state = await research_status(settings, identity, cancel=True)
            while state is not None:
                # A bound acknowledgement is useful diagnosis even while exit
                # remains unconfirmed. The Worker alone decides guard release.
                await _notify(on_exit, state)
                await _notify(on_progress, state.get("progress"))
                if state["exitConfirmed"]:
                    return state
                await asyncio.sleep(0.25)
                state = await research_status(settings, identity)
    except (Exception, asyncio.CancelledError):
        pass
    return None


async def _remote_research(settings, payload, *, timeout, identity, on_exit, on_progress):
    async def monitor():
        while True:
            await asyncio.sleep(15)
            try:
                state = await research_status(settings, identity)
                if state is not None:
                    await _notify(on_progress, state.get("progress"))
                    await _notify(on_exit, state)
            except Exception:
                pass

    progress_task = asyncio.create_task(monitor()) if identity is not None and on_progress is not None else None
    try:
        result = await _request_json(settings, "POST", "/v1/research", payload=payload,
                                     timeout=research_deadlines(timeout)["rpcSeconds"], uncertain=True,
                                     **({"identity": identity} if identity is not None else {}))
        if identity is not None:
            state = sanitize_lifecycle(result.get("lifecycle"), identity)
            await _notify(on_exit, state)
        return result
    except (Exception, asyncio.CancelledError) as exc:
        state = sanitize_lifecycle(getattr(exc, "research_execution_state", None), identity) if identity is not None else None
        if state is not None:
            await _notify(on_exit, state)
        if identity is not None and (state is None or not state["exitConfirmed"]):
            recovery = asyncio.create_task(_recover_research(settings, identity, on_exit=on_exit, on_progress=on_progress))
            try:
                state = await asyncio.shield(recovery)
            except asyncio.CancelledError:
                state = await asyncio.shield(recovery)
            if state is not None:
                exc.research_execution_state = state
        raise
    finally:
        if progress_task is not None:
            progress_task.cancel()
            await asyncio.gather(progress_task, return_exceptions=True)


async def _research(settings, prompt, schema, *, timeout=900, model=None, identity=None, on_exit=None, on_progress=None) -> dict:
    """Run exactly one public research invocation; this transport never retries."""
    payload = {"prompt": prompt, "schema": schema, "model": model, "timeout": timeout}
    if identity is not None:
        identity = call_identity(identity)
        payload["identity"] = identity
    _check_input(payload)
    if getattr(settings, "codex_runner_url", ""):
        result = await _remote_research(settings, payload, timeout=timeout, identity=identity, on_exit=on_exit, on_progress=on_progress)
        envelope_fields = {"report", "usage", "model", "executionReceipt", "lifecycle", "executionStatistics", "executionProgress"}
        if {"report", "usage", "model"} <= set(result) <= envelope_fields and isinstance(result.get("report"), dict):
            return codex_research.ResearchResult(result["report"], usage=result["usage"], model=_model(settings, result["model"]), execution_receipt=result.get("executionReceipt"), execution_receipt_transport_reason=getattr(result, "execution_receipt_transport_reason", None),
                                                execution_statistics=result.get("executionStatistics"), execution_progress=result.get("executionProgress"))
        return codex_research.ResearchResult(result, model=model, execution_receipt_transport_reason=getattr(result, "execution_receipt_transport_reason", None))
    path = getattr(settings, "daily_research_codex_path", None) or getattr(settings, "codex_path", None)
    executable = codex_research.resolve_codex_executable(path)
    if executable is None:
        raise CodexNotConfiguredError("Codex installation is unavailable")
    lifecycle = ResearchLifecycle(identity) if identity is not None else None
    try:
        return await codex_research.run_codex_research(
            executable, prompt, schema, timeout=timeout, model=model,
            **({"lifecycle": lifecycle} if lifecycle is not None else {}),
        )
    except TimeoutError:
        raise
    except Exception as exc:
        raise _failure_metadata(CodexCallError("Codex public research failed"), exc, capture_raw=True) from exc
    finally:
        if lifecycle is not None:
            await _notify(on_exit, sanitize_lifecycle(lifecycle.snapshot(), identity))
