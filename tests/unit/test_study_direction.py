"""Direction tracking freezes a precise report plan instead of current research."""

from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.errors import NotFoundError, ValidationError_
from backend.investment import (
    study,
    study_repository as repo,
    strategy_memory as memory,
)
from backend.investment.study_schemas import CreateStudyRequest

NOW = datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture(autouse=True)
def isolate_direction_evidence_from_live_prices(monkeypatch):
    """These fixtures authenticate old research; live pricing has integration tests."""
    monkeypatch.setattr(
        repo,
        "_refresh_origin_plan",
        AsyncMock(
            side_effect=lambda _conn, selected, *_args, **_kwargs: deepcopy(selected)
        ),
    )


def request(**changes):
    return {
        "originType": "DAILY_DIRECTION",
        "reportRunId": str(uuid4()),
        "valueDirectionKey": "healthcare-recovery",
        "subjectKind": "nav",
        "subjectId": "000001",
        "planTrack": "LEFT_VALUE",
        "horizonDays": 30,
        **changes,
    }


def plan(**changes):
    return {
        "methodVersion": "actionable-zones-v3",
        "subjectKind": "nav",
        "subjectId": "000001",
        "fundCode": "000001",
        "market": "FUND_CN",
        "label": "实际基金",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "asOf": "2026-10-16",
        "retrievedAt": NOW.isoformat(),
        "sourceUri": "https://fund.eastmoney.com/000001.html",
        "currentPrice": "1.0000",
        "volatilityMethod": "NAV_ABS_CHANGE_STD_V1",
        "indicators": {"navVolatility14": 0.012, "atr14": None},
        "selectedTrack": "LEFT_VALUE",
        "tracks": [
            {
                "track": "LEFT_VALUE",
                "state": "CANDIDATE",
                "priceState": "READY",
                "entryZoneMin": "0.9904",
                "entryZoneMax": "1.0000",
                "stopLossPrice": "0.9664",
                "takeProfitT1": "1.0300",
                "takeProfitT2": "1.0540",
                "reasons": [],
            }
        ],
        **changes,
    }


def report(**changes):
    return {
        "asOf": NOW.isoformat(),
        "valueDirections": [
            {
                "key": "healthcare-recovery",
                "title": "医药景气修复",
                "thesis": "多条行业新闻与信用改善共振",
                "signal": "LEFT_VALUE",
                "sourceIds": ["s1", "s2"],
                "macroCodes": ["M1_YOY"],
                "watchFor": ["订单和盈利兑现"],
                "plans": [plan()],
                "private": "never freeze",
            }
        ],
        "sources": [
            {"id": "s1", "url": "https://www.stats.gov.cn/one", "quote": "original"},
            {"id": "s2", "url": "https://www.gov.cn/two"},
            {"id": "unrelated", "private": "omit"},
        ],
        **changes,
    }


def test_direction_request_has_only_server_resolvable_identity():
    parsed = CreateStudyRequest.model_validate(request())
    assert (
        parsed.model_dump(by_alias=True, mode="json")["valueDirectionKey"]
        == "healthcare-recovery"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": {"fundCode": "999999", "fundMarket": "FUND_CN"}},
        {"eventId": "news"},
        {"scope": None},
        {"eventId": None},
        {"actionablePlan": plan()},
        {"thesis": "caller price"},
        {"subjectId": None},
        {"valueDirectionKey": "PRIVATE Title"},
        {"valueDirectionKey": "a" * 65},
        {"planTrack": None},
        {"reportRunId": None},
    ],
)
def test_direction_request_rejects_client_context_or_missing_identity(changes):
    with pytest.raises(ValidationError):
        CreateStudyRequest.model_validate(request(**changes))


