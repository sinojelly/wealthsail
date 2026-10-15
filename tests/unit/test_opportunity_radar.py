"""The closed summary prioritizes scoped evidence without certifying trades."""

from importlib import import_module, util
import pytest


def radar():
    assert util.find_spec("backend.macro.opportunity"), (
        "direction-scoped opportunity radar is missing"
    )
    return import_module("backend.macro.opportunity")


def series(code, value=3, delta=25, state="READY"):
    return {
        "code": code,
        "label": code,
        "value": value,
        "state": state,
        "frequency": "DAILY",
        "asOf": "2026-10-13",
        "unit": "%",
        "changes": {
            "change5Samples": delta,
            "change20Samples": None,
            "changeUnit": "bp",
        },
        "sourceUri": "https://home.treasury.gov/",
        "methodVersion": "v1",
    }


def build(direction="gold:physical", data=None, decision=None, portfolio=None):
    return radar().build_radar(
        {"items": [], "research": {"changes": [], "dimensions": []}, "events": []},
        data or [],
        [],
        {"id": direction, "market_id": direction.split(":")[0], "label": direction},
        decision or {"state": "WAIT", "summary": "研究条件不足", "gates": []},
        portfolio
        or {"state": "UNKNOWN", "summary": "持仓未核查", "heldSelected": None},
    )


def test_collapsed_brief_answers_four_questions_without_eligible_trade_claim():
    result = build()
    assert [q["id"] for q in result["questions"]] == [
        "CHANGE",
        "IMPACT",
        "QUALIFICATION",
        "PERSONAL",
    ]
    assert "条件" in result["questions"][2]["summary"]
    assert result["methodVersion"] == "opportunity-radar-v2"


def test_real_rate_rise_flags_gold_impact_but_not_unrelated_ashare_critical():
    gold = build(data=[series("US_T10_REAL", delta=45)])
    assert gold["headline"]["priority"] == "ATTENTION"
    assert gold["headline"]["kind"] == "RISK"
    assert "黄金" in gold["headline"]["impact"]
    ashare = build("a-share:broad", [series("US_T10_REAL", delta=45)])
    assert all(c["id"] != gold["headline"]["id"] for c in ashare["changes"])


def test_missing_data_is_a_gap_not_claimed_risk_increase():
    result = build("a-share:broad", [series("CN_T10", state="PERMISSION_REQUIRED")])
    assert result["headline"]["kind"] == "GAP"
    assert "风险上升" not in result["headline"]["impact"]
    assert not any(c["priority"] == "CRITICAL" for c in result["changes"])
    assert not any(q["critical"] for q in result["questions"])
    assert not any(s["criticalImpact"] for s in result["sections"])


def test_stale_rate_cannot_produce_confirmed_current_impact():
    result = build(data=[series("US_T10_REAL", delta=45, state="STALE")])
    assert not any(c["kind"] == "RISK" for c in result["changes"])


def test_known_personal_budget_breach_outranks_market_changes():
    result = build(
        data=[series("US_T10_REAL", delta=45)],
        decision={"state": "POSITION_REVIEW", "summary": "同指数敞口超限", "gates": []},
        portfolio={
            "state": "READY",
            "summary": "同指数敞口超限",
            "heldSelected": True,
            "budgetViolation": True,
            "budgetState": "BLOCKED",
        },
    )
    assert result["headline"]["id"] == "personal-budget"
    assert result["questions"][3]["critical"] is True


def test_unknown_consensus_does_not_generate_surprise_language():
    result = build(data=[series("US_T10_REAL")])
    assert "超预期" not in str(result) and "低于预期" not in str(result)


def test_direction_models_use_local_evidence_and_keep_required_gaps():
    model = import_module("backend.macro.direction_models")
    gold = model.direction_model(
        {"id": "gold:physical", "market_id": "gold"}, {}, [series("US_T10_REAL")], []
    )
    assert "US_T10_REAL" in gold["requiredEvidence"]
    assert "美元/汇率事实" in gold["missingEvidence"]
    usa = model.direction_model({"id": "us:broad", "market_id": "us"}, {}, [], [])
    assert "PMI" not in usa["requiredEvidence"]
    assert usa["state"] != "READY"


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), "bad"])
def test_ready_flag_cannot_make_a_bad_scalar_a_critical_rate_impact(value):
    result = build(data=[series("US_T10_REAL", value=value, delta=45)])
    assert not any(change["id"] == "rate:US_T10_REAL" for change in result["changes"])


