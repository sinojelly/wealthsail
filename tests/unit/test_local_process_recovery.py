"""Recover only recorded project identities; never infer ownership from ports."""

import json
import os
import sys
from contextlib import nullcontext
from importlib import import_module
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest

from scripts import local_dev, local_postgres


def process(pid, parent=1, **changes):
    row = {
        "ProcessId": pid,
        "ParentProcessId": parent,
        "created": f"created-{pid}",
        "Name": "python.exe",
        "ExecutablePath": "C:/Python312/python.exe",
        "CommandLine": f'"D:/workspace/.venv/Scripts/python.exe" -m uvicorn backend.main:app --port {pid}',
    }
    row.update(changes)
    return row


def module():
    return import_module("scripts.local_processes")


def seed(tmp_path, rows):
    helper = module()
    path = tmp_path / "services.json"
    helper.write_manifest(
        path,
        {
            "workspace": str(tmp_path.resolve()),
            "run_id": "owned-run",
            "children": [{**row, "service": "api"} for row in rows],
        },
    )
    return path


def test_record_captures_venv_real_python_descendants_and_no_unrelated_process(
    tmp_path,
):
    helper = module()
    path = tmp_path / "services.json"
    helper.record_services(
        path,
        tmp_path,
        "owned-run",
        {101: "api"},
        [process(101), process(102, 101), process(103, 102), process(999)],
    )
    saved = helper.read_manifest(path, tmp_path)
    assert {p["ProcessId"] for p in saved["children"]} == {101, 102, 103}
    assert all(
        set(("created", "Name", "ExecutablePath", "CommandLine")) <= set(p)
        for p in saved["children"]
    )
    assert "environment" not in saved and "secrets" not in saved


@pytest.mark.parametrize(
    "changed",
    [
        {"created": "new-owner"},
        {"Name": "node.exe"},
        {"ExecutablePath": "C:/other/python.exe"},
        {"CommandLine": "unrelated program"},
    ],
)
def test_reused_or_changed_process_identity_is_never_killed(tmp_path, changed):
    helper = module()
    path = seed(tmp_path, [process(101)])
    kill = Mock()
    result = helper.cleanup_services(
        path, tmp_path, lambda pid: process(pid, **changed), kill
    )
    kill.assert_not_called()
    assert not result and not path.exists()


def test_failed_child_cleanup_does_not_skip_others_and_keeps_only_remaining_proof(
    tmp_path,
):
    helper = module()
    live = {pid: process(pid) for pid in (101, 102, 103)}
    path = seed(tmp_path, list(live.values()))
    attempted = []

    def kill(pid):
        attempted.append(pid)
        if pid == 103:
            raise TimeoutError("fixture cleanup failure")
        live.pop(pid, None)

    remaining = helper.cleanup_services(path, tmp_path, live.get, kill)
    assert set(attempted) == {101, 102, 103}
    assert [p["ProcessId"] for p in remaining] == [103]
    assert [
        p["ProcessId"] for p in helper.read_manifest(path, tmp_path)["children"]
    ] == [103]


def test_identity_read_failure_keeps_record_and_does_not_mean_process_missing(tmp_path):
    helper = module()
    path = seed(tmp_path, [process(101)])
    query = Mock(side_effect=PermissionError("fixture access denied"))
    kill = Mock()
    assert helper.cleanup_services(path, tmp_path, query, kill)
    assert path.exists()
    kill.assert_not_called()


def test_manifest_for_other_workspace_is_rejected(tmp_path):
    path = seed(tmp_path, [process(101)])
    with pytest.raises(RuntimeError, match="workspace"):
        module().read_manifest(path, tmp_path / "different")


def test_root_identity_is_fixed_after_disappearance_and_reused_pid_is_not_adopted(
    tmp_path,
):
    helper = module()
    path = tmp_path / "services.json"
    helper.record_services(path, tmp_path, "owned-run", {101: "api"}, [process(101)])
    reused = process(101, created="later-foreign-owner")
    for _ in range(2):
        helper.record_services(
            path, tmp_path, "owned-run", {101: "api"}, [reused, process(102, 101)]
        )
        assert helper.read_manifest(path, tmp_path)["children"] == []


