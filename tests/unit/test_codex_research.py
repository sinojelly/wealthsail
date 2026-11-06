"""The CLI adapter is tested without invoking a real model."""

import asyncio
import json
import os
import sys
import time
import tomllib
from importlib import import_module, util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def adapter():
    assert util.find_spec("backend.integrations.codex_research"), (
        "Codex adapter missing"
    )
    return import_module("backend.integrations.codex_research")


def test_partial_cached_usage_is_not_discarded_when_input_is_unknown():
    assert adapter().normalize_usage({"cached_input_tokens": 10}) == {"cached_input_tokens": 10}


@pytest.mark.asyncio
async def test_timeout_retains_trusted_usage_and_safe_progress_from_prior_events(monkeypatch):
    mod = adapter()

    async def execute(args, **kwargs):
        kwargs["stdout_observer"](b'{"type":"thread.started"}\n{"type":"turn.completed","usage":{"input_tokens":17,"output_tokens":3}}\n')
        raise TimeoutError("private")

    monkeypatch.setattr(mod, "_run", execute)
    with pytest.raises(TimeoutError) as error:
        await mod.run_codex_research("fake", "public", {})
    assert error.value.usage["prompt_tokens"] == 17
    assert error.value.execution_progress["modelCompleted"] is True
    assert error.value.execution_statistics["version"] == "research-execution-stats-v1"


@pytest.mark.asyncio
async def test_public_research_keeps_usage_outside_the_strict_report_payload(monkeypatch):
    mod = adapter()

    async def execute(args, **kwargs):
        Path(args[args.index("--output-last-message") + 1]).write_text('{"summary":"public"}', encoding="utf-8")
        return 0, b'{"type":"turn.completed","usage":{"input_tokens":30,"cached_input_tokens":10,"output_tokens":7}}\n', b""

    monkeypatch.setattr(mod, "_run", execute)
    result = await mod.run_codex_research("native-codex", "public", {"type": "object"}, model="fixture", environment={})
    assert result == {"summary": "public"}
    assert getattr(result, "usage", None) == {"prompt_tokens": 30, "cached_input_tokens": 10, "completion_tokens": 7, "total_tokens": 37}
    assert result.model == "fixture"
    assert json.dumps(result) == '{"summary": "public"}'


def test_linux_native_codex_is_found_on_path(monkeypatch, tmp_path):
    mod = adapter()
    executable = tmp_path / "codex"
    executable.write_bytes(b"\x7fELF-native-fixture")
    monkeypatch.setattr(
        mod.shutil, "which",
        lambda name, path=None: str(executable) if name == "codex" else None,
    )
    assert mod.resolve_codex_executable(environment={"PATH": str(tmp_path)}) == str(
        executable.resolve()
    )


@pytest.mark.parametrize("suffix", [".cmd", ".ps1", ".bat", ".js"])
def test_explicit_shell_or_node_wrappers_are_rejected(tmp_path, suffix):
    executable = tmp_path / f"codex{suffix}"
    executable.touch()
    assert adapter().resolve_codex_executable(str(executable)) is None


def test_discovered_node_wrapper_is_not_a_native_runtime(monkeypatch, tmp_path):
    mod = adapter()
    wrapper = tmp_path / "codex.js"
    wrapper.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    monkeypatch.setattr(mod.shutil, "which", lambda *_args, **_kwargs: str(wrapper))
    assert mod.resolve_codex_executable(environment={"PATH": str(tmp_path)}) is None


def test_linux_home_survives_but_temp_and_credentials_are_scoped(tmp_path):
    env = adapter().public_environment(
        {"HOME": "/home/appuser", "CODEX_HOME": "/auth", "TMPDIR": "/private",
         "CODEX_RUNNER_TOKEN": "private", "MODEL_API_KEY": "private",
         "OPENAI_API_KEY": "private", "DATABASE_URL": "private"},
        tmp_path,
    )
    assert env["HOME"] == "/home/appuser"
    assert env["CODEX_HOME"] == "/auth"
    assert env["TMPDIR"] == str(tmp_path)
    assert not {"CODEX_RUNNER_TOKEN", "MODEL_API_KEY", "OPENAI_API_KEY", "DATABASE_URL"} & env.keys()


