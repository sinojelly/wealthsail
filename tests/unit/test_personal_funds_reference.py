"""Personal fund inventory stays independent of formal research eligibility."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.investment import personal_funds as service

NOW = datetime(2026, 11, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture(autouse=True)
def offline_nav_sync(monkeypatch):
    async def local_only(conn, codes, now, **kwargs):
        from backend.investment.live_fund_plans import _local_histories

        return {
            "histories": [row async for row in _local_histories(conn, codes, now)],
            "asOf": now,
            "refresh": {},
            "latestConfirmed": {},
        }

    monkeypatch.setattr(service, "ensure_nav_histories", local_only, raising=False)


def fund(code, relationship, **extra):
    return {
        "fundCode": code,
        "fundMarket": "FUND_CN",
        "label": code,
        "relationships": [relationship],
        "localNavEligible": True,
        **extra,
    }


def test_union_deduplicates_identity_without_promoting_observation():
    result = service.merge_fund_inventory(
        [
            fund("018344", "HELD"),
            fund("018344", "TRACKED"),
            fund("013477", "WATCHLIST"),
            fund("011035", "RESEARCH_MENTIONED"),
            fund("018344", "WATCHLIST", fundMarket="SSE"),
        ]
    )
    assert len(result) == 4
    held = next(row for row in result if row["key"] == "FUND_CN:018344")
    assert held["relationships"] == ["HELD", "TRACKED"]
    assert all(row["investmentEligible"] is False for row in result)


@pytest.mark.asyncio
async def test_local_reference_contains_unplanned_mentions_and_actual_full_window(
    monkeypatch,
):
    from tests.unit.test_live_fund_plans_v6 import history, LocalHistory

    h = history("011035")
    # This source proof is genuinely complete for the fixture, never synthesized.
    monkeypatch.setattr(
        service,
        "_inventory",
        AsyncMock(
            return_value=[
                fund("018344", "HELD"),
                fund("013477", "WATCHLIST"),
                fund("011035", "RESEARCH_MENTIONED"),
            ]
        ),
    )
    monkeypatch.setattr(service, "_persisted_estimates", AsyncMock(return_value={}))
    data = await service.personal_funds_reference(
        LocalHistory([h]),
        "tenant",
        "actor",
        NOW,
        include_research_mentions=True,
    )
    assert data["source"] == "LOCAL_DATABASE_ONLY"
    assert {row["fundCode"] for row in data["funds"]} == {"018344", "013477", "011035"}
    row = next(row for row in data["funds"] if row["fundCode"] == "011035")
    assert len(row["history"]["points"]) == 550
    assert row["history"]["summary"]["percentileMethod"] == "EMPIRICAL_MIDRANK_V1"
    assert {track["track"] for track in row["plan"]["tracks"]} == {
        "LEFT_VALUE",
        "RIGHT_TREND",
    }
    assert row["investmentEligible"] is False


@pytest.mark.asyncio
async def test_historical_current_reference_excludes_research_mentions(monkeypatch):
    from tests.unit.test_live_fund_plans_v6 import LocalHistory

    inventory = AsyncMock(return_value=[fund("013477", "WATCHLIST")])
    monkeypatch.setattr(service, "_inventory", inventory)
    monkeypatch.setattr(service, "_persisted_estimates", AsyncMock(return_value={}))
    data = await service.personal_funds_reference(
        LocalHistory(),
        "tenant",
        "actor",
        NOW,
        include_research_mentions=False,
    )
    assert inventory.await_args.kwargs["include_research_mentions"] is False
    assert data["funds"][0]["missingReasons"]
    assert data["funds"][0]["history"]["points"] == []


@pytest.mark.asyncio
async def test_unknown_and_foreign_watchlist_funds_remain_visible_without_using_cny_nav(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "list_watch_context",
        AsyncMock(return_value={"heldFunds": [], "suppressedFundKeys": []}),
    )
    monkeypatch.setattr(
        service.study_repository,
        "list_tracks",
        AsyncMock(return_value={"items": [], "total": 0}),
    )
    monkeypatch.setattr(service, "resolve_fund_scopes", AsyncMock(return_value=[]))

    class Local:
        async def fetch(self, sql, *args):
            if "identity.watchlist" in sql:
                assert args == ("actor", NOW)
                return [
                    {
                        "symbol": "000001",
                        "asset_name": "美元份额",
                        "asset_type": "FUND",
                    },
                    {
                        "symbol": "000002",
                        "asset_name": "未收录份额",
                        "asset_type": "FUND",
                    },
                ]
            if "research_fund_catalog" in sql:
                return [
                    {"fund_code": "000001", "fund_name": "美元份额", "currency": "USD"}
                ]
            raise AssertionError(sql)

    rows = await service._inventory(
        Local(), "tenant", "actor", NOW, include_research_mentions=False
    )
    assert len(rows) == 2
    assert all(
        row["relationships"] == ["WATCHLIST"] and row["localNavEligible"] is False
        for row in rows
    )


@pytest.mark.asyncio
async def test_manual_report_lookup_binds_actor_tenant_run_batch_and_all_visibility_clocks(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "list_watch_context",
        AsyncMock(return_value={"heldFunds": [], "suppressedFundKeys": []}),
    )
    monkeypatch.setattr(
        service.study_repository,
        "list_tracks",
        AsyncMock(return_value={"items": [], "total": 0}),
    )
    monkeypatch.setattr(service, "resolve_fund_scopes", AsyncMock(return_value=[]))

    class Local:
        async def fetch(self, sql, *args):
            return []

        async def fetchrow(self, sql, *args):
            assert args == (NOW, "tenant", "actor")
            for clause in [
                "q.tenant_id=$2",
                "q.user_id=$3",
                "q.report_run_id=r.run_id",
                "q.batch_id=r.source_batch_id",
                "q.started_at<=r.dispatched_at",
                "t.recorded_at<=q.completed_at",
                "q.completed_at<=$1",
                "r.run_mode='SCHEDULED' AND r.debug_request_id IS NULL",
            ]:
                assert clause in sql
            return None

    assert (
        await service._inventory(
            Local(), "tenant", "actor", NOW, include_research_mentions=True
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "future", "foreign-unit", "different-fund"])
async def test_internal_estimate_keeps_real_value_but_never_invents_public_source_or_usable_status(
    bad,
):
    from datetime import timedelta
    from decimal import Decimal

    row = {
        "fund_code": "018344",
        "value": Decimal("1.2345"),
        "unit": "CNY/份",
        "as_of": NOW - timedelta(minutes=5),
        "observed_at": NOW - timedelta(minutes=4),
        "metadata": {"confirmedNav": "1.2"},
        "origin_uri": "internal:worker:market",
        "is_trading_day": True,
    }
    if bad == "future":
        row["observed_at"] = NOW + timedelta(seconds=1)
    elif bad == "foreign-unit":
        row["unit"] = "USD"
    elif bad == "different-fund":
        row["fund_code"] = "000001"

    class Local:
        async def fetch(self, sql, *args):
            return [row]

    data = await service._persisted_estimates(Local(), ["018344"], NOW)
    if bad:
        assert data == {}
    else:
        quote = data["018344"]
        assert quote["value"] == "1.2345" and quote["changePct"] == "2.88"
        assert quote["sourceVerification"] == "LOCAL_RECORD_ONLY"
        assert quote["sourceUri"] is None and quote["usable"] is False
        assert quote["retrievedAt"] == row["observed_at"].isoformat()


@pytest.mark.asyncio
async def test_foreign_currency_cache_cannot_be_relabelled_as_cny_chart_or_confirmed_nav(
    monkeypatch,
):
    from tests.unit.test_live_fund_plans_v6 import LocalHistory, history

    bad = {
        **history("011035"),
        "currency": "USD",
        "unit": "USD/share",
        "fundName": "美元份额",
    }
    monkeypatch.setattr(
        service, "_inventory", AsyncMock(return_value=[fund("011035", "HELD")])
    )
    monkeypatch.setattr(service, "_persisted_estimates", AsyncMock(return_value={}))
    result = await service.personal_funds_reference(
        LocalHistory([bad]), "tenant", "actor", NOW
    )
    assert result["funds"][0]["history"]["points"] == []
    assert result["funds"][0]["confirmedNav"] is None


@pytest.mark.asyncio
async def test_personal_query_reprices_from_newly_persisted_history_for_every_relationship(
    monkeypatch,
):
    from tests.unit.test_on_demand_nav_sync import history, NOW as current
    from tests.unit.test_live_fund_plans_v6 import LocalHistory

    rows = [
        fund("011035", relationship)
        for relationship in ("HELD", "TRACKED", "WATCHLIST", "RESEARCH_MENTIONED")
    ]
    monkeypatch.setattr(
        service,
        "_inventory",
        AsyncMock(return_value=service.merge_fund_inventory(rows)),
    )
    fresh = history(count=550, lag=0)
    repaired = AsyncMock(
        return_value={
            "histories": [fresh],
            "asOf": current,
            "refresh": {"011035": {"state": "READY"}},
            "latestConfirmed": {},
        }
    )
    monkeypatch.setattr(service, "ensure_nav_histories", repaired)
    monkeypatch.setattr(service, "_persisted_estimates", AsyncMock(return_value={}))
    data = await service.personal_funds_reference(
        LocalHistory(), "tenant", "actor", current
    )
    row = data["funds"][0]
    assert repaired.await_args.args[1] == ["011035"]
    assert (
        row["history"]["asOf"]
        == row["actionableZones"]["asOf"]
        == current.date().isoformat()
    )
    from decimal import Decimal

    assert Decimal(row["confirmedNav"]["value"]) == Decimal(
        row["actionableZones"]["currentPrice"]
    )
    assert row["plan"] == row["actionableZones"]
    assert row["actionableZones"]["historySampleCount"] == 500
    assert row["actionableZones"]["priceRefresh"]["state"] == "READY"
    assert row["history"]["refresh"]["state"] == "READY"
    assert row["investmentEligible"] is False
