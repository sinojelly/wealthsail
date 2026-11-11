"""Runtime failures retain only closed diagnostic metadata across boundaries."""

import json
from unittest.mock import AsyncMock

import pytest

from backend.integrations import codex_research, codex_client
from backend import codex_runner
from backend.jobs import daily_research
from backend.ai_usage import calculation, repository, tracker
from backend.integrations import codex_private


def diagnostic(**changes):
    return {
        "version": "codex-diagnostic-v1",
        "origin": "CLI",
        "stage": "CLI_EXECUTION",
        "reasonCode": "CLI_FAILED_UNCLASSIFIED",
        "exitCode": 1,
        **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stderr,reason,status",
    [
        (
            b"token=private secret upstream HTTP 429 Too Many Requests",
            "RATE_LIMITED",
            429,
        ),
        (
            b"private@example.test unexpected runtime failure",
            "CLI_FAILED_UNCLASSIFIED",
            None,
        ),
        (b"private connection detail", "CLI_FAILED_UNCLASSIFIED", None),
        (b"private timeout detail", "CLI_FAILED_UNCLASSIFIED", None),
        (b"certificate verify failed /private/auth.json", "TLS_ERROR", None),
    ],
)
async def test_cli_exit_failure_has_safe_reason_without_raw_stderr(
    monkeypatch, stderr, reason, status
):
    execute = AsyncMock(return_value=(1, b'{"type":"turn.started"}\n', stderr))
    monkeypatch.setattr(codex_research, "_run", execute)
    with pytest.raises(codex_research.CodexExecutionError) as caught:
        await codex_research.run_codex_research("fixture", "private prompt", {})
    value = getattr(caught.value, "codex_diagnostic", {})
    assert value.get("reasonCode") == reason
    assert value.get("exitCode") == 1
    assert value.get("httpStatus") == status
    assert "private" not in json.dumps(value)
    assert "private" not in str(caught.value)
    assert execute.await_count == 1


@pytest.mark.asyncio
async def test_stdout_failed_event_diagnostic_crosses_runner_client_and_daily_reason(
    monkeypatch,
):
    async def execute(_args, **kwargs):
        kwargs["stdout_observer"](
            b'{"type":"turn.failed","error":{"code":"insufficient_quota","message":"private token=secret"}}\n'
        )
        return 1, b"", b""

    monkeypatch.setattr(codex_research, "_run", execute)
    with pytest.raises(codex_research.CodexExecutionError) as caught:
        await codex_research.run_codex_research("fixture", "private prompt", {})
    detail = codex_runner._error_detail(caught.value, "Codex execution failed")
    assert detail.get("diagnostic", {}).get("reasonCode") == "QUOTA_EXHAUSTED"
    wrapped = codex_client._failure_metadata(
        codex_client.CodexCallError("failed"), detail
    )
    assert (
        getattr(wrapped, "codex_diagnostic", {}).get("reasonCode") == "QUOTA_EXHAUSTED"
    )
    assert daily_research._canonical_reason_code(wrapped) == "QUOTA_EXHAUSTED"
    assert "private" not in json.dumps(detail)


def test_diagnostic_whitelist_rejects_arbitrary_fields_and_unsafe_values():
    value = diagnostic(message="token=private", origin="https://private", exitCode=True)
    error = codex_client._failure_metadata(
        codex_client.CodexCallError("failed"), {"diagnostic": value}
    )
    assert getattr(error, "codex_diagnostic", None) is None
    value = diagnostic(message="token=private", httpStatus=999, rpcCode=-32603)
    error = codex_client._failure_metadata(
        codex_client.CodexCallError("failed"), {"diagnostic": value}
    )
    safe = getattr(error, "codex_diagnostic", {})
    assert safe == diagnostic(rpcCode=-32603)
    assert "private" not in str(safe)


def test_usage_dto_keeps_safe_model_and_account_failures_separate():
    model = diagnostic()
    account = diagnostic(
        origin="ACCOUNT_RPC",
        stage="ACCOUNT_READ",
        reasonCode="ACCOUNT_RPC_FAILED",
        rpcCode=-32603,
    )
    before = {
        "status": "UNAVAILABLE",
        "diagnostic": {**account, "reason": "UNAVAILABLE", "elapsedMs": 15005},
    }
    row = {
        "id": "call",
        "model": "codex",
        "call_type": "research",
        "status": "FAILED",
        "started_at": None,
        "finished_at": None,
        "details": {
            "diagnostic": {**model, "message": "private"},
            "before": before,
            "after": {},
        },
    }
    result = repository.call_dto(row)
    assert result.get("diagnostic") == model
    safe_account = result["quota"]["snapshots"]["before"].get("diagnostic", {})
    assert safe_account.get("stage") == "ACCOUNT_READ"
    assert safe_account.get("reasonCode") == "ACCOUNT_RPC_FAILED"
    assert safe_account.get("rpcCode") == -32603
    assert "private" not in str(result)


