"""Linux-only, stdlib subprocess boundary for exactly one CLI invocation.

This process owns no unrelated runner children. Its anonymous control FD is not
inherited by the CLI. A proof is written only after waitpid reports ECHILD.
"""

import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

GRACE_SECONDS = 5
STOP_SECONDS = 12


def _descendants():
    records = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            records[int(entry.name)] = (int(stat[1]), stat[19])
        except (OSError, ValueError, IndexError):
            continue
    selected = {os.getpid()}
    while True:
        children = {pid for pid, (parent, _) in records.items() if parent in selected}
        if children <= selected:
            break
        selected |= children
    return {pid: records[pid][1] for pid in selected if pid != os.getpid()}


def _signal_descendants(sig, cli_pid, *, cli_reaped=False):
    # An unreaped session leader pins the group ID. After waitpid, that numeric
    # ID may be reused by an unrelated call and must never be signalled.
    if not cli_reaped:
        try:
            os.killpg(cli_pid, sig)
        except ProcessLookupError:
            pass
    for pid, started in _descendants().items():
        handle = None
        try:
            # pidfd remains bound if a PID exits/recycles between the ancestry
            # snapshot and the signal. Missing kernel support stays uncertain.
            if not hasattr(os, "pidfd_open") or not hasattr(
                signal, "pidfd_send_signal"
            ):
                continue
            handle = os.pidfd_open(pid)
            current = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if current[19] == started:
                signal.pidfd_send_signal(handle, sig)
        except (OSError, ValueError, IndexError):
            continue
        finally:
            if handle is not None:
                os.close(handle)


def supervise(args, control_fd, *, timeout=900, parent_pid=None):
    # Fail before dispatch when the kernel cannot establish exclusive adoption.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        return 125
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # The model deadline survives an HTTP handler or Runner crash. Parent death
    # also requests disposal; it never produces a certificate for a new Runner.
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0)
    deadline = time.monotonic() + timeout
    if parent_pid is not None and os.getppid() != parent_pid:
        return 125
    child = subprocess.Popen(args, start_new_session=True, close_fds=True)
    code, since, all_reaped = None, None, False
    while True:
        if time.monotonic() >= deadline or (
            parent_pid is not None and os.getppid() != parent_pid
        ):
            stopping = True
        try:
            while True:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
                if pid == child.pid:
                    code = os.waitstatus_to_exitcode(status)
                    child.returncode = code
        except ChildProcessError:
            all_reaped = code is not None
            break
        # Even a normal CLI exit may leave descendants. They are stopped before
        # certifying the invocation, including detached sessions adopted here.
        if stopping or code is not None:
            if since is None:
                since = time.monotonic()
            elapsed = time.monotonic() - since
            _signal_descendants(
                signal.SIGKILL if elapsed >= GRACE_SECONDS else signal.SIGTERM,
                child.pid,
                cli_reaped=code is not None,
            )
            if elapsed >= STOP_SECONDS:
                break
        time.sleep(0.02)
    evidence = {
        "reaped": code is not None,
        "descendantsConfirmed": all_reaped,
        "returncode": code,
    }
    os.write(control_fd, json.dumps(evidence, separators=(",", ":")).encode())
    os.close(control_fd)
    return code if code is not None and 0 <= code <= 255 else 1


def main():
    control_fd = int(sys.argv[1])
    try:
        return supervise(
            sys.argv[4:],
            control_fd,
            timeout=float(sys.argv[2]),
            parent_pid=int(sys.argv[3]),
        )
    except BaseException:
        # No exception text or supplied command is ever emitted.
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
