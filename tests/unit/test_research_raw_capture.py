"""Authenticated model output capture preserves bounded evidence, never logs."""

from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from backend.integrations import codex_research, codex_client
from tests.unit.test_codex_runner import runner, client_for, auth
from tests.unit.test_codex_client import settings, transport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    ['{"broken":', '"x"' * (1024 * 1024 + 1)],
    ids=["malformed", "oversized"],
)
async def test_decode_failure_retains_bounded_exact_output_and_full_digest(
    monkeypatch, raw
):
    async def execute(args, **kwargs):
        Path(args[args.index("--output-last-message") + 1]).write_text(
            raw, encoding="utf-8", newline=""
        )
        return 0, b'{"type":"turn.completed"}\n', b""

    monkeypatch.setattr(codex_research, "_run", execute)
    with pytest.raises(codex_research.CodexOutputError) as caught:
        await codex_research.run_codex_research("fixture", "public", {}, environment={})
    error = caught.value
    encoded = raw.encode("utf-8")
    assert error.inference_succeeded is True
    assert error.raw_output_bytes == len(encoded)
    assert error.raw_output_sha256 == sha256(encoded).hexdigest()
    assert error.raw_output == (raw if len(encoded) <= 2 * 1024 * 1024 else None)
    assert raw[:40] not in str(error)


@pytest.mark.asyncio
async def test_parseable_duplicate_fields_keep_report_and_capture_remains_exact(monkeypatch):
    raw = '{"duplicate":1,"duplicate":2}'
    calls = []

    async def execute(args, **kwargs):
        calls.append(args)
        path = Path(args[args.index("--output-last-message") + 1])
        path.write_text(raw, encoding="utf-8", newline="")
        captured = codex_research._capture_output_error(
            codex_research.CodexOutputError("fixture capture"), path
        )
        assert captured.raw_output == raw
        assert captured.raw_output_sha256 == sha256(raw.encode()).hexdigest()
        assert captured.raw_output_bytes == len(raw.encode())
        return 0, b'{"type":"turn.completed"}\n', b""

    monkeypatch.setattr(codex_research, "_run", execute)
    result = await codex_research.run_codex_research(
        "fixture", "public", {}, environment={}
    )
    assert result["duplicate"] == 2
    assert result["_bodyDuplicateFields"] == ["duplicate"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_runner_capture_is_research_only_and_authenticated(monkeypatch):
    error = codex_research.CodexOutputError("invalid report JSON")
    error.raw_output = '{"private-public-output":'
    error.raw_output_bytes = len(error.raw_output.encode())
    error.raw_output_sha256 = sha256(error.raw_output.encode()).hexdigest()
    error.inference_succeeded = True

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(runner(), "run_codex_research", fail)
    monkeypatch.setattr(runner(), "complete_private", fail)
    async with client_for() as (client, _):
        assert (
            await client.post("/v1/research", json={"prompt": "x", "schema": {}})
        ).status_code == 401
        response = await client.post(
            "/v1/research", headers=auth(), json={"prompt": "x", "schema": {}}
        )
        assert response.status_code == 502
        assert response.json()["detail"]["rawOutput"]["content"] == error.raw_output
        other = await client.post(
            "/v1/completions",
            headers=auth(),
            json={"system_prompt": "x", "user_prompt": "y"},
        )
        assert "rawOutput" not in other.json()["detail"]


@pytest.mark.asyncio
async def test_remote_client_carries_exact_research_raw_only_as_exception_metadata(
    monkeypatch,
):
    raw = '{"broken":'
    captured = {
        "content": raw,
        "sha256": sha256(raw.encode()).hexdigest(),
        "byteLength": len(raw.encode()),
    }
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(
            502,
            json={
                "detail": {
                    "message": "Codex execution failed",
                    "inferenceSucceeded": True,
                    "rawOutput": captured,
                }
            },
        )

    transport(monkeypatch, response)
    with pytest.raises(codex_client.CodexCallError) as caught:
        await codex_client.research(settings(), "public", {})
    assert caught.value.raw_output == raw
    assert caught.value.raw_output_sha256 == captured["sha256"]
    assert raw not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_local_client_wrapper_preserves_research_capture_without_message_body(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    raw = '{"broken":'
    error = codex_research.CodexOutputError("invalid report JSON")
    error.raw_output, error.raw_output_bytes = raw, len(raw.encode())
    error.raw_output_sha256 = sha256(raw.encode()).hexdigest()
    error.inference_succeeded = True
    monkeypatch.setattr(
        codex_research, "resolve_codex_executable", lambda *_args, **_kwargs: "fixture"
    )
    monkeypatch.setattr(
        codex_research, "run_codex_research", AsyncMock(side_effect=error)
    )
    with pytest.raises(codex_client.CodexCallError) as caught:
        await codex_client.research(settings(codex_runner_url=""), "public", {})
    assert caught.value.raw_output == raw
    assert caught.value.inference_succeeded is True
    assert raw not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["completion", "bad_digest"])
async def test_capture_is_not_accepted_for_other_calls_or_invalid_integrity(
    monkeypatch, endpoint
):
    raw = '{"broken":'
    captured = {"content": raw, "sha256": "0" * 64, "byteLength": len(raw.encode())}
    transport(
        monkeypatch,
        lambda _request: httpx.Response(
            502,
            json={
                "detail": {
                    "message": "Codex execution failed",
                    "inferenceSucceeded": True,
                    "rawOutput": captured,
                }
            },
        ),
    )
    with pytest.raises(codex_client.CodexCallError) as caught:
        if endpoint == "completion":
            await codex_client.completion(
                settings(), system_prompt="public", user_prompt="public"
            )
        else:
            await codex_client.research(settings(), "public", {})
    assert not hasattr(caught.value, "raw_output")


@pytest.mark.asyncio
async def test_escaped_research_capture_does_not_expand_http_response_limit(
    monkeypatch,
):
    raw = "\\" * (2 * 1024 * 1024)
    error = codex_research.CodexOutputError("invalid report JSON")
    error.raw_output = raw
    error.inference_succeeded = True

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(runner(), "run_codex_research", fail)
    async with client_for() as (client, _):
        response = await client.post(
            "/v1/research", headers=auth(), json={"prompt": "x", "schema": {}}
        )
    assert response.status_code == 502
    assert len(response.content) <= 2 * 1024 * 1024
    captured = response.json()["detail"]["rawOutput"]
    assert captured["content"] is None
    assert captured["sha256"] == sha256(raw.encode()).hexdigest()
