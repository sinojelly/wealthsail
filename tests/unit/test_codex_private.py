"""Private Codex runtime tests use fake subprocesses and no account or model."""

import asyncio
from importlib import import_module, util
import json
from pathlib import Path
import tomllib
from unittest.mock import AsyncMock

import pytest


def adapter():
    assert util.find_spec("backend.integrations.codex_private"), (
        "private Codex runtime missing"
    )
    return import_module("backend.integrations.codex_private")


@pytest.fixture(autouse=True)
def isolate_model_configuration(monkeypatch):
    # Runtime protocol tests must not depend on a developer's desktop config.
    monkeypatch.setattr(adapter(), "configured_model", lambda _environment: None)


@pytest.mark.asyncio
async def test_complete_private_context_stays_on_stdin_with_no_tools_or_provider_retry(
    monkeypatch,
):
    mod = adapter()
    calls = []

    async def execute(args, **kwargs):
        calls.append((args, kwargs))
        output = Path(args[args.index("--output-last-message") + 1])
        output.write_text('{"summary":"private result"}', encoding="utf-8")
        return (
            0,
            b'{"type":"turn.completed","usage":{"input_tokens":8,"output_tokens":3}}\n',
            b"",
        )

    monkeypatch.setattr(mod, "_run", execute)
    result = await mod.complete(
        "native-codex",
        "Holdings and cost context: private-system",
        "Explain private-user holdings",
        model="account-selected-model",
        environment={"CODEX_HOME": "isolated-auth", "WORKER_DATABASE_URL": "db-secret"},
    )
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert "private-system" not in " ".join(args)
    assert "private-user" not in " ".join(args)
    assert b"private-system" in kwargs["input_data"]
    assert b"private-user" in kwargs["input_data"]
    assert "--search" not in args and "--output-schema" not in args
    assert (
        "read-only" in args and "--ephemeral" in args and "--ignore-user-config" in args
    )
    assert 'web_search="disabled"' in args
    assert (
        "shell_tool" in args
        and "apps" in args
        and "plugins" in args
        and "multi_agent" in args
    )
    assert "mcp_servers={}" in args
    policy = {}
    for index, value in enumerate(args[:-1]):
        if value == "-c":
            policy.update(tomllib.loads(args[index + 1]))
    assert "Private context explicitly supplied" in policy["developer_instructions"]
    assert "public research packet" not in policy["developer_instructions"]
    assert "WORKER_DATABASE_URL" not in kwargs["env"]
    assert kwargs["env"]["CODEX_HOME"] == "isolated-auth"
    assert not Path(kwargs["cwd"]).exists()
    assert result == {
        "content": '{"summary":"private result"}',
        "model": "account-selected-model",
        "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
        "cost_cny": "0.0000",
    }


@pytest.mark.asyncio
async def test_complete_rejects_non_object_json_without_retry(monkeypatch):
    mod = adapter()

    async def execute(args, **kwargs):
        Path(args[args.index("--output-last-message") + 1]).write_text(
            "[]", encoding="utf-8"
        )
        return 0, b"", b""

    run = AsyncMock(side_effect=execute)
    monkeypatch.setattr(mod, "_run", run)
    with pytest.raises(mod.CodexOutputError):
        await mod.complete("native-codex", "system", "question", environment={})
    assert run.await_count == 1