@pytest.mark.asyncio
async def test_report_direction_origin_keeps_exact_plan_and_only_related_evidence():
    conn = AsyncMock()
    original = report()
    run = uuid4()
    conn.fetchrow.return_value = {
        "run_id": run,
        "recorded_at": NOW,
        "payload": original,
    }
    origin = await repo.daily_direction_origin(
        conn, run, "healthcare-recovery", "nav", "000001", "LEFT_VALUE", NOW
    )
    snapshot = study.freeze_hypothesis(
        origin, now=NOW, scope={"fundCode": "000001", "fundMarket": "FUND_CN"}
    )
    assert snapshot["actionablePlan"] == plan()
    assert snapshot["valueDirection"]["key"] == "healthcare-recovery"
    assert snapshot["valueDirection"]["macroCodes"] == ["M1_YOY"]
    assert snapshot["origin"]["valueDirectionKey"] == "healthcare-recovery"
    assert snapshot["origin"]["reportRunId"] == str(run)
    assert snapshot["title"] == "医药景气修复 · 实际基金"
    assert snapshot["thesis"] == "多条行业新闻与信用改善共振"
    assert [row["id"] for row in snapshot["sources"]] == ["s1", "s2"]
    assert "never freeze" not in str(snapshot) and "unrelated" not in str(snapshot)
    original["valueDirections"][0]["plans"][0]["tracks"][0]["entryZoneMin"] = "9"
    assert snapshot["actionablePlan"]["tracks"][0]["entryZoneMin"] == "0.9904"


@pytest.mark.asyncio
async def test_direction_catalysts_and_risks_remain_frozen_with_original_sources():
    original = report()
    original["valueDirections"][0].update(
        catalysts=[{"summary": "行业经营与资金意向共振。", "sourceIds": ["s1", "s2"]}],
        risks=[{"summary": "相关个股解禁可能形成短期压力。", "sourceIds": ["s2"]}],
    )
    conn = AsyncMock()
    run = uuid4()
    conn.fetchrow.return_value = {
        "run_id": run,
        "recorded_at": NOW,
        "payload": original,
    }
    origin = await repo.daily_direction_origin(
        conn, run, "healthcare-recovery", "nav", "000001", "LEFT_VALUE", NOW
    )
    snapshot = study.freeze_hypothesis(origin, now=NOW + timedelta(days=1))
    assert snapshot["valueDirection"]["catalysts"] == [
        {"summary": "行业经营与资金意向共振。", "sourceIds": ["s1", "s2"]}
    ]
    assert snapshot["valueDirection"]["risks"] == [
        {"summary": "相关个股解禁可能形成短期压力。", "sourceIds": ["s2"]}
    ]
    original["valueDirections"][0]["catalysts"][0]["summary"] = "后续资料改写"
    origin["valueDirection"]["risks"][0]["sourceIds"].append("later-source")
    assert (
        snapshot["valueDirection"]["catalysts"][0]["summary"]
        == "行业经营与资金意向共振。"
    )
    assert snapshot["valueDirection"]["risks"][0]["sourceIds"] == ["s2"]


