"""Report macro proof keeps original facts instead of saving-time observations."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.errors import ValidationError_
from backend.investment import study, study_repository as repo
from test_study_direction import NOW, report


def macro_proof(**changes):
    return {
        "code": "M1_YOY",
        "label": "M1同比",
        "value": "3.30",
        "unit": "%",
        "referencePeriod": "2026-09",
        "asOf": "2026-09-01",
        "sourceUri": "https://www.pbc.gov.cn/macro",
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "publishedAt": None,
        "timeBasis": "REFERENCE_PERIOD",
        "state": "READY",
        **changes,
    }


@pytest.mark.asyncio
async def test_direction_macro_proof_preserves_report_values_and_excludes_private_fields():
    payload = report()
    evidence = macro_proof(private="PRIVATE note", accountId="PRIVATE account")
    payload["valueDirections"][0]["macroEvidence"] = [evidence]
    conn = AsyncMock()
    run = uuid4()
    conn.fetchrow.return_value = {"run_id": run, "recorded_at": NOW, "payload": payload}
    later = NOW + timedelta(days=3)
    origin = await repo.daily_direction_origin(
        conn, run, "healthcare-recovery", "nav", "000001", "LEFT_VALUE", later
    )
    origin["observations"] = [
        {"metric": "M1_YOY", "value": "9.99", "asOf": later.date().isoformat()}
    ]
    frozen = study.freeze_hypothesis(origin, now=later)
    assert frozen["valueDirection"]["macroEvidence"] == [macro_proof()]
    assert frozen["observations"][0]["value"] == "9.99"
    evidence["value"] = "12.00"
    origin["valueDirection"]["macroEvidence"][0]["value"] = "13.00"
    assert frozen["valueDirection"]["macroEvidence"][0]["value"] == "3.30"
    assert "PRIVATE" not in str(frozen)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"retrievedAt": (NOW + timedelta(seconds=1)).isoformat()},
        {"availableAt": (NOW + timedelta(seconds=1)).isoformat()},
        {"publishedAt": (NOW + timedelta(seconds=1)).isoformat()},
        {"asOf": "2026-10-19"},
        {"referenceDate": "2026-10-19"},
        {"referencePeriod": "2026-11"},
        {"value": "NaN"},
        {"code": "UNRELATED"},
    ],
)
async def test_direction_cannot_freeze_macro_facts_from_after_the_report(changes):
    payload = report()
    payload["valueDirections"][0]["macroEvidence"] = [macro_proof(**changes)]
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "run_id": uuid4(),
        "recorded_at": NOW,
        "payload": payload,
    }
    with pytest.raises(ValidationError_):
        await repo.daily_direction_origin(
            conn,
            uuid4(),
            "healthcare-recovery",
            "nav",
            "000001",
            "LEFT_VALUE",
            NOW + timedelta(days=3),
        )


def test_direct_freezing_also_whitelists_macro_proof_and_keeps_legacy_directions():
    direction = deepcopy(report()["valueDirections"][0])
    direction["macroEvidence"] = [macro_proof(private="PRIVATE")]
    origin = {
        "originType": "DAILY_DIRECTION",
        "reportAsOf": NOW.isoformat(),
        "valueDirection": direction,
    }
    snapshot = study.freeze_hypothesis(origin, now=NOW)
    assert snapshot["valueDirection"]["macroEvidence"] == [macro_proof()]
    origin["valueDirection"]["macroEvidence"][0]["availableAt"] = (
        NOW + timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ValueError):
        study.freeze_hypothesis(origin, now=NOW + timedelta(days=3))
    origin["valueDirection"].pop("macroEvidence")
    assert (
        "macroEvidence"
        not in study.freeze_hypothesis(origin, now=NOW)["valueDirection"]
    )
