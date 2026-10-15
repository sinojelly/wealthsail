"""Decision priorities and data coverage have separate evidence semantics."""

from datetime import datetime, timedelta
from importlib import import_module, util
from zoneinfo import ZoneInfo
import pytest

NOW = datetime(2026, 10, 15, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def engine():
    assert util.find_spec("backend.macro.research_board"), (
        "canonical research board is missing"
    )
    return import_module("backend.macro.research_board")


def build(
    *,
    series=None,
    evidence=None,
    model=None,
    decision=None,
    portfolio=None,
    radar=None,
    scope=None,
    collection=None,
    manage=False,
    context=None,
):
    return engine().build_research_board(
        context or {"items": [], "events": []},
        series or [],
        evidence or [],
        {"id": "a-share:broad", "market_id": "a-share", "label": "A股宽基"},
        decision
        or {
            "state": "WAIT",
            "gates": [],
            "marketState": "WAIT",
            "personalState": "UNKNOWN",
        },
        portfolio
        or {
            "state": "UNKNOWN",
            "heldSelected": None,
            "budgetViolation": False,
            "budgetState": "UNKNOWN",
            "missingEvidence": [],
        },
        model=model
        or {
            "requiredEvidence": ["M1_YOY"],
            "missingRequiredEvidence": ["M1_YOY"],
            "missingEvidence": ["M1同比"],
            "recommendedChecks": ["进一步核查订单"],
            "supports": [],
            "counterFactors": [],
            "researchState": "INSUFFICIENT",
            "qualificationState": "INCOMPLETE",
        },
        radar=radar or {"changes": []},
        scope=scope or {"direction_id": "a-share:broad"},
        collection=collection or {},
        can_manage_operations=manage,
        now=NOW,
    )


def summary(board, identity):
    return next(s for s in board["summaries"] if s["id"] == identity)


def trend(index="hs300", positive=True):
    return {
        "indexId": index,
        "label": "沪深300",
        "state": "READY",
        "asOf": "2026-10-14",
        "sourceUri": "https://official.example/index",
        "trend": {
            "state": "READY",
            "ma200": 100,
            "distanceMA200": 0.1 if positive else -0.1,
            "momentum63": 0.05 if positive else -0.05,
        },
        "valuation": {"state": "UNKNOWN"},
        "earnings": {"state": "UNKNOWN"},
        "risk": {"state": "UNKNOWN"},
        "missingEvidence": ["当前指数估值", "企业盈利证据"],
    }


def test_missing_m1_is_one_neutral_blocker_and_no_red_or_risk_claim():
    result = build()
    gaps = [
        i
        for i in result["items"]
        if i["category"] == "DATA_GAP" and i["key"] == "required:M1_YOY"
    ]
    assert len(gaps) == 1 and gaps[0]["tone"] == "GRAY" and gaps[0]["severity"] == "LOW"
    assert not any(i["tone"] == "RED" for i in result["items"])
    assert not any(i["category"] == "RISK" for i in result["items"])
    assert "进一步核查订单" not in str([i.get("blocker") for i in result["items"]])
    assert gaps[0]["id"] in result["dataStatus"]["itemIds"]


def test_unknown_holdings_are_not_assessed_as_no_risk_or_not_held():
    card = summary(build(), "HOLDINGS")
    assert card["state"] == "UNKNOWN" and card["tone"] == "GRAY"
    assert "无风险" not in card["summary"] and "未持有" not in card["summary"]


@pytest.mark.parametrize(
    "code,target", [("PMI", "research-macro"), ("US_T10_REAL", "research-global")]
)
def test_important_changes_summary_opens_issues_before_the_evidence_topic(code, target):
    board = build(
        series=[{"code": code, "state": "READY", "value": 50.1}],
        radar={
            "changes": [
                {
                    "id": "change:" + code,
                    "kind": "SUPPORT",
                    "priority": "ATTENTION",
                    "summary": "条件跨线",
                    "evidenceIds": [code],
                }
            ]
        },
    )
    card = summary(board, "CHANGES")
    assert card["nextAction"]["type"] == "VIEW_ISSUES"
    assert card["nextAction"]["target"] == "CHANGES"
    issue = next(i for i in board["items"] if i["id"] in card["itemIds"])
    assert issue["nextAction"]["target"] == target


def test_held_but_unknown_budget_remains_risk_unknown():
    card = summary(
        build(
            portfolio={
                "state": "READY",
                "heldSelected": True,
                "budgetState": "UNKNOWN",
                "budgetViolation": False,
            }
        ),
        "HOLDINGS",
    )
    assert card["state"] == "UNKNOWN"


def test_only_fully_confirmed_personal_breach_is_red_and_has_real_actions():
    result = build(
        decision={"state": "POSITION_REVIEW", "gates": []},
        portfolio={
            "state": "READY",
            "heldSelected": True,
            "budgetViolation": True,
            "budgetState": "BLOCKED",
        },
    )
    issue = next(i for i in result["items"] if i["key"] == "personal-budget")
    assert (
        issue["category"] == "RISK"
        and issue["tone"] == "RED"
        and issue["confidence"] == "VERIFIED"
    )
    assert result["items"][0]["id"] == issue["id"]
    assert issue["nextAction"]["href"] == "/dashboard?scope=MINE"
    assert summary(result, "HOLDINGS")["state"] == "BREACH"


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "UNKNOWN"},
        {"heldSelected": None},
        {"budgetViolation": False},
        {"budgetState": "UNKNOWN"},
    ],
)
def test_incomplete_personal_proof_never_promotes_a_red_alert(changes):
    result = build(
        decision={"state": "POSITION_REVIEW"},
        portfolio={
            "state": "READY",
            "heldSelected": True,
            "budgetViolation": True,
            "budgetState": "BLOCKED",
            **changes,
        },
    )
    assert not any(i["tone"] == "RED" for i in result["items"])


