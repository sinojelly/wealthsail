"""Bounded disposal with exclusive Linux supervisor / Windows job evidence."""

import asyncio
import os
import signal
import time

from backend.integrations.research_lifecycle import (
    CLEANUP_SECONDS,
    ResearchLifecycle,
    _binding,
    call_identity,
)

# Only identities admitted by the immutable dispatch barrier reach this registry.
# Unknown entries never expire or lose their handles to make room for new work.
_LOCAL_JOBS = {}
_LOCAL_JOB_LIMIT = 256


def _local_key(identity):
    return _binding(call_identity(identity))


def local_research_exit_status(identity):
    """Read retained private kernel evidence; never signal or reopen a PID."""
    key = _local_key(identity)
    entry = _LOCAL_JOBS.get(key)
    state = (
        "LOCAL_PLATFORM_UNSUPPORTED"
        if os.name != "nt"
        else "LOCAL_EVIDENCE_NOT_RETAINED"
    )
    if entry is not None:
        process, job = entry["process"], entry["job"]
        try:
            if (
                not job.confirmed
                and job.handle
                and process.returncode is not None
                and job.drained()
            ):
                job.confirmed = True
                entry["lifecycle"].exited(
                    reaped=True,
                    descendants_confirmed=True,
                    returncode=process.returncode,
                )
                job.close()
            state = (
                "LOCAL_EXIT_CONFIRMED"
                if job.confirmed
                else "LOCAL_RUNNING"
                if process.returncode is None
                else "LOCAL_EXIT_UNCONFIRMED"
            )
        except (PermissionError, OSError):
            state = "LOCAL_EXIT_UNCONFIRMED"
    confirmed = state == "LOCAL_EXIT_CONFIRMED"
    return {
        "version": "research-local-exit-v1",
        "evidenceState": state,
        "exitConfirmed": confirmed,
        "canRelease": confirmed,
        "maintenanceRequired": state
        in {
            "LOCAL_EVIDENCE_NOT_RETAINED",
            "LOCAL_PLATFORM_UNSUPPORTED",
            "LOCAL_EXIT_UNCONFIRMED",
        },
        "reasonCodes": [] if confirmed else [state],
    }


def local_research_exit_proof(identity):
    if not local_research_exit_status(identity)["exitConfirmed"]:
        return None
    return _LOCAL_JOBS[_local_key(identity)]["lifecycle"].snapshot()


def forget_local_research_exit(identity):
    """Discard only affirmative evidence, after a successful durable guard CAS."""
    key = _local_key(identity)
    entry = _LOCAL_JOBS.get(key)
    if entry is not None and entry["job"].confirmed:
        del _LOCAL_JOBS[key]


async def _observe_windows_job(job):
    # Drain the private kernel port promptly to pin identities while alive.
    # Failure remains uncertain. The task never terminates any process.
    while job.handle:
        try:
            job.drained()
        except (PermissionError, OSError):
            pass
        await asyncio.sleep(0.05)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong),
            ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD),
            ("minimum", ctypes.c_size_t),
            ("maximum", ctypes.c_size_t),
            ("processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", _BasicLimits),
            ("io", ctypes.c_ulonglong * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t),
            ("peak_job", ctypes.c_size_t),
        ]

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("times", ctypes.c_longlong * 4),
            ("faults", wintypes.DWORD),
            ("total", wintypes.DWORD),
            ("active", wintypes.DWORD),
            ("terminated", wintypes.DWORD),
        ]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("thread_id", wintypes.DWORD),
            ("process_id", wintypes.DWORD),
            ("base_priority", wintypes.LONG),
            ("delta_priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
        ]

    class _CompletionPort(ctypes.Structure):
        _fields_ = [("key", ctypes.c_void_p), ("port", wintypes.HANDLE)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for _name, (_result, _arguments) in {
        "CreateJobObjectW": (wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
        "SetInformationJobObject": (
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
        ),
        "QueryInformationJobObject": (
            wintypes.BOOL,
            [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
            ],
        ),
        "AssignProcessToJobObject": (wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
        "TerminateJobObject": (wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
        "CreateIoCompletionPort": (
            wintypes.HANDLE,
            [wintypes.HANDLE, wintypes.HANDLE, ctypes.c_size_t, wintypes.DWORD],
        ),
        "GetQueuedCompletionStatus": (
            wintypes.BOOL,
            [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.POINTER(ctypes.c_void_p),
                wintypes.DWORD,
            ],
        ),
        "CreateToolhelp32Snapshot": (wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]),
        "Thread32First": (
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)],
        ),
        "Thread32Next": (
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)],
        ),
        "OpenThread": (
            wintypes.HANDLE,
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
        ),
        "GetProcessIdOfThread": (wintypes.DWORD, [wintypes.HANDLE]),
        "ResumeThread": (wintypes.DWORD, [wintypes.HANDLE]),
        "OpenProcess": (
            wintypes.HANDLE,
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
        ),
        "IsProcessInJob": (
            wintypes.BOOL,
            [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)],
        ),
        "WaitForSingleObject": (wintypes.DWORD, [wintypes.HANDLE, wintypes.DWORD]),
        "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
    }.items():
        _function = getattr(_kernel32, _name)
        _function.restype, _function.argtypes = _result, _arguments


