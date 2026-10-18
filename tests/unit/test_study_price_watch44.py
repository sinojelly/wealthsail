"""Frozen price lines and visible market facts must not turn watching into a trade."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from importlib import import_module
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 19, 14, 40, tzinfo=ZoneInfo("Asia/Shanghai"))


def plan():
    return {
        "subjectKind": "nav",
        "subjectId": "000051",
        "fundCode": "000051",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "asOf": "2026-10-16",
        "selectedTrack": "LEFT_VALUE",
        "currentPrice": "1.20",
        "tracks": [
            {
                "track": "LEFT_VALUE",
                "state": "CANDIDATE",
                "priceState": "READY",
                "entryZoneMin": "1.05",
                "entryZoneMax": "1.10",
                "stopLossPrice": "0.95",
                "takeProfitT1": "1.40",
                "takeProfitT2": "1.60",
            }
        ],
    }


def quote(base_value="1.20", **updates):
    return {
        "value": base_value,
        "subjectKind": "nav",
        "subjectId": "000051",
        "fundCode": "000051",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY/share",
        "quality": "READY",
        "usable": True,
        "asOf": "2026-10-16",
        "retrievedAt": NOW.isoformat(),
        "sourceUri": "https://fund.eastmoney.com/000051.html",
        **updates,
    }


def module():
    return import_module("backend.investment.study_price_watch")


@pytest.mark.parametrize(
    "value,holding,expected",
    [
        ("0.94", "CONFIRMED", "STOP_LOSS"),
        ("1.40", "CONFIRMED", "TAKE_PROFIT"),
        ("1.05", "CONFIRMED", "ENTRY_ZONE"),
        ("1.10", "UNKNOWN", "ENTRY_ZONE"),
        ("1.20", "CONFIRMED", "HOLDING"),
        ("1.20", "UNKNOWN", "OBSERVING"),
    ],
)
def test_current_price_compares_to_original_lines_and_requires_real_holding(
    value, holding, expected
):
    frozen = plan()
    before = deepcopy(frozen)
    result = module().price_watch(
        frozen, confirmed=quote(value), holding={"state": holding}, now=NOW
    )
    assert result["state"] == expected
    assert result["basis"] == "CONFIRMED_NAV"
    assert result["frozenPlan"] == before and frozen == before


@pytest.mark.parametrize(
    "change",
    [
        {"subjectId": "000216"},
        {"fundCode": "000216"},
        {"priceBasis": "PROVIDER_RETURN_INDEX"},
        {"unit": "points"},
        {"asOf": "2026-10-20"},
        {"asOf": "2026-10-15"},
        {"quality": "STALE", "usable": False},
        {"value": "NaN"},
        {"retrievedAt": (NOW + timedelta(seconds=1)).isoformat()},
    ],
)
def test_unusable_prices_never_create_a_frozen_line_hit(change):
    result = module().price_watch(plan(), confirmed=quote("0.90", **change), now=NOW)
    assert result["state"] == "UNKNOWN" and result["basis"] is None


def test_1430_estimate_is_distinct_and_only_current_before_cutoff():
    estimate = quote(
        "0.90",
        asOf=NOW.replace(hour=14, minute=30).isoformat(),
        checkedAt=NOW.replace(hour=14, minute=31).isoformat(),
        quality="ESTIMATED",
    )
    result = module().price_watch(plan(), confirmed=quote(), estimate=estimate, now=NOW)
    assert result["state"] == "STOP_LOSS" and result["basis"] == "ESTIMATE_1430"
    assert result["confirmedNav"]["value"] == "1.20"
    assert result["estimate1430"]["value"] == "0.90"
    after = module().price_watch(
        plan(), confirmed=quote(), estimate=estimate, now=NOW.replace(hour=15, minute=1)
    )
    assert after["state"] == "OBSERVING" and after["basis"] == "CONFIRMED_NAV"
    assert after["estimate1430"]["usable"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"asOf": (NOW - timedelta(days=1)).isoformat()},
        {"checkedAt": (NOW + timedelta(minutes=1)).isoformat()},
        {"asOf": NOW.replace(hour=13).isoformat()},
        {"usable": False},
    ],
)
def test_stale_or_future_estimate_does_not_override_confirmed_nav(change):
    estimate = quote(
        "0.90",
        **{
            "quality": "ESTIMATED",
            "asOf": NOW.replace(minute=30).isoformat(),
            "checkedAt": NOW.isoformat(),
            **change,
        },
    )
    result = module().price_watch(
        plan(), confirmed=quote("1.07"), estimate=estimate, now=NOW
    )
    assert result["state"] == "ENTRY_ZONE" and result["basis"] == "CONFIRMED_NAV"


def test_no_market_fact_is_unknown_even_with_a_holding_and_invalid_track_is_not_guessed():
    assert (
        module().price_watch(plan(), holding={"state": "CONFIRMED"}, now=NOW)["state"]
        == "UNKNOWN"
    )
    frozen = plan()
    frozen["selectedTrack"] = "RIGHT_TREND"
    assert (
        module().price_watch(frozen, confirmed=quote("0.90"), now=NOW)["state"]
        == "UNKNOWN"
    )


@pytest.mark.asyncio
async def test_list_batch_reads_are_actor_scoped_and_do_not_use_old_version_checks():
    actor, tenant, track, version, old = [uuid4() for _ in range(5)]
    frozen = plan()

    class Conn:
        def __init__(self):
            self.queries = []

        async def fetch(self, sql, *args):
            self.queries.append((sql, args))
            if "study_versions" in sql:
                assert tenant in args and actor in args
                return [
                    {
                        "track_id": track,
                        "id": version,
                        "generation": 1,
                        "created_at": NOW - timedelta(days=1),
                        "snapshot": {"actionablePlan": frozen},
                    }
                ]
            if "fund_research_nav_histories" in sql:
                return []
            if "alpha_opportunity_runs" in sql:
                assert tenant in args and actor in args
                check = {
                    "trackId": str(track),
                    "versionId": str(old),
                    "price": "0.90",
                    "asOf": NOW.replace(minute=30).isoformat(),
                    "state": "STOP_LOSS",
                }
                return [
                    {
                        "tenant_id": tenant,
                        "user_id": actor,
                        "run_date": NOW.date(),
                        "finished_at": NOW,
                        "result": {"checks": [check]},
                    }
                ]
            return []

    conn = Conn()
    items = [{"id": str(track), "generation": 1, "scope": {"fundCode": "000051"}}]
    result = await module().enrich_tracks(conn, tenant, actor, items, NOW)
    watch = result[0]["priceWatch"]
    assert watch["frozenPlan"] == frozen
    assert watch["estimate1430"] is None and watch["state"] == "UNKNOWN"
    assert (
        len(conn.queries) <= 6
    )  # fixed bulk reads, never per-item estimate/provider calls


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["list", "detail"])
async def test_both_service_surfaces_return_the_frozen_price_watch(
    monkeypatch, surface
):
    from unittest.mock import AsyncMock
    from backend.investment import study_service as service, study_repository as repo

    actor, tenant, track, version = [uuid4() for _ in range(4)]
    item = {"id": str(track), "generation": 1, "scope": {"fundCode": "000051"}}
    saved = {
        "id": str(version),
        "track_id": track,
        "generation": 1,
        "created_at": NOW - timedelta(days=1),
        "snapshot": {"actionablePlan": plan(), "scope": {}},
    }
    conn = AsyncMock()
    conn.fetch.side_effect = (
        lambda sql, *args: [saved] if "study_versions" in sql else []
    )
    conn.fetchrow.return_value = None
    monkeypatch.setattr(
        repo, "list_tracks", AsyncMock(return_value={"items": [item], "total": 1})
    )
    monkeypatch.setattr(
        repo, "detail_rows", AsyncMock(return_value={"track": item, "version": saved})
    )
    if surface == "list":
        data = await service.list_studies(conn, tenant, actor, NOW)
        result = data["items"][0]["priceWatch"]
    else:
        data = await service.get_detail(
            conn, tenant, actor, track, NOW, observations=[]
        )
        result = data["priceWatch"]
        assert data["track"]["priceWatch"] == result
    assert result["frozenPlan"]["tracks"][0]["entryZoneMin"] == "1.05"
    assert result["state"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_pause_resume_generation_preserves_the_latest_frozen_price_version():
    track, version, tenant, actor = [uuid4() for _ in range(4)]
    item = {"id": str(track), "generation": 3, "scope": {"fundCode": "000051"}}
    saved = {
        "id": str(version),
        "track_id": track,
        "generation": 1,
        "created_at": NOW - timedelta(days=1),
        "snapshot": {"actionablePlan": plan()},
    }
    from unittest.mock import AsyncMock

    conn = AsyncMock()
    conn.fetch.return_value = []
    result = await module().enrich_tracks(
        conn, tenant, actor, [item], NOW, versions={str(track): saved}
    )
    assert result[0]["priceWatch"]["frozenPlan"]["currentPrice"] == "1.20"


def test_persisted_estimate_requires_source_and_matching_run_and_plan():
    tenant, actor, track, version = [uuid4() for _ in range(4)]
    frozen = plan()
    check = {
        "trackId": str(track),
        "versionId": str(version),
        "selectedTrack": "LEFT_VALUE",
        "state": "STOP_LOSS",
        "priceBasis": "UNIT_NAV",
        "unit": "CNY",
        "price": "0.90",
        "asOf": NOW.replace(minute=30).isoformat(),
        "planAsOf": frozen["asOf"],
        **{
            key: frozen["tracks"][0][key]
            for key in ("entryZoneMin", "entryZoneMax", "stopLossPrice", "takeProfitT1")
        },
    }
    row = {
        "tenant_id": tenant,
        "user_id": actor,
        "finished_at": NOW,
        "run_date": NOW.date(),
        "result": {"checks": [check]},
    }
    quote_without_source = module()._estimate(
        [row], tenant, actor, track, version, frozen, NOW
    )
    assert quote_without_source["usable"] is False
    check["marketSourceUri"] = "https://fund.example.org/estimate/000051"
    checked = module()._estimate([row], tenant, actor, track, version, frozen, NOW)
    assert (
        checked["usable"] is True and checked["sourceUri"] == check["marketSourceUri"]
    )
    row["run_date"] = NOW.date() - timedelta(days=1)
    assert module()._estimate([row], tenant, actor, track, version, frozen, NOW) is None


def test_1430_checker_preserves_actual_market_provenance_for_readback():
    from backend.jobs.alpha_opportunities import compare_frozen_price

    observation = quote(
        "1.07", quality="ESTIMATED", asOf=NOW.replace(minute=30).isoformat()
    )
    result = compare_frozen_price(plan(), observation, NOW)
    assert result["marketSourceUri"] == observation["sourceUri"]
    assert result["state"] == "IN_ENTRY_ZONE"


def test_current_holding_session_requires_complete_calendar_to_cutoff():
    old = {"calendar_date": date(2026, 10, 9), "is_trading_day": True}
    assert module()._current_session([old], NOW) is None
