"""Candidate gates and fixed PIT research never manufacture missing evidence."""

from datetime import datetime, timedelta
from importlib import import_module, util
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Asia/Shanghai")


def engine():
    assert util.find_spec("backend.investment.decision"), "decision engine missing"
    return import_module("backend.investment.decision")


def dataset():
    start = datetime(2020, 1, 1, 18, tzinfo=TZ)
    return {
        "sourceMode": "REAL_PIT",
        "pointInTimeVerified": True,
        "sourceUri": "https://evidence.example.test/archive",
        "configVersion": "locked-v1",
        "seriesKind": "ETF_HFQ",
        "returnBasisVerified": True,
        "costsVerified": True,
        "transactionMechanismVerified": True,
        "feeBps": 5,
        "slippageBps": 5,
        "points": [
            {
                "date": (start + timedelta(days=i)).date().isoformat(),
                "close": 100 + i * 0.1,
                "decisionAt": start + timedelta(days=i),
                "availableAt": start + timedelta(days=i, hours=-2),
                "retrievedAt": start + timedelta(days=i, hours=-1),
                "sourceUri": "https://evidence.example.test/archive",
                "configVersion": "locked-v1",
                "isTradingDay": True,
            }
            for i in range(505)
        ],
    }


def gates():
    return {
        "value": {"state": "PASS"},
        "trend": {"state": "PASS"},
        "macro": {"state": "PASS"},
        "product": {"state": "PASS"},
        "validation": {
            "state": "PASS",
            "sourceMode": "REAL_PIT",
            "pointInTimeVerified": True,
        },
        "portfolio": {"state": "PASS"},
        "tradability": {"state": "PASS"},
    }


def test_candidate_needs_every_gate_and_personal_ownership():
    decision = engine()
    assert decision.evaluate(gates(), held=False)["state"] == "ENTRY_CANDIDATE"
    for key in gates():
        changed = gates()
        changed[key]["state"] = "UNKNOWN"
        result = decision.evaluate(changed, held=False)
        assert result["state"] == "WAIT"
        assert result["gates"]
    assert decision.evaluate(gates(), held=None)["state"] == "WAIT"


def test_unvalidated_synthetic_report_cannot_authorize_candidate():
    changed = gates()
    changed["validation"]["sourceMode"] = "SYNTHETIC"
    assert engine().evaluate(changed, held=False)["state"] == "WAIT"


def test_confirmed_holding_risk_has_priority_without_implying_trade():
    changed = gates()
    changed["value"]["state"] = "UNKNOWN"
    assert (
        engine().evaluate(changed, held=True, budget_violation=True)["state"]
        == "POSITION_REVIEW"
    )
    assert engine().evaluate(changed, held=True)["state"] == "HOLD_REVIEW"


@pytest.mark.parametrize(
    "change",
    [
        {"sourceMode": "SYNTHETIC"},
        {"pointInTimeVerified": False},
        {"costsVerified": False},
        {"transactionMechanismVerified": False},
        {"returnBasisVerified": False},
        {"feeBps": None},
    ],
)
def test_backtest_missing_real_proof_or_costs_is_unavailable(change):
    data = {**dataset(), **change}
    assert engine().validate_pit(data)["state"] == "UNAVAILABLE"


def test_backtest_504_points_is_insufficient():
    data = dataset()
    data["points"].pop()
    assert engine().validate_pit(data)["state"] == "INSUFFICIENT"


def test_backfilled_history_with_late_retrieval_is_not_pit():
    data = dataset()
    for row in data["points"]:
        row["retrievedAt"] = datetime(2026, 10, 14, 18, tzinfo=TZ)
    assert engine().validate_pit(data)["state"] == "UNAVAILABLE"


def test_execution_lags_signal_and_charges_both_sides_and_benchmark():
    report = engine().validate_pit(dataset())
    assert report["state"] == "NOT_VALIDATED"  # one directional run lacks >=3 switches
    assert (
        report["executions"][0]["executionDate"]
        > report["executions"][0]["decisionDate"]
    )
    assert report["executions"][0]["costRate"] == pytest.approx(0.001)
    assert report["benchmarkNetReturn"] < report["benchmarkGrossReturn"]
    assert "winRate" not in report