class _WindowsJob:
    """A non-inheritable job assigned before the CLI's first instruction.

    No breakaway flags are enabled. Kernel job membership survives an exited
    parent or intermediate child, and never targets a recycled numeric PID.
    """

    def __init__(self):
        self.handle = _kernel32.CreateJobObjectW(None, None)
        self.port = None
        self.confirmed = False
        self.members = []
        self.observation_error = False
        self.zero_notified = False
        self.observer = None
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        self.port = _kernel32.CreateIoCompletionPort(
            ctypes.c_void_p(-1).value, None, 0, 1
        )
        port = _CompletionPort(self.handle, self.port)
        if not self.port or not _kernel32.SetInformationJobObject(
            self.handle, 7, ctypes.byref(port), ctypes.sizeof(port)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        # asyncio's Popen owns the original process handle, even after exit.
        # Opening by PID here would discard that identity guarantee.
        popen = process._transport.get_extra_info("subprocess")
        if not _kernel32.AssignProcessToJobObject(self.handle, int(popen._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process):
        snapshot = _kernel32.CreateToolhelp32Snapshot(4, 0)  # SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        thread = None
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            threads = []
            found = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.process_id == process.pid:
                    threads.append(entry.thread_id)
                found = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18 or len(threads) != 1:  # NO_MORE_FILES
                raise OSError("cannot identify suspended process thread")
            thread = _kernel32.OpenThread(0x2 | 0x0800, False, threads[0])
            if not thread or _kernel32.GetProcessIdOfThread(thread) != process.pid:
                raise OSError("cannot bind suspended process thread")
            if _kernel32.ResumeThread(thread) != 1:
                raise OSError("cannot resume suspended process thread")
        finally:
            if thread:
                _kernel32.CloseHandle(thread)
            _kernel32.CloseHandle(snapshot)

    def active(self):
        value = _Accounting()
        if not _kernel32.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.active

    def drained(self):
        # Accounting can reach zero before asynchronous termination completes.
        # Require the private port's kernel exit notifications as well. Missing
        # notifications stay uncertain; their delivery is not guaranteed.
        for _ in range(512):
            code, key, value = wintypes.DWORD(), ctypes.c_size_t(), ctypes.c_void_p()
            if not _kernel32.GetQueuedCompletionStatus(
                self.port, ctypes.byref(code), ctypes.byref(key), ctypes.byref(value), 0
            ):
                if ctypes.get_last_error() == 258:  # WAIT_TIMEOUT
                    break
                raise ctypes.WinError(ctypes.get_last_error())
            if key.value != self.handle:
                raise OSError("foreign job notification")
            if code.value == 6:  # NEW_PROCESS
                handle = _kernel32.OpenProcess(0x100000 | 0x1000, False, value.value)
                if handle:
                    belongs = wintypes.BOOL()
                    checked = _kernel32.IsProcessInJob(
                        handle, self.handle, ctypes.byref(belongs)
                    )
                    if not checked or not belongs.value:
                        self.observation_error |= not checked
                        _kernel32.CloseHandle(handle)
                        handle = None
                elif (
                    ctypes.get_last_error() != 87
                ):  # Already exited / INVALID_PARAMETER
                    self.observation_error = True
                self.members.append(
                    {"pid": value.value, "handle": handle, "exited": False}
                )
                self.zero_notified = False
            elif code.value in {7, 8}:  # EXIT_PROCESS / ABNORMAL_EXIT_PROCESS
                for member in self.members:
                    if member["pid"] == value.value and not member["exited"]:
                        member["exited"] = True
                        break
            elif code.value == 4:  # ACTIVE_PROCESS_ZERO
                self.zero_notified = True
        value = _Accounting()
        if not _kernel32.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return (
            value.active == 0
            and self.zero_notified
            and len(self.members) == value.total
            and value.total > 0
            and not self.observation_error
            and all(
                _kernel32.WaitForSingleObject(member["handle"], 0) == 0
                if member["handle"]
                else member["exited"]
                for member in self.members
            )
        )

    def terminate(self):
        if not _kernel32.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.observer is not None:
            self.observer.cancel()
            self.observer = None
        if self.handle:
            _kernel32.CloseHandle(self.handle)
            self.handle = None
        if self.port:
            _kernel32.CloseHandle(self.port)
            self.port = None
        for member in self.members:
            if member["handle"]:
                _kernel32.CloseHandle(member["handle"])
                member["handle"] = None


async def spawn_research_process(*args, research_identity=None, **options):
    """Contain Windows descendants before allowing the subprocess to run."""
    if os.name != "nt":
        return await asyncio.create_subprocess_exec(*args, **options)
    key = _local_key(research_identity) if research_identity is not None else None
    if key is not None:
        # Losing an old proof is fail-closed, but throwing away an unconfirmed
        # job would also throw away its only trusted process identities.
        if len(_LOCAL_JOBS) >= _LOCAL_JOB_LIMIT and key not in _LOCAL_JOBS:
            for old_key, entry in list(_LOCAL_JOBS.items()):
                if entry is not None and entry["job"].confirmed:
                    del _LOCAL_JOBS[old_key]
                    break
        if key in _LOCAL_JOBS or len(_LOCAL_JOBS) >= _LOCAL_JOB_LIMIT:
            raise ValueError("local research evidence registry occupied or full")
    job, process = _WindowsJob(), None
    if key is not None:
        _LOCAL_JOBS[key] = None  # Reserve before the asynchronous spawn.
    try:
        options["creationflags"] = (
            options.get("creationflags", 0) | 0x4
        )  # CREATE_SUSPENDED
        process = await asyncio.create_subprocess_exec(*args, **options)
        job.assign(process)
        job.resume(process)
        process._research_windows_job = job
        if key is not None:
            lifecycle = ResearchLifecycle(research_identity)
            lifecycle.started()
            _LOCAL_JOBS[key] = {"process": process, "job": job, "lifecycle": lifecycle}
            process._research_local_key = key
            job.observer = asyncio.create_task(_observe_windows_job(job))
        return process
    except BaseException:
        if key is not None:
            _LOCAL_JOBS.pop(key, None)
        # A failed assignment/resume never runs the invocation uncontained or
        # leaves an indefinitely suspended child. kill() uses its owned handle.
        try:
            if process is not None:
                if process.returncode is None:
                    process.kill()
                await asyncio.wait_for(process.wait(), CLEANUP_SECONDS)
        finally:
            job.close()
        raise


async def _cleanup_windows(process, job, deadline):
    try:
        if job.handle is None:
            return process.returncode is not None, job.confirmed
        job.drained()  # Pin live member handles before asynchronous termination.
        if job.active():
            job.terminate()
        await asyncio.wait_for(process.wait(), max(0.001, deadline - time.monotonic()))
        while job.active() or not job.drained():
            if time.monotonic() >= deadline:
                return process.returncode is not None, False
            await asyncio.sleep(0.02)
        job.confirmed = process.returncode is not None
        entry = _LOCAL_JOBS.get(getattr(process, "_research_local_key", None))
        if entry is not None and job.confirmed:
            entry["lifecycle"].exited(
                reaped=True, descendants_confirmed=True, returncode=process.returncode
            )
        return job.confirmed, job.confirmed
    except (TimeoutError, PermissionError, OSError):
        return process.returncode is not None, False
    finally:
        # Retain uncertain private handles so a late kernel receipt can recover
        # this exact invocation. No timeout or parent returncode closes the gap.
        # Helpers without a dispatched research identity cannot export proof or
        # use recovery. Dispose their exclusively owned job without certifying
        # descendants, rather than leaking handles after the helper is dropped.
        if job.confirmed or not hasattr(process, "_research_local_key"):
            job.close()


async def cleanup_process(process, *, isolated_group=False, supervisor=False):
    deadline = time.monotonic() + CLEANUP_SECONDS
    job = getattr(process, "_research_windows_job", None)
    if os.name == "nt" and isinstance(job, _WindowsJob):
        return await _cleanup_windows(process, job, deadline)

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
