"""Offline replay has a hard process deadline and no credential inheritance."""

import asyncio
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def module():
    return import_module("backend.macro.research_acceptance_replay")


def test_child_environment_does_not_inherit_provider_or_database_credentials(
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_PROVIDER")
    monkeypatch.setenv("DATABASE_URL", "PRIVATE_DB")
    monkeypatch.setenv("PYTHONPATH", "PRIVATE_PATH")
    env = module().child_environment()
    assert "PRIVATE" not in str(env)
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert set(env) <= {
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "PYTHONIOENCODING",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_terminate_and_wait_for_child(
    monkeypatch, cancel
):
    mod = module()
    entered = asyncio.Event()
    proc = SimpleNamespace(
        returncode=None,
        terminate=lambda: setattr(proc, "returncode", 1),
        kill=lambda: setattr(proc, "returncode", -9),
        wait=AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(mod, "_exchange", blocked)
    task = asyncio.create_task(
        mod.run_offline_validation(
            {"researchId": "x"}, timeout=0.02 if not cancel else 5
        )
    )
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(mod.OfflineReplayError) as raised:
        await task
    assert raised.value.code == ("CANCELLED" if cancel else "TIMEOUT")
    assert proc.returncode == 1
    proc.wait.assert_awaited()


@pytest.mark.asyncio
async def test_stdout_read_has_a_hard_byte_limit():
    mod = module()
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * (mod.OUTPUT_LIMIT + 1))
    reader.feed_eof()
    with pytest.raises(mod.OfflineReplayError, match="OUTPUT_LIMIT"):
        await mod._read_output(reader)


@pytest.mark.asyncio
async def test_stopping_child_drains_remaining_pipe_bytes_before_waiting():
    mod = module()
    reader = asyncio.StreamReader()
    reader.feed_data(b"remaining output")
    reader.feed_eof()
    async def wait():
        while not reader.at_eof():
            await asyncio.sleep(0)
        return 1
    proc = SimpleNamespace(returncode=None, stdout=reader, terminate=lambda: setattr(proc, "returncode", 1), kill=lambda: setattr(proc, "returncode", -9), wait=wait)
    assert await asyncio.wait_for(mod._stop(proc), 0.2) == 1


@pytest.mark.asyncio
async def test_real_child_uses_frozen_time_and_returns_current_revision_without_provider(
    monkeypatch,
):
    from tests.unit.test_research_acceptance_repository import NOW, RUN

    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_PROVIDER")
    result = await module().run_offline_validation(
        {
            "raw": "invalid JSON",
            "packet": {},
            "sourceChecks": {},
            "researchId": str(RUN),
            "validatedAt": NOW.isoformat(),
        }
    )
    assert result["decision"] == "REJECTED"
    assert result["validatedAt"] == NOW.isoformat()
    assert len(result["validatorRevision"]) == 64
    assert "PRIVATE" not in str(result)