def test_child_older_than_current_parent_identity_is_not_adopted(tmp_path):
    helper = module()
    path = tmp_path / "services.json"
    helper.record_services(
        path,
        tmp_path,
        "owned-run",
        {101: "api"},
        [
            process(101, created="2026-10-16T12:00:00Z"),
            process(102, 101, created="2026-10-16T11:00:00Z"),
        ],
    )
    assert [
        row["ProcessId"] for row in helper.read_manifest(path, tmp_path)["children"]
    ] == [101]


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher lock")
def test_stop_recovers_recorded_children_without_run_json(tmp_path, monkeypatch):
    live = {pid: process(pid) for pid in (101, 102)}
    path = seed(tmp_path, list(live.values()))
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(local_dev, "ROOT", tmp_path)
    monkeypatch.setattr(local_dev, "process_info", lambda pid, env: live.get(pid))
    kill = Mock(side_effect=lambda pid, env: live.pop(pid, None))
    monkeypatch.setattr(local_dev, "terminate_service_process", kill, raising=False)
    stop_db = Mock()
    monkeypatch.setattr(local_postgres, "stop_database", stop_db)
    local_dev.runtime_action("stop", {}, None)
    assert not live and not path.exists()
    assert kill.call_count == 2
    stop_db.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher lock")
def test_start_rejects_unclean_children_before_overwriting_old_run_state(
    tmp_path, monkeypatch
):
    seed(tmp_path, [process(101)])
    previous = {
        "pid": 900,
        "created": "dead-supervisor",
        "run_id": "owned-run",
        "phase": "ready",
    }
    (tmp_path / "run.json").write_text(json.dumps(previous), "utf-8")
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(local_dev, "ROOT", tmp_path)
    monkeypatch.setattr(
        local_dev, "process_info", lambda pid, env: process(101) if pid == 101 else None
    )
    prepare = Mock()
    monkeypatch.setattr(local_dev, "prepare_runtime", prepare)
    monkeypatch.setattr(sys, "argv", ["local_dev.py", "--skip-install"])
    with pytest.raises(RuntimeError, match="Stop"):
        local_dev.main()
    assert json.loads((tmp_path / "run.json").read_text("utf-8")) == previous
    prepare.assert_not_called()


def test_no_manifest_does_not_authorize_killing_unknown_listener(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(local_dev, "ROOT", tmp_path)
    monkeypatch.setattr(local_dev, "launcher_lock", lambda _path: nullcontext())
    kill = Mock()
    monkeypatch.setattr(local_dev, "terminate_service_process", kill, raising=False)
    monkeypatch.setattr(local_postgres, "stop_database", Mock())
    local_dev.runtime_action("stop", {}, Path("missing-postgres"))
    kill.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher lock")
def test_recovery_still_stops_managed_database_when_a_child_cannot_be_stopped(
    tmp_path, monkeypatch
):
    path = seed(tmp_path, [process(101)])
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(local_dev, "ROOT", tmp_path)
    monkeypatch.setattr(
        local_dev, "cleanup_service_records", Mock(return_value=[process(101)])
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "pg_ctl.exe").touch()
    stop = Mock()
    monkeypatch.setattr(local_postgres, "stop_database", stop)
    with pytest.raises(RuntimeError, match="retained"):
        local_dev.runtime_action("stop", {}, binary)
    stop.assert_called_once_with(binary, tmp_path, {})
    assert path.exists()


def test_serve_manifest_cleanup_exception_still_cleans_popen_owned_processes(
    tmp_path, monkeypatch
):
    (tmp_path / "logs").mkdir()
    (tmp_path / "run.json").write_text(json.dumps({"run_id": "owned-run"}), "utf-8")
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(local_dev, "ROOT", tmp_path)
    monkeypatch.setattr(
        local_dev, "service_commands", lambda args: [("api", ["fixture"], tmp_path)]
    )
    child = MagicMock(pid=101)
    child.poll.return_value = None
    monkeypatch.setattr(local_dev.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(
        local_dev, "service_process_inventory", Mock(return_value=[process(101)])
    )
    monkeypatch.setattr(
        local_dev,
        "wait_ready",
        Mock(side_effect=RuntimeError("fixture startup failure")),
    )
    monkeypatch.setattr(
        local_dev,
        "cleanup_service_records",
        Mock(side_effect=RuntimeError("fixture manifest read failure")),
    )
    kill = Mock()
    monkeypatch.setattr(local_dev, "terminate_service_process", kill)
    with pytest.raises(RuntimeError):
        local_dev.serve(type("Args", (), {"api_port": 8000, "web_port": 5173})(), {})
    kill.assert_called_once()
    assert kill.call_args.args[0] == 101
    child.wait.assert_called_once_with(timeout=10)