class ScriptedProcess:
    def __init__(self, *, mode="complete", stderr=b""):
        self.mode = mode
        self.stdout = asyncio.StreamReader(limit=3 * 1024 * 1024)
        self.stderr = asyncio.StreamReader(limit=3 * 1024 * 1024)
        if stderr:
            self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = None
        self.sent = []
        self.finished = asyncio.Event()
        self.turn_started = asyncio.Event()
        self.wait_calls = 0
        self.terminated = False
        self.killed = False
        self.stdin = self.Writer(self)

    class Writer:
        def __init__(self, process):
            self.process = process
            self.closed = False

        def write(self, data):
            request = json.loads(data)
            self.process.sent.append(request)
            self.process.reply(request)

        async def drain(self):
            await asyncio.sleep(0)

        def close(self):
            self.closed = True

    def emit(self, data):
        self.stdout.feed_data(json.dumps(data).encode("utf-8") + b"\n")

    def reply(self, request):
        method = request.get("method")
        if method == "initialize":
            self.emit({"id": request["id"], "result": {}})
        elif method == "thread/start":
            self.emit({"id": request["id"], "result": {"thread": {"id": "thread"}}})
        elif method == "turn/start":
            self.turn_started.set()
            if self.mode == "early-delta":
                self.emit(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "delta": "before ack ",
                        },
                    }
                )
            self.emit({"id": request["id"], "result": {"turn": {"id": "turn"}}})
            if self.mode == "hang":
                return
            if self.mode == "malformed":
                self.stdout.feed_data(b"not-json\n")
                return
            if self.mode == "oversized":
                self.emit(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "delta": "x" * 4096,
                        },
                    }
                )
                return
            if self.mode == "provider-error":
                self.emit(
                    {
                        "method": "error",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "message": "private provider failure details",
                            "willRetry": True,
                        },
                    }
                )
                return
            if self.mode == "invalid-unicode":
                self.emit(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "delta": "\ud83d",
                        },
                    }
                )
                return
            self.emit(
                {
                    "method": "item/reasoning/summaryTextDelta",
                    "params": {
                        "threadId": "thread",
                        "turnId": "turn",
                        "delta": "hidden reasoning",
                    },
                }
            )
            self.emit(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "other-thread",
                        "turnId": "turn",
                        "delta": "wrong scope",
                    },
                }
            )
            self.emit(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread",
                        "turnId": "other-turn",
                        "delta": "wrong turn",
                    },
                }
            )
            self.emit(
                {
                    "id": 90,
                    "method": "item/commandExecution/requestApproval",
                    "params": {"threadId": "thread", "turnId": "turn"},
                }
            )
            for text in (
                () if self.mode == "no-delta" else ("first real ", "second delta")
            ):
                self.emit(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "delta": text,
                        },
                    }
                )
            self.emit(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread",
                        "turnId": "turn",
                        "item": {
                            "type": "agentMessage",
                            "text": "do not duplicate final text",
                        },
                    },
                }
            )
            self.emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread",
                        "turn": {
                            "id": "turn",
                            "status": "failed"
                            if self.mode == "failed"
                            else "completed",
                        },
                    },
                }
            )

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.finished.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.finished.set()

    async def wait(self):
        self.wait_calls += 1
        await self.finished.wait()
        return self.returncode


@pytest.mark.asyncio
async def test_chat_yields_only_true_scoped_app_server_deltas(monkeypatch):
    mod = adapter()
    process = ScriptedProcess()
    factory = AsyncMock(return_value=process)
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", factory)
    chunks = [
        text
        async for text in mod.stream_chat(
            "native-codex", "private context", "question", environment={}
        )
    ]
    assert chunks == ["first real ", "second delta"]
    args, kwargs = factory.await_args
    assert "app-server" in args and "exec" not in args and "--search" not in args
    assert 'web_search="disabled"' in args and "shell_tool" in args
    assert not Path(kwargs["cwd"]).exists()
    assert [packet["method"] for packet in process.sent if "method" in packet][:4] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
    ]
    thread = next(
        packet["params"]
        for packet in process.sent
        if packet.get("method") == "thread/start"
    )
    turn = next(
        packet["params"]
        for packet in process.sent
        if packet.get("method") == "turn/start"
    )
    assert thread["ephemeral"] and thread["approvalPolicy"] == "never"
    assert turn["approvalPolicy"] == "never" and turn["sandboxPolicy"] == {
        "type": "readOnly"
    }
    assert any(packet.get("id") == 90 and "error" in packet for packet in process.sent)
    assert process.terminated and process.wait_calls >= 1 and process.stdin.closed


@pytest.mark.asyncio
async def test_completion_accepts_plain_text_when_json_mode_is_disabled(monkeypatch):
    mod = adapter()

    async def execute(args, **kwargs):
        Path(args[args.index("--output-last-message") + 1]).write_text(
            "actual plain text", encoding="utf-8"
        )
        return 0, b"", b""

    monkeypatch.setattr(mod, "_run", execute)
    result = await mod.complete(
        "native-codex", "system", "question", json_mode=False, environment={}
    )
    assert result == {
        "content": "actual plain text",
        "model": "codex",
        "usage": {},
        "cost_cny": "0.0000",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["not-json", '{"x":1,"x":2}', ""])
async def test_completion_rejects_invalid_duplicate_or_empty_content(monkeypatch, text):
    mod = adapter()

    async def execute(args, **kwargs):
        Path(args[args.index("--output-last-message") + 1]).write_text(
            text, encoding="utf-8"
        )
        return 0, b"", b""

    monkeypatch.setattr(mod, "_run", execute)
    with pytest.raises(mod.CodexOutputError):
        await mod.complete("native-codex", "system", "question", environment={})


