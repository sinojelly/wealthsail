"""Manual-only workers retain optional-audit startup visibility."""

import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_manual_only_service_warns_when_optional_audit_is_disabled(caplog):
    from backend.jobs.research_debug import run_debug_research_service

    stopped = asyncio.Event()
    stopped.set()
    settings = SimpleNamespace(
        daily_research_enabled=False, daily_research_manual_enabled=True
    )
    with caplog.at_level("WARNING"):
        await run_debug_research_service(object(), settings, stop_event=stopped)
    assert caplog.text.count("daily_research_audit_disabled") == 1


def test_manual_only_audit_reports_unwritable_storage_without_exposing_path(
    tmp_path, caplog
):
    from backend.macro.research_audit import check_research_audit

    path = tmp_path / "PRIVATE-OPERATOR-PATH"
    path.write_text("file", encoding="utf-8")
    check_research_audit(
        SimpleNamespace(
            daily_research_enabled=False,
            daily_research_manual_enabled=True,
            daily_research_audit_dir=str(path),
        )
    )
    assert "daily_research_audit_unavailable reason=NOT_WRITABLE" in caplog.text
    assert "PRIVATE-OPERATOR-PATH" not in caplog.text
