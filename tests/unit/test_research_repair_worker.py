"""Future repair is deterministic and never rewrites the original acceptance."""

from copy import deepcopy

import pytest

from tests.unit.test_daily_research_job import NOW as JOB_NOW, settings
from tests.unit.test_research_acceptance_worker import boundary
from tests.unit.test_research_repair import NOW, fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("header_proven", [False, True])
async def test_worker_repairs_once_only_when_auto_gate_passes_and_archives_original(
    monkeypatch, header_proven
):
    from backend.macro.daily_research import validate_report
    from backend.macro.research_acceptance import validate_report_outcome

    mod, state = boundary(monkeypatch)
    packet, raw, checks = fixture(narrative=header_proven)
    original = deepcopy(raw)
    monkeypatch.setattr(mod, "validate_report", validate_report)
    monkeypatch.setattr(mod, "validate_report_outcome", validate_report_outcome)
    monkeypatch.setattr(mod, "now_shanghai", lambda: NOW)
    mod.run_codex_research.side_effect = None
    mod.run_codex_research.return_value = raw
    mod.verify_research_sources.side_effect = None
    mod.verify_research_sources.return_value = checks
    result = await mod._execute_daily_research(object(), settings(), NOW, packet=packet)
    assert result["status"] == ("READY" if header_proven else "FAILED")
    saved = state["outcome"][1]
    assert saved["decision"] == "PARTIAL" and saved["formalPublishable"] is False
    assert saved["deterministicRepair"]["strictPass"] is True
    assert saved["repairedFormalPublished"] is header_proven
    assert state["artifacts"][2] == original
    payload = state["terminal"][0]["payload"]
    if header_proven:
        assert payload["deterministicRepair"]["quarantinedCount"] == 1
        assert len(payload["valueDirections"]) == 1
    else:
        assert payload is None
    assert raw == original
    mod.run_codex_research.assert_awaited_once()
    mod.verify_research_sources.assert_awaited_once()
    mod.release_model_guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_does_not_attempt_repair_for_complete_formal_report(monkeypatch):
    from unittest.mock import Mock

    mod, state = boundary(monkeypatch)
    repair = Mock(side_effect=AssertionError("must not repair successful report"))
    monkeypatch.setattr(mod, "repair_report", repair, raising=False)
    result = await mod.sync_daily_research(object(), settings(), JOB_NOW)
    assert result["status"] == "READY"
    repair.assert_not_called()
    assert "deterministicRepair" not in state["outcome"][1]