def test_child_environment_uses_existing_user_auth_but_no_worker_or_api_secrets(
    tmp_path,
):
    env = adapter().public_environment(
        {
            "PATH": "runtime",
            "USERPROFILE": "user",
            "CODEX_HOME": "auth-home",
            "WORKER_DATABASE_URL": "private",
            "DATABASE_URL": "private",
            "WEALTHSAIL_ENV_FILE": ".local/macro.env",
            "MODEL_API_KEY": "private",
            "OPENAI_API_KEY": "private",
            "JWT_SECRET": "private",
        },
        tmp_path,
    )
    assert env["USERPROFILE"] == "user"
    assert env["CODEX_HOME"] == "auth-home"
    assert not any(
        key in env
        for key in (
            "WORKER_DATABASE_URL",
            "DATABASE_URL",
            "WEALTHSAIL_ENV_FILE",
            "MODEL_API_KEY",
            "OPENAI_API_KEY",
            "JWT_SECRET",
        )
    )


def test_preserves_current_user_model_without_copying_auth_or_loading_connectors(
    tmp_path,
):
    directory = tmp_path / "codex-home"
    directory.mkdir()
    (directory / "config.toml").write_text(
        'model = "current-user-model"\n[mcp_servers.private]\nurl="https://private.invalid"\n',
        encoding="utf-8",
    )
    assert (
        adapter().configured_model({"CODEX_HOME": str(directory)})
        == "current-user-model"
    )


def test_rejects_duplicate_model_json_fields(monkeypatch, tmp_path):
    # JSON schema validation must see the actual unique keys, never silently
    # replace an earlier unsafe assertion with a later value.
    mod = adapter()
    with pytest.raises(mod.CodexOutputError):
        mod.decode_report('{"summary":"first","summary":"second"}')


