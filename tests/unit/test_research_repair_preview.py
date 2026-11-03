"""File preview reads exports only and cannot supply a new evidence clock or publish."""

from datetime import timedelta
import json
from unittest.mock import AsyncMock

import pytest

from test_daily_research import NOW
from test_research_frozen_bundle import RUN, export_bundle


@pytest.mark.asyncio
async def test_file_preview_uses_same_frozen_clock_and_omits_private_report(
    tmp_path, monkeypatch
):
    from scripts import research_repair_preview as preview
    from backend.macro import research_acceptance_replay as child

    path = tmp_path / "export.json"
    exported = export_bundle()
    path.write_text(json.dumps(exported), encoding="utf-8")
    runner = AsyncMock(
        return_value={
            "decision": "REPAIRED",
            "strictPass": True,
            "autoPublishAllowed": False,
            "historicalPublishAllowed": True,
            "formalReport": {"PRIVATE": "scores"},
            "candidateRaw": {"PRIVATE": "raw"},
            "originalOutcome": {"PRIVATE": "issues"},
            "quarantined": [
                {
                    "itemType": "VALUE_DIRECTION",
                    "itemIndex": 6,
                    "businessKey": "orphan",
                    "ruleId": "WATCH_REVIEW_UNLINKED",
                    "originalItem": {"PRIVATE": "raw direction"},
                }
            ],
            "retained": {
                "counts": {"valueDirections": 6},
                "directionKeys": ["retained-key"],
            },
            "revision": "a" * 64,
            "blockers": [],
            "repairVersion": "research-repair-v1",
        }
    )
    monkeypatch.setattr(child, "run_offline_repair", runner, raising=False)
    result = await preview.preview_export(
        path, RUN, observed_at=NOW + timedelta(days=3)
    )
    assert result["operation"] == "OFFLINE_DRY_RUN"
    assert (
        result["identityVerification"] == "EXPORT_SELF_CONSISTENT_NOT_SERVER_ATTESTED"
    )
    assert result["actionable"] is False and result["published"] is False
    assert result["originalValidatedAt"] == NOW.isoformat()
    assert "PRIVATE" not in str(result)
    assert result["retained"] == {
        "counts": {"valueDirections": 6},
        "directionKeys": ["retained-key"],
    }
    assert result["counts"] == {"valueDirections": 6}
    assert result["repairRevision"] == "a" * 64
    assert result["quarantined"][0]["businessKey"] == "orphan"
    runner.assert_awaited_once()
    assert runner.await_args.args[0]["validatedAt"] == NOW.isoformat()
    assert path.read_text("utf-8") == json.dumps(exported)


@pytest.mark.asyncio
async def test_tampered_export_cannot_reach_offline_validator(tmp_path, monkeypatch):
    from scripts import research_repair_preview as preview
    from backend.macro import research_acceptance_replay as child

    path = tmp_path / "export.json"
    exported = export_bundle()
    exported["items"][0]["content"] += " "
    path.write_text(json.dumps(exported), encoding="utf-8")
    runner = AsyncMock()
    monkeypatch.setattr(child, "run_offline_repair", runner, raising=False)
    with pytest.raises(ValueError):
        await preview.preview_export(path, RUN, observed_at=NOW + timedelta(days=3))
    runner.assert_not_awaited()


@pytest.mark.parametrize("option", ["--clock", "--publish", "--raw", "--patch"])
def test_cli_has_no_publish_patch_or_evidence_clock_override(option, monkeypatch):
    from scripts import research_repair_preview as preview

    monkeypatch.setattr(
        "sys.argv",
        ["preview", "--bundle", "input.json", "--run-id", RUN, option, "value"],
    )
    with pytest.raises(SystemExit) as error:
        preview.main()
    assert error.value.code == 2