def test_rate_change_is_an_orange_conditional_observation_even_for_legacy_critical():
    result = build(
        radar={
            "changes": [
                {
                    "id": "rate:CN_T10",
                    "kind": "RISK",
                    "priority": "CRITICAL",
                    "summary": "利率近5样本+45基点",
                    "impact": "复核折现成本",
                    "evidenceIds": ["CN_T10"],
                    "relatedDirectionIds": ["a-share:broad"],
                }
            ]
        },
        series=[
            {
                "code": "CN_T10",
                "state": "READY",
                "value": 3,
                "frequency": "DAILY",
                "unit": "%",
            }
        ],
    )
    issue = next(i for i in result["items"] if i["key"] == "rate:CN_T10")
    assert issue["tone"] == "ORANGE" and issue["confidence"] == "PARTIAL"
    assert issue["severity"] != "HIGH"


def test_unknown_or_stale_fact_cannot_become_a_market_risk_observation():
    result = build(
        radar={
            "changes": [
                {
                    "id": "rate:CN_T10",
                    "kind": "RISK",
                    "priority": "CRITICAL",
                    "summary": "上涨",
                    "impact": "风险",
                    "evidenceIds": ["CN_T10"],
                }
            ]
        },
        series=[{"code": "CN_T10", "state": "STALE", "value": 3}],
    )
    assert not any(i["key"] == "rate:CN_T10" for i in result["items"])


def test_positive_current_trend_is_a_limited_line_of_research_not_new_improvement():
    result = build(evidence=[trend()])
    issue = next(i for i in result["items"] if i["category"] == "OPPORTUNITY")
    assert issue["tone"] == "GREEN" and issue["key"] == "trend:hs300"
    assert (
        "当前" in issue["summary"]
        and "由弱转强" not in issue["summary"]
        and "新增" not in issue["summary"]
    )
    assert summary(result, "OPPORTUNITIES")["state"] == "PRIORITY_RESEARCH"
    assert "建仓" not in summary(result, "OPPORTUNITIES")["summary"]
    assert issue["nextAction"]["target"] == "op-research-targets"


