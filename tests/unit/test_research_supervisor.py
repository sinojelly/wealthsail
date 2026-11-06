"""Fault injection at the isolated supervisor's trusted OS boundary."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from backend.integrations import research_supervisor as supervisor


def setup(monkeypatch, waits):
    monkeypatch.setattr(supervisor.os, "WNOHANG", 1, raising=False)
    monkeypatch.setattr(supervisor.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        supervisor.ctypes,
        "CDLL",
        lambda *args, **kwargs: SimpleNamespace(prctl=lambda *args: 0),
    )
    child = SimpleNamespace(pid=123, returncode=None)
    spawn = Mock(return_value=child)
    monkeypatch.setattr(supervisor.subprocess, "Popen", spawn)
    monkeypatch.setattr(supervisor.signal, "signal", lambda *args: None)
    monkeypatch.setattr(supervisor.time, "sleep", lambda *args: None)
    monkeypatch.setattr(supervisor.os, "close", lambda *args: None)
    monkeypatch.setattr(supervisor.os, "waitstatus_to_exitcode", lambda value: value)
    monkeypatch.setattr(supervisor.os, "waitpid", Mock(side_effect=waits))
    writes = []
    monkeypatch.setattr(
        supervisor.os, "write", lambda fd, value: writes.append(json.loads(value))
    )
    return spawn, writes


def test_no_exit_certificate_until_all_adopted_children_are_reaped(monkeypatch):
    spawn, writes = setup(monkeypatch, [(123, 0), (456, 0), ChildProcessError()])
    assert supervisor.supervise(["offline-fixture"], 88) == 0
    assert writes == [{"reaped": True, "descendantsConfirmed": True, "returncode": 0}]
    assert spawn.call_count == 1
    assert spawn.call_args.kwargs == {"start_new_session": True, "close_fds": True}


def test_unreapable_descendant_keeps_exit_unconfirmed_after_kill_budget(monkeypatch):
    _, writes = setup(monkeypatch, [(123, 0), (0, 0), (0, 0)])
    monkeypatch.setattr(
        supervisor.time, "monotonic", Mock(side_effect=[0, 0, 0, 0, 13, 13])
    )
    signals = []
    monkeypatch.setattr(
        supervisor,
        "_signal_descendants",
        lambda sig, pid, **kwargs: signals.append(sig),
    )
    supervisor.supervise(["offline-fixture"], 88)
    assert writes == [{"reaped": True, "descendantsConfirmed": False, "returncode": 0}]
    assert supervisor.signal.SIGTERM in signals and supervisor.signal.SIGKILL in signals


def test_missing_subreaper_capability_fails_before_single_cli_dispatch(monkeypatch):
    spawn, writes = setup(monkeypatch, [])
    monkeypatch.setattr(
        supervisor.ctypes,
        "CDLL",
        lambda *args, **kwargs: SimpleNamespace(prctl=lambda *args: -1),
    )
    assert supervisor.supervise(["offline-fixture"], 88) == 125
    spawn.assert_not_called()
    assert writes == []


def test_reaped_cli_group_id_is_never_signalled_after_pid_reuse(monkeypatch):
    group = Mock()
    monkeypatch.setattr(supervisor.os, "killpg", group, raising=False)
    monkeypatch.setattr(supervisor, "_descendants", lambda: {})
    supervisor._signal_descendants(supervisor.signal.SIGTERM, 123, cli_reaped=True)
    group.assert_not_called()


def test_detached_descendant_signal_is_bound_to_pidfd_and_start_time(monkeypatch):
    monkeypatch.setattr(supervisor, "_descendants", lambda: {456: "original"})
    monkeypatch.setattr(supervisor.os, "pidfd_open", lambda pid: 88, raising=False)
    monkeypatch.setattr(supervisor.os, "close", lambda fd: None)
    fields = ["0"] * 20
    fields[19] = "original"
    monkeypatch.setattr(
        supervisor,
        "Path",
        lambda path: SimpleNamespace(
            read_text=lambda: "456 (test) " + " ".join(fields)
        ),
    )
    signal_bound = Mock()
    monkeypatch.setattr(
        supervisor.signal, "pidfd_send_signal", signal_bound, raising=False
    )
    monkeypatch.setattr(
        supervisor.os,
        "kill",
        Mock(side_effect=AssertionError("numeric pid may be reused")),
    )
    supervisor._signal_descendants(supervisor.signal.SIGTERM, 123, cli_reaped=True)
    signal_bound.assert_called_once_with(88, supervisor.signal.SIGTERM)
