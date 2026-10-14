"""Windows launcher contention must not read a locked byte or disturb its owner."""

import json
import os
import sys
from unittest.mock import Mock

import pytest

from scripts import local_dev, local_postgres

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows byte-range locking")


def setup_main(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(sys, "argv", ["local_dev.py", "--skip-install", "--no-browser"])
    for name in ("prepare_runtime", "serve"):
        monkeypatch.setattr(
            local_dev, name, Mock(side_effect=AssertionError(f"unexpected {name}"))
        )
    monkeypatch.setattr(local_postgres, "start_database", Mock())
    monkeypatch.setattr(local_postgres, "stop_database", Mock())


def test_competing_start_reports_already_running_without_touching_owner_state(
    tmp_path, monkeypatch
):
    import msvcrt

    setup_main(tmp_path, monkeypatch)
    state = json.dumps({"run_id": "existing-owner", "pid": 12345, "phase": "ready"})
    (tmp_path / "run.json").write_text(state, encoding="utf-8")
    process_info = Mock(side_effect=AssertionError("competitor must not initialize"))
    monkeypatch.setattr(local_dev, "process_info", process_info)
    path = tmp_path / "launcher.lock"
    path.write_bytes(b"0")
    with path.open("r+b") as owner:
        msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
        with pytest.raises(RuntimeError, match="Already running"):
            local_dev.main()
    assert (tmp_path / "run.json").read_text(encoding="utf-8") == state
    process_info.assert_not_called()
    local_dev.prepare_runtime.assert_not_called()
    local_postgres.start_database.assert_not_called()
    local_postgres.stop_database.assert_not_called()


def test_failed_preparation_cleans_its_own_state_and_stop_request(
    tmp_path, monkeypatch
):
    setup_main(tmp_path, monkeypatch)
    monkeypatch.setattr(
        local_dev, "process_info", Mock(return_value={"created": "fixture"})
    )

    def fail(*_args):
        state = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
        (tmp_path / "stop-request").write_text(state["run_id"], encoding="utf-8")
        raise RuntimeError("preparation failed")

    monkeypatch.setattr(local_dev, "prepare_runtime", fail)
    with pytest.raises(RuntimeError, match="preparation failed"):
        local_dev.main()
    assert not (tmp_path / "run.json").exists()
    assert not (tmp_path / "stop-request").exists()
    with local_dev.launcher_lock(tmp_path / "launcher.lock"):
        pass
    local_postgres.stop_database.assert_not_called()


def test_empty_lock_file_can_be_locked_contended_and_reacquired_without_read_or_write(
    tmp_path,
):
    path = tmp_path / "launcher.lock"
    with local_dev.launcher_lock(path):
        assert path.stat().st_size == 0
        with pytest.raises(RuntimeError, match="Already running"):
            with local_dev.launcher_lock(path):
                pytest.fail("second launcher must not acquire the same lock")
    with local_dev.launcher_lock(path):
        assert path.stat().st_size == 0


@pytest.mark.parametrize("failure", ["process_info", "write_state"])
def test_failed_initialization_releases_lock_and_keeps_unowned_state(
    tmp_path, monkeypatch, failure
):
    setup_main(tmp_path, monkeypatch)
    state = json.dumps({"run_id": "previous-owner", "pid": 111, "phase": "ready"})
    (tmp_path / "run.json").write_text(state, encoding="utf-8")
    monkeypatch.setattr(
        local_dev, "process_info", Mock(return_value={"created": "fixture"})
    )
    monkeypatch.setattr(
        local_dev, failure, Mock(side_effect=RuntimeError("initialization failed"))
    )
    with pytest.raises(RuntimeError, match="initialization failed") as retained_error:
        local_dev.main()
    assert retained_error.value.__traceback__ is not None
    assert (tmp_path / "run.json").read_text(encoding="utf-8") == state
    with local_dev.launcher_lock(tmp_path / "launcher.lock"):
        pass
    local_postgres.stop_database.assert_not_called()