@pytest.mark.asyncio
async def test_one_exec_stdin_read_only_public_scratch_and_validated_json(
    monkeypatch, tmp_path
):
    mod = adapter()
    executable = tmp_path / "codex.exe"
    executable.touch()
    calls = []

    async def execute(args, **kwargs):
        calls.append((args, kwargs))
        directory = Path(kwargs["cwd"])
        assert not (directory / ".env").exists()
        assert not (directory / ".local").exists()
        assert "WORKER_DATABASE_URL" not in kwargs["env"]
        schema_path = Path(args[args.index("--output-schema") + 1])
        assert json.loads(schema_path.read_text("utf-8")) == {"type": "object"}
        # Codex's own host writes the final message, never a model shell command.
        Path(args[args.index("--output-last-message") + 1]).write_text(
            '{"summary":"public"}'
        )
        return 0, b'{"type":"thread.started"}\n', b""

    monkeypatch.setattr(mod, "_run", execute)
    result = await mod.run_codex_research(
        str(executable),
        "one public prompt",
        {"type": "object"},
        environment={"WORKER_DATABASE_URL": "secret"},
    )
    assert result == {"summary": "public"}
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[-1] == "-" and kwargs["input_data"] == b"one public prompt"
    for value in (
        "--search",
        "never",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "shell_tool",
        "apps",
        "plugins",
        "multi_agent",
        "mcp_servers={}",
    ):
        assert value in args
    assert not Path(kwargs["cwd"]).exists(), (
        "public scratch must be removed after completion"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", ["project_doc_max_bytes", "developer_instructions"])
async def test_model_session_disables_project_docs_and_pins_public_research_scope(
    monkeypatch, tmp_path, setting
):
    mod = adapter()
    executable = tmp_path / "codex.exe"
    executable.touch()
    captured = {}

    async def execute(args, **kwargs):
        for index, value in enumerate(args[:-1]):
            if value == "-c":
                captured.update(tomllib.loads(args[index + 1]))
        Path(args[args.index("--output-last-message") + 1]).write_text(
            '{"summary":"public"}', encoding="utf-8"
        )
        return 0, b"", b""

    monkeypatch.setattr(mod, "_run", execute)
    await mod.run_codex_research(
        str(executable), "one public prompt", {"type": "object"}, environment={}
    )
    assert setting in captured
    if setting == "project_doc_max_bytes":
        assert captured[setting] == 0
    else:
        assert "public research packet" in captured[setting]
        assert "private files" in captured[setting]
        assert "untrusted" in captured[setting]


@pytest.mark.asyncio
async def test_preflight_failure_exposes_only_safe_error_class(monkeypatch, tmp_path):
    mod = adapter()
    executable = tmp_path / "codex.exe"
    executable.touch()
    monkeypatch.setattr(
        mod, "_run", AsyncMock(return_value=(1, b"", b"sensitive stderr"))
    )
    with pytest.raises(mod.CodexUnavailableError) as error:
        await mod.preflight_codex(str(executable))
    assert "sensitive" not in str(error.value)


@pytest.mark.asyncio
async def test_preflight_checks_each_disabled_feature_before_consuming_slot(
    monkeypatch, tmp_path
):
    mod = adapter()
    executable = tmp_path / "codex.exe"
    executable.touch()

    async def command(args, **kwargs):
        if args[-2:] == ["features", "list"]:
            # An old CLI lacking one of the named safety features must fail closed.
            return (
                0,
                b"shell_tool stable false\napps stable false\nplugins stable false\n",
                b"",
            )
        if args[-1] == "--version":
            return 0, b"codex-cli 0.159.2", b""
        return (
            0,
            b"--search --ask-for-approval --disable --output-schema --ephemeral --ignore-user-config --sandbox --output-last-message",
            b"",
        )

    monkeypatch.setattr(mod, "_run", command)
    with pytest.raises(mod.CodexUnavailableError):
        await mod.preflight_codex(str(executable))


@pytest.mark.asyncio
async def test_cancel_terminates_and_awaits_child(monkeypatch):
    mod = adapter()
    started = asyncio.Event()
    child = AsyncMock()
    child.returncode = None
    child.stdin.write = lambda data: None
    child.stdin.close = lambda: None
    child.terminate = lambda: setattr(child, "returncode", -15)
    child.stdout.read.side_effect = asyncio.CancelledError
    child.stderr.read.return_value = b""

    async def wait():
        started.set()
        await asyncio.Event().wait()

    async def await_child():
        if child.returncode is not None:
            return child.returncode
        return await wait()

    child.wait.side_effect = await_child
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=child)
    )
    task = asyncio.create_task(
        mod._run(["fake"], cwd=".", env={}, input_data=b"", timeout=10)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.returncode == -15
    await asyncio.gather(*list(mod._OWNED_CLEANUPS), return_exceptions=True)
    assert child.wait.await_count >= 2


@pytest.mark.asyncio
async def test_timeout_in_stdin_drain_also_terminates_and_awaits_child(monkeypatch):
    mod = adapter()
    child = AsyncMock()
    child.returncode = None
    child.stdin.write = lambda data: None
    child.stdin.close = lambda: None
    child.terminate = lambda: setattr(child, "returncode", -15)
    child.stdout.read.return_value = b""
    child.stderr.read.return_value = b""

    async def drain():
        await asyncio.Future()

    async def wait():
        if child.returncode is not None:
            return child.returncode
        await asyncio.Future()

    child.stdin.drain.side_effect = drain
    child.wait.side_effect = wait
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=child)
    )
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            mod._run(["fake"], cwd=".", env={}, input_data=b"public", timeout=0.01), 0.2
        )
    # Outer guard must not be what enforced the timeout.
    assert time.monotonic() - started < 0.1
    assert child.returncode == -15
    assert child.wait.await_count >= 2


@pytest.mark.asyncio
async def test_cli_timeout_preserves_bound_exit_state_and_origin(monkeypatch):
    from uuid import uuid4
    from backend.integrations.research_lifecycle import ResearchLifecycle

    mod = adapter()
    state = ResearchLifecycle({key: str(uuid4()) for key in ("runId", "ownerToken", "callId")})
    with pytest.raises(TimeoutError) as error:
        await mod._run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=".",
                       env=dict(os.environ), timeout=0.03, lifecycle=state)
    assert error.value.timeout_origin == "CLI_MODEL_TIMEOUT"
    assert state.snapshot()["reaped"] is True
    assert state.snapshot()["terminationRequested"] is True
    assert state.snapshot()["exitConfirmed"] is (sys.platform == "linux")


