"""Deployment provenance must cover candidate rules without a runtime Git command."""

from pathlib import Path

import pytest

from backend.macro import research_acceptance as acceptance
from backend.macro.research_acceptance_replay import child_environment
from backend.macro.research_acceptance_repository import safe_outcome_summary
from test_industrial_metals_research_regression import case
from test_daily_research import NOW


def test_rule_fingerprint_includes_candidate_budget_diagnostics_and_dependencies():
    paths = {
        path.relative_to(acceptance._ROOT).as_posix()
        for path in acceptance._REVISION_FILES
    }
    assert {
        "backend/integrations/daily_sources.py",
        "backend/macro/research_diagnostics.py",
        "requirements.txt",
    } <= paths
    assert {
        path.relative_to(acceptance._ROOT).as_posix()
        for path in (acceptance._ROOT / "backend").rglob("*.py")
    } <= paths


@pytest.mark.parametrize("commit", ["A" * 40, "b" * 64])
def test_build_commit_round_trips_receipt_projection_and_offline_child(
    monkeypatch, commit
):
    monkeypatch.setenv("WEALTHSAIL_BUILD_COMMIT", commit)
    packet, raw, checks = case()
    outcome = acceptance.validate_report_outcome(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        completed_at=NOW,
    )
    original = outcome.to_dict()
    assert original["buildCommit"] == commit.lower()
    assert safe_outcome_summary(original)["buildCommit"] == commit.lower()
    assert child_environment()["WEALTHSAIL_BUILD_COMMIT"] == commit.lower()
    monkeypatch.setenv("WEALTHSAIL_BUILD_COMMIT", "c" * 40)
    assert outcome.to_dict()["buildCommit"] == commit.lower()


@pytest.mark.parametrize(
    "commit", ["", "main", "1234567", "https://example.test/private", "x" * 40]
)
def test_unknown_build_commit_never_echoes_arbitrary_metadata(monkeypatch, commit):
    monkeypatch.setenv("WEALTHSAIL_BUILD_COMMIT", commit)
    packet, raw, checks = case()
    outcome = acceptance.validate_report_outcome(
        raw,
        packet,
        checks,
        research_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        completed_at=NOW,
    )
    assert outcome.formal_payload is not None
    assert outcome.to_dict()["buildCommit"] is None
    assert (
        safe_outcome_summary({**outcome.to_dict(), "buildCommit": commit})[
            "buildCommit"
        ]
        is None
    )
    assert "WEALTHSAIL_BUILD_COMMIT" not in child_environment()


def test_identical_rule_bytes_at_different_logical_paths_have_different_fingerprint(
    tmp_path, monkeypatch
):
    first, second = tmp_path / "backend/a/rule.py", tmp_path / "backend/b/rule.py"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("rule = 1\n", encoding="utf-8")
    monkeypatch.setattr(acceptance, "_ROOT", Path(tmp_path))
    monkeypatch.setattr(acceptance, "_REVISION_FILES", (first,))
    acceptance.validator_revision.cache_clear()
    old = acceptance.validator_revision()
    monkeypatch.setattr(acceptance, "_REVISION_FILES", (second,))
    acceptance.validator_revision.cache_clear()
    assert acceptance.validator_revision() != old
    acceptance.validator_revision.cache_clear()
