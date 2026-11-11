"""Research insights retain current real NAV coordinates regardless of audit status."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from backend.investment.live_fund_plans import refresh_report_fund_plans
from tests.unit.test_live_fund_plans_v6 import LocalHistory, NOW, history, old_plan


@pytest.mark.asyncio
async def test_observation_direction_and_body_mention_get_real_price_plans_and_charts():
    report = {
        "valueDirections": [{
            "key": "hardware", "qualificationStatus": "OBSERVATION_ONLY",
            "auditStatus": "UNAUDITED_SCOPE", "plans": [old_plan()],
            "fundPlans": [old_plan("007466")],
        }],
        "mentionedFunds": [{
            "fundCode": "011035", "subjectKind": "nav", "subjectId": "011035",
            "market": "FUND_CN", "label": "正文基金", "directionKeys": ["hardware"],
        }],
    }
    saved = deepcopy(report)
    result = await refresh_report_fund_plans(
        LocalHistory([history(code) for code in ("008298", "007466", "011035")]),
        report, NOW, include_series=True,
    )
    direction = result["valueDirections"][0]
    assert direction["auditStatus"] == "UNAUDITED_SCOPE"
    for fund in [*direction["plans"], *direction["fundPlans"], *result["mentionedFunds"]]:
        zones = fund["actionableZones"]
        assert zones["methodVersion"] == "actionable-zones-v6"
        assert zones["currentPrice"] != "99.99"
        assert zones["tracks"][0]["entryZoneMin"] is not None
        assert zones["series"]["points"][-1]["date"] == zones["asOf"]
        assert zones["buyZone"]["min"] is not None
        assert zones["sellZone"]["max"] is not None
        assert zones["stopLoss"] is not None and len(zones["targets"]) == 2
        assert fund["confirmedNav"]["value"] == zones["currentPrice"]
        assert fund["confirmedNav"]["asOf"] == zones["asOf"]
        assert zones["pricePosition"] == fund["pricePosition"]
    assert result["mentionedFunds"][0]["directionKeys"] == ["hardware"]
    assert report == saved


@pytest.mark.asyncio
async def test_authenticated_report_repairs_every_direction_mention_and_quant_fund(monkeypatch):
    prepared_at = NOW + timedelta(seconds=2)
    codes = ["008298", "007466", "011035"]
    ensure = AsyncMock(return_value={
        "asOf": prepared_at, "histories": [history(code) for code in codes],
        "refresh": {code: {"state": "READY"} for code in codes}, "latestConfirmed": {},
    })
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", ensure)
    result = await refresh_report_fund_plans(
        conn := LocalHistory(), {
            "valueDirections": [{"plans": [old_plan()], "qualificationStatus": "PENDING_REVIEW"}],
            "mentionedFunds": [{
                "fundCode": "007466", "subjectKind": "nav", "subjectId": "007466", "market": "FUND_CN",
            }],
            "quantitativeScan": {"items": [old_plan("011035")]},
        }, NOW, tenant_id="tenant", user_id="actor", include_series=True,
    )
    assert set(ensure.await_args.args[1]) == set(codes)
    assert ensure.await_args.args[0] is conn
    assert ensure.await_args.kwargs == {"tenant_id": "tenant", "user_id": "actor", "provider_budget": 3}
    for plan in [result["valueDirections"][0]["plans"][0], result["mentionedFunds"][0], result["quantitativeScan"]["items"][0]]:
        assert plan["actionableZones"]["navSync"] == {"state": "READY"}
        assert plan["actionableZones"]["priceRefresh"]["calculatedAt"] == prepared_at.isoformat()
        assert plan["actionableZones"]["series"]["points"]


@pytest.mark.asyncio
async def test_no_history_keeps_fund_reference_without_inventing_price():
    result = await refresh_report_fund_plans(LocalHistory(), {
        "mentionedFunds": [{
            "fundCode": "011035", "subjectKind": "nav", "subjectId": "011035", "market": "FUND_CN",
        }],
    }, NOW, include_series=True)
    mention = result["mentionedFunds"][0]
    assert mention["fundCode"] == "011035"
    assert mention["actionableZones"]["currentPrice"] is None
    assert mention["actionableZones"]["buyZone"] == {"min": None, "max": None}
    assert mention["actionableZones"]["series"]["points"] == []


@pytest.mark.asyncio
async def test_saved_report_read_reprices_all_local_references_without_provider_work(monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("Saved reports must not wait for external NAV"))
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", forbidden)
    monkeypatch.setattr("backend.funds.latest_quotes.latest_quote_cache.get", forbidden)
    monkeypatch.setattr("backend.funds.nav_history.fetch_on_demand_nav_history", forbidden)
    codes = ["008298", "007466", "011035", "001234"]
    report = {
        "valueDirections": [{"auditStatus": "UNAUDITED_SCOPE", "plans": [old_plan(codes[0])]}],
        "researchTopics": [{"key": "memory", "plans": [old_plan(codes[1])]}],
        "mentionedFunds": [old_plan(codes[2])],
        "quantitativeScan": {"items": [old_plan(codes[3])]},
    }
    before = deepcopy(report)

    result = await refresh_report_fund_plans(
        LocalHistory([history(code) for code in codes]), report, NOW,
        tenant_id="tenant", user_id="actor", include_series=True, repair_nav=False,
    )

    forbidden.assert_not_awaited()
    assert result["valueDirections"][0]["auditStatus"] == "UNAUDITED_SCOPE"
    rows = [
        result["valueDirections"][0]["plans"][0], result["researchTopics"][0]["plans"][0],
        result["mentionedFunds"][0], result["quantitativeScan"]["items"][0],
    ]
    assert [row["fundCode"] for row in rows] == codes
    for row in rows:
        assert row["currentPrice"] == "1.5000"
        assert row["asOf"] == "2026-10-27"
        assert row["actionableZones"]["buyZone"]["min"] is not None
        assert row["actionableZones"]["sellZone"]["max"] is not None
        assert len(row["series"]["points"]) == 500
    assert report == before


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [timedelta(minutes=1), timedelta(days=11)])
@pytest.mark.parametrize("invalid_local", [False, True])
async def test_saved_server_curve_remains_real_reference_when_local_cohort_is_missing(monkeypatch, age, invalid_local):
    archived = await refresh_report_fund_plans(
        LocalHistory([history()]), {"valueDirections": [{"plans": [old_plan()]}]},
        NOW, include_series=True,
    )
    before = deepcopy(archived)
    saved = archived["valueDirections"][0]["plans"][0]
    forbidden = AsyncMock(side_effect=AssertionError("No external work on saved report reads"))
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", forbidden)
    rows = []
    if invalid_local:
        malformed = history()
        malformed["sourceUri"] = "https://unverified.example/nav"
        rows.append(malformed)

    result = await refresh_report_fund_plans(
        LocalHistory(rows), archived, NOW + age, tenant_id="tenant", user_id="actor",
        include_series=True, repair_nav=False,
    )

    current = result["valueDirections"][0]["plans"][0]
    assert current["currentPrice"] == saved["currentPrice"]
    assert current["asOf"] == saved["asOf"]
    assert current["retrievedAt"] == saved["retrievedAt"]
    assert current["series"]["points"] == saved["series"]["points"]
    for track, old_track in zip(current["tracks"], saved["tracks"]):
        for field in ("entryZoneMin", "entryZoneMax", "stopLossPrice", "takeProfitT1", "takeProfitT2"):
            assert track[field] == old_track[field]
        assert track["entryReady"] is False
    assert current["priceRefresh"]["calculatedAt"] == (NOW + age).isoformat()
    assert current["historyQuality"] == "PARTIAL"
    assert archived == before
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_curve_roundtrip_keeps_disclosed_return_on_distribution_day():
    source = history()
    source["points"][-1]["returnPct"] = "1.25"
    archived = await refresh_report_fund_plans(
        LocalHistory([source]), {"actionablePlans": [old_plan()]}, NOW, include_series=True,
    )
    before = deepcopy(archived)
    saved = archived["actionablePlans"][0]
    assert saved["currentChangePct"] == saved["confirmedNav"]["changePct"] == "1.25"

    reread = await refresh_report_fund_plans(
        LocalHistory(), archived, NOW + timedelta(minutes=1), include_series=True, repair_nav=False,
    )

    current = reread["actionablePlans"][0]
    assert current["currentPrice"] == saved["currentPrice"]
    assert current["asOf"] == saved["asOf"]
    assert current["series"]["points"] == saved["series"]["points"]
    assert current["currentChangePct"] == current["confirmedNav"]["changePct"] == "1.25"
    assert archived == before


@pytest.mark.asyncio
async def test_minimal_custom_series_cannot_create_a_confirmed_nav_from_a_declared_url():
    custom = {
        "subjectKind": "nav", "subjectId": "008298", "fundCode": "008298",
        "market": "FUND_CN", "priceBasis": "UNIT_NAV", "unit": "CNY/份",
        "series": {
            "fundCode": "008298", "priceBasis": "UNIT_NAV", "unit": "CNY/份",
            "sourceUri": "https://fund.eastmoney.com/008298.html",
            "retrievedAt": NOW.isoformat(), "asOf": "2026-10-27",
            "points": [{"date": "2026-10-27", "value": "999"}],
        },
    }
    result = await refresh_report_fund_plans(
        LocalHistory(), {"actionablePlans": [custom]}, NOW, include_series=True, repair_nav=False,
    )
    current = result["actionablePlans"][0]
    assert current["currentPrice"] is None and current["confirmedNav"] is None
    assert current["historyQuality"] == "UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["method", "tracks", "price", "as_of", "source", "retrieved", "available", "calculated", "sample_count"])
async def test_saved_curve_requires_the_original_server_plan_to_match_its_source(invalid):
    archived = await refresh_report_fund_plans(
        LocalHistory([history()]), {"actionablePlans": [old_plan()]}, NOW, include_series=True,
    )
    saved = archived["actionablePlans"][0]
    if invalid == "method":
        saved["methodVersion"] = "model-proposed"
    elif invalid == "tracks":
        saved["tracks"] = []
    elif invalid == "price":
        saved["currentPrice"] = "999"
    elif invalid == "as_of":
        saved["asOf"] = "2026-10-26"
    elif invalid == "source":
        saved["sourceUri"] = "https://unverified.example/nav"
    elif invalid == "retrieved":
        saved["retrievedAt"] = (NOW - timedelta(seconds=1)).isoformat()
    elif invalid == "available":
        saved.pop("availableAt")
    elif invalid == "calculated":
        saved["priceRefresh"]["calculatedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    else:
        saved["inputWindow"]["sampleCount"] = 1
    before = deepcopy(archived)

    result = await refresh_report_fund_plans(
        LocalHistory(), archived, NOW, include_series=True, repair_nav=False,
    )

    current = result["actionablePlans"][0]
    assert current["currentPrice"] is None and current["confirmedNav"] is None
    assert current["historyQuality"] == "UNAVAILABLE"
    assert archived == before


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["source", "future", "fund_identity", "as_of", "unit"])
async def test_saved_curve_fallback_never_promotes_invalid_source_identity_or_time(monkeypatch, invalid):
    archived = await refresh_report_fund_plans(
        LocalHistory([history()]), {"valueDirections": [{"plans": [old_plan()]}]},
        NOW, include_series=True,
    )
    saved = archived["valueDirections"][0]["plans"][0]
    if invalid == "source":
        saved["series"]["sourceUri"] = "https://unverified.example/nav"
    elif invalid == "future":
        saved["series"]["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif invalid == "fund_identity":
        saved["series"]["fundCode"] = "999999"
    elif invalid == "as_of":
        saved["series"]["asOf"] = "2026-10-26"
    else:
        saved["series"]["unit"] = "USD"
    before = deepcopy(archived)
    forbidden = AsyncMock(side_effect=AssertionError("Saved report reads stay local"))
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", forbidden)

    result = await refresh_report_fund_plans(
        LocalHistory(), archived, NOW, tenant_id="tenant", user_id="actor",
        include_series=True, repair_nav=False,
    )

    current = result["valueDirections"][0]["plans"][0]
    assert current["fundCode"] == saved["fundCode"]
    assert current["currentPrice"] is None
    assert current["series"]["points"] == []
    assert current["historyQuality"] == "UNAVAILABLE"
    assert archived == before
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticated_failed_repair_keeps_older_actual_prices_marked_partial(monkeypatch):
    ensure = AsyncMock(return_value={
        "asOf": NOW, "histories": [history()],
        "refresh": {"008298": {"state": "FAILED"}},
        "latestConfirmed": {"008298": {"asOf": "2026-10-28", "quality": "CONFIRMED"}},
    })
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", ensure)
    result = await refresh_report_fund_plans(
        LocalHistory(), {"valueDirections": [{"plans": [old_plan()]}]}, NOW,
        tenant_id="tenant", user_id="actor", include_series=True,
    )
    plan = result["valueDirections"][0]["plans"][0]
    assert plan["priceRefresh"]["state"] == "PARTIAL"
    assert plan["priceRefresh"]["reason"] == "NEWER_CONFIRMED_NAV_AVAILABLE"
    assert plan["asOf"] == "2026-10-27"
    assert plan["actionableZones"]["buyZone"]["min"] is not None
    assert plan["actionableZones"]["series"]["points"]


@pytest.mark.asyncio
async def test_generic_topic_uses_canonical_industry_claims_to_match_real_memory_funds():
    from backend.investment.research_fund_references import resolve_report_fund_references
    from tests.unit.test_opportunity_fund_recall import Connection, NOW as MATCH_NOW, product, status

    original = {"researchTopics": [{
        "key": "industrial_orders", "title": "产业订单增长", "thesis": "企业利润改善",
        "industryClaims": ["MEMORY"], "relatedIndustryIds": ["BANK"],
    }]}
    connection = Connection([product()], [status()])
    result = await resolve_report_fund_references(connection, original, {}, MATCH_NOW)
    assert result["researchTopics"][0]["plans"][0]["fundCode"] == "981001"
    keywords = connection.calls[0][1][1]
    assert "芯片" in keywords and "银行" in keywords
    assert "plans" not in original["researchTopics"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("claims", [None, 0])
async def test_malformed_optional_claims_keep_text_matched_topic_funds(claims):
    from backend.investment.research_fund_references import resolve_report_fund_references
    from tests.unit.test_opportunity_fund_recall import Connection, NOW as MATCH_NOW, product, status

    result = await resolve_report_fund_references(Connection([product()], [status()]), {
        "researchTopics": [{"key": "memory", "title": "存储芯片", "industryClaims": claims}],
    }, {}, MATCH_NOW)
    assert result["researchTopics"][0]["plans"][0]["fundCode"] == "981001"