def test_non_bp_rate_change_cannot_be_reported_as_basis_points():
    item = series("US_T10_REAL", delta=45)
    item["changes"]["changeUnit"] = "pp"
    result = build(data=[item])
    assert not any(change["id"] == "rate:US_T10_REAL" for change in result["changes"])


def test_unchanged_rate_is_neither_lower_rate_support_nor_higher_rate_pressure():
    model = import_module("backend.macro.direction_models")
    result = model.direction_model(
        {"id": "gold:physical", "market_id": "gold"},
        {},
        [series("US_T10_REAL", delta=0)],
        [],
    )
    assert result["supports"] == []
    assert result["counterFactors"] == []


def test_pmi_numeric_string_uses_validated_scalar_instead_of_raw_comparison():
    model = import_module("backend.macro.direction_models")
    result = model.direction_model(
        {"id": "a-share:broad", "market_id": "a-share"},
        {
            "items": [
                {
                    "code": "PMI",
                    "label": "PMI",
                    "value": "51",
                    "state": "READY",
                    "unit": "",
                }
            ]
        },
        [],
        [],
    )
    assert any(channel["label"] == "制造业景气" for channel in result["supports"])


def test_ready_trend_without_finite_parameters_remains_missing_evidence():
    model = import_module("backend.macro.direction_models")
    evidence = [
        {
            "indexId": "idx",
            "trend": {
                "state": "READY",
                "ma200": None,
                "distanceMA200": None,
                "momentum63": None,
            },
        }
    ]
    result = model.direction_model(
        {"id": "us:broad", "market_id": "us"}, {}, [], evidence
    )
    assert "当前指数趋势" in result["missingEvidence"]
    assert not any(
        channel["label"] == "指数长期趋势"
        for channel in result["counterFactors"] + result["supports"]
    )


def test_above_ma200_but_negative_momentum_does_not_claim_satisfied_trend_conditions():
    model = import_module("backend.macro.direction_models")
    evidence = [
        {
            "indexId": "idx",
            "trend": {
                "state": "READY",
                "ma200": 100,
                "distanceMA200": 0.1,
                "momentum63": -0.1,
            },
        }
    ]
    result = model.direction_model(
        {"id": "us:broad", "market_id": "us"}, {}, [], evidence
    )
    assert not any(channel["label"] == "指数长期趋势" for channel in result["supports"])


def test_verified_trend_and_valuation_are_not_still_reported_missing():
    model = import_module("backend.macro.direction_models")
    evidence = [
        {
            "indexId": "hs300",
            "trend": {
                "state": "READY",
                "ma200": 100,
                "distanceMA200": 0.1,
                "momentum63": 0.2,
            },
            "valuation": {"state": "READY"},
            "earnings": {"state": "UNKNOWN"},
        }
    ]
    result = model.direction_model(
        {"id": "a-share:broad", "market_id": "a-share"}, {}, [], evidence
    )
    assert "指数估值与趋势" not in result["missingEvidence"]
    assert "企业盈利证据" in result["missingEvidence"]
    assert result["state"] == "PARTIAL"


def test_position_review_label_without_confirmed_budget_cannot_create_critical_risk():
    result = build(
        decision={"state": "POSITION_REVIEW"},
        portfolio={"state": "UNKNOWN", "heldSelected": None, "budgetViolation": False},
    )
    assert not any(c["priority"] == "CRITICAL" for c in result["changes"])
    assert not any(q["critical"] for q in result["questions"])


def test_larger_rate_review_changes_importance_without_claiming_confirmed_risk():
    smaller = next(
        c
        for c in build(data=[series("US_T10_REAL", delta=25)])["changes"]
        if c["id"].startswith("rate:")
    )
    larger = next(
        c
        for c in build(data=[series("US_T10_REAL", delta=45)])["changes"]
        if c["id"].startswith("rate:")
    )
    assert smaller["severity"] == "MEDIUM" and larger["severity"] == "HIGH"
    assert larger["priority"] == "ATTENTION"


