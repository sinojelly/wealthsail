"""Personal study records never accept caller-certified facts or orders."""

from importlib import import_module, util
from pathlib import Path

import pytest
from pydantic import ValidationError


def module(name):
    assert util.find_spec(name), f"missing implementation: {name}"
    return import_module(name)


def test_study_requests_require_an_actual_origin_and_reject_forged_snapshots():
    schemas = module("backend.investment.study_schemas")
    with pytest.raises(ValidationError):
        schemas.CreateStudyRequest.model_validate(
            {"originType": "DAILY_EVENT", "eventId": "event:x"}
        )
    with pytest.raises(ValidationError):
        schemas.CreateStudyRequest.model_validate(
            {
                "originType": "SELECTION",
                "scope": {"indexId": "hs300"},
                "snapshot": {"factVerification": "VERIFIED"},
            }
        )
    body = schemas.CreateStudyRequest.model_validate(
        {"originType": "SELECTION", "scope": {"indexId": "hs300"}}
    )
    assert body.horizon_days == 30


def test_versions_require_generation_reason_and_notes_do_not_accept_orders():
    schemas = module("backend.investment.study_schemas")
    with pytest.raises(ValidationError):
        schemas.VersionRequest.model_validate({"expectedGeneration": 1, "reason": ""})
    with pytest.raises(ValidationError):
        schemas.NoteRequest.model_validate(
            {
                "expectedGeneration": 1,
                "kind": "DECISION",
                "action": "EXECUTE_BUY",
                "reason": "test",
            }
        )
    assert (
        schemas.NoteRequest.model_validate(
            {
                "expectedGeneration": 1,
                "kind": "REVIEW",
                "verdict": "INCONCLUSIVE",
                "reason": "缺少资料",
                "errorCategories": [],
            }
        ).verdict
        == "INCONCLUSIVE"
    )


def test_versions_reject_values_that_would_be_silently_truncated_by_freeze():
    schemas = module("backend.investment.study_schemas")
    for field, value in (
        ("transmission", ["依据"] * 9),
        ("watchFor", ["x" * 2001]),
        ("conditions", [{}] * 9),
    ):
        with pytest.raises(ValidationError):
            schemas.VersionRequest.model_validate(
                {"expectedGeneration": 1, "reason": "修订", field: value}
            )


def test_explicit_note_version_is_for_decisions_and_reviews_not_ack():
    from uuid import uuid4

    schemas = module("backend.investment.study_schemas")
    version_id = str(uuid4())
    request = schemas.NoteRequest.model_validate(
        {
            "expectedGeneration": 2,
            "kind": "REVIEW",
            "versionId": version_id,
            "verdict": "REFUTED",
            "reason": "复盘原始假设",
        }
    )
    assert str(request.version_id) == version_id
    with pytest.raises(ValidationError):
        schemas.NoteRequest.model_validate(
            {"expectedGeneration": 2, "kind": "ACK", "versionId": version_id}
        )


def test_migration_has_actor_force_rls_append_only_history_and_no_worker_ledger_authority():
    path = Path(__file__).resolve().parents[2] / "migrations/082_study_tracking.sql"
    assert path.exists(), "study migration missing"
    sql = path.read_text("utf-8")
    for table in (
        "study_tracks",
        "study_versions",
        "study_checks",
        "study_notes",
        "study_trade_links",
    ):
        assert f"CREATE TABLE research.{table}" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "study history is append-only" in sql
    assert "FOREIGN KEY(tenant_id,user_id,track_id)" in sql
    assert "GRANT UPDATE(enabled,generation,updated_at)" in sql
    assert "GRANT INSERT ON ledger" not in sql
