"""Bounded parent disposal; only a dedicated supervisor can certify descendants."""

import asyncio
import os
import signal
import time

from backend.integrations.research_lifecycle import CLEANUP_SECONDS


async def cleanup_process(process, *, isolated_group=False, supervisor=False):
    deadline = time.monotonic() + CLEANUP_SECONDS

    def send(sig):
        try:
            pid = getattr(process, "pid", None)
            if isolated_group and type(pid) is int:
                os.killpg(pid, sig)
            elif process.returncode is None:
                process.terminate() if sig == signal.SIGTERM else process.kill()
        except ProcessLookupError:
            pass

    try:
        if process.returncode is None:
            send(signal.SIGTERM)
        # The supervisor owns its TERM -> KILL -> wait budget. Allow it to
        # return proof before killing it. Legacy/private disposal retains 5s.
        grace = max(0.001, deadline - time.monotonic()) if supervisor else 5
        try:
            await asyncio.wait_for(process.wait(), grace)
        except TimeoutError:
            send(getattr(signal, "SIGKILL", 9))
            await asyncio.wait_for(
                process.wait(), max(0.001, deadline - time.monotonic())
            )
        return process.returncode is not None, False
    except (TimeoutError, PermissionError, OSError):
        return process.returncode is not None, False