def test_known_failed_trend_is_waiting_for_conditions_not_missing_trend():
    result = build(evidence=[trend(positive=False)])
    assert summary(result, "OPPORTUNITIES")["state"] == "WAIT_CONDITIONS"
    assert not any(
        i.get("blocker", {}).get("code") == "INDEX_TREND" for i in result["items"]
    )
    assert not any("转弱" in i["summary"] for i in result["items"])


def test_ready_flag_without_finite_trend_parameters_does_not_create_opportunity():
    item = trend()
    item["trend"]["momentum63"] = float("nan")
    assert not any(
        i["category"] == "OPPORTUNITY" for i in build(evidence=[item])["items"]
    )


def test_same_index_gap_referenced_twice_only_has_one_canonical_body():
    model = {
        "requiredEvidence": ["INDEX_EARNINGS"],
        "missingRequiredEvidence": ["INDEX_EARNINGS"],
        "missingEvidence": ["企业盈利证据"],
        "recommendedChecks": [],
        "supports": [],
        "counterFactors": [],
    }
    result = build(model=model, evidence=[trend()])
    gaps = [
        i
        for i in result["items"]
        if i.get("blocker", {}).get("code") == "INDEX_EARNINGS"
    ]
    assert len(gaps) == 1
    ids = {i["id"] for i in result["items"]}
    assert all(ref in ids for s in result["summaries"] for ref in s["itemIds"])


def test_ids_are_stable_across_refresh_and_separate_full_selection_scopes():
    first = build()
    later = build(collection={"collectedAt": NOW + timedelta(minutes=1)})
    assert [i["id"] for i in first["items"]] == [i["id"] for i in later["items"]]
    other = build(
        scope={
            "direction_id": "a-share:broad",
            "fund_code": "000001",
            "fund_market": "HK",
        }
    )
    assert {i["id"] for i in first["items"]}.isdisjoint(i["id"] for i in other["items"])
    assert all(
        i["firstSeenAt"] is None and i["lastChangedAt"] is None for i in first["items"]
    )


def test_source_failure_has_real_owner_attempt_and_no_false_running_or_retry_button():
    stamp = NOW - timedelta(hours=1)
    result = build(
        collection={
            "collectedAt": stamp,
            "nextCheckAt": NOW,
            "collectionState": "FINISHED",
            "sourceAttemptAt": {"PBOC_M1": stamp},
            "sourceResults": {
                "PBOC_M1": {"status": "FAILED", "errorClass": "HTTPStatusError"}
            },
        }
    )
    issue = next(
        i for i in result["items"] if i.get("blocker", {}).get("code") == "M1_YOY"
    )
    assert issue["category"] == "SYSTEM" and issue["blocker"]["owner"] == "MACRO_WORKER"
    assert issue["blocker"]["lastAttemptAt"] == stamp
    assert issue["blocker"]["state"] == "FAILED"
    assert "运行中" not in str(result) and "立即重试" not in str(result)


def test_new_empty_cancel_does_not_relabel_an_old_source_failure_as_recently_attempted():
    failed_at = NOW - timedelta(days=2)
    result = build(
        collection={
            "collectedAt": NOW - timedelta(minutes=1),
            "collectionState": "CANCELLED",
            "sourceAttemptAt": {"PBOC_M1": failed_at},
            "sourceResults": {
                "PBOC_M1": {
                    "status": "FAILED",
                    "errorClass": "TimeoutError",
                    "error": "private-error-detail",
                }
            },
        }
    )
    issue = next(
        i for i in result["items"] if i.get("blocker", {}).get("code") == "M1_YOY"
    )
    assert issue["blocker"]["lastAttemptAt"] == failed_at
    assert "private-error-detail" not in str(result)


