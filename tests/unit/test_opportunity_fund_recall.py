"""Opportunity recalls use real identities beyond the frozen observation pool."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib import import_module, util
from uuid import uuid4

import pytest


NOW = datetime(2026, 11, 6, 8, tzinfo=timezone.utc)


def recall():
    assert util.find_spec("backend.investment.opportunity_fund_recall"), (
        "AI topics do not yet have market-wide fund recall"
    )
    return import_module(
        "backend.investment.opportunity_fund_recall"
    ).recall_topic_funds


def topic(key="chip", **changes):
    return {
        "topicId": f"topic:{key}",
        "title": "存储芯片国产替代",
        "summary": "存储芯片新增订单带动设备需求",
        "match": {"industryIds": [], "unresolvedClaims": ["存储芯片"]},
        **changes,
    }


def product(code="981001", name="测试芯片ETF联接A", keyword="芯片", **changes):
    return {
        "fund_code": code,
        "fund_name": name,
        "fund_type": "股票型-指数",
        "instrument_id": None,
        "market": "FUND_CN",
        "currency": "CNY",
        "trade_form": "FUND",
        "mapping_count": 1,
        "identity_verified_at": NOW,
        "matched_keyword": keyword,
        "matched_index_name": None,
        "matched_index_source_uri": None,
        "overseas_calendar_required": False,
        **changes,
    }


def status(code="981001", purchase="OPEN", **changes):
    return {
        "fund_code": code,
        "purchase_status": purchase,
        "daily_purchase_limit": None,
        "retrieved_at": NOW,
        **changes,
    }


class Connection:
    """Double only the database boundary, preserving production status parsing."""

    def __init__(self, products=(), statuses=()):
        self.products, self.statuses = list(products), list(statuses)
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "WITH products AS" in sql:
            return self.statuses
        return self.products


@pytest.mark.asyncio
async def test_unregistered_chip_topic_reaches_real_master_outside_core_pool():
    conn = Connection([product()], [status()])
    packet = {"discoveryUniverse": [{"fundCode": f"{i:06}"} for i in range(46)]}
    before = deepcopy(packet)
    result = (await recall()(conn, [topic()], packet, NOW))["topic:chip"]
    fund = result["funds"][0]
    assert fund["fundCode"] == "981001"
    assert fund["matchLevel"] == "DYNAMIC_MATCH"
    assert fund["name"] == "测试芯片ETF联接A"
    assert fund["canSubscribe"] is True
    assert fund["canBuyNow"] is False and fund["actionable"] is False
    assert fund["quantitativeScore"] is None
    assert fund["subscription"]["status"] == "OPEN"
    assert result["unavailableReason"] is None
    assert packet == before
    assert len(conn.calls) == 2


@pytest.mark.asyncio
async def test_verified_tracking_index_matches_even_when_fund_name_is_generic():
    rows = [product(name="测试成长精选A", matched_index_name="中证芯片产业指数")]
    result = await recall()(Connection(rows, [status()]), [topic()], {}, NOW)
    fund = result["topic:chip"]["funds"][0]
    assert fund["matchLevel"] == "DYNAMIC_MATCH"
    assert (
        "跟踪指数" in fund["matchReason"] and "中证芯片产业指数" in fund["matchReason"]
    )


@pytest.mark.asyncio
async def test_core_exact_sector_is_preferred_without_claiming_buy_permission():
    conn = Connection([product("320007", "测试科技A", "")], [status("320007")])
    packet = {
        "discoveryUniverse": [{"fundCode": "320007", "discoverySector": "MEMORY"}]
    }
    row = topic(match={"industryIds": ["MEMORY"]})
    fund = (await recall()(conn, [row], packet, NOW))["topic:chip"]["funds"][0]
    assert fund["matchLevel"] == "CORE_MATCH"
    assert fund["canSubscribe"] is True and fund["actionable"] is False


@pytest.mark.asyncio
async def test_unknown_custom_theme_uses_its_proposed_search_term():
    row = topic(
        "quantum",
        title="量子纠错商业化",
        summary="研发成果转向订单",
        match={"industryIds": [], "proposedScopes": [{"searchTerm": "量子纠错"}]},
    )
    conn = Connection(
        [product(name="测试量子纠错指数A", keyword="量子纠错")], [status()]
    )
    fund = (await recall()(conn, [row], {}, NOW))["topic:quantum"]["funds"][0]
    assert fund["matchLevel"] == "DYNAMIC_MATCH"
    assert "量子纠错" in fund["matchReason"]


@pytest.mark.asyncio
async def test_paused_core_falls_through_to_available_dynamic_fund():
    conn = Connection(
        [product("320007"), product("981002")],
        [status("320007", "SUSPENDED"), status("981002")],
    )
    fund = (await recall()(conn, [topic()], {}, NOW))["topic:chip"]["funds"][0]
    assert fund["fundCode"] == "981002" and fund["matchLevel"] == "DYNAMIC_MATCH"
    assert fund["canSubscribe"] is True


@pytest.mark.asyncio
async def test_broad_fallback_is_explained_and_unknown_subscription_stays_unknown():
    conn = Connection([product("981003", "测试中证A500ETF联接A", "中证A500")])
    result = (await recall()(conn, [topic()], {}, NOW))["topic:chip"]
    fund = result["funds"][0]
    assert fund["matchLevel"] == "BROAD_FALLBACK"
    assert "宽基" in fund["matchReason"] and "芯片" in fund["matchReason"]
    assert fund["subscription"]["status"] == "UNKNOWN"
    assert fund["canSubscribe"] is False
    assert result["unavailableReason"] == "SUBSCRIPTION_UNVERIFIED"
    assert "间接" in " ".join(fund["riskWarnings"])


@pytest.mark.asyncio
async def test_paused_and_small_limit_products_remain_visible_with_reasons():
    conn = Connection(
        [product(), product("981002")],
        [
            status(purchase="SUSPENDED"),
            status("981002", "LIMITED", daily_purchase_limit="10"),
        ],
    )
    result = (await recall()(conn, [topic()], {}, NOW))["topic:chip"]
    assert {f["subscription"]["status"] for f in result["funds"]} == {
        "SUSPENDED",
        "LIMITED",
    }
    assert not any(f["canSubscribe"] for f in result["funds"])
    assert result["unavailableReason"] == "SUBSCRIPTION_RESTRICTED"
    assert "申购" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retrieved", [None, NOW + timedelta(seconds=1), NOW - timedelta(days=30)]
)
async def test_missing_future_or_stale_status_does_not_claim_current_openness(
    retrieved,
):
    conn = Connection([product()], [status(retrieved_at=retrieved)])
    result = (await recall()(conn, [topic()], {}, NOW))["topic:chip"]
    assert result["funds"][0]["subscription"]["status"] == "UNKNOWN"
    assert result["funds"][0]["canSubscribe"] is False
    assert result["unavailableReason"] == "SUBSCRIPTION_UNVERIFIED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"fund_code": "made-up"},
        {"mapping_count": 2},
        {"identity_verified_at": NOW + timedelta(seconds=1)},
        {"fund_name": "测试芯片清盘A"},
        {"currency": "USD"},
        {"instrument_id": "not-a-uuid"},
        {"trade_form": "ETF"},
    ],
)
async def test_invalid_or_non_otc_identity_never_becomes_a_recommendation(changes):
    conn = Connection([product(**changes)], [status()])
    result = (await recall()(conn, [topic()], {}, NOW))["topic:chip"]
    assert result["status"] == "UNAVAILABLE" and result["funds"] == []
    assert result["unavailableReason"] == "NO_VERIFIED_FUND_MATCH"


@pytest.mark.asyncio
async def test_conflicting_real_identities_cannot_be_deduplicated_arbitrarily():
    conn = Connection(
        [product(instrument_id=uuid4()), product(instrument_id=uuid4())], [status()]
    )
    result = (await recall()(conn, [topic()], {}, NOW))["topic:chip"]
    assert result["funds"] == []


@pytest.mark.asyncio
async def test_all_archived_topics_are_recalled_beyond_the_46_fund_pool_cap():
    topics = [topic(str(i)) for i in range(72)]
    conn = Connection([product()], [status()])
    result = await recall()(conn, topics, {}, NOW)
    assert len(result) == 72
    assert all(row["funds"][0]["fundCode"] == "981001" for row in result.values())
    assert len(conn.calls) == 2


@pytest.mark.asyncio
async def test_recall_has_per_topic_and_query_bounds_without_synthetic_codes():
    rows = [product(f"981{i:03}") for i in range(20)]
    conn = Connection(rows, [status(f"981{i:03}") for i in range(20)])
    result = (await recall()(conn, [topic()], {}, NOW))["topic:chip"]
    assert len(result["funds"]) == 3
    assert {f["fundCode"] for f in result["funds"]} <= {f"981{i:03}" for i in range(20)}
    sql = conn.calls[0][0]
    assert (
        "reference.fund_master" in sql
        and "reference.research_product_index_links" in sql
    )
    assert "keyword_rank" in sql


@pytest.mark.asyncio
async def test_foreign_macro_topic_does_not_silently_fall_back_to_a_share_fund():
    conn = Connection(
        [product("981003", "测试沪深300指数A", "沪深300")], [status("981003")]
    )
    row = topic(
        "us", title="美股降息受益", summary="美国权益市场", match={"industryIds": []}
    )
    result = (await recall()(conn, [row], {}, NOW))["topic:us"]
    assert result["funds"] == []


@pytest.mark.asyncio
async def test_core_ranking_prefers_real_local_price_evidence_and_copies_only_its_fund():
    from tests.unit.test_daily_research import NOW as clock
    from tests.unit.test_quantitative_scan import scan_plan

    conn = Connection(
        [
            product("000051", identity_verified_at=clock),
            product("008281", identity_verified_at=clock),
        ],
        [status("000051", retrieved_at=clock), status("008281", retrieved_at=clock)],
    )
    packet = {
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v1",
            "asOf": clock.isoformat(),
            "minimumHistorySamples": 220,
            "items": [scan_plan("008281")],
        }
    }
    fund = (await recall()(conn, [topic()], packet, clock))["topic:chip"]["funds"][0]
    assert fund["fundCode"] == "008281"
    assert fund["confirmedNav"]["value"] == "1.0000"
    assert fund["referenceTracks"][0]["entryZoneMin"] == "0.9900"
    assert isinstance(fund["quantitativeScore"], (int, float))
    assert (
        fund["quantitativeRiskPenaltyApplied"]
        == -fund["scoreBreakdown"]["industryCyclePenalty"]
    )
    assert "LOCAL_QUANTITATIVE_EVIDENCE_MISSING" not in fund["missingEvidence"]
    assert fund["actionable"] is False and fund["canBuyNow"] is False


@pytest.mark.asyncio
async def test_core_ranking_uses_existing_score_for_two_trustworthy_price_plans():
    from tests.unit.test_daily_research import NOW as clock
    from tests.unit.test_quantitative_scan import scan_plan

    strong = scan_plan("008281")
    strong["tracks"][0]["state"] = "CANDIDATE"
    strong["signals"][0]["triggerState"] = "CANDIDATE"
    conn = Connection(
        [
            product("000051", identity_verified_at=clock),
            product("008281", identity_verified_at=clock),
        ],
        [status("000051", retrieved_at=clock), status("008281", retrieved_at=clock)],
    )
    packet = {
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v1",
            "asOf": clock.isoformat(),
            "minimumHistorySamples": 220,
            "items": [scan_plan("000051"), strong],
        }
    }
    funds = (await recall()(conn, [topic()], packet, clock))["topic:chip"]["funds"]
    assert funds[0]["fundCode"] == "008281"
    assert funds[0]["quantitativeScore"] > funds[1]["quantitativeScore"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title", ["日本工资上涨提振日经股票", "欧洲企业利润增长", "越南企业利润增长"]
)
async def test_overseas_equity_topics_never_fall_back_to_unrelated_china_equity(title):
    conn = Connection(
        [product("981003", "测试沪深300指数A", "沪深300")], [status("981003")]
    )
    row = topic("foreign", title=title, summary=title, match={"industryIds": []})
    result = (await recall()(conn, [row], {}, NOW))["topic:foreign"]
    assert result["funds"] == []


@pytest.mark.asyncio
async def test_unsupported_overseas_mapping_is_explicit_and_does_not_invent_a_proxy():
    row = topic(
        "foreign",
        title="越南企业利润增长",
        summary="越南经济",
        match={"industryIds": []},
    )
    result = (await recall()(Connection(), [row], {}, NOW))["topic:foreign"]
    assert result["unavailableReason"] == "NO_EXPLAINABLE_ASSET_MAPPING"
    assert "地域" in result["message"]


@pytest.mark.asyncio
async def test_japan_topic_can_fall_back_to_real_japan_equity_index():
    conn = Connection(
        [product("981003", "测试日经225指数A", "日经225")], [status("981003")]
    )
    row = topic(
        "japan",
        title="日本工资上涨",
        summary="日本企业利润增长",
        match={"industryIds": []},
    )
    result = (await recall()(conn, [row], {}, NOW))["topic:japan"]
    fund = result["funds"][0]
    assert fund["matchLevel"] == "BROAD_FALLBACK"
    assert "日本权益" in fund["matchReason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [timedelta(hours=36, seconds=1), timedelta(days=45)])
async def test_old_frozen_prices_keep_recommendations_without_current_score(age):
    from tests.unit.test_daily_research import NOW as clock
    from tests.unit.test_quantitative_scan import scan_plan

    evaluated = clock + age
    conn = Connection(
        [product("008281", identity_verified_at=clock)],
        [status("008281", retrieved_at=evaluated)],
    )
    packet = {
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v1",
            "asOf": clock.isoformat(),
            "minimumHistorySamples": 220,
            "items": [scan_plan("008281")],
        }
    }
    result = (await recall()(conn, [topic()], packet, evaluated))["topic:chip"]
    fund = result["funds"][0]
    assert result["status"] == "MATCHED" and fund["canSubscribe"] is True
    assert fund["quantitativeScore"] is None
    assert fund["confirmedNav"] is None and fund["referenceTracks"] == []
    assert "STALE_QUANTITATIVE_EVIDENCE" in fund["missingEvidence"]


@pytest.mark.asyncio
async def test_recent_retrieval_does_not_make_old_nav_fresh():
    from tests.unit.test_daily_research import NOW as clock
    from tests.unit.test_quantitative_scan import scan_plan

    price = scan_plan("008281", asOf=(clock - timedelta(days=11)).date().isoformat())
    conn = Connection(
        [product("008281", identity_verified_at=clock)],
        [status("008281", retrieved_at=clock)],
    )
    packet = {
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v1",
            "asOf": clock.isoformat(),
            "minimumHistorySamples": 220,
            "items": [price],
        }
    }
    fund = (await recall()(conn, [topic()], packet, clock))["topic:chip"]["funds"][0]
    assert fund["quantitativeScore"] is None
    assert "STALE_QUANTITATIVE_EVIDENCE" in fund["missingEvidence"]


@pytest.mark.asyncio
async def test_stale_high_score_cannot_outrank_todays_fresh_price_plan():
    from tests.unit.test_daily_research import NOW as clock
    from tests.unit.test_quantitative_scan import scan_plan

    evaluated = clock + timedelta(days=45)
    stale = scan_plan("000051")
    stale["tracks"][0]["state"] = "CANDIDATE"
    stale["signals"][0]["triggerState"] = "CANDIDATE"
    fresh = scan_plan(
        "008281",
        asOf=(evaluated - timedelta(days=1)).date().isoformat(),
        retrievedAt=evaluated.isoformat(),
        availableAt=evaluated.isoformat(),
    )
    conn = Connection(
        [
            product("000051", identity_verified_at=clock),
            product("008281", identity_verified_at=clock),
        ],
        [
            status("000051", retrieved_at=evaluated),
            status("008281", retrieved_at=evaluated),
        ],
    )
    packet = {
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v1",
            "asOf": evaluated.isoformat(),
            "minimumHistorySamples": 220,
            "items": [stale, fresh],
        }
    }
    funds = (await recall()(conn, [topic()], packet, evaluated))["topic:chip"]["funds"]
    assert funds[0]["fundCode"] == "008281"
    assert funds[1]["quantitativeScore"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "a_purchase,expected_first", [("SUSPENDED", "981002"), ("OPEN", "981001")]
)
async def test_open_c_share_is_eligible_and_open_a_share_keeps_preference(
    a_purchase, expected_first
):
    a_id, c_id = uuid4(), uuid4()
    conn = Connection(
        [
            product("981001", "测试芯片ETF联接A", instrument_id=a_id),
            product("981002", "测试芯片ETF联接C", instrument_id=c_id),
        ],
        [status("981001", a_purchase), status("981002")],
    )
    funds = (await recall()(conn, [topic()], {}, NOW))["topic:chip"]["funds"]
    assert funds[0]["fundCode"] == expected_first
    c_share = next((fund for fund in funds if fund["fundCode"] == "981002"), None)
    assert c_share is not None
    assert c_share["canSubscribe"] is True
    assert c_share["subjectId"] == str(c_id) and c_share["subjectKind"] == "fund"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["测试芯片分级B", "测试芯片基金后端", "测试芯片ETF联接C（美元现汇）"]
)
async def test_c_share_support_keeps_leveraged_b_backend_and_foreign_currency_excluded(
    name,
):
    result = (
        await recall()(Connection([product(name=name)], [status()]), [topic()], {}, NOW)
    )["topic:chip"]
    assert result["funds"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("country", ["美国", "日本", "欧洲"])
async def test_overseas_bond_generic_claim_cannot_match_domestic_bond_exposure(country):
    row = topic(
        "bond",
        title=f"{country}国债收益率下行",
        summary=f"{country}长久期债券价格改善",
        match={"industryIds": ["BOND"], "unresolvedClaims": ["国债"]},
    )
    conn = Connection(
        [product("000051", "测试中国国债指数A", "国债")], [status("000051")]
    )
    packet = {"discoveryUniverse": [{"fundCode": "000051", "discoverySector": "BOND"}]}
    result = (await recall()(conn, [row], packet, NOW))["topic:bond"]
    assert result["funds"] == []
    assert result["unavailableReason"] == "NO_EXPLAINABLE_ASSET_MAPPING"


@pytest.mark.asyncio
@pytest.mark.parametrize("use_index", [False, True])
async def test_us_treasury_matches_only_regional_name_or_verified_index(use_index):
    row = topic(
        "bond",
        title="美国国债收益率下行",
        summary="美国长久期债券价格改善",
        match={"industryIds": [], "unresolvedClaims": ["国债"]},
    )
    product_row = product(
        name="测试海外利率精选A" if use_index else "测试美国国债指数A",
        keyword="美国国债",
        matched_index_name="美国国债指数" if use_index else None,
    )
    result = (await recall()(Connection([product_row], [status()]), [row], {}, NOW))[
        "topic:bond"
    ]
    assert result["funds"][0]["canSubscribe"] is True
    assert "美国国债" in result["funds"][0]["matchReason"]


@pytest.mark.asyncio
async def test_domestic_bond_topic_keeps_domestic_bond_recall():
    row = topic(
        "bond",
        title="国内国债收益率下行",
        summary="国内长久期债券价格改善",
        match={"industryIds": [], "unresolvedClaims": ["国债"]},
    )
    result = (
        await recall()(
            Connection([product(name="测试中国国债指数A", keyword="国债")], [status()]),
            [row],
            {},
            NOW,
        )
    )["topic:bond"]
    assert result["funds"][0]["fundCode"] == "981001"