@pytest.mark.asyncio
async def test_completion_failure_is_sanitized_and_not_retried(monkeypatch):
    mod = adapter()
    run = AsyncMock(return_value=(1, b"private output", b"private stderr secret"))
    monkeypatch.setattr(mod, "_run", run)
    with pytest.raises(mod.CodexExecutionError) as caught:
        await mod.complete("native-codex", "system", "question", environment={})
    assert "secret" not in str(caught.value) and "private output" not in str(
        caught.value
    )
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_completion_output_limit_is_enforced(monkeypatch):
    mod = adapter()
    monkeypatch.setattr(mod, "_MAX_OUTPUT_BYTES", 512)

    async def execute(args, **kwargs):
        Path(args[args.index("--output-last-message") + 1]).write_text(
            "x" * 513, encoding="utf-8"
        )
        return 0, b"", b""

    monkeypatch.setattr(mod, "_run", execute)
    with pytest.raises(mod.CodexOutputError, match="oversized"):
        await mod.complete("native-codex", "system", "question", environment={})


@pytest.mark.asyncio
async def test_chat_first_delta_is_delivered_before_completion_and_aclose_reaps_child(
    monkeypatch,
):
    mod = adapter()
    process = ScriptedProcess()
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    stream = mod.stream_chat("native-codex", "system", "question", environment={})
    assert await stream.__anext__() == "first real "
    assert not process.terminated
    await stream.aclose()
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_chat_cancellation_terminates_and_awaits_owned_process(monkeypatch):
    mod = adapter()
    process = ScriptedProcess(mode="hang")
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )

    async def consume():
        return [
            text
            async for text in mod.stream_chat(
                "native-codex", "system", "question", environment={}
            )
        ]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(process.turn_started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_chat_timeout_terminates_and_awaits_owned_process(monkeypatch):
    mod = adapter()
    process = ScriptedProcess(mode="hang")
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    with pytest.raises(TimeoutError):
        _ = [
            text
            async for text in mod.stream_chat(
                "native-codex", "system", "question", timeout=0.01, environment={}
            )
        ]
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["failed", "no-delta", "malformed", "provider-error", "invalid-unicode"]
)
async def test_chat_rejects_failed_empty_or_malformed_stream_without_final_text_fallback(
    monkeypatch, mode
):
    mod = adapter()
    process = ScriptedProcess(mode=mode)
    factory = AsyncMock(return_value=process)
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", factory)
    with pytest.raises((mod.CodexExecutionError, mod.CodexOutputError)):
        _ = [
            text
            async for text in mod.stream_chat(
                "native-codex", "system", "question", environment={}
            )
        ]
    assert factory.await_count == 1 and process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_chat_keeps_deltas_arriving_before_turn_start_acknowledgement(
    monkeypatch,
):
    mod = adapter()
    process = ScriptedProcess(mode="early-delta")
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    chunks = [
        text
        async for text in mod.stream_chat(
            "native-codex", "system", "question", environment={}
        )
    ]
    assert chunks == ["before ack ", "first real ", "second delta"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["stdout", "stderr"])
async def test_chat_bounds_both_process_streams_and_reaps_child(monkeypatch, source):
    mod = adapter()
    monkeypatch.setattr(mod, "_MAX_OUTPUT_BYTES", 2048)
    process = ScriptedProcess(
        mode="oversized" if source == "stdout" else "complete",
        stderr=b"x" * 2049 if source == "stderr" else b"",
    )
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    with pytest.raises(mod.CodexOutputError, match="limit"):
        _ = [
            text
            async for text in mod.stream_chat(
                "native-codex", "system", "question", environment={}
            )
        ]
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_chat_broken_stdin_is_sanitized_and_reaps_child(monkeypatch):
    mod = adapter()
    process = ScriptedProcess()
    process.stdin.drain = AsyncMock(
        side_effect=BrokenPipeError("private transport details")
    )
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    with pytest.raises(mod.CodexExecutionError) as caught:
        _ = [
            text
            async for text in mod.stream_chat(
                "native-codex", "system", "question", environment={}
            )
        ]
    assert "private transport" not in str(caught.value)
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_chat_forces_kill_and_awaits_if_process_ignores_termination(monkeypatch):
    mod = adapter()
    monkeypatch.setattr(mod, "_STOP_TIMEOUT_SECONDS", 0.01)
    process = ScriptedProcess()

    def ignore_termination():
        process.terminated = True

    process.terminate = ignore_termination
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    chunks = [
        text
        async for text in mod.stream_chat(
            "native-codex", "system", "question", environment={}
        )
    ]
    assert chunks == ["first real ", "second delta"]
    assert process.terminated and process.killed and process.wait_calls == 2
