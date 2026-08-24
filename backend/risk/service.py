"""Risk service: current policy + version history, and computed budget checks.

The rule engine is deterministic and read-only: it compares current portfolio
state (positions, NAV history, cash) against the CONFIRMED policy limits and
reports OK / WARNING / VIOLATION. It never mutates positions or places orders.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal

import asyncpg

from backend.common.formatting import fmt_money, merge_quality, pct
from backend.errors import DataUnavailableError
from backend.portfolio import repository as portfolio_repo
from backend.portfolio.service import ZERO, _d

POLICY_FIELDS = (
    "max_drawdown_limit",
    "single_position_limit",
    "equity_target_pct",
    "bond_target_pct",
    "gold_target_pct",
    "cash_target_pct",
    "rebalance_tolerance_pct",
    "investment_horizon_months",
    "cash_need_amount",
    "alert_budget_per_day",
)


def _policy_hash(row: asyncpg.Record) -> str:
    canonical = json.dumps(
        {field: (str(row[field]) if row[field] is not None else None) for field in POLICY_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_policies(
    conn: asyncpg.Connection, tenant_id: str
) -> dict:
    """当前生效政策 + 全部版本历史 + 风险预算。"""
    current = await conn.fetchrow(
        """
        SELECT p.*, prof.nickname AS confirmed_by_nickname
        FROM risk.investment_policies p
        LEFT JOIN identity.profiles prof ON prof.id = p.confirmed_by
        WHERE p.tenant_id = $1
          AND p.policy_status = 'CONFIRMED'
          AND (p.effective_to IS NULL OR p.effective_to >= CURRENT_DATE)
        ORDER BY p.version DESC
        LIMIT 1
        """,
        tenant_id,
    )
    history_rows = await conn.fetch(
        """
        SELECT p.*, prof.nickname AS confirmed_by_nickname
        FROM risk.investment_policies p
        LEFT JOIN identity.profiles prof ON prof.id = p.confirmed_by
        WHERE p.tenant_id = $1
        ORDER BY p.version DESC
        """,
        tenant_id,
    )

    current_dto = _policy_dto(current) if current else None
    history = [_policy_dto(r) for r in history_rows]
    return {
        "current": current_dto,
        "history": history,
        "budget": (
            _budget_dto(current) if current else _default_budget()
        ),
    }


def _policy_dto(row: asyncpg.Record) -> dict:
    return {
        "id": str(row["id"]),
        "version": row["version"],
        "status": row["policy_status"],
        "effectiveFrom": row["effective_from"].isoformat(),
        "effectiveTo": row["effective_to"].isoformat() if row["effective_to"] else None,
        "confirmedAt": row["confirmed_at"],
        "confirmedBy": row["confirmed_by_nickname"] or (
            str(row["confirmed_by"]) if row["confirmed_by"] else None
        ),
        "confirmationProvenance": row["confirmation_provenance"],
        "policyHash": _policy_hash(row),
        "accountId": str(row["account_id"]) if row["account_id"] else None,
    }


def _budget_dto(row: asyncpg.Record) -> dict:
    return {
        "maxDrawdownLimit": _pct_str(row["max_drawdown_limit"]),
        "singlePositionLimit": _pct_str(row["single_position_limit"]),
        "equityTargetPct": _pct_str(row["equity_target_pct"]),
        "bondTargetPct": _pct_str(row["bond_target_pct"]),
        "goldTargetPct": _pct_str(row["gold_target_pct"]),
        "cashTargetPct": _pct_str(row["cash_target_pct"]),
        "rebalanceTolerancePct": _pct_str(row["rebalance_tolerance_pct"]),
        "minCashRatio": _pct_str(row["cash_target_pct"]),
        "investmentHorizonMonths": row["investment_horizon_months"],
        "cashNeedAmount": fmt_money(row["cash_need_amount"]) if row["cash_need_amount"] is not None else None,
        "allowedAssetTypes": row["allowed_asset_types"],
        "alertBudgetPerDay": row["alert_budget_per_day"],
    }


def _default_budget() -> dict:
    return {
        "maxDrawdownLimit": None,
        "singlePositionLimit": None,
        "equityTargetPct": None,
        "bondTargetPct": None,
        "goldTargetPct": None,
        "cashTargetPct": None,
        "rebalanceTolerancePct": None,
        "minCashRatio": None,
        "investmentHorizonMonths": None,
        "cashNeedAmount": None,
        "allowedAssetTypes": None,
        "alertBudgetPerDay": None,
    }


def _pct_str(value: object) -> str | None:
    return fmt_money(_d(value)) if value is not None else None


async def get_rules(
    conn: asyncpg.Connection, tenant_id: str
) -> dict:
    """风控规则列表 + 基于当前组合状态计算的预算合规检查。"""
    rules = await conn.fetch(
        """
        SELECT ar.id, ar.rule_type, ar.rule_params, ar.silence_window_minutes,
               ar.status, i.canonical_name AS instrument_name,
               (SELECT MAX(triggered_at) FROM risk.alert_events ae
                WHERE ae.tenant_id = ar.tenant_id AND ae.rule_id = ar.id) AS last_triggered_at
        FROM risk.alert_rules ar
        LEFT JOIN reference.instruments i ON i.id = ar.instrument_id
        WHERE ar.tenant_id = $1
        ORDER BY ar.created_at ASC
        """,
        tenant_id,
    )
    rules_dto = [
        {
            "id": str(r["id"]),
            "ruleType": r["rule_type"],
            "name": _rule_name(r["rule_type"], r["instrument_name"]),
            "params": r["rule_params"],
            "silenceWindowMinutes": r["silence_window_minutes"],
            "status": r["status"],
            "instrumentName": r["instrument_name"],
            "lastTriggeredAt": r["last_triggered_at"],
        }
        for r in rules
    ]

    checks = await _compute_checks(conn, tenant_id)
    return {"rules": rules_dto, "checks": checks}


def _rule_name(rule_type: str, instrument: str | None) -> str:
    names = {
        "PRICE_TOUCH_STOP_LOSS": "价格止损触点",
        "PRICE_TOUCH_TAKE_PROFIT": "价格止盈触点",
        "ESTIMATED_NAV_SWING": "基金估值异动",
        "MACRO_CYCLE_SHIFT": "宏观周期信号",
    }
    base = names.get(rule_type, rule_type)
    return f"{base} · {instrument}" if instrument else base


async def _compute_checks(
    conn: asyncpg.Connection, tenant_id: str
) -> list[dict]:
    policy = await conn.fetchrow(
        """
        SELECT single_position_limit, max_drawdown_limit, cash_target_pct
        FROM risk.investment_policies
        WHERE tenant_id = $1 AND policy_status = 'CONFIRMED'
          AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
        ORDER BY version DESC
        LIMIT 1
        """,
        tenant_id,
    )
    if policy is None:
        return []

    navs = await portfolio_repo.latest_nav_per_account(conn, tenant_id)
    if not navs:
        raise DataUnavailableError("暂无资产快照，无法计算风控检查", retryable=False)
    total_asset = sum((_d(r["total_nav"]) for r in navs), ZERO)
    cash = sum((_d(r["cash_balance"]) for r in navs), ZERO)
    positions = await portfolio_repo.latest_positions(conn, tenant_id)

    checks: list[dict] = []

    # 1. 单一标的最大仓位
    limit = _d(policy["single_position_limit"])
    if limit:
        max_weight = max((_d(p["market_value"]) for p in positions), default=ZERO)
        weight_pct = pct(max_weight, total_asset)
        checks.append(
            {
                "check": "singlePositionLimit",
                "name": "单一标的最大仓位",
                "limit": fmt_money(limit),
                "current": fmt_money(weight_pct),
                "status": "VIOLATION" if weight_pct > limit else (
                    "WARNING" if weight_pct > limit * Decimal("0.9") else "OK"
                ),
                "message": (
                    f"最大单一持仓占比 {fmt_money(weight_pct)}% 超过政策上限 {fmt_money(limit)}%"
                    if weight_pct > limit
                    else f"最大单一持仓占比 {fmt_money(weight_pct)}%，政策上限 {fmt_money(limit)}%"
                ),
            }
        )

    # 2. 组合最大回撤（近 60 个交易日峰值回撤）
    drawdown_limit = _d(policy["max_drawdown_limit"])
    history = await portfolio_repo.nav_history(conn, tenant_id, limit=60)
    if drawdown_limit and len(history) >= 2:
        peak = Decimal("-inf")
        max_drawdown = Decimal("0.00")
        for row in history:
            nav = _d(row["total_nav"])
            peak = max(peak, nav)
            if peak > 0:
                drawdown = (peak - nav) / peak * Decimal(100)
                max_drawdown = max(max_drawdown, drawdown)
        current_dd = max_drawdown.quantize(Decimal("0.01"))
        checks.append(
            {
                "check": "maxDrawdown",
                "name": "组合最大回撤",
                "limit": fmt_money(drawdown_limit),
                "current": fmt_money(current_dd),
                "status": "VIOLATION" if current_dd > drawdown_limit else (
                    "WARNING" if current_dd > drawdown_limit * Decimal("0.8") else "OK"
                ),
                "message": f"近 60 交易日峰值回撤 {fmt_money(current_dd)}%，政策上限 {fmt_money(drawdown_limit)}%",
            }
        )

    # 3. 最低现金比例
    cash_target = _d(policy["cash_target_pct"])
    if cash_target:
        cash_pct = pct(cash, total_asset)
        floor = cash_target * Decimal("0.5")
        checks.append(
            {
                "check": "minCashRatio",
                "name": "最低现金比例",
                "limit": fmt_money(cash_target),
                "current": fmt_money(cash_pct),
                "status": "VIOLATION" if cash_pct < floor else (
                    "WARNING" if cash_pct < cash_target else "OK"
                ),
                "message": f"现金占比 {fmt_money(cash_pct)}%，目标 {fmt_money(cash_target)}%",
            }
        )

    return checks