def macro_changes(changes, *, value=49.5, previous=49, delta=0.5):
    result = radar().build_radar(
        {
            "items": [
                {
                    "code": "PMI",
                    "label": "制造业PMI",
                    "state": "READY",
                    "value": value,
                    "unit": "",
                    "periodChange": delta,
                    "statistics": {"previousValue": previous},
                    "benchmark": {"value": 50},
                }
            ],
            "research": {"changes": changes},
        },
        [],
        [],
        {"id": "a-share:broad", "market_id": "a-share"},
        {"state": "WAIT"},
        {"state": "UNKNOWN"},
    )
    return [row for row in result["changes"] if row["id"].startswith("macro:PMI:")]


def test_pmi_identity_survives_unrelated_cpi_insert_and_reordered_changes():
    pmi = {"code": "PMI", "summary": "制造业PMI较前月+0.50指数点。"}
    original = macro_changes([pmi])
    inserted = macro_changes([{"code": "CPI_YOY", "summary": "CPI更新"}, pmi])
    assert len(original) == len(inserted) == 1
    assert original[0]["id"] == inserted[0]["id"]


def test_pmi_crossing_and_month_change_share_one_fact_identity_without_summary_matching():
    before = macro_changes(
        [
            {"code": "PMI", "summary": "跨越50参考线"},
            {"code": "PMI", "summary": "较前月+2指数点"},
        ],
        value=51,
        previous=49,
        delta=2,
    )
    reordered = macro_changes(
        [
            {"code": "PMI", "summary": "月度观察文字修订"},
            {"code": "CPI_YOY", "summary": "其他更新"},
            {"code": "PMI", "summary": "制造业参考线变化文字修订"},
        ],
        value=51,
        previous=49,
        delta=2,
    )
    assert len(before) == len(reordered) == 1
    assert before[0]["id"] == reordered[0]["id"]
    assert before[0]["priority"] == reordered[0]["priority"] == "ATTENTION"


def test_crossing_words_without_an_actual_boundary_crossing_cannot_raise_importance():
    item = macro_changes([{"code": "PMI", "summary": "跨越文字不构成真实跨线证据"}])[0]
    assert item["priority"] == "INFO"
    assert "跨越" not in item["summary"]


def test_opposite_real_benchmark_crossings_have_distinct_fact_identities():
    changes = [{"code": "PMI", "summary": "相同的可修改摘要"}]
    up = macro_changes(changes, value=51, previous=49, delta=2)[0]
    down = macro_changes(changes, value=49, previous=51, delta=-2)[0]
    assert up["id"] != down["id"]
    assert up["priority"] == down["priority"] == "ATTENTION"


@pytest.mark.parametrize(
    "value,previous,delta,kind",
    [
        (51, 49, 2, "BENCHMARK_CROSSING_UP"),
        (49, 51, -2, "BENCHMARK_CROSSING_DOWN"),
        (50, 49, 1, "BENCHMARK_CROSSING_UP"),
        (49, 50, -1, "BENCHMARK_CROSSING_DOWN"),
        (51, 50, 1, "PERIOD_CHANGE"),
        (50, 51, -1, "PERIOD_CHANGE"),
    ],
)
def test_same_direction_crossing_remains_stable_when_unrelated_rows_or_wording_change(
    value, previous, delta, kind
):
    original = macro_changes(
        [{"code": "PMI", "summary": "原始摘要"}],
        value=value,
        previous=previous,
        delta=delta,
    )
    refreshed = macro_changes(
        [
            {"code": "CPI_YOY", "summary": "其他指标更新"},
            {"code": "PMI", "summary": "修订后的月度说明"},
            {"code": "PMI", "summary": "修订后的参考线说明"},
        ],
        value=value,
        previous=previous,
        delta=delta,
    )
    assert len(original) == len(refreshed) == 1
    assert original[0]["id"] == refreshed[0]["id"]
    assert original[0]["id"] == "macro:PMI:" + kind
