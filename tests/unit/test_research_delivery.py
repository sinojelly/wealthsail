"""The daily economic entry reads this run's safe archive, never an old READY."""

from copy import deepcopy
from datetime import timedelta, timezone
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.unit.test_daily_repository import NOW, run

RUN = uuid4()


def module():
    return import_module("backend.macro.research_delivery")


def test_source_publication_date_uses_shanghai_day_of_original_utc_instant():
    validated = NOW.replace(hour=0, minute=20).astimezone(timezone.utc)
    source = {
        "id": "s1",
        "url": "https://example.org/source",
        "publishedDate": NOW.date().isoformat(),
        "retrievedAt": validated.isoformat(),
    }
    assert module()._source_visible(source, validated) is True
    assert validated.hour == 16


def test_execution_not_observed_remains_explicit_coverage_metadata():
    assert module().safe_delivery_coverage(
        {"executionMatchStatus": "EXECUTION_NOT_OBSERVED"}
    ) == {"executionMatchStatus": "EXECUTION_NOT_OBSERVED"}
    assert module().safe_delivery_coverage(
        {"researchDeliveryStatus": "PARTIALLY_VALIDATED_READ_ONLY"}
    ) == {}


def current_run(**changes):
    return run(**{"run_id": RUN, "status": "FAILED", "recorded_at": NOW, **changes})


def summary(**changes):
    return {
        "researchId": str(RUN),
        "kind": "INITIAL",
        "archiveState": "ARCHIVED",
        "archivePending": False,
        "validatedAt": NOW.isoformat(),
        "recordedAt": NOW.isoformat(),
        "decision": "PARTIAL",
        "acceptedContentComplete": True,
        "issueCount": 0,
        "issueListComplete": True,
        "accepted": {
            "sources": [
                {
                    "id": "s1",
                    "title": "本轮来源",
                    "url": "https://example.org/source",
                    "quote": "公开资料。",
                    "verification": "SOURCE_CHECKED",
                    "publishedDate": NOW.date().isoformat(),
                    "retrievedAt": NOW.isoformat(),
                }
            ],
            "events": [
                {
                    "id": "event:today",
                    "title": "本轮经济观察",
                    "summary": "影响仍为条件推演。",
                    "phase": "ANNOUNCED",
                    "importance": "HIGH",
                    "sourceIds": ["s1"],
                    "channels": ["经济传导"],
                    "scenarios": [],
                    "watchFor": ["正式披露"],
                    "unknowns": ["传导幅度"],
                }
            ],
            "upcoming": [],
            "valueDirections": [],
            "industryCycleReviews": [],
        },
        **changes,
    }


def coverage(**changes):
    return {
        "researchDeliveryStatus": "VALIDATED_READ_ONLY",
        "discoveredTopicCount": 2,
        "pendingVerificationTopicCount": 0,
        "evidenceBackedTopicRatioPercent": 100.0,
        "evidenceBackedTopicCount": 2,
        "qualifiedFormalDirectionCount": 0,
        **changes,
    }


async def read(monkeypatch, *, receipt=None, measured=None, visible=None):
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        visible or current_run(),
        {
            "coverage": coverage() if measured is None else measured,
            "validated_at": NOW,
            "created_at": NOW,
        },
    ]
    source = AsyncMock(return_value=summary() if receipt is None else receipt)
    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary", source
    )
    result = await module().read_economic_research(conn, RUN, NOW)
    return result, conn, source


@pytest.mark.asyncio
async def test_available_is_current_archived_safe_content_and_never_action_authority(
    monkeypatch,
):
    result, conn, source = await read(monkeypatch)
    assert result["status"] == "AVAILABLE"
    assert result["researchId"] == str(RUN) and result["asOf"] == NOW.isoformat()
    assert result["observations"]["events"][0]["title"] == "本轮经济观察"
    assert result["actionable"] is False and result["archivePending"] is False
    source.assert_awaited_once_with(conn, RUN, NOW, admin=False)
    sql = " ".join(call.args[0] for call in conn.fetchrow.await_args_list)
    assert (
        "frozen_bundle" not in sql
        and "public_packet" not in sql
        and "outcome" not in sql
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["AUDIT_PENDING", "ARCHIVING", "UNRECOVERABLE"])
async def test_queue_receipt_exposes_status_only_and_never_its_pending_body(
    monkeypatch, state
):
    result, conn, _ = await read(
        monkeypatch,
        receipt=summary(archiveState=state, archivePending=state != "UNRECOVERABLE"),
    )
    assert result["status"] == (
        "UNAVAILABLE" if state == "UNRECOVERABLE" else "PENDING"
    )
    assert (
        result["observations"]["events"] == []
        and result["observations"]["sources"] == []
    )
    assert result["archivePending"] is (state != "UNRECOVERABLE")
    assert conn.fetchrow.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"pendingVerificationTopicCount": 1},
        {"researchDeliveryStatus": "OBSERVATION_READ_ONLY"},
        {"researchDeliveryStatus": "PARTIALLY_VALIDATED_READ_ONLY"},
        {"researchDeliveryStatus": "UNAVAILABLE"},
        {},
    ],
)
async def test_pending_or_unproven_coverage_never_claims_fully_available(
    monkeypatch, changes
):
    measured = coverage(**changes) if changes else {}
    result, _, _ = await read(monkeypatch, measured=measured)
    assert result["status"] == "PARTIAL"
    assert result["observations"]["events"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes", [{"issueCount": 1}, {"acceptedContentComplete": False}]
)
async def test_quarantine_or_excerpt_is_explicit_partial(monkeypatch, changes):
    result, _, _ = await read(monkeypatch, receipt=summary(**changes))
    assert result["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_legacy_without_archive_is_unknown_and_running_run_is_pending(
    monkeypatch,
):
    source = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary", source
    )
    for state, expected in (("FAILED", "UNKNOWN"), (None, "PENDING")):
        conn = AsyncMock()
        conn.fetchrow.return_value = current_run(status=state)
        result = await module().read_economic_research(conn, RUN, NOW)
        assert result["status"] == expected and result["observations"]["events"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "legacy-mock-id"])