@pytest.mark.parametrize(
    "source_time",
    [None, "invalid", datetime(2026, 10, 15, 11), NOW + timedelta(seconds=1)],
)
def test_unknown_or_unavailable_source_attempt_time_never_falls_back_to_global_collection(
    source_time,
):
    result = build(
        collection={
            "collectedAt": NOW - timedelta(minutes=1),
            "sourceAttemptAt": {"PBOC_M1": source_time},
            "sourceResults": {"PBOC_M1": {"status": "FAILED"}},
        }
    )
    issue = next(
        i for i in result["items"] if i.get("blocker", {}).get("code") == "M1_YOY"
    )
    assert issue["blocker"]["lastAttemptAt"] is None


def test_ops_links_are_not_exposed_to_viewers_or_point_to_fake_source_settings():
    source = [{"code": "M1_YOY", "state": "NOT_COLLECTED"}]
    viewer = build(series=source)
    admin = build(series=source, manage=True)
    assert "/operations" not in str(viewer) and "/settings" not in str(admin)
    assert admin["dataStatus"]["adminAction"]["href"] == "/operations"
    assert all(
        i["nextAction"]["type"] == "VIEW_DATA"
        for i in admin["items"]
        if i.get("blocker")
    )


def test_acknowledged_notice_cannot_remove_active_risk_issue():
    kwargs = {
        "decision": {"state": "POSITION_REVIEW"},
        "portfolio": {
            "state": "READY",
            "heldSelected": True,
            "budgetViolation": True,
            "budgetState": "BLOCKED",
        },
    }
    assert any(i["key"] == "personal-budget" for i in build(**kwargs)["items"])


def test_board_has_exactly_three_entry_summaries_and_does_not_claim_global_counts():
    result = build(evidence=[trend()])
    assert [i["id"] for i in result["summaries"]] == [
        "HOLDINGS",
        "OPPORTUNITIES",
        "CHANGES",
    ]
    assert "全市场" not in str(result)


@pytest.mark.asyncio
async def test_collection_status_uses_persisted_time_and_retry_schedule():
    from unittest.mock import AsyncMock
    from backend.macro.market_repository import read_collection

    stamp = NOW - timedelta(minutes=25)
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "result": {
            "status": "PARTIAL",
            "retryUntil": (NOW + timedelta(hours=2)).isoformat(),
            "sourceResults": {},
        },
        "recorded_at": stamp,
        "phase": "FINISHED",
    }
    result = await read_collection(conn, NOW)
    assert result["collectedAt"] == stamp
    assert result["nextCheckAt"] == stamp + timedelta(hours=1)
    assert result["collectionState"] == "FINISHED"


@pytest.mark.asyncio
async def test_active_lease_preserves_last_terminal_source_results_without_claiming_worker_online():
    from unittest.mock import AsyncMock
    from backend.macro.market_repository import read_collection

    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {
            "phase": "CLAIMED",
            "recorded_at": NOW - timedelta(minutes=1),
            "lease_until": NOW + timedelta(minutes=9),
            "result": {},
        },
        {
            "phase": "FINISHED",
            "recorded_at": NOW - timedelta(hours=2),
            "result": {
                "sourceResults": {"PBOC_M1": {"status": "FAILED"}},
                "status": "PARTIAL",
            },
        },
    ]
    result = await read_collection(conn, NOW)
    assert result["collectionState"] == "CLAIMED" and result[
        "collectedAt"
    ] == NOW - timedelta(hours=2)
    assert result["sourceResults"]["PBOC_M1"]["status"] == "FAILED"


def test_high_importance_rate_observation_stays_orange_with_partial_impact_confidence():
    result = build(
        radar={
            "changes": [
                {
                    "id": "rate:CN_T10",
                    "kind": "RISK",
                    "priority": "ATTENTION",
                    "severity": "HIGH",
                    "summary": "利率变化",
                    "impact": "核查影响渠道",
                    "evidenceIds": ["CN_T10"],
                }
            ]
        },
        series=[{"code": "CN_T10", "state": "READY", "value": 3}],
    )
    issue = next(i for i in result["items"] if i["key"] == "rate:CN_T10")
    assert (
        issue["severity"] == "HIGH"
        and issue["tone"] == "ORANGE"
        and issue["confidence"] == "PARTIAL"
    )