def test_future_revision_and_tail_do_not_change_existing_oos_decisions():
    data = dataset()
    before = engine().validate_pit(data)
    revised = {
        **data["points"][300],
        "close": 999,
        "availableAt": datetime(2026, 10, 14, 18, tzinfo=TZ),
        "retrievedAt": datetime(2026, 10, 14, 18, tzinfo=TZ),
    }
    data["points"].append(revised)
    after = engine().validate_pit(data)
    assert before["executions"] == after["executions"]
    assert before["netReturn"] == after["netReturn"]


def test_mine_portfolio_aggregates_same_instrument_and_index_across_accounts():
    accounts = [
        {
            "account_id": "a",
            "base_currency": "CNY",
            "as_of": "2026-10-14",
            "total_nav": 100,
            "cash_balance": 50,
            "valuation_coverage_pct": 100,
            "valuation_current": True,
        },
        {
            "account_id": "b",
            "base_currency": "CNY",
            "as_of": "2026-10-14",
            "total_nav": 100,
            "cash_balance": 50,
            "valuation_coverage_pct": 100,
            "valuation_current": True,
        },
    ]
    positions = [
        {
            "account_id": "a",
            "instrument_id": "fund",
            "as_of": "2026-10-14",
            "quantity": 1,
            "market_value": 50,
            "currency": "CNY",
            "index_ids": ["hs300"],
        },
        {
            "account_id": "b",
            "instrument_id": "fund",
            "as_of": "2026-10-14",
            "quantity": 1,
            "market_value": 50,
            "currency": "CNY",
            "index_ids": ["hs300"],
        },
    ]
    policy = {
        "policy_status": "CONFIRMED",
        "single_position_limit": 40,
        "cash_target_pct": 20,
        "max_drawdown_limit": 35,
    }
    result = engine().portfolio_summary(
        accounts, positions, policy, instrument_id="fund", index_id="hs300"
    )
    assert result["selectedWeight"] == 0.5 and result["sameIndexWeight"] == 0.5
    assert result["cashRatio"] == 0.5
    assert result["budgetViolation"] is True
    positions[0]["market_value"] = None
    assert (
        engine().portfolio_summary(
            accounts, positions, policy, instrument_id="fund", index_id="hs300"
        )["state"]
        == "UNKNOWN"
    )


@pytest.mark.parametrize("quality", ["STALE", "ESTIMATED", "UNAVAILABLE"])
def test_incomplete_or_stale_personal_valuation_does_not_pass_budget(quality):
    account = {
        "account_id": "a",
        "base_currency": "CNY",
        "as_of": "2026-10-14",
        "total_nav": 100,
        "cash_balance": 50,
        "positions_market_value": 50,
        "valuation_coverage_pct": 100,
        "data_quality_status": quality,
    }
    assert (
        engine().portfolio_summary([account], [], None, instrument_id="fund")["state"]
        == "UNKNOWN"
    )


def test_missing_position_projection_cannot_claim_target_is_not_held():
    account = {
        "account_id": "a",
        "base_currency": "CNY",
        "as_of": "2026-10-14",
        "total_nav": 100,
        "cash_balance": 50,
        "positions_market_value": 50,
        "valuation_coverage_pct": 100,
        "data_quality_status": "FRESH",
    }
    output = engine().portfolio_summary([account], [], None, instrument_id="fund")
    assert output["heldSelected"] is None and output["state"] == "UNKNOWN"


