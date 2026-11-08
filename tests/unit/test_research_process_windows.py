"""Windows process receipts use only harmless children created by these tests."""

import asyncio
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from backend.integrations import codex_research, research_process
from backend.integrations.research_lifecycle import ResearchLifecycle


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "nt", reason="Windows jobs")]


def lifecycle():
    return ResearchLifecycle(
        {"runId": str(uuid4()), "ownerToken": str(uuid4()), "callId": str(uuid4())}
    )


async def test_normal_windows_completion_confirms_parent_and_process_tree(tmp_path):
    state = lifecycle()
    code, stdout, _ = await codex_research._run(
        [sys.executable, "-c", "print('completed')"],
        cwd=str(tmp_path), env=dict(os.environ), lifecycle=state,
    )
    assert code == 0 and stdout.strip() == b"completed"
    assert state.snapshot()["reaped"] is True
    assert state.snapshot()["descendantsConfirmed"] is True
    assert state.snapshot()["exitConfirmed"] is True


async def test_unguarded_process_exit_does_not_certify_unknown_descendants():
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    await process.wait()
    assert await research_process.cleanup_process(process) == (True, False)


@contextmanager
def child_handle(pid):
    """Pin the test-created child's identity; never signal a numeric PID."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE, read-only
    assert handle, "test-created child must be alive before cleanup"
    try:
        assert kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        yield lambda: kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


async def spawn(program):
    return await research_process.spawn_research_process(
        sys.executable, "-c", program,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


async def dispose(process):
    """Failure cleanup touches only this fixture's private job/owned handle."""
    job = getattr(process, "_research_windows_job", None)
    if job is not None:
        job.close()
    if process.returncode is None:
        process.kill()
    await asyncio.wait_for(process.wait(), 5)


async def test_exited_parent_with_surviving_child_is_cleaned_and_confirmed():
    program = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "print(p.pid,flush=True)"
    )
    process = await spawn(program)
    try:
        pid = int(await asyncio.wait_for(process.stdout.readline(), 5))
        with child_handle(pid) as exited:
            assert await asyncio.wait_for(process.wait(), 5) == 0
            assert await research_process.cleanup_process(process) == (True, True)
            assert exited(), "parent exit alone must not leave its child alive"
    finally:
        await dispose(process)


async def test_exited_intermediate_parent_cannot_hide_surviving_grandchild():
    intermediate = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "print(p.pid,flush=True)"
    )
    program = (
        "import subprocess,sys; "
        f"p=subprocess.run([sys.executable,'-c',{intermediate!r}],"
        "stdout=subprocess.PIPE,check=True,text=True); print(p.stdout.strip(),flush=True)"
    )
    process = await spawn(program)
    try:
        pid = int(await asyncio.wait_for(process.stdout.readline(), 5))
        with child_handle(pid) as exited:
            assert await asyncio.wait_for(process.wait(), 5) == 0
            assert await research_process.cleanup_process(process) == (True, True)
            assert exited()
    finally:
        await dispose(process)


async def test_timeout_confirms_entire_tree_without_terminating_other_processes(tmp_path):
    marker = tmp_path / "child.pid"
    program = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"open({str(marker)!r},'w').write(str(p.pid)); time.sleep(60)"
    )
    unrelated = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    state = lifecycle()
    running = asyncio.create_task(codex_research._run(
        [sys.executable, "-c", program], cwd=str(tmp_path),
        env=dict(os.environ), timeout=3, lifecycle=state,
    ))
    try:
        async with asyncio.timeout(5):
            while not marker.exists() or not marker.read_text():
                await asyncio.sleep(0.01)
        with child_handle(int(marker.read_text())) as exited:
            with pytest.raises(TimeoutError) as error:
                await running
            assert error.value.timeout_origin == "CLI_MODEL_TIMEOUT"
            assert state.snapshot()["exitConfirmed"] is True
            assert state.snapshot()["reaped"] is True
            assert exited()
            assert unrelated.returncode is None
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await dispose(unrelated)


@pytest.mark.parametrize("operation", ["active", "terminate"])
async def test_windows_access_failure_never_confirms_tree(monkeypatch, operation):
    process = await spawn("import time; print('ready',flush=True); time.sleep(60)")
    try:
        await asyncio.wait_for(process.stdout.readline(), 5)
        job = process._research_windows_job

        def denied():
            raise PermissionError("test-owned job denied")

        monkeypatch.setattr(job, operation, denied)
        assert (await research_process.cleanup_process(process))[1] is False
        await asyncio.wait_for(process.wait(), 5)
        assert await research_process.cleanup_process(process) == (True, False)
    finally:
        await dispose(process)


async def test_residual_job_membership_past_budget_never_confirms(monkeypatch):
    process = await spawn("import time; print('ready',flush=True); time.sleep(60)")
    try:
        await asyncio.wait_for(process.stdout.readline(), 5)
        monkeypatch.setattr(research_process, "CLEANUP_SECONDS", 0.1)
        monkeypatch.setattr(process._research_windows_job, "active", lambda: 1)
        assert await research_process.cleanup_process(process) == (True, False)
    finally:
        await dispose(process)


@pytest.mark.parametrize("failure", ["missing_notifications", "unsignaled_member", "member_access_denied"])
async def test_incomplete_kernel_evidence_never_confirms_exit(monkeypatch, failure):
    process = await spawn("import time; print('ready',flush=True); time.sleep(60)")
    try:
        await asyncio.wait_for(process.stdout.readline(), 5)
        monkeypatch.setattr(research_process, "CLEANUP_SECONDS", 0.1)
        kernel = research_process._kernel32

        if failure == "missing_notifications":
            def missing(*args):
                ctypes.set_last_error(258)  # WAIT_TIMEOUT, no kernel notification
                return False
            monkeypatch.setattr(kernel, "GetQueuedCompletionStatus", missing)
        elif failure == "unsignaled_member":
            monkeypatch.setattr(kernel, "WaitForSingleObject", lambda *args: 258)
        else:
            def denied(*args):
                ctypes.set_last_error(5)  # ACCESS_DENIED
                return None
            monkeypatch.setattr(kernel, "OpenProcess", denied)

        assert await research_process.cleanup_process(process) == (True, False)
    finally:
        await dispose(process)


@pytest.mark.parametrize("operation", ["assign", "resume"])
async def test_failed_containment_reaps_suspended_process_without_running_it(
    monkeypatch, tmp_path, operation,
):
    created = []
    create = asyncio.create_subprocess_exec
    marker = tmp_path / "must-not-run.txt"

    async def capture(*args, **kwargs):
        process = await create(*args, **kwargs)
        created.append(process)
        return process

    def denied(*args):
        raise PermissionError("test containment denied")

    monkeypatch.setattr(research_process.asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(research_process._WindowsJob, operation, denied)
    try:
        with pytest.raises(PermissionError):
            await spawn(f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')")
        assert len(created) == 1
        assert created[0].returncode is not None
        assert not marker.exists()
    finally:
        for process in created:
            await dispose(process)