@pytest.mark.asyncio
async def test_tracker_preserves_account_failure_origin_and_stage(monkeypatch):
    account = diagnostic(
        origin="ACCOUNT_RPC",
        stage="ACCOUNT_READ",
        reasonCode="ACCOUNT_RPC_FAILED",
        rpcCode=-32603,
    )
    monkeypatch.setattr(
        codex_client,
        "account",
        AsyncMock(return_value={"status": "UNAVAILABLE", "diagnostic": account}),
    )
    result = await tracker.account_snapshot(object(), stage="before")
    assert result["diagnostic"].get("origin") == "ACCOUNT_RPC"
    assert result["diagnostic"].get("stage") == "ACCOUNT_READ"
    assert result["diagnostic"].get("reason") == "UNAVAILABLE"
    assert (
        calculation.snapshot_diagnostic(result).get("diagnostic", {}).get("reasonCode")
        == "ACCOUNT_RPC_FAILED"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message,reason",
    [
        ("15 second RPC failure private token", "ACCOUNT_RPC_FAILED"),
        ("HTTP 401 private token", "AUTHENTICATION_FAILED"),
        ("private request timed out", "RPC_RESPONSE_TIMEOUT"),
        ("workspace routing discovery timed out", "WORKSPACE_ROUTING_TIMEOUT"),
    ],
)
async def test_account_rpc_failure_preserves_stage_and_rpc_code_without_message(
    monkeypatch, message, reason
):
    from tests.unit.test_codex_private import ScriptedProcess
    from backend.integrations.codex_account_cache import AccountMetadataCache

    class FailedAccount(ScriptedProcess):
        def reply(self, request):
            if request.get("method") == "account/read":
                self.emit(
                    {"id": request["id"], "error": {"code": -32603, "message": message}}
                )
            else:
                super().reply(request)

    process = FailedAccount()
    monkeypatch.setattr(
        codex_private.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    value = await codex_private.read_account("fixture", environment={})
    assert value["status"] == "UNAVAILABLE"
    assert value.get("diagnostic", {}).get("stage") == "ACCOUNT_READ"
    assert value.get("diagnostic", {}).get("reasonCode") == reason
    assert value.get("diagnostic", {}).get("rpcCode") == -32603
    assert "private" not in json.dumps(value)
    cached = AccountMetadataCache(
        AsyncMock(return_value=value), generation=lambda: None
    )
    result = await cached.get(fresh=True)
    assert result.get("diagnostic") == value["diagnostic"]
    assert (
        len([item for item in process.sent if item.get("method") == "account/read"])
        == 1
    )


@pytest.mark.asyncio
async def test_remote_http_error_without_cli_details_is_runner_error_not_auth(
    monkeypatch,
):
    import httpx
    from tests.unit.test_codex_client import settings, transport

    transport(
        monkeypatch,
        lambda _request: httpx.Response(502, json={"detail": "private failure"}),
    )
    with pytest.raises(codex_client.CodexCallError) as caught:
        await codex_client.research(settings(), "public", {}, trace_usage=False)
    assert getattr(caught.value, "codex_diagnostic", {}) == {
        "version": "codex-diagnostic-v1",
        "origin": "RUNNER",
        "stage": "RUNNER_HTTP",
        "reasonCode": "RUNNER_HTTP_ERROR",
        "httpStatus": 502,
    }
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_account_eof_retains_only_recognized_stderr_classification(monkeypatch):
    from tests.unit.test_codex_private import ScriptedProcess

    class DisconnectedAccount(ScriptedProcess):
        def reply(self, request):
            if request.get("method") == "account/read":
                self.stdout.feed_eof()
            else:
                super().reply(request)

    process = DisconnectedAccount(stderr=b"certificate verify failed token=private")
    monkeypatch.setattr(
        codex_private.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    result = await codex_private.read_account("fixture", environment={})
    assert result.get("diagnostic", {}).get("reasonCode") == "TLS_ERROR"
    assert result["diagnostic"]["origin"] == "ACCOUNT_RPC"
    assert result["diagnostic"]["stage"] == "ACCOUNT_READ"
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
async def test_real_subprocess_stderr_is_classified_and_returned_without_content(
    tmp_path,
):
    import os
    import sys

    code, stdout, stderr = await codex_research._run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('HTTP 429 token=private'); sys.exit(3)",
        ],
        cwd=str(tmp_path),
        env=os.environ.copy(),
        timeout=10,
    )
    assert code == 3 and stdout == b"" and stderr == b""
    assert stderr.codex_diagnostic["reasonCode"] == "RATE_LIMITED"
    assert stderr.codex_diagnostic["httpStatus"] == 429
    assert "private" not in str(stderr.codex_diagnostic)