@pytest.mark.asyncio
async def test_cancel_during_spawn_still_disposes_created_child(monkeypatch):
    mod = adapter()
    started, release = asyncio.Event(), asyncio.Event()
    child = AsyncMock()
    child.returncode = None
    child.stdin.close = lambda: None
    child.stdout.read.return_value = b""
    child.stderr.read.return_value = b""
    child.terminate = lambda: setattr(child, "returncode", -15)

    async def spawn(*args, **kwargs):
        started.set()
        await release.wait()
        return child

    async def wait():
        if child.returncode is None:
            await asyncio.Event().wait()
        return child.returncode

    child.wait.side_effect = wait
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", spawn)
    active = asyncio.create_task(mod._run(["fake"], cwd=".", env={}))
    await started.wait()
    active.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert child.returncode == -15


@pytest.mark.asyncio
async def test_cancel_during_stuck_os_spawn_is_bounded_and_never_confirms_exit(monkeypatch):
    from uuid import uuid4
    from backend.integrations.research_lifecycle import ResearchLifecycle

    mod = adapter()
    monkeypatch.setattr(mod, "CLEANUP_SECONDS", 0.02, raising=False)
    started = asyncio.Event()
    os_tasks = []

    async def spawn(*args, **kwargs):
        os_tasks.append(asyncio.current_task())
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", spawn)
    state = ResearchLifecycle({key: str(uuid4()) for key in ("runId", "ownerToken", "callId")})
    active = asyncio.create_task(mod._run(["fake"], cwd=".", env={}, lifecycle=state))
    await started.wait()
    active.cancel()
    try:
        done, _ = await asyncio.wait({active}, timeout=0.15)
        assert active in done, "OS spawn must not hold the caller beyond cleanup budget"
        with pytest.raises(asyncio.CancelledError):
            await active
        assert state.snapshot()["exitConfirmed"] is False
        assert state.snapshot()["state"] == "UNKNOWN"
    finally:
        for task in os_tasks:
            task.cancel()
        await asyncio.gather(*os_tasks, active, return_exceptions=True)


@pytest.mark.asyncio
async def test_late_spawn_handle_is_owned_and_disposed_after_caller_returns(monkeypatch):
    from uuid import uuid4
    from backend.integrations.research_lifecycle import ResearchLifecycle

    mod = adapter()
    monkeypatch.setattr(mod, "CLEANUP_SECONDS", 0.02, raising=False)
    started, release = asyncio.Event(), asyncio.Event()
    child = AsyncMock()
    child.returncode = None
    child.stdin.close = lambda: None
    child.stdout.read.return_value = b""
    child.stderr.read.return_value = b""
    child.terminate = lambda: setattr(child, "returncode", -15)
    async def wait():
        return child.returncode

    child.wait.side_effect = wait

    async def spawn(*args, **kwargs):
        started.set()
        await release.wait()
        return child

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", spawn)
    state = ResearchLifecycle({key: str(uuid4()) for key in ("runId", "ownerToken", "callId")})
    active = asyncio.create_task(mod._run(["fake"], cwd=".", env={}, lifecycle=state))
    await started.wait()
    active.cancel()
    try:
        done, _ = await asyncio.wait({active}, timeout=0.15)
        assert active in done
        with pytest.raises(asyncio.CancelledError):
            await active
        assert state.snapshot()["exitConfirmed"] is False
    finally:
        release.set()
        await asyncio.gather(active, return_exceptions=True)
    for _ in range(100):
        if child.returncode is not None:
            break
        await asyncio.sleep(0)
    assert child.returncode == -15
    await asyncio.gather(*list(mod._OWNED_CLEANUPS), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Linux production process group isolation")
async def test_timeout_reaps_process_group_even_when_child_ignores_term(tmp_path):
    from uuid import uuid4
    from backend.integrations.research_lifecycle import ResearchLifecycle

    mod = adapter()
    marker = tmp_path / "child.pid"
    program = (
        "import os,signal,subprocess,sys,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "p=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)']); "
        "open(sys.argv[1],'w').write(str(p.pid)); time.sleep(30)"
    )
    state = ResearchLifecycle({key: str(uuid4()) for key in ("runId", "ownerToken", "callId")})
    with pytest.raises(TimeoutError):
        await mod._run([sys.executable, "-c", program, str(marker)], cwd=str(tmp_path),
                       env=dict(os.environ), timeout=0.3, lifecycle=state)
    assert state.snapshot()["exitConfirmed"] is True
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