@pytest.mark.parametrize("proof", [None, False])
def test_historical_fresh_flag_cannot_certify_current_risk_without_proof(proof):
    account = {
        "account_id": "a",
        "base_currency": "CNY",
        "as_of": "2020-01-02",
        "total_nav": 100,
        "cash_balance": 10,
        "positions_market_value": 90,
        "valuation_coverage_pct": 100,
        "data_quality_status": "FRESH",
    }
    if proof is not None:
        account["valuation_current"] = proof
    position = {
        "account_id": "a",
        "instrument_id": "fund",
        "as_of": "2020-01-02",
        "quantity": 1,
        "market_value": 90,
        "currency": "CNY",
        "index_ids": ["hs300"],
        "data_quality_status": "FRESH",
    }
    policy = {
        "policy_status": "CONFIRMED",
        "single_position_limit": 40,
        "cash_target_pct": 10,
        "max_drawdown_limit": 35,
    }
    output = engine().portfolio_summary(
        [account], [position], policy, instrument_id="fund", index_id="hs300"
    )
    assert output["heldSelected"] is True and output["asOf"] == "2020-01-02"
    assert (
        output["state"] == "UNKNOWN"
        and output["budgetState"] == "UNKNOWN"
        and output["budgetViolation"] is False
    )
    assert "历史快照" in output["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expected,verified,weekday",
    [
        ("2026-10-14", True, True),
        ("2026-10-15", False, True),
        (None, True, True),
        ("2026-10-16", True, False),
    ],
)
async def test_current_snapshot_proof_reuses_calendar_and_verified_publication_hour(
    monkeypatch, expected, verified, weekday
):
    from datetime import date
    from unittest.mock import AsyncMock
    from backend.investment import decision_repository as repo

    now = datetime(2026, 10, 15 if weekday else 18, 10, tzinfo=TZ)
    snapshot = date(2026, 10, 14 if weekday else 16)
    calendar = AsyncMock(
        return_value=date.fromisoformat(expected) if expected else None
    )
    monkeypatch.setattr(repo, "expected_trading_day", calendar, raising=False)
    navs = [{"account_id": "a", "as_of": snapshot, "positions_market_value": 90}]
    positions = [
        {
            "account_id": "a",
            "instrument_id": "fund",
            "as_of": snapshot,
            "quantity": 1,
            "primary_market": "FUND_CN",
            "instrument_metadata": {
                "research": {
                    "sourceVerified": verified,
                    "sourceUri": "https://official.test",
                    "publishHour": 23,
                }
            },
        }
    ]
    await repo.mark_current_valuations(object(), navs, positions, now)
    assert navs[0]["valuation_current"] is (
        expected is not None and date.fromisoformat(expected) <= snapshot
    )
    cutoff = date(2026, 10, 14 if verified else 15) if weekday else date(2026, 10, 17)
    assert calendar.await_args.args[2] == cutoff
    assert calendar.await_args.kwargs["history_start"] == snapshot


@pytest.mark.asyncio
async def test_non_trading_projection_date_cannot_fall_back_to_a_sparse_calendar_tail(
    monkeypatch,
):
    from datetime import date
    from unittest.mock import AsyncMock
    from backend.investment import decision_repository as repo

    calendar = AsyncMock(side_effect=[date(2020, 1, 2), None])
    monkeypatch.setattr(repo, "expected_trading_day", calendar)
    now = datetime(2026, 10, 18, 10, tzinfo=TZ)
    navs = [{"account_id": "a", "as_of": now.date(), "positions_market_value": 90}]
    positions = [
        {
            "account_id": "a",
            "instrument_id": "fund",
            "as_of": now.date(),
            "quantity": 1,
            "primary_market": "SSE",
        }
    ]
    await repo.mark_current_valuations(object(), navs, positions, now)
    assert navs[0]["valuation_current"] is False
    assert calendar.await_args.kwargs["history_start"] == date(2020, 1, 2)


def test_current_source_or_config_change_revokes_prior_validation():
    repo = import_module("backend.investment.decision_repository")
    report = {
        "state": "PASS",
        "sourceMode": "REAL_PIT",
        "pointInTimeVerified": True,
        "sourceUri": "https://source.test/prices",
        "configVersion": "v1",
        "seriesKind": "ETF_HFQ",
        "costsVerified": True,
        "transactionMechanismVerified": True,
        "returnBasisVerified": True,
    }
    facts = {"source": "https://source.test/prices", "seriesKind": "ETF_HFQ"}
    assert repo.validation_matches(report, facts, "v1") is True
    assert repo.validation_matches(report, facts, "v2") is False
    assert (
        repo.validation_matches(report, {**facts, "source": "https://other.test"}, "v1")
        is False
    )