def test_non_earning_assets_do_not_get_an_unrelated_earnings_blocker():
    model = {
        "requiredEvidence": ["US_T10_REAL", "USD_FX"],
        "missingRequiredEvidence": ["USD_FX"],
        "supports": [],
        "counterFactors": [],
    }
    result = build(model=model, evidence=[trend("au9999")])
    assert not any(
        i.get("blocker", {}).get("code") == "INDEX_EARNINGS" for i in result["items"]
    )


def test_stale_outer_snapshot_cannot_keep_a_ready_inner_trend_opportunity():
    evidence = trend()
    evidence["state"] = "STALE"
    result = build(evidence=[evidence])
    assert not any(i["category"] == "OPPORTUNITY" for i in result["items"])


def test_product_and_pit_unknown_gates_have_one_responsible_blocker_and_real_next_step():
    result = build(
        decision={
            "state": "WAIT",
            "gates": [
                {
                    "id": "product",
                    "state": "UNKNOWN",
                    "label": "基金质量",
                    "reason": "来源与配置缺少",
                },
                {
                    "id": "validation",
                    "state": "UNKNOWN",
                    "label": "真实PIT",
                    "reason": "历史档案缺少",
                },
            ],
        }
    )
    for code in ("PRODUCT_QUALITY", "PIT_VALIDATION"):
        issue = next(
            i for i in result["items"] if i.get("blocker", {}).get("code") == code
        )
        assert issue["owner"] == "RESEARCH_DATA" and issue["tone"] == "GRAY"
        assert issue["nextAction"]["target"] == "research-data"


def test_ordinary_updates_do_not_count_as_important_changes_in_the_home_summary():
    result = build(
        radar={
            "changes": [
                {
                    "id": "macro:PMI:0",
                    "kind": "OBSERVATION",
                    "priority": "INFO",
                    "summary": "普通月度更新",
                    "impact": "观察",
                    "evidenceIds": ["PMI"],
                }
            ]
        },
        series=[{"code": "PMI", "state": "READY", "value": 51}],
    )
    assert summary(result, "CHANGES")["state"] == "NO_CONFIRMED_CHANGE"
    assert not summary(result, "CHANGES")["itemIds"]


def test_data_status_exposes_only_the_persisted_collection_phase():
    assert (
        build(collection={"collectionState": "FINISHED"})["dataStatus"][
            "collectionState"
        ]
        == "FINISHED"
    )


def test_direction_references_real_research_items_and_separates_actual_blockers():
    result = build(evidence=[trend(), trend("zz500")])
    by_id = {i["id"]: i for i in result["items"]}
    direction = result["direction"]
    assert result["methodVersion"] == "research-board-v2"
    assert direction["itemIds"]
    assert all(
        by_id[key]["category"] in {"RISK", "OPPORTUNITY", "CHANGE"}
        for key in direction["itemIds"]
    )
    assert direction["blockerIds"] == result["dataStatus"]["itemIds"]
    assert not set(direction["itemIds"]) & set(direction["blockerIds"])
    assert direction["relatedIndexIds"] == ["hs300", "zz500"]
    assert direction["summary"] and direction["nextStep"]
    assert direction["nextAction"]["type"] == "VIEW_DIRECTION"
    assert direction["nextAction"]["target"] == "current-direction"


def test_direction_preserves_support_counter_and_known_failed_rule_without_making_a_gap():
    supports = [{"label": "支持", "summary": "已知制造业景气", "evidenceIds": ["PMI"]}]
    counters = [{"label": "逆风", "summary": "已知融资变化", "evidenceIds": ["CN_T10"]}]
    failed = [
        {
            "id": "INDEX_TREND",
            "label": "趋势",
            "reason": "当前未满足规则",
            "evidenceIds": ["hs300"],
        }
    ]
    result = build(
        evidence=[trend(positive=False)],
        model={
            "requiredEvidence": ["INDEX_TREND"],
            "missingRequiredEvidence": [],
            "supports": supports,
            "counterFactors": counters,
            "failedConditions": failed,
            "qualificationState": "BLOCKED",
            "researchState": "WAIT_CONDITIONS",
        },
    )
    assert result["direction"]["supports"] == supports
    assert result["direction"]["counterFactors"] == counters
    assert result["direction"]["failedConditions"] == failed
    assert result["direction"]["blockerIds"] == []