@pytest.mark.asyncio
async def test_pure_quantitative_direction_freezes_original_scan_without_fake_news():
    original = report()
    evidence = {
        **plan(historyQuality="READY"),
        "signals": [
            {
                "track": "LEFT_VALUE",
                "triggerState": "CANDIDATE",
                "inEntryZone": True,
                "distanceToEntryPct": 0,
            }
        ],
        "private": "NEVER_COPY",
    }
    original["valueDirections"][0].update(
        sourceIds=[],
        macroCodes=[],
        quantitativeFundCodes=["000001"],
        quantitativeEvidence=[evidence],
    )
    conn = AsyncMock()
    run = uuid4()
    conn.fetchrow.return_value = {
        "run_id": run,
        "recorded_at": NOW,
        "payload": original,
    }
    origin = await repo.daily_direction_origin(
        conn, run, "healthcare-recovery", "nav", "000001", "LEFT_VALUE", NOW
    )
    snapshot = study.freeze_hypothesis(origin, now=NOW + timedelta(days=1))
    assert snapshot["sources"] == []
    assert snapshot["valueDirection"]["quantitativeFundCodes"] == ["000001"]
    assert (
        snapshot["valueDirection"]["quantitativeEvidence"][0]["currentPrice"]
        == "1.0000"
    )
    assert "NEVER_COPY" not in str(snapshot)
    evidence["currentPrice"] = "9"
    assert (
        snapshot["valueDirection"]["quantitativeEvidence"][0]["currentPrice"]
        == "1.0000"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_ids", [["s1"], []])
@pytest.mark.parametrize("complete_prices", [True, False])
async def test_generated_scored_direction_can_be_frozen_from_its_original_report(
    source_ids,
    complete_prices,
):
    from backend.macro.daily_research import validate_report
    from test_daily_research import report as raw_report
    from test_value_directions import direction, quantitative_packet

    packet = {
        **quantitative_packet(),
        "directions": [{"id": "a-share:broad"}],
        "discoveryUniverse": [],
    }
    if complete_prices:
        for item in [
            *packet["discoveryActionableZones"],
            *packet["quantitativeScan"]["items"],
        ]:
            item["tracks"][0].update(
                priceState="READY", stopLossPrice="1.0000", takeProfitT2="1.6000"
            )
    original = validate_report(
        {
            **raw_report(),
            "macroCycle": "行业与真实净值研究。",
            "actionablePlans": [],
            "valueDirections": [
                direction(
                    sourceIds=source_ids,
                    macroCodes=[],
                    quantitativeFundCodes=["005112"],
                )
            ],
        },
        packet,
        {},
        completed_at=NOW,
    )
    selected = deepcopy(original["valueDirections"][0]["plans"][0])
    conn = AsyncMock()
    run = uuid4()
    conn.fetchrow.return_value = {
        "run_id": run,
        "recorded_at": NOW,
        "payload": original,
    }

    origin = await repo.daily_direction_origin(
        conn,
        run,
        "healthcare-recovery",
        "nav",
        "005112",
        "LEFT_VALUE",
        NOW + timedelta(days=30),
    )
    snapshot = study.freeze_hypothesis(origin, now=NOW + timedelta(days=30))

    assert snapshot["actionablePlan"] == selected
    if complete_prices:
        assert snapshot["actionablePlan"]["tracks"][0]["opportunityScore"] > 0
    assert snapshot["actionablePlan"]["tracks"][0]["entryZoneMin"] == "1.1000"
    assert (
        snapshot["valueDirection"]["quantitativeEvidence"][0]["currentPrice"]
        == "1.2500"
    )
    assert snapshot["origin"]["reportRunId"] == str(run)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "wrong_price",
        "future",
        "missing",
        "unrelated",
        "wrong_entry",
        "wrong_stop",
        "wrong_target",
        "wrong_state",
    ],
)
async def test_direction_freeze_rejects_changed_or_unrelated_quantitative_facts(
    problem,
):
    original = report()
    proof = plan(historyQuality="READY")
    original["valueDirections"][0].update(
        quantitativeFundCodes=["000001"], quantitativeEvidence=[proof]
    )
    if problem == "wrong_price":
        proof["currentPrice"] = "2"
    elif problem == "future":
        proof["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif problem == "missing":
        original["valueDirections"][0]["quantitativeEvidence"] = []
    elif problem.startswith("wrong_"):
        field, value = {
            "wrong_entry": ("entryZoneMin", "0.9800"),
            "wrong_stop": ("stopLossPrice", "0.9500"),
            "wrong_target": ("takeProfitT1", "1.0400"),
            "wrong_state": ("state", "WATCH"),
        }[problem]
        proof["tracks"] = deepcopy(proof["tracks"])
        proof["tracks"][0][field] = value
    else:
        proof["subjectId"] = "another"
    conn = AsyncMock()
    run = uuid4()
    conn.fetchrow.return_value = {
        "run_id": run,
        "recorded_at": NOW,
        "payload": original,
    }
    with pytest.raises(ValidationError_):
        await repo.daily_direction_origin(
            conn, run, "healthcare-recovery", "nav", "000001", "LEFT_VALUE", NOW
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "direction",
        "subject",
        "track",
        "duplicate_direction",
        "duplicate_plan",
        "future",
        "bad_price",
        "missing_title",
        "missing_thesis",
        "future_source",
    ],
)
async def test_report_direction_rejects_missing_ambiguous_or_unavailable_plan(problem):
    conn = AsyncMock()
    payload = report()
    direction = payload["valueDirections"][0]
    if problem == "direction":
        direction["key"] = "other"
    elif problem == "subject":
        direction["plans"][0]["subjectId"] = "999999"
    elif problem == "track":
        direction["plans"][0]["selectedTrack"] = "RIGHT_TREND"
    elif problem == "duplicate_direction":
        payload["valueDirections"].append(deepcopy(direction))
    elif problem == "duplicate_plan":
        direction["plans"].append(deepcopy(direction["plans"][0]))
    elif problem == "future":
        payload["asOf"] = (NOW + timedelta(seconds=1)).isoformat()
    elif problem == "bad_price":
        direction["plans"][0]["tracks"][0]["stopLossPrice"] = "2"
    elif problem == "missing_title":
        direction.pop("title")
    elif problem == "missing_thesis":
        direction.pop("thesis")
    elif problem == "future_source":
        payload["sources"][0]["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    conn.fetchrow.return_value = (
        None
        if problem == "missing"
        else {"run_id": uuid4(), "recorded_at": NOW, "payload": payload}
    )
    with pytest.raises((NotFoundError, ValidationError_)):
        await repo.daily_direction_origin(
            conn, uuid4(), "healthcare-recovery", "nav", "000001", "LEFT_VALUE", NOW
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["plan_date", "plan_retrieval", "source_retrieval"])
async def test_historical_direction_rejects_facts_unavailable_at_report_time(problem):
    payload = report()
    selected = payload["valueDirections"][0]["plans"][0]
    if problem == "plan_date":
        selected["asOf"] = (NOW + timedelta(days=1)).date().isoformat()
    elif problem == "plan_retrieval":
        selected["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    else:
        payload["sources"][0]["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
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
            NOW + timedelta(days=30),
        )


def test_discovery_memory_uses_frozen_key_and_exports_only_counts():
    assert (
        memory.theme_for_snapshot(
            {
                "valueDirection": {
                    "key": "healthcare-recovery",
                    "title": "PRIVATE title",
                },
                "scope": {"themeCode": "AI_MEMORY_HBM"},
            }
        )
        == "DISCOVERY:healthcare-recovery"
    )
    tenant, actor = uuid4(), uuid4()
    rows = memory.aggregate_reviews(
        [
            {
                "id": str(uuid4()),
                "track_id": str(uuid4()),
                "tenant_id": tenant,
                "user_id": actor,
                "theme_code": "DISCOVERY:healthcare-recovery",
                "verdict": "REFUTED",
                "has_lessons": True,
                "error_categories": ["TIMING"],
                "created_at": NOW,
                "lessons": "PRIVATE note",
            }
        ],
        tenant,
        actor,
        "DISCOVERY:healthcare-recovery",
        NOW,
    )
    public = memory.public_memory(rows, NOW)
    assert public[0]["themeCode"] == "DISCOVERY:healthcare-recovery"
    assert public[0]["sampleCount"] == 1
    assert public[0]["supportRate"] == "0" and public[0]["winRate"] is None
    assert "PRIVATE" not in str(public)


@pytest.mark.parametrize(
    "key", ["PRIVATE Title", "../escape", "A_KEY", "a" * 65, None, ["bad"]]
)
def test_memory_does_not_promote_private_or_malformed_direction_keys(key):
    assert memory.theme_for_snapshot({"valueDirection": {"key": key}}) == "UNCLASSIFIED"