@pytest.mark.asyncio
async def test_context_preserves_confirmed_notice_budget_without_enabling_candidate(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    decision = engine()
    repo = import_module("backend.investment.decision_repository")
    monkeypatch.setattr(repo, "resolve_instrument", AsyncMock(return_value=None))
    monkeypatch.setattr(
        repo,
        "mine_portfolio",
        AsyncMock(
            return_value=(
                [],
                [],
                {
                    "policy_status": "CONFIRMED",
                    "alert_budget_per_day": 3,
                    "default_silence_minutes": 60,
                },
            )
        ),
    )
    monkeypatch.setattr(repo, "list_notices", AsyncMock(return_value=[]))
    result = await decision.decision_context(
        object(),
        "tenant",
        "actor",
        direction_id=None,
        index_id=None,
        instrument_id=None,
    )
    assert result["portfolio"]["noticeBudget"] == 3
    assert result["portfolio"]["silenceMinutes"] == 60
    assert result["decision"]["state"] == "WAIT"


def test_known_revision_changes_proof_hash_but_never_rewrites_earlier_decisions():
    data = dataset()
    before = engine().validate_pit(data)
    correction = {
        **data["points"][100],
        "close": 2000,
        "availableAt": data["points"][300]["decisionAt"],
        "retrievedAt": data["points"][300]["decisionAt"],
    }
    data["points"].append(correction)
    after = engine().validate_pit(data)
    assert before["datasetHash"] != after["datasetHash"]
    assert before["signals"][:48] == after["signals"][:48]


@pytest.mark.asyncio
async def test_context_rejects_research_snapshot_retrieved_after_requested_time(
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from backend.investment import decision_repository as repo, service

    now = datetime(2026, 10, 14, 18, tzinfo=TZ)
    monkeypatch.setattr(repo, "resolve_instrument", AsyncMock(return_value="fund"))
    monkeypatch.setattr(repo, "mine_portfolio", AsyncMock(return_value=([], [], None)))
    monkeypatch.setattr(repo, "latest_validation", AsyncMock(return_value=None))
    monkeypatch.setattr(repo, "list_notices", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "product_quality", AsyncMock(return_value={}))
    monkeypatch.setattr(
        service,
        "analysis",
        AsyncMock(
            return_value={
                "retrievedAt": now + timedelta(days=1),
                "asOf": "2026-10-14",
                "dataQuality": "READY",
                "valueEvidence": {"eligible": True},
                "indicators": {"ma200": 100, "momentum63": 0.2},
                "points": [{"close": 120}],
            }
        ),
    )
    output = await engine().decision_context(
        object(),
        "tenant",
        "actor",
        direction_id=None,
        index_id=None,
        instrument_id="fund",
        now=now,
    )
    states = {row["id"]: row["state"] for row in output["decision"]["gates"]}
    assert states["value"] == states["trend"] == "UNKNOWN"


def test_fixed_rule_can_pass_only_with_executed_switches_and_same_cost_benchmark():
    data = dataset()  # fixture proof is a unit contract, never a deployed validation
    anchors = [(0, 100), (251, 130), (300, 200), (355, 80), (430, 200), (504, 100)]
    for i, row in enumerate(data["points"]):
        for (a, pa), (b, pb) in zip(anchors, anchors[1:]):
            if a <= i <= b:
                row["close"] = pa + (pb - pa) * (i - a) / (b - a)
                break
    output = engine().validate_pit(data)
    assert output["state"] == "PASS"
    assert len(output["executions"]) >= 3
    assert {row["side"] for row in output["executions"]} == {"BUY", "SELL"}
    assert output["netReturn"] > max(0, output["benchmarkNetReturn"])
    assert output["maxDrawdown"] <= 0.35
    assert output["curve"][-1]["nav"] == pytest.approx(1 + output["netReturn"])


@pytest.mark.asyncio
async def test_controlled_validation_writer_rejects_inputs_not_yet_available():
    from unittest.mock import AsyncMock
    from backend.errors import ValidationError_
    from backend.investment import decision_repository as repo

    data = dataset()
    report = engine().validate_pit(data)
    with pytest.raises(ValidationError_):
        await repo.save_validation(
            AsyncMock(), "fund", data, report, data["points"][300]["decisionAt"]
        )


@pytest.mark.parametrize(
    "held,portfolio,expected",
    [
        (None, {}, ("UNKNOWN", "UNKNOWN")),
        (True, {"state": "UNKNOWN", "budgetViolation": False}, ("HELD", "UNKNOWN")),
        (
            True,
            {"state": "READY", "budgetState": "UNKNOWN", "budgetViolation": False},
            ("HELD", "UNKNOWN"),
        ),
        (
            True,
            {"state": "READY", "budgetState": "PASS", "budgetViolation": False},
            ("HELD", "CHECKED"),
        ),
        (
            False,
            {"state": "READY", "budgetState": "PASS", "budgetViolation": False},
            ("NOT_HELD", "CHECKED"),
        ),
        (
            True,
            {"state": "READY", "budgetState": "BLOCKED", "budgetViolation": True},
            ("HELD", "BREACH"),
        ),
        (
            True,
            {"state": "UNKNOWN", "budgetState": "BLOCKED", "budgetViolation": True},
            ("HELD", "UNKNOWN"),
        ),
        (True, {"state": "READY", "budgetState": "PASS"}, ("HELD", "UNKNOWN")),
    ],
)
def test_assessment_keeps_known_holding_separate_from_unknown_risk(
    held, portfolio, expected
):
    result = engine().evaluate(
        gates(),
        held=held,
        portfolio=portfolio,
        direction_model={"researchState": "PRIORITY_RESEARCH"},
    )
    assert result["assessment"] == {
        "holdingState": expected[0],
        "riskState": expected[1],
        "marketResearchState": "PRIORITY_RESEARCH",
    }


def test_legacy_budget_default_and_research_clue_cannot_certify_personal_risk_or_entry():
    changed = gates()
    changed["validation"]["state"] = "UNKNOWN"
    result = engine().evaluate(
        changed, held=True, direction_model={"researchState": "PRIORITY_RESEARCH"}
    )
    assert result["state"] == "HOLD_REVIEW"
    assert result["marketState"] == "WAIT"
    assert result["assessment"]["riskState"] == "UNKNOWN"


def test_personal_actions_only_navigate_to_real_existing_pages():
    result = engine().evaluate(gates(), held=None)
    actions = result["actions"]
    assert {a["type"]: a["href"] for a in actions} == {
        "VIEW_HOLDINGS": "/dashboard?scope=MINE",
        "UPDATE_HOLDINGS": "/onboarding",
        "VIEW_POLICY": "/risk-policy",
    }
    assert all(
        set(a) == {"type", "label", "target", "href", "enabled", "disabledReason"}
        for a in actions
    )
    assert all(a["enabled"] is True and a["disabledReason"] is None for a in actions)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,expected",
    [
        (
            {
                "state": "PARTIAL",
                "qualificationState": "READY",
                "missingRequiredEvidence": [],
                "counterFactors": [],
                "failedConditions": [],
            },
            "PASS",
        ),
        ({"state": "READY", "missingEvidence": [], "counterFactors": []}, "UNKNOWN"),
        (
            {
                "qualificationState": "READY",
                "missingRequiredEvidence": ["INDEX_EARNINGS"],
                "counterFactors": [],
            },
            "UNKNOWN",
        ),
        (
            {
                "qualificationState": "READY",
                "missingRequiredEvidence": [],
                "counterFactors": [{"label": "counter"}],
            },
            "UNKNOWN",
        ),
        (
            {
                "qualificationState": "INCOMPLETE",
                "missingRequiredEvidence": [],
                "researchState": "PRIORITY_RESEARCH",
            },
            "UNKNOWN",
        ),
        (
            {
                "qualificationState": "BLOCKED",
                "missingRequiredEvidence": [],
                "failedConditions": [{"id": "INDEX_TREND"}],
            },
            "BLOCKED",
        ),
    ],
)
async def test_macro_gate_uses_full_qualification_not_limited_research_state(
    monkeypatch, model, expected
):
    from unittest.mock import AsyncMock
    from backend.investment import decision_repository as repo

    monkeypatch.setattr(repo, "resolve_instrument", AsyncMock(return_value=None))
    monkeypatch.setattr(repo, "mine_portfolio", AsyncMock(return_value=([], [], None)))
    monkeypatch.setattr(repo, "list_notices", AsyncMock(return_value=[]))
    output = await engine().decision_context(
        object(),
        "tenant",
        "actor",
        direction_id=None,
        index_id=None,
        instrument_id=None,
        direction_model=model,
    )
    assert (
        next(g["state"] for g in output["decision"]["gates"] if g["id"] == "macro")
        == expected
    )
    assert output["decision"]["state"] == "WAIT"
