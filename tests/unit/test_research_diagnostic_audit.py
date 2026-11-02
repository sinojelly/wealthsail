"""Small optional summaries remain safe, atomic and independent of raw reports."""

import json
from types import SimpleNamespace
from uuid import uuid4

from backend.macro import research_audit as audit
from tests.unit.test_research_audit import NOW


def test_startup_reports_unwritable_audit_without_exposing_path(tmp_path, caplog):
    target = tmp_path / "PRIVATE-PATH"
    target.write_text("file", encoding="utf-8")
    audit.check_research_audit(
        SimpleNamespace(
            daily_research_enabled=True, daily_research_audit_dir=str(target)
        )
    )
    assert "daily_research_audit_unavailable reason=NOT_WRITABLE" in caplog.text
    assert "PRIVATE-PATH" not in caplog.text


def test_disabled_service_has_no_audit_warning(caplog):
    audit.check_research_audit(SimpleNamespace(daily_research_enabled=False))
    assert not caplog.records


def test_diagnostic_summary_has_only_safe_fields_and_private_file(
    tmp_path, monkeypatch
):
    import os
    from unittest.mock import Mock

    chmod = Mock(wraps=audit.os.chmod)
    monkeypatch.setattr(audit.os, "chmod", chmod)
    identity = uuid4()
    assert audit.write_research_diagnostic(
        SimpleNamespace(daily_research_audit_dir=str(tmp_path)),
        identity,
        stage="REPORT_VALIDATION",
        error_class="ResearchValidationError",
        reason_code="DIRECTION_VALIDATION_FAILED",
        rule_id="INDUSTRY_RISK_CHECK_MISSING",
        item_index=2,
        report_status="FAILED",
        completed_at=NOW,
    )
    path = tmp_path / f"{identity}.diagnostic.json"
    assert json.loads(path.read_text("utf-8")) == {
        "stage": "REPORT_VALIDATION",
        "errorClass": "ResearchValidationError",
        "reasonCode": "DIRECTION_VALIDATION_FAILED",
        "ruleId": "INDUSTRY_RISK_CHECK_MISSING",
        "itemIndex": 2,
        "reportStatus": "FAILED",
    }
    assert chmod.call_args.args[1] == 0o600
    if os.name != "nt":  # Windows mode bits do not represent its ACL permissions.
        assert path.stat().st_mode & 0o077 == 0


def test_untrusted_summary_metadata_is_discarded_and_atomic_failure_keeps_previous(
    tmp_path, monkeypatch, caplog
):
    configured = SimpleNamespace(daily_research_audit_dir=str(tmp_path))
    identity = uuid4()
    fields = dict(
        stage="SOURCE_VERIFICATION",
        error_class="ValueError",
        report_status="FAILED",
        completed_at=NOW,
    )
    assert audit.write_research_diagnostic(configured, identity, **fields)
    target = tmp_path / f"{identity}.diagnostic.json"
    original = target.read_bytes()

    def fail_replace(*args):
        raise PermissionError("PRIVATE-DSN")

    monkeypatch.setattr(audit.os, "replace", fail_replace)
    assert not audit.write_research_diagnostic(configured, identity, **fields)
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))
    assert "PRIVATE" not in caplog.text


def test_raw_and_summary_retention_do_not_evict_each_other(tmp_path):
    import os

    configured = SimpleNamespace(daily_research_audit_dir=str(tmp_path))
    for i in range(65):
        for suffix in (".json", ".diagnostic.json"):
            path = tmp_path / f"00000000-0000-0000-0000-{i:012x}{suffix}"
            path.write_text("{}", encoding="utf-8")
            os.utime(path, (1000 + i, 1000 + i))
    identity = uuid4()
    assert audit.write_research_diagnostic(
        configured,
        identity,
        stage="REPORT_VALIDATION",
        error_class=None,
        report_status="READY",
        completed_at=NOW,
    )
    assert audit.write_research_audit(
        configured, identity, raw={}, stage="READY", completed_at=NOW
    )
    assert len(list(tmp_path.glob("*.diagnostic.json"))) == 60
    assert len(list(tmp_path.glob("*.json"))) == 120
