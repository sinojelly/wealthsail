"""A cancellation acknowledgement is useful diagnosis, never confirmed exit."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.integrations import codex_client
from backend.integrations.research_lifecycle import ResearchLifecycle


def pending(identity):
    lifecycle = ResearchLifecycle(identity)
    lifecycle.started()
    lifecycle.terminating("CANCELLED")
    return lifecycle.snapshot()


def identity():
    return {key: str(uuid4()) for key in ("runId", "ownerToken", "callId")}


@pytest.mark.asyncio
async def test_cancel_acknowledgement_survives_recovery_timeout_without_claiming_exit(
    monkeypatch,
):
    owner, events, requests = identity(), [], []

    async def request(_settings, _method, endpoint, **kwargs):
        requests.append(endpoint)
        return {"lifecycle": pending(owner)}

    inference = AsyncMock(side_effect=AssertionError("status must never infer"))
    monkeypatch.setattr(codex_client, "_request_json", request)
    monkeypatch.setattr(codex_client, "research", inference)
    monkeypatch.setattr(codex_client, "RECOVERY_SECONDS", 0.01)
    result = await codex_client._recover_research(
        SimpleNamespace(), owner, on_exit=events.append
    )
    assert result is None
    assert events, "the acknowledged termination request must remain visible"
    assert events[-1]["terminationRequested"] is True
    assert events[-1]["exitConfirmed"] is False
    assert events[-1]["state"] == "TERMINATING"
    assert requests[0] == "/v1/research/cancel"
    assert set(requests) <= {"/v1/research/status", "/v1/research/cancel"}
    inference.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_monitor_delivers_bound_pending_lifecycle_before_rpc_returns(
    monkeypatch,
):
    owner, observed, events = identity(), asyncio.Event(), []
    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await real_sleep(0.001)

    async def request(_settings, _method, endpoint, **kwargs):
        if endpoint == "/v1/research":
            await observed.wait()
            lifecycle = ResearchLifecycle(owner)
            lifecycle.exited(reaped=True, descendants_confirmed=True, returncode=0)
            return {"report": {}, "lifecycle": lifecycle.snapshot()}
        return {"lifecycle": pending(owner)}

    def receive(value):
        events.append(value)
        if not value["exitConfirmed"]:
            observed.set()

    monkeypatch.setattr(codex_client, "_request_json", request)
    monkeypatch.setattr(codex_client.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(codex_client, "RECOVERY_SECONDS", 0.01)
    result = await asyncio.wait_for(
        codex_client._remote_research(
            SimpleNamespace(),
            {},
            timeout=900,
            identity=owner,
            on_exit=receive,
            on_progress=lambda value: None,
        ),
        1,
    )
    assert result["report"] == {}
    assert events[0]["state"] == "TERMINATING" and events[0]["exitConfirmed"] is False
    assert events[-1]["exitConfirmed"] is True
    assert all(row["runId"] == owner["runId"] for row in events)


@pytest.mark.asyncio
async def test_foreign_cancel_receipt_never_reaches_lifecycle_callback(monkeypatch):
    owner, events = identity(), []
    monkeypatch.setattr(
        codex_client,
        "_request_json",
        AsyncMock(return_value={"lifecycle": pending(identity())}),
    )
    assert (
        await codex_client._recover_research(
            SimpleNamespace(), owner, on_exit=events.append
        )
        is None
    )
    assert events == []
