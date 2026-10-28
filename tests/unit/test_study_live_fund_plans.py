"""New follows freeze the same local NAV coordinates displayed on the page."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.errors import ValidationError_
from backend.investment import study, study_repository as repo, study_service
from backend.investment.live_fund_plans import refresh_report_fund_plans
from backend.investment.quantitative_scan import report_with_quantitative_scan
from test_live_fund_plans_v6 import NOW, history
from test_study_direction import report as direction_report
from test_study_quant_scan import scan_request, scan_row


class OriginConnection:
    def __init__(self, row, histories):
        self.row, self.histories = row, histories
        self.history_reads = 0

    async def fetchrow(self, *_args):
        return self.row

    async def fetch(self, *_args):
        self.history_reads += 1
        return [
            {"fund_code": item["fundCode"], "payload": item} for item in self.histories
        ]


def origin_row(kind):
    if kind == "scan":
        row = scan_row()
        return row, "100000", "RIGHT_TREND"
    return (
        {"run_id": uuid4(), "payload": direction_report(), "recorded_at": NOW},
        "000001",
        "LEFT_VALUE",
    )


async def read_origin(conn, kind, code, track):
    if kind == "scan":
        return await repo.daily_quant_scan_origin(
            conn, conn.row["run_id"], "nav", code, track, NOW
        )
    return await repo.daily_direction_origin(
        conn, conn.row["run_id"], "healthcare-recovery", "nav", code, track, NOW
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["direction", "scan"])
async def test_new_follow_freezes_displayed_v6_prices_and_original_research(kind):
    row, code, track = origin_row(kind)
    original = deepcopy(row)
    conn = OriginConnection(row, [history(code)])
    displayed_report = row["payload"]
    if kind == "scan":
        displayed_report = report_with_quantitative_scan(
            displayed_report, row["public_packet"], NOW
        )
    displayed = await refresh_report_fund_plans(
        conn, displayed_report, NOW, packet=row.get("public_packet")
    )
    expected = (
        displayed["quantitativeScan"]["items"][0]
        if kind == "scan"
        else displayed["valueDirections"][0]["plans"][0]
    )
    origin = await read_origin(conn, kind, code, track)
    snapshot = study.freeze_hypothesis(origin, now=NOW)
    plan = snapshot["actionablePlan"]
    assert plan["methodVersion"] == "actionable-zones-v6"
    for field in ("asOf", "currentPrice", "currentChangePct", "tracks"):
        assert plan.get(field) == expected.get(field)
    assert plan["asOf"] == "2026-10-27"
    assert plan["retrievedAt"] == NOW.isoformat()
    assert snapshot["origin"]["reportAsOf"] == row["payload"]["asOf"]
    if kind == "direction":
        assert snapshot["thesis"] == original["payload"]["valueDirections"][0]["thesis"]
        assert snapshot["sources"][0]["quote"] == "original"
    assert row == original


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["direction", "scan"])
async def test_missing_local_nav_cannot_silently_freeze_old_report_prices(kind):
    row, code, track = origin_row(kind)
    conn = OriginConnection(row, [])
    with pytest.raises(ValidationError_, match="本地.*净值|净值.*本地"):
        await read_origin(conn, kind, code, track)


@pytest.mark.asyncio
async def test_new_prices_do_not_rewrite_frozen_direction_quantitative_evidence():
    row, code, track = origin_row("direction")
    direction = row["payload"]["valueDirections"][0]
    proof = {**deepcopy(direction["plans"][0]), "historyQuality": "READY"}
    direction.update(quantitativeFundCodes=[code], quantitativeEvidence=[proof])
    origin = await read_origin(
        OriginConnection(row, [history(code)]), "direction", code, track
    )
    snapshot = study.freeze_hypothesis(origin, now=NOW)
    assert snapshot["actionablePlan"]["currentPrice"] != "1.0000"
    assert (
        snapshot["valueDirection"]["quantitativeEvidence"][0]["currentPrice"]
        == "1.0000"
    )
    assert snapshot["origin"]["reportAsOf"] == row["payload"]["asOf"]


@pytest.mark.asyncio
async def test_current_prices_cannot_authenticate_a_future_original_news_source():
    row, code, track = origin_row("direction")
    row["payload"]["sources"][0]["retrievedAt"] = NOW.isoformat()
    conn = OriginConnection(row, [history(code)])
    with pytest.raises(ValidationError_):
        await read_origin(conn, "direction", code, track)
    assert conn.history_reads == 0


@pytest.mark.asyncio
async def test_index_follow_keeps_original_coordinate_without_fund_history_read():
    row, _code, track = origin_row("direction")
    selected = row["payload"]["valueDirections"][0]["plans"][0]
    selected.update(
        subjectKind="index",
        subjectId="hs300",
        priceBasis="INDEX_POINTS",
        unit="points",
        market="SSE",
    )
    selected.pop("fundCode")
    conn = OriginConnection(row, [])
    origin = await repo.daily_direction_origin(
        conn, row["run_id"], "healthcare-recovery", "index", "hs300", track, NOW
    )
    assert origin["actionablePlan"] == selected
    assert conn.history_reads == 0


@pytest.mark.asyncio
async def test_live_refresh_cannot_infer_a_market_that_original_scope_rejected():
    row, code, track = origin_row("direction")
    row["payload"]["valueDirections"][0]["plans"][0].pop("market")
    conn = OriginConnection(row, [history(code)])
    origin = await read_origin(conn, "direction", code, track)
    with pytest.raises(ValidationError_, match="市场"):
        await repo.direction_plan_scope(conn, origin["actionablePlan"])
    assert conn.history_reads == 0


@pytest.mark.asyncio
async def test_click_uses_same_current_alpha_history_input_as_display(monkeypatch):
    from backend.macro import alpha_repository

    row, code, track = origin_row("direction")
    latest = history(code)
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"fundHistories": [latest]}),
    )
    conn = OriginConnection(row, [])
    displayed = await refresh_report_fund_plans(
        conn, row["payload"], NOW, histories=[latest]
    )
    origin = await read_origin(conn, "direction", code, track)
    expected = displayed["valueDirections"][0]["plans"][0]
    assert origin["actionablePlan"]["tracks"] == expected["tracks"]
    assert origin["actionablePlan"]["currentPrice"] == expected["currentPrice"]


@pytest.mark.asyncio
async def test_cross_entry_matching_requires_the_current_method_and_prices():
    row, code, track = origin_row("scan")
    origin = await read_origin(
        OriginConnection(row, [history(code)]), "scan", code, track
    )
    conn = AsyncMock()
    conn.fetchval.return_value = None
    await repo.matching_report_plan_track(conn, uuid4(), uuid4(), origin, NOW)
    sql, *args = conn.fetchval.await_args.args
    assert "jsonb_build_object" in sql
    assert "'methodVersion'" in sql and "'currentPrice'" in sql
    assert args[8] == repo.dump(repo.live_plan_coordinate(origin["actionablePlan"]))
    assert "estimatedProfitMaxPct" in args[9]


@pytest.mark.asyncio
async def test_new_v6_coordinate_cannot_reuse_old_same_entry_track(monkeypatch):
    from backend.investment import fund_watch

    row, code, track = origin_row("scan")
    original = await repo.daily_quant_scan_origin(
        OriginConnection(row, [history(code)]), row["run_id"], "nav", code, track, NOW
    )
    lookup = AsyncMock(return_value=deepcopy(original))
    legacy = deepcopy(original)
    legacy["actionablePlan"].pop("priceRefresh", None)
    legacy["actionablePlan"]["methodVersion"] = "actionable-zones-v4"
    lookup.return_value = legacy
    monkeypatch.setattr(repo, "daily_quant_scan_origin", lookup)
    monkeypatch.setattr(
        repo,
        "direction_plan_scope",
        AsyncMock(return_value={"fundCode": code, "fundMarket": "FUND_CN"}),
    )
    monkeypatch.setattr(
        repo, "matching_report_plan_track", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        study_service,
        "validate_scope",
        AsyncMock(side_effect=lambda _conn, scope, *_: scope),
    )
    monkeypatch.setattr(fund_watch, "resume_existing_study", AsyncMock())
    monkeypatch.setattr(
        study_service,
        "get_detail",
        AsyncMock(
            side_effect=lambda _conn, _tenant, _user, ident, *_: {"track": str(ident)}
        ),
    )
    conn, tenant, actor = AsyncMock(), uuid4(), uuid4()
    old_id, new_id, keys = uuid4(), uuid4(), []

    async def existing(_sql, _tenant, _actor, key):
        keys.append(key)
        return old_id if key == keys[0] else new_id

    conn.fetchval.side_effect = existing
    request = scan_request(reportRunId=str(row["run_id"]))
    assert (await study_service.create_study(conn, tenant, actor, request, NOW))[
        "track"
    ] == str(old_id)
    lookup.return_value = deepcopy(original)
    assert (await study_service.create_study(conn, tenant, actor, request, NOW))[
        "track"
    ] == str(new_id)
    lookup.return_value["actionablePlan"]["priceRefresh"]["calculatedAt"] = (
        NOW + timedelta(minutes=5)
    ).isoformat()
    assert (await study_service.create_study(conn, tenant, actor, request, NOW))[
        "track"
    ] == str(new_id)
    assert keys[0] != keys[1] == keys[2]
    lookup.return_value["actionablePlan"]["currentPrice"] = "1.6000"
    await study_service.create_study(conn, tenant, actor, request, NOW)
    assert keys[3] != keys[2]
