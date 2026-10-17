"""Independent market tracks, personal risk and historical PIT reference."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from backend.dependencies import now_shanghai

VERSION = "opportunity-decision-v2"
PIT_VERSION = "trend-pit-v1"
PARAMETERS = {
    "minimumSamples": 505,
    "warmup": 252,
    "oosDecisions": 252,
    "minimumSwitches": 3,
    "executionLag": 1,
    "maximumDrawdown": 0.35,
    "ma": 200,
    "momentum": 63,
}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and math.isfinite(float(result)) else None
    except (ValueError, InvalidOperation, OverflowError):
        return None


def timestamp(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if (
        not isinstance(result, datetime)
        or result.tzinfo is None
        or result.utcoffset() is None
    ):
        raise ValueError("strategy evidence timestamp requires timezone")
    return result


def semantic_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


def evaluate(
    gates=None,
    *,
    held,
    budget_violation=False,
    portfolio=None,
    direction_model=None,
    actionable_zones=None,
):
    """Legacy evidence cannot veto a current track or certify personal risk."""
    tracks = (actionable_zones or {}).get("tracks") or []
    candidates = {row.get("track") for row in tracks if row.get("state") == "CANDIDATE"}
    market_state = (
        "BOTH_CANDIDATE"
        if {"LEFT_VALUE", "RIGHT_TREND"} <= candidates
        else "LEFT_VALUE_CANDIDATE"
        if "LEFT_VALUE" in candidates
        else "RIGHT_TREND_CANDIDATE"
        if "RIGHT_TREND" in candidates
        else "WATCH"
        if any(row.get("state") == "WATCH" for row in tracks)
        else "INSUFFICIENT"
    )
    personal_state = (
        "UNKNOWN"
        if held is None
        else "POSITION_REVIEW"
        if held and budget_violation
        else "HOLD_REVIEW"
        if held
        else "NOT_HELD"
    )
    summary = {
        "LEFT_VALUE_CANDIDATE": "左侧价值/超跌条件触发，查看同口径研究区间。",
        "RIGHT_TREND_CANDIDATE": "右侧趋势条件触发，查看同口径研究区间。",
        "BOTH_CANDIDATE": "左侧与右侧条件同时触发，两条轨道分别保留。",
        "WATCH": "当前可核验条件尚未触发，继续观察。",
        "INSUFFICIENT": "当前可用价格或指标不足，等待补齐实际输入。",
    }[market_state]
    portfolio = portfolio or {}
    research_state = (direction_model or {}).get("researchState")
    if research_state not in {
        "PRIORITY_RESEARCH",
        "WAIT_CONDITIONS",
        "RISK_OBSERVATION",
        "INSUFFICIENT",
    }:
        research_state = "INSUFFICIENT"
    risk_state = "UNKNOWN"
    if portfolio.get("state") == "READY" and held is not None:
        if (
            held is True
            and portfolio.get("budgetState") == "BLOCKED"
            and portfolio.get("budgetViolation") is True
        ):
            risk_state = "BREACH"
        elif (
            portfolio.get("budgetState") == "PASS"
            and portfolio.get("budgetViolation") is False
        ):
            risk_state = "CHECKED"
    return {
        "state": market_state,
        "marketState": market_state,
        "personalState": personal_state,
        "summary": summary,
        "gates": [],
        "nextChecks": list(
            dict.fromkeys(
                text for row in tracks for text in row.get("missingEvidence", [])
            )
        ),
        "actionableZones": actionable_zones,
        "methodVersion": VERSION,
        "assessment": {
            "holdingState": "HELD"
            if held is True
            else "NOT_HELD"
            if held is False
            else "UNKNOWN",
            "riskState": risk_state,
            "marketResearchState": research_state,
        },
        "actions": [
            {
                "type": kind,
                "label": label,
                "target": target,
                "href": href,
                "enabled": True,
                "disabledReason": None,
            }
            for kind, label, target, href in (
                ("VIEW_HOLDINGS", "查看我的持仓", "HOLDINGS", "/dashboard?scope=MINE"),
                ("VIEW_POLICY", "查看风险政策", "POLICY", "/risk-policy"),
                ("UPDATE_HOLDINGS", "补充持仓数据", "HOLDINGS_IMPORT", "/onboarding"),
            )
        ],
    }


def validate_pit(dataset):
    result = {
        "state": "UNAVAILABLE",
        "methodVersion": PIT_VERSION,
        "parameters": dict(PARAMETERS),
        "sourceMode": dataset.get("sourceMode"),
        "pointInTimeVerified": dataset.get("pointInTimeVerified") is True,
        "summary": "缺少真实历史时点、来源口径、费用或交易机制证明。",
        "limitations": [
            "仅验证规定窗口和固定研究规则；不保证未来收益。",
            "收盘执行是研究假设，不代表真实成交记录。",
        ],
    }
    if dataset.get("sourceMode") != "REAL_PIT" or not all(
        dataset.get(key) is True
        for key in (
            "pointInTimeVerified",
            "returnBasisVerified",
            "costsVerified",
            "transactionMechanismVerified",
        )
    ):
        return result
    if (
        dataset.get("seriesKind") not in {"ETF_HFQ", "PROVIDER_RETURN_INDEX"}
        or not dataset.get("sourceUri")
        or not dataset.get("configVersion")
    ):
        return result
    fee, slippage = number(dataset.get("feeBps")), number(dataset.get("slippageBps"))
    if (
        fee is None
        or slippage is None
        or fee < 0
        or slippage < 0
        or fee + slippage >= 10000
    ):
        return result
    points = dataset.get("points") or []
    by_day = {}
    try:
        for row in points:
            day = date.fromisoformat(row["date"])
            if (
                row.get("isTradingDay") is not True
                or row.get("sourceUri") != dataset["sourceUri"]
                or row.get("configVersion") != dataset["configVersion"]
            ):
                return result
            price = number(row.get("close"))
            if price is None or price <= 0:
                return {
                    **result,
                    "state": "CONFLICT",
                    "summary": "价格数据不是有效有限正数。",
                }
            normalized = {
                **row,
                "date": day,
                "close": price,
                "availableAt": timestamp(row["availableAt"]),
                "retrievedAt": timestamp(row["retrievedAt"]),
                "decisionAt": timestamp(row["decisionAt"]),
            }
            if normalized["decisionAt"].date() != day:
                return result
            by_day.setdefault(day, []).append(normalized)
    except (ValueError, KeyError, TypeError):
        return result
    days = sorted(by_day)
    if len(days) < 505:
        return {
            **result,
            "state": "INSUFFICIENT",
            "summary": "至少需要505个有时点证明的真实交易观察值。",
            "sampleCount": len(days),
        }
    days = days[:505]
    base = []
    for day in days:
        # Initial vintage defines the decision clock; later revisions cannot move it.
        first = min(
            by_day[day], key=lambda row: (row["retrievedAt"], row["availableAt"])
        )
        if (
            first["availableAt"] > first["decisionAt"]
            or first["retrievedAt"] > first["decisionAt"]
        ):
            return result
        base.append(first)
    cost = (fee + slippage) / 10000
    capital, peak, drawdown = Decimal(1), Decimal(1), Decimal(0)
    holding = False
    executions, signals, curve = [], [], []
    previous_price = base[252]["close"]
    for i in range(252, 504):
        at = base[i]["decisionAt"]
        prefix, vintages = [], []
        for day in days[: i + 1]:
            candidates = [
                row
                for row in by_day[day]
                if row["availableAt"] <= at and row["retrievedAt"] <= at
            ]
            if not candidates:
                return result
            current = max(
                candidates, key=lambda row: (row["retrievedAt"], row["availableAt"])
            )
            prefix.append(current["close"])
            vintages.append(
                {
                    key: current[key]
                    for key in (
                        "date",
                        "close",
                        "retrievedAt",
                        "availableAt",
                        "sourceUri",
                        "configVersion",
                    )
                }
            )
        desired = (
            prefix[-1] > sum(prefix[-200:]) / 200 and prefix[-1] / prefix[-64] - 1 > 0
        )
        signals.append(
            {
                "decisionDate": days[i].isoformat(),
                "decisionAt": at.isoformat(),
                "target": "INVESTED" if desired else "CASH",
                "inputHash": semantic_hash(vintages),
            }
        )
        execution = base[i + 1]
        if execution["decisionAt"] <= at:
            return result
        price = execution["close"]
        # Existing holdings earn the interval before the next-close order executes.
        if holding:
            capital *= price / previous_price
        if desired != holding:
            capital *= 1 - cost
            holding = desired
            executions.append(
                {
                    "decisionDate": days[i].isoformat(),
                    "executionDate": days[i + 1].isoformat(),
                    "side": "BUY" if desired else "SELL",
                    "costRate": float(cost),
                    "price": float(price),
                }
            )
        previous_price = price
        peak = max(peak, capital)
        drawdown = max(drawdown, 1 - capital / peak)
        curve.append({"date": days[i + 1].isoformat(), "nav": float(capital)})
    # Same next-close entry, same terminal liquidation costs for the benchmark.
    if holding:
        capital *= 1 - cost
        curve[-1]["nav"] = float(capital)
    benchmark_gross = base[-1]["close"] / base[253]["close"] - 1
    benchmark_net = (1 + benchmark_gross) * (1 - cost) ** 2 - 1
    peak = max(peak, capital)
    drawdown = max(drawdown, 1 - capital / peak)
    passed = (
        len(executions) >= 3
        and any(row["side"] == "BUY" for row in executions)
        and any(row["side"] == "SELL" for row in executions)
        and capital > 1
        and capital - 1 > benchmark_net
        and drawdown <= Decimal("0.35")
    )
    return {
        **result,
        "state": "PASS" if passed else "NOT_VALIDATED",
        "summary": "规定样本外研究门槛已通过。"
        if passed
        else "样本外成交次数、净收益/基准或回撤门槛未全部通过。",
        "executions": executions,
        "signals": signals,
        "curve": curve,
        "sampleCount": 505,
        "netReturn": float(capital - 1),
        "maxDrawdown": float(drawdown),
        "benchmarkNetReturn": float(benchmark_net),
        "benchmarkGrossReturn": float(benchmark_gross),
        "datasetHash": semantic_hash(
            {
                "source": dataset["sourceUri"],
                "config": dataset["configVersion"],
                "initialVintages": base,
                "decisionInputs": [row["inputHash"] for row in signals],
            }
        ),
        "configHash": semantic_hash(
            {**PARAMETERS, "feeBps": fee, "slippageBps": slippage}
        ),
        "windowStart": days[252].isoformat(),
        "windowEnd": days[-1].isoformat(),
        "openPositionAtEnd": holding,
    }


def portfolio_summary(
    accounts,
    positions,
    policy,
    *,
    instrument_id,
    index_id=None,
    cashflow_adjusted_drawdown=None,
):
    result = {
        "state": "UNKNOWN",
        "heldSelected": None,
        "selectedWeight": None,
        "sameIndexWeight": None,
        "cashRatio": None,
        "asOf": None,
        "summary": "个人持仓、估值或预算证据尚未完整核查。",
        "missingEvidence": [],
        "budgetViolation": False,
        "budgetState": "UNKNOWN",
    }
    if not instrument_id or not accounts:
        result["missingEvidence"] = ["唯一基金身份或个人账户快照"]
        return result
    owned = {str(row["account_id"]) for row in accounts}
    valid_positions = [row for row in positions if str(row["account_id"]) in owned]
    quantities = [number(row.get("quantity")) for row in valid_positions]
    if any(value is None or value < 0 for value in quantities):
        result["missingEvidence"] = ["持仓数量"]
        return result
    result["heldSelected"] = any(
        str(row["instrument_id"]) == str(instrument_id) and number(row["quantity"]) > 0
        for row in valid_positions
    )
    known_dates = {
        str(row["as_of"]) for row in accounts if row.get("as_of") is not None
    }
    if len(known_dates) == 1 and all(row.get("as_of") is not None for row in accounts):
        result["asOf"] = next(iter(known_dates))
    for account in accounts:
        current_positions = [
            row
            for row in valid_positions
            if str(row["account_id"]) == str(account["account_id"])
        ]
        projected = [number(row.get("market_value")) for row in current_positions]
        disclosed = number(account.get("positions_market_value"))
        if "positions_market_value" in account and (
            disclosed is None
            or any(v is None for v in projected)
            or abs(sum(projected) - disclosed) > Decimal("0.01")
        ):
            if result["heldSelected"] is False:
                result["heldSelected"] = None
            result["missingEvidence"] = ["完整且一致的持仓投影"]
            return result
    dates = {str(row["as_of"]) for row in accounts + valid_positions}
    currencies = {row.get("base_currency") for row in accounts} | {
        row.get("currency") for row in valid_positions
    }
    values = (
        [number(row.get("total_nav")) for row in accounts]
        + [number(row.get("cash_balance")) for row in accounts]
        + [number(row.get("market_value")) for row in valid_positions]
    )
    if (
        len(dates) != 1
        or len(currencies) != 1
        or None in currencies
        or any(value is None or value < 0 for value in values)
        or any(number(row.get("valuation_coverage_pct")) != 100 for row in accounts)
        or any(
            row.get("data_quality_status", "FRESH") != "FRESH"
            for row in accounts + valid_positions
        )
    ):
        result["missingEvidence"] = ["同一时点、同币种的完整个人估值"]
        return result
    total = sum(number(row["total_nav"]) for row in accounts)
    cash = sum(number(row["cash_balance"]) for row in accounts)
    if total <= 0 or cash > total:
        result["missingEvidence"] = ["有效资产总额与现金"]
        return result
    if any(row.get("valuation_current") is not True for row in accounts):
        result["missingEvidence"] = ["当前可得估值日的完整个人估值"]
        result["summary"] = (
            "保留已识别持仓与最近历史快照；当前估值日尚未完整核验，个人风险未知。"
        )
        return result
    selected = sum(
        number(row["market_value"])
        for row in valid_positions
        if str(row["instrument_id"]) == str(instrument_id)
    )
    mapped = all(
        len(row.get("index_ids") or []) == 1
        for row in valid_positions
        if number(row["quantity"]) > 0
    )
    same_index = (
        sum(
            number(row["market_value"])
            for row in valid_positions
            if index_id in (row.get("index_ids") or [])
        )
        if index_id and mapped
        else None
    )
    result.update(
        state="READY",
        selectedWeight=float(selected / total),
        sameIndexWeight=float(same_index / total) if same_index is not None else None,
        cashRatio=float(cash / total),
        asOf=next(iter(dates)),
        summary="仅核查当前用户自己的及共同所有账户。",
    )
    if not policy or policy.get("policy_status") != "CONFIRMED":
        result["missingEvidence"].append("已确认个人风险预算")
        return result
    limit, floor, dd_limit = (
        number(policy.get(key))
        for key in ("single_position_limit", "cash_target_pct", "max_drawdown_limit")
    )
    if any(
        value is None or value < 0 or value > 100 for value in (limit, floor, dd_limit)
    ):
        result["missingEvidence"].append("完整风险限制")
        return result
    concentration = (
        max(
            Decimal(str(result["selectedWeight"])),
            Decimal(str(result["sameIndexWeight"])),
        )
        if same_index is not None
        else Decimal(str(result["selectedWeight"]))
    )
    violation = concentration > limit / 100 or cash / total < floor / 100
    adjusted = number(cashflow_adjusted_drawdown)
    if adjusted is not None:
        violation = violation or adjusted > dd_limit / 100
    else:
        result["missingEvidence"].append("现金流调整后的组合回撤")
    if index_id and not mapped:
        result["missingEvidence"].append("同指数完整敞口映射")
    result.update(
        budgetViolation=bool(violation),
        budgetState="BLOCKED"
        if violation
        else "UNKNOWN"
        if result["missingEvidence"]
        else "PASS",
    )
    return result


async def decision_context(
    conn,
    tenant_id,
    user_id,
    *,
    direction_id,
    index_id,
    instrument_id,
    fund_code=None,
    fund_market=None,
    evaluated_index_id=None,
    market_evidence=None,
    direction_model=None,
    now=None,
):
    from backend.investment import decision_repository as repository
    from backend.investment.service import analysis, actionable_plan_for_analysis

    now = now or now_shanghai()
    identity = await repository.resolve_instrument(
        conn, instrument_id, fund_code, fund_market
    )
    accounts, positions, policy = await repository.mine_portfolio(
        conn, tenant_id, user_id, now
    )
    portfolio = portfolio_summary(
        accounts,
        positions,
        policy,
        instrument_id=identity,
        index_id=evaluated_index_id or index_id,
    )
    if policy and policy.get("policy_status") == "CONFIRMED":
        portfolio.update(
            noticeBudget=policy.get("alert_budget_per_day"),
            silenceMinutes=policy.get("default_silence_minutes"),
        )
    report = (
        await repository.latest_validation(conn, identity, now) if identity else None
    )
    validation = {
        **(report or {}),
        "state": (report or {}).get("state", "UNAVAILABLE"),
        "summary": (report or {}).get("summary", "尚无真实时点样本外验证报告。"),
        "methodVersion": PIT_VERSION,
    }
    facts = {}
    if identity:
        try:
            facts = await analysis(conn, "fund", identity)
        except Exception:  # noqa: BLE001 -- missing research never implies candidate qualification
            facts = {}
        try:
            if (
                timestamp(facts.get("retrievedAt")) > now
                or date.fromisoformat(str(facts.get("asOf"))[:10]) > now.date()
            ):
                facts = {}
        except (TypeError, ValueError):
            facts = {}
    product = await repository.product_quality(conn, identity, now) if identity else {}
    if validation.get("state") == "PASS" and not repository.validation_matches(
        validation, facts, product.get("configVersion")
    ):
        validation.update(
            state="UNAVAILABLE",
            summary="验证报告与当前来源、收益口径或已确认配置不一致，需重新核验。",
        )
    plan = None
    if identity:
        plan = actionable_plan_for_analysis(facts, "fund", identity, now=now)
    elif not (instrument_id or fund_code):
        selected_index = evaluated_index_id or index_id
        possible = [
            row.get("actionableZones")
            for row in market_evidence or []
            if (not selected_index or row.get("indexId") == selected_index)
            and isinstance(row.get("actionableZones"), dict)
        ]
        if len(possible) == 1:
            plan = possible[0]
    decision = evaluate(
        held=portfolio["heldSelected"],
        budget_violation=portfolio["budgetViolation"],
        portfolio=portfolio,
        direction_model=direction_model,
        actionable_zones=plan,
    )
    decision["validation"] = validation
    decision["valueEvidence"] = facts.get("valueEvidence") or {"state": "INCOMPLETE"}
    notices = await repository.list_notices(
        conn,
        tenant_id,
        user_id,
        direction_id=direction_id,
        index_id=index_id,
        instrument_id=instrument_id,
        fund_code=fund_code,
        fund_market=fund_market,
        exact_scope=True,
    )
    return {"decision": decision, "portfolio": portfolio, "notices": notices}
