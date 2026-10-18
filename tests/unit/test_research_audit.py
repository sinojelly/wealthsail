"""Public model diagnostics are opt-in, bounded and isolated from report state."""

from datetime import datetime, timezone
import importlib
import importlib.util
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest


def writer():
    assert importlib.util.find_spec("backend.macro.research_audit") is not None
    return importlib.import_module("backend.macro.research_audit")


NOW = datetime(2026, 10, 19, 2, 40, tzinfo=timezone.utc)


def test_daily_audit_is_disabled_by_default_in_runtime_settings(monkeypatch):
    from backend.config import Settings

    monkeypatch.delenv("DAILY_RESEARCH_AUDIT_DIR", raising=False)
    assert getattr(Settings(_env_file=None), "daily_research_audit_dir", None) == ""


def test_local_macro_defaults_to_ignored_public_audit_directory(tmp_path, monkeypatch):
    from backend.integrations import codex_research
    from dotenv import dotenv_values
    from scripts import local_dev

    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(codex_research, "resolve_codex_executable", lambda *_: None)
    monkeypatch.delenv("DAILY_RESEARCH_AUDIT_DIR", raising=False)
    local_dev.configure_macro({"worker_password": "fixture"}, 55434)
    assert dotenv_values(tmp_path / "macro.env").get("DAILY_RESEARCH_AUDIT_DIR") == str(
        tmp_path / "research-audit"
    ).replace("\\", "/")


def test_audit_stores_only_public_raw_checks_and_bounded_validation_message(tmp_path):
    m = writer()
    identity = uuid4()
    settings = SimpleNamespace(
        daily_research_audit_dir=str(tmp_path),
        worker_database_url="must-not-be-serialized",
    )
    raw = {"summary": "公开研究", "valueDirections": []}
    checks = {
        "source1": {"verification": "UNVERIFIED", "reason": "SOURCE_NOT_ALLOWLISTED"}
    }
    assert (
        m.write_research_audit(
            settings,
            identity,
            raw=raw,
            source_checks=checks,
            stage="VALIDATION_FAILED",
            completed_at=NOW,
            error=ValueError("字段不匹配" * 200),
        )
        is True
    )
    payload = json.loads((tmp_path / f"{identity}.json").read_text("utf-8"))
    assert payload == {
        "runId": str(identity),
        "stage": "VALIDATION_FAILED",
        "completedAt": NOW.isoformat(),
        "raw": raw,
        "sourceChecks": checks,
        "validationError": {
            "type": "ValueError",
            "message": ("字段不匹配" * 200)[:500],
        },
    }
    assert "must-not-be-serialized" not in json.dumps(payload)


def test_missing_audit_setting_writes_nothing(tmp_path):
    m = writer()
    assert (
        m.write_research_audit(
            SimpleNamespace(), uuid4(), raw={}, stage="MODEL_RETURNED", completed_at=NOW
        )
        is False
    )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "identity",
    ["../../outside", "report.json", "", "00000000-0000-0000-0000-000000000000.json"],
)
def test_run_id_cannot_choose_an_output_path(tmp_path, identity):
    m = writer()
    assert (
        m.write_research_audit(
            SimpleNamespace(daily_research_audit_dir=str(tmp_path)),
            identity,
            raw={},
            stage="MODEL_RETURNED",
            completed_at=NOW,
        )
        is False
    )
    assert list(tmp_path.iterdir()) == []


def test_oversized_audit_keeps_previous_valid_file_and_has_no_partial_temporary_output(
    tmp_path,
):
    m = writer()
    settings = SimpleNamespace(daily_research_audit_dir=str(tmp_path))
    identity = uuid4()
    assert m.write_research_audit(
        settings,
        identity,
        raw={"summary": "first"},
        stage="MODEL_RETURNED",
        completed_at=NOW,
    )
    original = (tmp_path / f"{identity}.json").read_bytes()
    assert not m.write_research_audit(
        settings,
        identity,
        raw={"summary": "字" * 1_000_000},
        stage="VALIDATION_FAILED",
        completed_at=NOW,
    )
    assert (tmp_path / f"{identity}.json").read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 1