def test_known_failed_trend_is_a_verified_current_change_with_the_same_index_key():
    good = next(
        i for i in build(evidence=[trend()])["items"] if i["key"] == "trend:hs300"
    )
    bad = next(
        i
        for i in build(evidence=[trend(positive=False)])["items"]
        if i["key"] == "trend:hs300"
    )
    assert good["id"] == bad["id"]
    assert (good["category"], good["condition"]) == ("OPPORTUNITY", "PASSED")
    assert (bad["category"], bad["condition"]) == ("CHANGE", "FAILED")
    assert good["factVerification"] == bad["factVerification"] == "VERIFIED"
    assert bad["severity"] == "MEDIUM" and bad["tone"] != "RED"
    assert "当前" in bad["summary"]
    assert all(word not in bad["summary"] for word in ("刚", "转弱", "卖出"))
    stale = trend(positive=False)
    stale["state"] = "STALE"
    assert not any(i["key"] == "trend:hs300" for i in build(evidence=[stale])["items"])


def test_five_groups_classify_without_merging_independent_source_states_or_times():
    result = build(
        model={
            "requiredEvidence": ["INDEX_TREND", "M1_YOY", "TSF_STOCK_YOY", "CN_T10"],
            "missingRequiredEvidence": [
                "INDEX_TREND",
                "M1_YOY",
                "TSF_STOCK_YOY",
                "CN_T10",
            ],
        },
        series=[{"code": "CN_T10", "state": "PERMISSION_REQUIRED"}],
        decision={
            "state": "WAIT",
            "gates": [
                {"id": key, "label": key, "state": "UNKNOWN"}
                for key in ("product", "validation", "portfolio")
            ],
        },
        collection={
            "sourceResults": {
                "PBOC_M1": {"status": "FAILED", "errorClass": "HTTPStatusError"},
                "PBOC_TSF": {"status": "FAILED", "errorClass": "HTTPStatusError"},
            },
            "sourceAttemptAt": {
                "PBOC_M1": NOW - timedelta(hours=1),
                "PBOC_TSF": NOW - timedelta(days=2),
            },
        },
    )
    groups = result["dataStatus"]["groups"]
    assert [g["id"] for g in groups] == [
        "MARKET",
        "MACRO",
        "PERMISSION",
        "PORTFOLIO",
        "STRATEGY",
    ]
    assert all(
        set(g) == {"id", "label", "summary", "impact", "owner", "itemIds"}
        for g in groups
    )
    assert all(g["itemIds"] for g in groups)
    grouped = [key for group in groups for key in group["itemIds"]]
    assert len(grouped) == len(set(grouped)) == len(result["dataStatus"]["itemIds"])
    items = {i["id"]: i for i in result["items"]}
    macro = next(g for g in groups if g["id"] == "MACRO")
    children = {
        items[key]["blocker"]["code"]: items[key]["blocker"] for key in macro["itemIds"]
    }
    assert children["M1_YOY"]["sourceKey"] == "PBOC_M1"
    assert children["TSF_STOCK_YOY"]["sourceKey"] == "PBOC_TSF"
    assert children["M1_YOY"]["lastAttemptAt"] == NOW - timedelta(hours=1)
    assert children["TSF_STOCK_YOY"]["lastAttemptAt"] == NOW - timedelta(days=2)
    assert all(child["state"] == "FAILED" for child in children.values())