@pytest.mark.asyncio
async def test_actual_usage_observation_persists_failure_metadata_once(monkeypatch):
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from types import SimpleNamespace

    events = []
    persist_function = repository.persist

    async def persist(_pool, _tenant, _user, event, data):
        events.append((event, deepcopy(data)))

    monkeypatch.setattr(repository, "persist", persist)
    monkeypatch.setattr(
        tracker, "account_snapshot", AsyncMock(return_value={"status": "UNAVAILABLE"})
    )
    error = codex_client.CodexCallError("safe failure")
    error.codex_diagnostic = {**diagnostic(), "message": "private"}
    operation = AsyncMock(side_effect=error)
    with pytest.raises(codex_client.CodexCallError):
        async with tracker.usage_task(
            object(),
            SimpleNamespace(),
            tenant_id=None,
            user_id=None,
            task_type="DAILY_RESEARCH",
            task_name="fixture",
        ):
            await tracker.trace_inference(
                SimpleNamespace(),
                model="fixture",
                call_type="research",
                operation=operation,
            )
    calls = [data for event, data in events if event == "finish_call"]
    assert len(calls) == 1
    assert calls[0]["diagnostic"] == diagnostic()
    assert calls[0]["status"] == "FAILED"
    assert operation.await_count == 1
    assert "private" not in json.dumps(calls, default=str)

    writes = []

    class Connection:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def execute(self, sql, *args):
            writes.append((sql, args))

    @asynccontextmanager
    async def boundary(_pool):
        yield Connection()

    monkeypatch.setattr(repository, "system_connection", boundary)
    await persist_function(object(), None, None, "finish_call", calls[0])
    details = json.loads(
        next(args[5] for sql, args in writes if "UPDATE research.ai_usage_calls" in sql)
    )
    assert details["diagnostic"] == diagnostic()


@pytest.mark.asyncio
async def test_cached_historical_account_keeps_latest_failure_diagnostic():
    from backend.integrations.codex_account_cache import AccountMetadataCache
    from tests.unit.test_codex_account_cache import ready

    failure = diagnostic(
        origin="ACCOUNT_RPC",
        stage="ACCOUNT_READ",
        reasonCode="ACCOUNT_RPC_FAILED",
        rpcCode=-32603,
    )
    fetch = AsyncMock(
        side_effect=[ready(), codex_private.unavailable_account(diagnostic=failure)]
    )
    cache = AccountMetadataCache(fetch, generation=lambda: None)
    assert (await cache.get())["status"] == "READY"
    result = await cache.get(refresh=True)
    assert result["status"] == "STALE"
    assert result.get("diagnostic") == failure
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_real_http_error_envelope_keeps_closed_diagnostic_and_one_dispatch(
    monkeypatch,
):
    import httpx
    from tests.unit.test_codex_runner import client_for, auth
    from tests.unit.test_codex_client import settings, transport

    error = codex_research.CodexExecutionError("token=private must not escape")
    error.codex_diagnostic = {**diagnostic(), "message": "token=private"}
    execution = AsyncMock(side_effect=error)
    monkeypatch.setattr(codex_runner, "run_codex_research", execution)
    async with client_for() as (client, _app):
        response = await client.post(
            "/v1/research", headers=auth(), json={"prompt": "private", "schema": {}}
        )
    assert response.status_code == 502
    assert response.json()["detail"]["diagnostic"] == diagnostic()
    assert "private" not in response.text
    execution.assert_awaited_once()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(502, json=response.json())

    transport(monkeypatch, handler)
    with pytest.raises(codex_client.CodexCallError) as caught:
        await codex_client.research(settings(), "public", {}, trace_usage=False)
    assert caught.value.codex_diagnostic == diagnostic()
    assert (
        daily_research._canonical_reason_code(caught.value) == "CLI_FAILED_UNCLASSIFIED"
    )
    assert len(requests) == 1


def test_real_offline_child_can_import_pure_diagnostics_and_still_denies_execution():
    import subprocess
    import sys
    from backend.macro.research_acceptance_replay import PROJECT_ROOT, child_environment

    script = """
import json, socket, sys
from backend.macro.research_acceptance_replay import _deny_external_execution
sys.addaudithook(_deny_external_execution)
from backend.common.runtime_diagnostics import classify_failure
safe = classify_failure({'error': {'code': -32603, 'message': 'workspace routing discovery timed out'}}, origin='ACCOUNT_RPC', stage='ACCOUNT_READ', default='ACCOUNT_RPC_FAILED')
provider_blocked = network_blocked = False
try:
    import backend.integrations.codex_research
except ImportError:
    provider_blocked = True
try:
    socket.socket()
except PermissionError:
    network_blocked = True
print(json.dumps({'reason': safe['reasonCode'], 'providerBlocked': provider_blocked, 'networkBlocked': network_blocked}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=child_environment(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout) == {
        "reason": "WORKSPACE_ROUTING_TIMEOUT",
        "providerBlocked": True,
        "networkBlocked": True,
    }
