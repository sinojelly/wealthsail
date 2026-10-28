"""Virtual slow account RPCs exercise deadlines without launching Codex."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend import codex_runner
from backend.integrations import codex_client, codex_private


@pytest.fixture
def virtual_account(monkeypatch):
    real_timeout = asyncio.timeout
    state = {"latency": 17.0, "budgets": [], "hang": False}
    process = SimpleNamespace(stderr=object())
    cleanup = AsyncMock()

    def timeout(seconds):
        state["budgets"].append(seconds)
        # A virtual 17-second provider yields once; insufficient time cancels it
        # immediately. Hanging RPCs always exhaust the scaled deadline.
        return real_timeout(0 if seconds < state["latency"] else 0.05)

    class Server:
        def __init__(self, *args):
            pass

        async def rpc(self, request_id, method, params):
            if state["hang"]:
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            if method == "account/read":
                return {"account": {"type": "chatgpt", "email": "fixture@example.test"}}
            if method == "account/rateLimits/read":
                return {
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 7,
                            "windowDurationMins": 300,
                            "resetsAt": 1999999999,
                        }
                    }
                }
            return {}

        send = AsyncMock()

    monkeypatch.setattr(codex_private.asyncio, "timeout", timeout)
    monkeypatch.setattr(
        codex_private.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    monkeypatch.setattr(codex_private, "_AppServer", Server)
    monkeypatch.setattr(codex_private, "_drain_stderr", AsyncMock())
    monkeypatch.setattr(codex_private, "_stop_process", cleanup)
    return state, cleanup


@pytest.mark.asyncio
@pytest.mark.parametrize("latency", [16.5, 19.0])
async def test_account_reads_slower_than_old_fifteen_second_deadline_succeed(
    virtual_account, latency
):
    state, cleanup = virtual_account
    state["latency"] = latency
    result = await codex_private.read_account("fake-codex", environment={})
    assert result["status"] == "READY"
    assert result["windows"][0]["remainingPercent"] == 93
    assert latency <= state["budgets"][0] < 23
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_larger_requested_deadline_is_capped_with_cleanup_headroom(
    virtual_account,
):
    state, cleanup = virtual_account
    result = await codex_private.read_account("fake-codex", environment={}, timeout=120)
    assert result["status"] == "READY"
    assert state["budgets"] == [20]
    # Terminate and kill each retain their bounded half-second wait.
    assert cleanup.await_args.kwargs["timeout"] == 0.5
    assert state["budgets"][0] + 2 * cleanup.await_args.kwargs["timeout"] < 23


@pytest.mark.asyncio
async def test_hanging_account_still_times_out_and_runs_cleanup(virtual_account):
    state, cleanup = virtual_account
    state["hang"] = True
    result = await codex_private.read_account("fake-codex", environment={})
    assert result["status"] == "UNAVAILABLE"
    assert result["windows"] == []
    assert state["budgets"] == [20]
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_shorter_account_deadline_is_honoured(virtual_account):
    state, cleanup = virtual_account
    result = await codex_private.read_account(
        "fake-codex", environment={}, timeout=0.01
    )
    assert result["status"] == "UNAVAILABLE"
    assert state["budgets"] == [0.01]
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["runner", "local"])
async def test_account_runtime_uses_shared_read_deadline(monkeypatch, runtime):
    budgets = []
    default = (
        inspect.signature(codex_private.read_account).parameters["timeout"].default
    )

    async def read(executable, *, environment=None, timeout=default):
        budgets.append(timeout)
        return codex_private.account_snapshot(
            {"account": {"type": "chatgpt", "email": "fixture@example.test"}},
            {"rateLimits": {"primary": {"usedPercent": 7}}},
        )

    monkeypatch.setattr(codex_private, "read_account", read)
    if runtime == "runner":
        token = "fixture-runner-token-" + "x" * 32
        app = codex_runner.create_app(token=token, environment={})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://runner"
        ) as client:
            result = (
                await client.get(
                    "/v1/account?fresh=true",
                    headers={"Authorization": f"Bearer {token}"},
                )
            ).json()
    else:
        monkeypatch.setattr(
            codex_client.codex_research,
            "resolve_codex_executable",
            lambda path: "fake-codex",
        )
        result = await codex_client.account(
            SimpleNamespace(codex_path="fake-codex"), fresh=True
        )
    assert result["status"] == "READY"
    assert budgets == [20]