def test_business_gap_actions_are_the_same_for_admin_and_viewer_and_no_task_is_invented():
    viewer, admin = build(), build(manage=True)
    assert viewer["dataStatus"]["adminAction"] is None
    assert admin["dataStatus"]["adminAction"]["type"] == "VIEW_OPERATIONS"
    for board in (viewer, admin):
        issue = next(i for i in board["items"] if i.get("blocker"))
        assert issue["nextAction"]["type"] == "VIEW_DATA"
        assert issue["nextAction"]["target"] == "research-data"
        assert issue["blocker"]["lastAttemptAt"] is None
        assert "未检测到执行记录" in issue["impact"]
        assert "等待" not in issue["impact"] and "正在处理" not in issue["impact"]


def test_valid_rate_fact_is_verified_while_its_impact_remains_conditional():
    result = build(
        series=[
            {
                "code": "US_T10_REAL",
                "state": "READY",
                "value": 2,
                "frequency": "DAILY",
                "unit": "%",
            }
        ],
        radar={
            "changes": [
                {
                    "id": "rate:real",
                    "kind": "RISK",
                    "priority": "ATTENTION",
                    "severity": "HIGH",
                    "summary": "已知利率变化",
                    "evidenceIds": ["US_T10_REAL"],
                }
            ]
        },
    )
    item = next(i for i in result["items"] if i["key"] == "rate:real")
    assert item["factVerification"] == "VERIFIED"
    assert item["confidence"] == "PARTIAL" and item["tone"] == "ORANGE"
    assert all(
        i["factVerification"] == "UNKNOWN" for i in result["items"] if i.get("blocker")
    )


@pytest.mark.parametrize(
    "verification,expected",
    [("VERIFIED", "VERIFIED"), ("UNVERIFIED", "UNKNOWN"), (None, "UNKNOWN")],
)
def test_macro_fact_verification_requires_actual_value_verification(
    verification, expected
):
    item = {
        "code": "PMI",
        "state": "READY",
        "value": 51,
        "valueVerification": verification,
    }
    result = build(
        context={"items": [item]},
        radar={
            "changes": [
                {
                    "id": "macro:PMI:BENCHMARK_CROSSING_UP",
                    "kind": "OBSERVATION",
                    "priority": "ATTENTION",
                    "summary": "已知月度事实",
                    "evidenceIds": ["PMI"],
                }
            ]
        },
    )
    assert (
        next(i for i in result["items"] if i["key"].startswith("macro:PMI:"))[
            "factVerification"
        ]
        == expected
    )


@pytest.mark.parametrize(
    "published", [NOW + timedelta(seconds=1), "invalid", datetime(2026, 10, 15, 11)]
)
def test_unavailable_official_event_timestamp_cannot_become_a_verified_business_fact(
    published,
):
    result = build(
        context={
            "events": [
                {
                    "id": "event",
                    "sourceType": "OFFICIAL_FACT",
                    "publishedAt": published,
                    "sourceUri": "https://www.pbc.gov.cn/event",
                }
            ]
        },
        radar={
            "changes": [
                {
                    "id": "event:event",
                    "kind": "OBSERVATION",
                    "priority": "ATTENTION",
                    "summary": "公告",
                    "evidenceIds": ["event"],
                }
            ]
        },
    )
    assert not any(i["key"] == "event:event" for i in result["items"])


def entry_inputs():
    return {
        "decision": {
            "state": "ENTRY_CANDIDATE",
            "gates": [
                {"id": key, "state": "PASS"}
                for key in (
                    "value",
                    "trend",
                    "macro",
                    "product",
                    "validation",
                    "portfolio",
                    "tradability",
                )
            ],
            "validation": {
                "state": "PASS",
                "sourceMode": "REAL_PIT",
                "pointInTimeVerified": True,
            },
        },
        "portfolio": {
            "state": "READY",
            "heldSelected": False,
            "budgetState": "PASS",
            "budgetViolation": False,
        },
        "model": {
            "qualificationState": "READY",
            "missingRequiredEvidence": [],
            "counterFactors": [],
            "failedConditions": [],
        },
    }


