"""Cached public evidence never acquires a newer source, date or definition."""

from copy import deepcopy
from datetime import datetime, timedelta
from importlib import import_module, util
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def engine():
    assert util.find_spec("backend.macro.alpha_repository"), (
        "alpha cache repository missing"
    )
    return import_module("backend.macro.alpha_repository")


def observation(code="CN_T10", day="2026-10-16", value="1.82", **changes):
    monthly = code in {"M1_YOY", "M2_YOY", "M1_M2_GAP"} or code.startswith("HOUSE_")
    key = (
        "AKSHARE_MONEY_SUPPLY"
        if monthly and not code.startswith("HOUSE_")
        else "AKSHARE_HOUSE_HAINAN"
        if code.startswith("HOUSE_")
        else "AKSHARE_CN_T10"
    )
    row = {
        "code": code,
        "referenceDate": day,
        "value": value,
        "frequency": "MONTHLY" if monthly else "DAILY",
        "unit": "percentage_points" if code == "M1_M2_GAP" else "%",
        "sourceKey": key,
        "sourceUri": "https://data.eastmoney.com/cjsj/data.html",
        "sourceName": "东方财富公开数据",
        "publishedAt": None,
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "timeBasis": "RETRIEVAL_ONLY",
        "methodVersion": "alpha-public-v1",
        "metadata": {
            "provider": "EASTMONEY",
            "definitionVersion": "EASTMONEY_REPORTED",
        },
    }
    row.update(changes)
    return row


def bundle(rows):
    return {
        "methodVersion": "alpha-public-v1",
        "asOf": NOW.isoformat(),
        "observations": rows,
        "catalysts": [],
        "odds": [],
        "launches": [],
        "sourceResults": {row["sourceKey"]: {"status": "SUCCESS"} for row in rows},
    }


def merged(rows, existing=None):
    return engine().merge_alpha_series(existing or [], bundle(rows), NOW)


@pytest.mark.asyncio
async def test_empty_cache_returns_complete_arrays_without_network_or_write():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    result = await engine().read_alpha_public(conn, NOW)
    assert all(
        result[key] == []
        for key in ("observations", "catalysts", "odds", "launches", "fundHistories")
    )
    assert result["sourceResults"] == result["themeNavSourceResults"] == {}
    assert result["collectionState"] == "NOT_COLLECTED"
    assert not conn.execute.called


@pytest.mark.asyncio
async def test_today_failure_retains_prior_payload_with_actual_old_collection_date():
    old = NOW - timedelta(days=1)
    payload = bundle(
        [observation(retrievedAt=old.isoformat(), availableAt=old.isoformat())]
    )
    payload["asOf"] = old.isoformat()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {
            "id": "today",
            "run_date": NOW.date(),
            "claimed_at": NOW,
            "completed_at": NOW,
            "status": "FAILED",
            "payload": {"errorClass": "TimeoutError"},
        },
        {
            "id": "prior",
            "run_date": old.date(),
            "claimed_at": old,
            "completed_at": old,
            "status": "PARTIAL",
            "payload": payload,
        },
    ]
    result = await engine().read_alpha_public(conn, NOW)
    assert result["collectionState"] == "FAILED"
    assert result["collectionAsOf"] == old.isoformat()
    assert result["lastAttempt"]["status"] == "FAILED"
    assert result["observations"][0]["retrievedAt"] == old.isoformat()
    assert result["asOf"] == old.isoformat()


@pytest.mark.asyncio
async def test_future_terminal_payload_is_not_visible_as_a_completed_attempt():
    old = NOW - timedelta(days=1)
    prior = bundle([])
    prior["asOf"] = old.isoformat()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {
            "id": "today",
            "run_date": NOW.date(),
            "claimed_at": NOW,
            "completed_at": NOW + timedelta(hours=1),
            "status": "FAILED",
            "payload": {"errorClass": "FutureSecret"},
        },
        {
            "id": "prior",
            "run_date": old.date(),
            "claimed_at": old,
            "completed_at": old,
            "status": "READY",
            "payload": prior,
        },
    ]
    result = await engine().read_alpha_public(conn, NOW)
    assert result["collectionState"] == "COLLECTING"
    assert result["lastAttempt"]["status"] == "CLAIMED"
    assert (
        result["lastAttempt"]["completedAt"] is None
        and "errorClass" not in result["lastAttempt"]
    )


def test_current_alpha_can_fill_chinabond_gap_without_mutating_permission_evidence():
    prior = {
        "code": "CN_T10",
        "state": "PERMISSION_REQUIRED",
        "value": None,
        "sourceUri": "https://yield.chinabond.com.cn/",
        "metadata": {"sourceKey": "CHINABOND"},
    }
    before = deepcopy(prior)
    result = merged([observation()], [prior])
    row = next(row for row in result if row["code"] == "CN_T10")
    assert row["state"] == "READY" and row["value"] == 1.82
    assert row["sourceUri"].startswith("https://data.eastmoney.com")
    assert row["previousEvidence"]["state"] == "PERMISSION_REQUIRED"
    assert prior == before


