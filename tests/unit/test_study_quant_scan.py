"""A scan follow freezes one server-authenticated report coordinate."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.errors import NotFoundError, ValidationError_
from backend.investment import study, study_repository as repo, study_service as service
from backend.investment.study_schemas import CreateStudyRequest
from test_quantitative_scan import NOW, scan, scan_plan


def scan_request(**updates):
    return {
        "originType": "DAILY_QUANT_SCAN",
        "reportRunId": str(uuid4()),
        "subjectKind": "nav",
        "subjectId": "100000",
        "planTrack": "RIGHT_TREND",
        **updates,
    }


def scan_row(**updates):
    return {
        "run_id": uuid4(),
        "recorded_at": NOW,
        "payload": {"asOf": NOW.isoformat(), "quantitativeScan": scan(1)},
        "public_packet": {"quantitativeScan": scan(2)},
        **updates,
    }


def test_scan_command_accepts_only_resolvable_identity():
    request = CreateStudyRequest.model_validate(scan_request())
    assert request.origin_type == "DAILY_QUANT_SCAN"
    assert request.plan_track == "RIGHT_TREND"


@pytest.mark.parametrize(
    "updates",
    [
        {"scope": None},
        {"scope": {"fundCode": "100000"}},
        {"valueDirectionKey": "ai"},
        {"valueDirectionKey": None},
        {"eventId": None},
        {"eventId": "news"},
        {"actionablePlan": scan_plan()},
        {"currentPrice": "9"},
        {"reportRunId": None},
        {"planTrack": None},
        {"subjectKind": "index"},
        {"subjectId": None},
    ],
)
def test_scan_command_rejects_context_injection_and_missing_identity(updates):
    with pytest.raises(ValidationError):
        CreateStudyRequest.model_validate(scan_request(**updates))


@pytest.mark.asyncio
async def test_scan_origin_freezes_original_prices_even_after_a_month():
    row = scan_row()
    conn = AsyncMock()
    conn.fetchrow.return_value = row
    origin = await repo.daily_quant_scan_origin(
        conn, row["run_id"], "nav", "100000", "RIGHT_TREND", NOW + timedelta(days=30)
    )
    frozen = study.freeze_hypothesis(origin, now=NOW + timedelta(days=30))
    assert frozen["origin"]["originType"] == "DAILY_QUANT_SCAN"
    assert frozen["origin"]["reportRunId"] == str(row["run_id"])
    assert frozen["origin"]["subjectId"] == "100000"
    assert frozen["origin"]["planTrack"] == "RIGHT_TREND"
    expected = {**scan(1)["items"][0], "selectedTrack": "RIGHT_TREND"}
    assert frozen["actionablePlan"]["tracks"] == expected["tracks"]
    assert frozen["actionablePlan"]["currentPrice"] == "1.0000"
    assert len(frozen["sources"]) == 1
    row["payload"]["quantitativeScan"]["items"][0]["tracks"][0]["entryZoneMin"] = "9"
    assert frozen["actionablePlan"]["tracks"][0]["entryZoneMin"] == "0.9900"
    query = conn.fetchrow.await_args.args[0]
    assert "t.status='READY'" in query and "t.recorded_at<=$2" in query
    assert "r.dispatched_at<=$2" in query and "r.public_packet" in query


@pytest.mark.asyncio
async def test_old_scan_origin_uses_own_dispatch_fallback():
    conn = AsyncMock()
    row = scan_row(payload={"asOf": NOW.isoformat()})
    conn.fetchrow.return_value = row
    origin = await repo.daily_quant_scan_origin(
        conn, row["run_id"], "nav", "100001", "RIGHT_TREND", NOW
    )
    assert origin["actionablePlan"]["fundCode"] == "100001"
    assert origin["actionablePlan"]["selectedTrack"] == "RIGHT_TREND"


@pytest.mark.parametrize(
    "change",
    [
        "future_report",
        "other_fund",
        "wrong_track",
        "duplicate",
        "missing_source",
        "not_ready",
        "invalid_report",
    ],
)
@pytest.mark.asyncio
async def test_scan_origin_rejects_unavailable_or_ambiguous_original_coordinate(change):
    conn = AsyncMock()
    row = scan_row()
    item = row["payload"]["quantitativeScan"]["items"][0]
    code, track = "100000", "RIGHT_TREND"
    if change == "future_report":
        row["payload"]["asOf"] = (NOW + timedelta(seconds=1)).isoformat()
    elif change == "other_fund":
        code = "999999"
    elif change == "wrong_track":
        track = "LEFT_VALUE"
    elif change == "duplicate":
        row["payload"]["quantitativeScan"]["items"].append(deepcopy(item))
    elif change == "missing_source":
        item["sourceUri"] = None
    elif change == "not_ready":
        item["historyQuality"] = "UNKNOWN"
    else:
        row["payload"] = None
    conn.fetchrow.return_value = row
    with pytest.raises(ValidationError_):
        await repo.daily_quant_scan_origin(conn, row["run_id"], "nav", code, track, NOW)
    conn.fetchrow.return_value = None
    with pytest.raises(NotFoundError):
        await repo.daily_quant_scan_origin(conn, row["run_id"], "nav", code, track, NOW)


@pytest.mark.asyncio
async def test_scan_service_uses_original_origin_and_stable_cross_report_key(
    monkeypatch,
):
    from backend.investment import fund_watch

    monkeypatch.setattr(fund_watch, "resume_existing_study", AsyncMock())
    row = scan_row()
    conn = AsyncMock()
    conn.fetchrow.return_value = row
    origin = await repo.daily_quant_scan_origin(
        conn, row["run_id"], "nav", "100000", "RIGHT_TREND", NOW
    )
    lookup = AsyncMock(return_value=origin)
    monkeypatch.setattr(repo, "daily_quant_scan_origin", lookup)
    monkeypatch.setattr(
        repo,
        "direction_plan_scope",
        AsyncMock(return_value={"fundCode": "100000", "fundMarket": "FUND_CN"}),
    )
    monkeypatch.setattr(
        repo, "matching_report_plan_track", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        service, "validate_scope", AsyncMock(side_effect=lambda _, value, *__: value)
    )
    monkeypatch.setattr(
        service, "get_detail", AsyncMock(return_value={"original": True})
    )
    conn.fetchval.return_value = uuid4()
    tenant, actor = uuid4(), uuid4()
    request = scan_request(reportRunId=str(row["run_id"]))
    result = await service.create_study(conn, tenant, actor, request, NOW)
    assert result == {"original": True}
    first_key = conn.fetchval.await_args.args[-1]
    lookup.return_value = {**origin, "reportRunId": str(uuid4())}
    await service.create_study(
        conn, tenant, actor, {**request, "reportRunId": str(uuid4())}, NOW
    )
    assert first_key == conn.fetchval.await_args.args[-1]
    assert any("study-plan:" in str(call.args) for call in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_scan_service_reuses_direction_track_only_for_same_report_plan(
    monkeypatch,
):
    from backend.investment import fund_watch

    monkeypatch.setattr(fund_watch, "resume_existing_study", AsyncMock())
    conn = AsyncMock()
    row = scan_row()
    conn.fetchrow.return_value = row
    origin = await repo.daily_quant_scan_origin(
        conn, row["run_id"], "nav", "100000", "RIGHT_TREND", NOW
    )
    existing = uuid4()
    matching = AsyncMock(return_value=existing)
    monkeypatch.setattr(repo, "daily_quant_scan_origin", AsyncMock(return_value=origin))
    monkeypatch.setattr(
        repo,
        "direction_plan_scope",
        AsyncMock(return_value={"fundCode": "100000", "fundMarket": "FUND_CN"}),
    )
    monkeypatch.setattr(repo, "matching_report_plan_track", matching)
    monkeypatch.setattr(
        service, "validate_scope", AsyncMock(side_effect=lambda _, value, *__: value)
    )
    monkeypatch.setattr(
        service, "get_detail", AsyncMock(return_value={"track": str(existing)})
    )
    tenant, actor = uuid4(), uuid4()
    result = await service.create_study(
        conn, tenant, actor, scan_request(reportRunId=str(row["run_id"])), NOW
    )
    assert result["track"] == str(existing)
    assert matching.await_args.args[:2] == (conn, tenant)
    assert conn.fetchval.await_count == 0


@pytest.mark.asyncio
async def test_two_independent_directions_do_not_collapse_into_one_scan_track():
    original = scan_row()
    plan = {**scan_plan("100000"), "selectedTrack": "RIGHT_TREND"}
    original["payload"].update(
        valueDirections=[
            {
                "key": key,
                "title": key,
                "thesis": "独立研究逻辑",
                "plans": [plan],
                "sourceIds": ["one", "two"],
                "macroCodes": [],
            }
            for key in ("policy-finance", "dividend-value")
        ],
        sources=[
            {"id": key, "url": "https://example.org/" + key} for key in ("one", "two")
        ],
    )
    conn = AsyncMock()
    conn.fetchrow.return_value = original
    origin = await repo.daily_direction_origin(
        conn, original["run_id"], "policy-finance", "nav", "100000", "RIGHT_TREND", NOW
    )
    assert (
        await repo.matching_report_plan_track(conn, uuid4(), uuid4(), origin, NOW)
        is None
    )
    conn.fetchval.assert_not_awaited()