def test_complete_entry_has_its_own_verified_qualification_item_for_monitoring():
    board = build(**entry_inputs())
    issue = next(i for i in board["items"] if i["key"] == "qualification:entry")
    assert (
        issue["category"] == "OPPORTUNITY" and issue["factVerification"] == "VERIFIED"
    )
    assert "人工复核" in issue["impact"]
    assert issue["id"] in summary(board, "OPPORTUNITIES")["itemIds"]


def test_empty_groups_do_not_invent_new_unavailable_judgments_or_processing():
    board = build(**entry_inputs())
    assert not board["dataStatus"]["itemIds"]
    for group in board["dataStatus"]["groups"]:
        assert group["itemIds"] == []
        assert all(
            word not in group["impact"] for word in ("受限", "尚不能", "等待", "处理中")
        )


def test_verified_official_event_fact_does_not_certify_its_unknown_market_impact():
    result = build(
        context={
            "events": [
                {
                    "id": "event",
                    "sourceType": "OFFICIAL_FACT",
                    "publishedAt": NOW - timedelta(hours=1),
                    "retrievedAt": NOW - timedelta(minutes=1),
                    "sourceUri": "https://www.pbc.gov.cn/event",
                }
            ]
        },
        radar={
            "changes": [
                {
                    "id": "event:event",
                    "kind": "OBSERVATION",
                    "priority": "INFO",
                    "summary": "官方公告已发布",
                    "evidenceIds": ["event"],
                }
            ]
        },
    )
    item = next(i for i in result["items"] if i["key"] == "event:event")
    assert item["factVerification"] == "VERIFIED"
    assert item["confidence"] == "PARTIAL"
    assert item["severity"] == "LOW" and item["category"] == "CHANGE"


@pytest.mark.parametrize(
    "change",
    [
        "missing_gate",
        "unknown_gate",
        "synthetic",
        "unknown_portfolio",
        "held_unknown",
        "budget_unknown",
        "model_incomplete",
    ],
)
def test_entry_label_without_all_actual_proof_cannot_create_a_qualification_item(
    change,
):
    inputs = entry_inputs()
    if change == "missing_gate":
        inputs["decision"]["gates"].pop()
    elif change == "unknown_gate":
        inputs["decision"]["gates"][0]["state"] = "UNKNOWN"
    elif change == "synthetic":
        inputs["decision"]["validation"]["sourceMode"] = "SYNTHETIC"
    elif change == "unknown_portfolio":
        inputs["portfolio"]["state"] = "UNKNOWN"
    elif change == "held_unknown":
        inputs["portfolio"]["heldSelected"] = None
    elif change == "budget_unknown":
        inputs["portfolio"]["budgetState"] = "UNKNOWN"
    else:
        inputs["model"]["qualificationState"] = "INCOMPLETE"
    board = build(**inputs)
    assert not any(i["key"] == "qualification:entry" for i in board["items"])
    assert summary(board, "OPPORTUNITIES")["state"] != "ENTRY_CANDIDATE"


@pytest.mark.parametrize("positive", [True, False])
def test_homepage_names_the_known_index_condition_instead_of_only_a_count(positive):
    board = build(evidence=[trend(positive=positive)])
    assert "沪深300" in summary(board, "OPPORTUNITIES")["summary"]
    assert "沪深300" in board["direction"]["summary"]


@pytest.mark.parametrize("status", [403, 404])
def test_actual_http_code_is_preserved_without_raw_error_text(status):
    board = build(
        collection={
            "sourceResults": {
                "PBOC_M1": {
                    "status": "FAILED",
                    "errorClass": "HTTPStatusError",
                    "httpStatus": status,
                    "error": "private secret",
                }
            }
        }
    )
    assert board["dataStatus"]["sourceResults"]["PBOC_M1"]["httpStatus"] == status
    assert "secret" not in str(board)


@pytest.mark.parametrize("status", [True, "403", 99, 600])
def test_invalid_http_code_is_not_a_verified_status(status):
    board = build(
        collection={
            "sourceResults": {"PBOC_M1": {"status": "FAILED", "httpStatus": status}}
        }
    )
    assert "httpStatus" not in board["dataStatus"]["sourceResults"]["PBOC_M1"]
