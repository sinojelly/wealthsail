"""Current-run read-only projections never borrow another report or private raw."""

from datetime import timedelta
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.unit.test_research_delivery import NOW, summary
from tests.unit.test_daily_repository import run

RUN = uuid4()


def module():
    assert util.find_spec("backend.macro.research_opportunities"), (
        "independent observation reader missing"
    )
    return import_module("backend.macro.research_opportunities")


def record(**changes):
    return {
        "run_id": RUN,
        "dispatched_at": NOW - timedelta(minutes=5),
        "recorded_at": NOW,
        "run_mode": "MANUAL",
        "status": "FAILED",
        "payload": None,
        "public_packet": {
            "asOf": (NOW - timedelta(minutes=5)).isoformat(),
            "quantitativeScan": {"items": []},
            "discoveryUniverse": [],
        },
        **changes,
    }


async def read(monkeypatch, *, receipt=None, row=None, identity=RUN, snapshot=None):
    mod = module()
    conn = AsyncMock()
    conn.fetchrow.return_value = snapshot
    conn.fetch.return_value = []
    receipt = summary(researchId=str(RUN)) if receipt is None else receipt
    get_receipt = AsyncMock(return_value=receipt)
    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary",
        get_receipt,
    )
    projected = {
        "version": "fund-observation-v1",
        "actionable": False,
        "investmentEligible": False,
    }
    calls = []

    def project(packet, now, **kwargs):
        calls.append((packet, now, kwargs))
        return projected.copy()

    result = await mod.read_fund_observation(
        conn,
        record() if row is None else row,
        str(identity),
        NOW,
        _projector=project,
    )
    return result, calls, conn, get_receipt


@pytest.mark.asyncio
async def test_current_failed_run_scan_remains_readable_without_formal_report(
    monkeypatch,
):
    result, calls, conn, get_receipt = await read(monkeypatch)
    assert result["actionable"] is False and result["investmentEligible"] is False
    assert calls[0][2]["research_id"] == str(RUN)
    assert calls[0][2]["formal_report"] is None
    get_receipt.assert_awaited_once_with(conn, RUN, NOW, admin=False)
    assert conn.fetchrow.await_count == 1
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary", ["run", "dispatch", "validation", "recorded", "kind"]
)
async def test_wrong_run_future_material_or_non_initial_is_not_used(
    monkeypatch, boundary
):
    row = record()
    receipt = summary(researchId=str(RUN))
    identity = RUN
    if boundary == "run":
        identity = uuid4()
    elif boundary == "dispatch":
        row["dispatched_at"] = NOW + timedelta(seconds=1)
    elif boundary == "validation":
        receipt["validatedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif boundary == "recorded":
        receipt["recordedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    else:
        receipt["kind"] = "REPLAY"
    result, calls, *_ = await read(
        monkeypatch, row=row, receipt=receipt, identity=identity
    )
    assert result is None and not calls


@pytest.mark.asyncio
async def test_cross_tenant_missing_receipt_does_not_expose_manual_scan(monkeypatch):
    result, calls, *_ = await read(monkeypatch, receipt={})
    assert result is None and not calls


@pytest.mark.asyncio
async def test_previous_day_run_keeps_actual_clock_instead_of_becoming_today(
    monkeypatch,
):
    previous = NOW - timedelta(days=1)
    receipt = summary(
        researchId=str(RUN),
        validatedAt=previous.isoformat(),
        recordedAt=previous.isoformat(),
    )
    row = record(dispatched_at=previous - timedelta(minutes=5), recorded_at=previous)
    row["public_packet"]["asOf"] = (previous - timedelta(minutes=5)).isoformat()
    result, calls, *_ = await read(monkeypatch, receipt=receipt, row=row)
    assert result is not None and calls[0][2]["validated_at"] == previous
    assert calls[0][0]["asOf"] != NOW.isoformat()


@pytest.mark.asyncio
async def test_pending_archive_never_supplies_topic_or_current_review_proof(
    monkeypatch,
):
    result, calls, conn, _ = await read(
        monkeypatch,
        receipt=summary(
            researchId=str(RUN),
            archiveState="AUDIT_PENDING",
            archivePending=True,
        ),
    )
    assert result is not None
    assert calls[0][2]["topics"] == [] and calls[0][2]["industry_reviews"] == []
    assert result["archiveState"] == "AUDIT_PENDING"
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_failure_propagates_without_fake_success(monkeypatch):
    mod = module()
    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary",
        AsyncMock(side_effect=OSError("owned test database unavailable")),
    )
    with pytest.raises(OSError):
        await mod.read_fund_observation(AsyncMock(), record(), str(RUN), NOW)


@pytest.mark.asyncio
async def test_expected_invalid_observation_input_does_not_hide_other_research(
    monkeypatch,
):
    from backend.investment.fund_observation import FundObservationDataError

    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary",
        AsyncMock(return_value=summary(researchId=str(RUN))),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = None

    def invalid(*args, **kwargs):
        raise FundObservationDataError("private frozen input details")

    result = await module().read_fund_observation(
        conn,
        record(),
        str(RUN),
        NOW,
        _projector=invalid,
    )
    assert result["status"] == "UNAVAILABLE" and result["items"] == []
    assert result["actionable"] is False and result["investmentEligible"] is False
    assert result["missingEvidence"] == ["FROZEN_OBSERVATION_INVALID"]
    assert "private" not in str(result)


@pytest.mark.asyncio
async def test_unexpected_projection_bug_is_not_disguised_as_no_opportunities(
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.macro.research_acceptance_repository.read_validation_summary",
        AsyncMock(return_value=summary(researchId=str(RUN))),
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = None

    def broken(*args, **kwargs):
        raise RuntimeError("system defect")

    with pytest.raises(RuntimeError, match="system defect"):
        await module().read_fund_observation(
            conn, record(), str(RUN), NOW, _projector=broken
        )


@pytest.mark.asyncio
async def test_daily_observation_uses_latest_run_not_the_retained_ready_report(
    monkeypatch,
):
    from backend.macro import daily_repository as daily

    older = NOW - timedelta(days=1)
    latest = run(**record(), slot_date=NOW.date())
    previous = run(
        run_id=uuid4(),
        slot_date=older.date(),
        dispatched_at=older,
        status="READY",
        recorded_at=older,
        payload={"summary": "old READY"},
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [latest, previous]
    monkeypatch.setattr(
        "backend.macro.daily_pipeline_repository.read_pipeline",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "backend.macro.research_delivery.read_economic_research",
        AsyncMock(return_value={}),
    )
    projection = AsyncMock(return_value={"researchId": str(RUN), "actionable": False})
    monkeypatch.setattr(module(), "read_fund_observation", projection)
    result = await daily.read_daily_research(conn, NOW)
    assert result["reportRunId"] == str(previous["run_id"])
    assert result["fundObservation"]["researchId"] == str(RUN)
    projection.assert_awaited_once_with(conn, latest, str(RUN), NOW)