async def test_no_current_identity_performs_no_archive_read(monkeypatch, invalid):
    conn, source = AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary", source
    )
    result = await module().read_economic_research(conn, invalid, NOW)
    assert result["status"] == "UNKNOWN" and result["researchId"] is None
    conn.fetchrow.assert_not_awaited()
    source.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    [
        "other_run",
        "old_day",
        "future_dispatch",
        "future_validation",
        "future_recording",
    ],
)
async def test_other_run_old_day_and_future_archives_are_never_used(
    monkeypatch, boundary
):
    visible, receipt = current_run(), summary()
    if boundary == "other_run":
        receipt["researchId"] = str(uuid4())
    elif boundary == "old_day":
        visible["slot_date"] = (NOW - timedelta(days=1)).date()
    elif boundary == "future_dispatch":
        visible["dispatched_at"] = NOW + timedelta(minutes=1)
    elif boundary == "future_validation":
        receipt["validatedAt"] = (NOW + timedelta(minutes=1)).isoformat()
    else:
        receipt["recordedAt"] = (NOW + timedelta(minutes=1)).isoformat()
    result, _, _ = await read(monkeypatch, receipt=receipt, visible=visible)
    assert result["status"] == "UNKNOWN" and result["observations"]["sources"] == []


@pytest.mark.asyncio
async def test_future_source_cannot_leak_through_an_otherwise_visible_receipt(
    monkeypatch,
):
    receipt = summary()
    receipt["accepted"]["sources"][0]["retrievedAt"] = (
        NOW + timedelta(days=1)
    ).isoformat()
    result, _, _ = await read(monkeypatch, receipt=receipt)
    assert result["observations"]["sources"] == []
    assert result["observations"]["events"] == []
    assert result["status"] != "AVAILABLE"


@pytest.mark.asyncio
async def test_public_projection_drops_raw_prices_private_fields_and_does_not_upgrade_proof(
    monkeypatch,
):
    receipt = summary()
    receipt["accepted"]["events"][0].update(raw="PRIVATE", plans=[{"price": "PRIVATE"}])
    receipt["accepted"]["sources"][0]["verification"] = "UNVERIFIED"
    result, _, _ = await read(
        monkeypatch,
        receipt=receipt,
        measured=coverage(
            researchDeliveryStatus="OBSERVATION_READ_ONLY",
            private="PRIVATE",
            pendingVerificationTopicCount=True,
            evidenceBackedTopicRatioPercent=True,
        ),
    )
    assert "PRIVATE" not in str(result)
    assert result["observations"]["sources"][0]["verification"] == "UNVERIFIED"
    assert "pendingVerificationTopicCount" not in result["coverage"]
    assert "evidenceBackedTopicRatioPercent" not in result["coverage"]
    assert result["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_database_errors_propagate_instead_of_becoming_a_successful_empty_entry(
    monkeypatch,
):
    conn = AsyncMock()
    conn.fetchrow.return_value = current_run()
    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary",
        AsyncMock(side_effect=OSError("owned database unavailable")),
    )
    with pytest.raises(OSError):
        await module().read_economic_research(conn, RUN, NOW)


@pytest.mark.asyncio
async def test_daily_additive_entry_preserves_failed_state_and_old_ready_fallback(
    monkeypatch,
):
    from backend.macro import daily_repository as daily

    yesterday = NOW - timedelta(days=1)
    old = run(
        run_id=uuid4(),
        slot_date=yesterday.date(),
        status="READY",
        recorded_at=yesterday,
        payload={"summary": "旧报告"},
    )
    latest = current_run()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        latest,
        old,
        latest,
        {"coverage": coverage(), "validated_at": NOW, "created_at": NOW},
    ]
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary",
        AsyncMock(return_value=summary()),
    )
    before = deepcopy(old)

    result = await daily.read_daily_research(conn, NOW)

    assert result["state"] == "FAILED" and result["report"] == {"summary": "旧报告"}
    assert result["reportRunId"] == str(old["run_id"])
    assert result["economicResearch"]["researchId"] == str(RUN)
    assert (
        result["economicResearch"]["observations"]["events"][0]["title"]
        == "本轮经济观察"
    )
    assert old == before
