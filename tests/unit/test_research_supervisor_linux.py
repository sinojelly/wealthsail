"""Run on Linux before rollout: Python-only process fault injection, no AI/network."""

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
from uuid import uuid4

import pytest

from backend.integrations.codex_research import _run
from backend.integrations.research_lifecycle import ResearchLifecycle

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        sys.platform != "linux", reason="Linux subreaper/pidfd fault injection"
    ),
]

CLI_FIXTURE = """
import json, os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
ready = sys.argv[1] + '.ready'
child = subprocess.Popen([
    sys.executable, '-c',
    'import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
    'open(sys.argv[1], "w").write("ready"); time.sleep(60)', ready,
], start_new_session=True)
while not os.path.exists(ready):
    time.sleep(0.01)
with open(sys.argv[1], 'w') as handle:
    json.dump({'cli': os.getpid(), 'detached': child.pid}, handle)
while True:
    time.sleep(1)
"""

RUNNER_FIXTURE = """
import asyncio, os, sys
from backend.integrations.codex_research import _run
from backend.integrations.research_lifecycle import ResearchLifecycle
identity = dict(zip(('runId', 'ownerToken', 'callId'), sys.argv[3:6]))
asyncio.run(_run([sys.executable, '-c', sys.argv[1], sys.argv[2]],
                cwd=os.path.dirname(sys.argv[2]), env=dict(os.environ),
                timeout=40, lifecycle=ResearchLifecycle(identity)))
"""


async def _wait_marker(marker):
    async with asyncio.timeout(8):
        while True:
            try:
                return json.loads(marker.read_text())
            except (FileNotFoundError, ValueError):
                await asyncio.sleep(0.02)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def _wait_gone(pids):
    async with asyncio.timeout(16):
        while any(_alive(pid) for pid in pids):
            await asyncio.sleep(0.02)


def _handles(pids):
    return [os.pidfd_open(pid) for pid in pids]


def _require_pidfd():
    handle = None
    try:
        handle = os.pidfd_open(os.getpid())
        signal.pidfd_send_signal(handle, 0)
    except (AttributeError, OSError):
        pytest.skip(
            "Linux kernel/Python pidfd support is required for detached cleanup"
        )
    finally:
        if handle is not None:
            os.close(handle)


def _force_cleanup(handles):
    for handle in handles:
        try:
            signal.pidfd_send_signal(handle, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            os.close(handle)


async def test_cancel_reaps_detached_setsid_descendant_that_ignores_term(tmp_path):
    _require_pidfd()
    marker = tmp_path / "pids.json"
    state = ResearchLifecycle(
        {key: str(uuid4()) for key in ("runId", "ownerToken", "callId")}
    )
    active = asyncio.create_task(
        _run(
            [sys.executable, "-c", CLI_FIXTURE, str(marker)],
            cwd=str(tmp_path),
            env=dict(os.environ),
            timeout=40,
            lifecycle=state,
        )
    )
    handles = []
    try:
        pids = list((await _wait_marker(marker)).values())
        handles = _handles(pids)
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert state.snapshot()["exitConfirmed"] is True
        await _wait_gone(pids)
    finally:
        _force_cleanup(handles)
        if not active.done():
            active.cancel()
        await asyncio.gather(active, return_exceptions=True)


async def test_runner_parent_sigkill_still_disposes_stubborn_detached_descendants(
    tmp_path,
):
    _require_pidfd()
    marker = tmp_path / "pids.json"
    identity = [str(uuid4()) for _ in range(3)]
    parent = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        RUNNER_FIXTURE,
        CLI_FIXTURE,
        str(marker),
        *identity,
        cwd=str(Path(__file__).resolve().parents[2]),
        env=dict(os.environ),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    handles = []
    try:
        pids = list((await _wait_marker(marker)).values())
        handles = _handles(pids)
        parent.kill()
        await parent.wait()
        await _wait_gone(pids)
        # A restarted Runner's in-memory registry has no trusted certificate.
        from backend.integrations.research_lifecycle import ResearchRegistry

        assert (
            ResearchRegistry().lookup(
                dict(zip(("runId", "ownerToken", "callId"), identity))
            )
            is None
        )
    finally:
        _force_cleanup(handles)
        if parent.returncode is None:
            parent.kill()
        await parent.wait()