def test_ready_official_source_is_not_overwritten_by_alpha_vendor():
    prior = {
        "code": "M1_YOY",
        "state": "READY",
        "value": 3.1,
        "sourceUri": "https://www.pbc.gov.cn/release",
        "metadata": {"sourceKey": "PBOC_M1"},
    }
    row = next(
        row
        for row in merged([observation("M1_YOY", "2026-09-30", "3.2")], [prior])
        if row["code"] == "M1_YOY"
    )
    assert row == prior


def test_m1_history_fills_missing_month_keeps_official_value_and_definition():
    prior = {
        "code": "M1_YOY", "state": "READY", "value": 3.1,
        "sourceUri": "https://www.pbc.gov.cn/release",
        "metadata": {"sourceKey": "PBOC_M1"},
        "history": [{"date": "2026-09-01", "value": 3.1, "definitionVersion": "PBOC_M1_2025"}],
    }
    provider = [observation("M1_YOY", "2026-08-31", "2.8"), observation("M1_YOY", "2026-09-30", "3.2")]
    row = next(row for row in merged(provider, [prior]) if row["code"] == "M1_YOY")
    assert [(point["date"], point["value"]) for point in row["history"]] == [("2026-08-31", 2.8), ("2026-09-01", 3.1)]
    assert [point["definitionVersion"] for point in row["history"]] == ["EASTMONEY_REPORTED:2025_ONWARD", "PBOC_M1_2025"]


def test_independent_labels_and_adjacent_yield_change_use_basis_points():
    result = merged([observation(day="2026-10-15", value="1.80"), observation()])
    row = next(row for row in result if row["code"] == "CN_T10")
    assert row["label"] == "中国10年期国债收益率（东方财富）"
    assert row["changes"]["periodChange"] == 2
    assert row["changes"]["changeUnit"] == "bp" and row["changes"]["sampleCount"] == 2
    assert (
        len(row["history"]) == 2
        and row["history"][0]["sourceUri"] == observation()["sourceUri"]
    )


def test_missing_month_or_changed_definition_never_manufactures_delta():
    rows = [
        observation("M1_YOY", "2026-07-31", "2.0"),
        observation("M1_YOY", "2026-09-30", "3.0"),
    ]
    row = next(row for row in merged(rows) if row["code"] == "M1_YOY")
    assert row["changes"]["periodChange"] is None
    rows[0]["referenceDate"] = "2026-08-31"
    rows[0]["metadata"] = {
        "provider": "EASTMONEY",
        "definitionVersion": "different-definition",
    }
    row = next(row for row in merged(rows) if row["code"] == "M1_YOY")
    assert row["changes"]["periodChange"] is None and row["changes"]["sampleCount"] == 1


@pytest.mark.parametrize(
    "problem", ["future", "bad", "source_failed", "unknown_source"]
)
def test_invalid_alpha_cannot_fill_existing_gap_and_exposes_reason(problem):
    row = observation()
    current = bundle([row])
    if problem == "future":
        row["retrievedAt"] = (NOW + timedelta(days=1)).isoformat()
    elif problem == "bad":
        row["value"] = True
    elif problem == "source_failed":
        current["sourceResults"][row["sourceKey"]]["status"] = "FAILED"
    else:
        row["sourceUri"] = "https://127.0.0.1/private"
    prior = {"code": "CN_T10", "state": "PERMISSION_REQUIRED", "value": None}
    output = engine().merge_alpha_series([prior], current, NOW)
    result = next(row for row in output if row["code"] == "CN_T10")
    assert result["state"] == "PERMISSION_REQUIRED" and result["value"] is None
    assert result["alphaFallback"]["state"] != "READY"


def test_identical_duplicates_are_one_sample_and_conflicting_duplicates_are_not_selected():
    first, second = observation(), observation()
    row = next(row for row in merged([first, second]) if row["code"] == "CN_T10")
    assert row["changes"]["sampleCount"] == 1 and len(row["history"]) == 1
    second["value"] = "2.82"
    row = next(row for row in merged([first, second]) if row["code"] == "CN_T10")
    assert row["state"] == "CONFLICT" and row["value"] is None


def test_m1_reported_definition_break_does_not_create_cross_2025_change():
    at = datetime(2025, 2, 18, 9, tzinfo=NOW.tzinfo)
    rows = [
        observation(
            "M1_YOY",
            "2024-12-31",
            "2.0",
            retrievedAt=at.isoformat(),
            availableAt=at.isoformat(),
        ),
        observation(
            "M1_YOY",
            "2025-01-31",
            "3.0",
            retrievedAt=at.isoformat(),
            availableAt=at.isoformat(),
        ),
    ]
    output = engine().merge_alpha_series([], bundle(rows), at)
    row = next(row for row in output if row["code"] == "M1_YOY")
    assert row["changes"]["periodChange"] is None and row["changes"]["sampleCount"] == 1


def test_revision_order_uses_actual_instants_not_lexical_timezone_strings():
    from datetime import timezone

    newer = NOW.astimezone(timezone.utc).replace(hour=2)
    rows = [
        observation(value="1.82"),
        observation(
            value="2.82", retrievedAt=newer.isoformat(), availableAt=newer.isoformat()
        ),
    ]
    output = engine().merge_alpha_series([], bundle(rows), NOW + timedelta(hours=3))
    row = next(row for row in output if row["code"] == "CN_T10")
    assert row["value"] == 2.82 and len(row["history"]) == 1