def test_atomic_write_failure_never_exposes_error_message_or_public_raw_in_logs(
    tmp_path, monkeypatch, caplog
):
    m = writer()
    settings = SimpleNamespace(daily_research_audit_dir=str(tmp_path))

    def blocked(*args):
        raise PermissionError("PRIVATE-SYSTEM-PATH")

    monkeypatch.setattr(m.os, "replace", blocked)
    with caplog.at_level("WARNING"):
        assert (
            m.write_research_audit(
                settings,
                uuid4(),
                raw={"summary": "RAW-NOT-LOGGED"},
                stage="MODEL_RETURNED",
                completed_at=NOW,
            )
            is False
        )
    assert "PermissionError" in caplog.text
    assert "PRIVATE-SYSTEM-PATH" not in caplog.text
    assert "RAW-NOT-LOGGED" not in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_audit_replaces_same_run_with_later_stage_without_mutating_public_data(
    tmp_path,
):
    m = writer()
    identity = uuid4()
    settings = SimpleNamespace(daily_research_audit_dir=str(tmp_path))
    raw = {"summary": "公开原始结果", "suggestedOutputPath": "../../model-owned.json"}
    assert m.write_research_audit(
        settings, identity, raw=raw, stage="MODEL_RETURNED", completed_at=NOW
    )
    assert m.write_research_audit(
        settings,
        identity,
        raw=raw,
        source_checks={},
        stage="VALIDATED",
        completed_at=NOW,
    )
    written = json.loads((tmp_path / f"{identity}.json").read_text("utf-8"))
    assert written["stage"] == "VALIDATED" and written["validationError"] is None
    assert written["raw"] == raw
    assert list(tmp_path.iterdir()) == [tmp_path / f"{identity}.json"]


@pytest.mark.parametrize("problem", ["nonfinite", "naive_clock"])
def test_non_json_or_ambiguous_clock_cannot_produce_a_diagnostic_file(
    tmp_path, problem
):
    m = writer()
    raw, clock = (
        ({"number": float("nan")}, NOW)
        if problem == "nonfinite"
        else ({}, NOW.replace(tzinfo=None))
    )
    assert not m.write_research_audit(
        SimpleNamespace(daily_research_audit_dir=str(tmp_path)),
        uuid4(),
        raw=raw,
        stage="MODEL_RETURNED",
        completed_at=clock,
    )
    assert list(tmp_path.iterdir()) == []


def test_local_audit_override_and_explicit_disable_survive_generated_restart(
    tmp_path, monkeypatch
):
    from backend.integrations import codex_research
    from dotenv import dotenv_values
    from scripts import local_dev

    monkeypatch.setattr(local_dev, "LOCAL", tmp_path)
    monkeypatch.setattr(codex_research, "resolve_codex_executable", lambda *_: None)
    chosen = (tmp_path / "operator-audit").as_posix()
    monkeypatch.setenv("DAILY_RESEARCH_AUDIT_DIR", chosen)
    local_dev.configure_macro({"worker_password": "fixture"}, 55434)
    monkeypatch.delenv("DAILY_RESEARCH_AUDIT_DIR")
    local_dev.configure_macro({"worker_password": "fixture"}, 55434)
    assert dotenv_values(tmp_path / "macro.env")["DAILY_RESEARCH_AUDIT_DIR"] == chosen
    monkeypatch.setenv("DAILY_RESEARCH_AUDIT_DIR", "")
    local_dev.configure_macro({"worker_password": "fixture"}, 55434)
    monkeypatch.delenv("DAILY_RESEARCH_AUDIT_DIR")
    local_dev.configure_macro({"worker_password": "fixture"}, 55434)
    assert dotenv_values(tmp_path / "macro.env")["DAILY_RESEARCH_AUDIT_DIR"] == ""
