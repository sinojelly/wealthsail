"""Risk service: current policy + version history, and computed budget checks.

The rule engine is deterministic and read-only: it compares current portfolio
state (positions, NAV history, cash) against the CONFIRMED policy limits and
reports OK / WARNING / VIOLATION. It never mutates positions or places orders.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import asyncpg

from backend.common.auth import verify_password
from backend.common.formatting import fmt_money, pct
from backend.common.tenancy import TenantContext
from backend.dependencies import write_audit
from backend.errors import (
    AuthRequiredError,
    DataUnavailableError,
    ForbiddenError,
    NotFoundError,
    ValidationError_,
    VersionConflictError,
)
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

L1_POLICY_FIELDS = (
    "benchmark",
    "horizonMonths",
    "maxPosition",
    "maxDrawdown",
    "minCash",
    "maxAlertsPerDay",
    "silenceMinutes",
)


def _canonical_decimal(value: object) -> str:
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise ValidationError_("风险预算必须是有限数值")
    return format(decimal.normalize(), "f")


def canonical_policy_hash(values: dict) -> str:
    """Hash every L1 field that can affect preview/confirmation semantics."""
    missing = [field for field in L1_POLICY_FIELDS if field not in values]
    if missing:
        raise ValidationError_(f"风险政策缺少字段: {', '.join(missing)}")
    canonical = {
        "benchmark": str(values["benchmark"]).strip().upper(),
        "horizonMonths": int(values["horizonMonths"]),
        "maxPosition": _canonical_decimal(values["maxPosition"]),
        "maxDrawdown": _canonical_decimal(values["maxDrawdown"]),
        "minCash": _canonical_decimal(values["minCash"]),
        "maxAlertsPerDay": int(values["maxAlertsPerDay"]),
        "silenceMinutes": int(values["silenceMinutes"]),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_l1_policy(values: dict) -> dict:
    policy_hash = canonical_policy_hash(values)
    normalized = {
        "benchmark": str(values["benchmark"]).strip().upper(),
        "horizonMonths": int(values["horizonMonths"]),
        "maxPosition": Decimal(str(values["maxPosition"])),
        "maxDrawdown": Decimal(str(values["maxDrawdown"])),
        "minCash": Decimal(str(values["minCash"])),
        "maxAlertsPerDay": int(values["maxAlertsPerDay"]),
        "silenceMinutes": int(values["silenceMinutes"]),
        "policyHash": policy_hash,
    }
    ranges = (
        ("maxPosition", normalized["maxPosition"], Decimal("1"), Decimal("100")),
        ("maxDrawdown", normalized["maxDrawdown"], Decimal("1"), Decimal("80")),
        ("minCash", normalized["minCash"], Decimal("0"), Decimal("100")),
    )
    for field, value, lower, upper in ranges:
        if value < lower or value > upper:
            raise ValidationError_(f"{field} 超出允许范围")
    if not 3 <= normalized["horizonMonths"] <= 120:
        raise ValidationError_("horizonMonths 超出允许范围")
    if not 1 <= normalized["maxAlertsPerDay"] <= 20:
        raise ValidationError_("maxAlertsPerDay 超出允许范围")
    if not 5 <= normalized["silenceMinutes"] <= 1440:
        raise ValidationError_("silenceMinutes 超出允许范围")
    return normalized


async def preview_policy(
    conn: asyncpg.Connection,
    ctx: TenantContext,
    values: dict,
    *,
    idempotency_key: str,
) -> dict:
    """Persist an auditable PREVIEW row; it has no effect on risk checks."""
    if ctx.tenant_role not in {"OWNER", "ADMIN", "EDITOR"}:
        raise ForbiddenError("当前角色不能编辑风险政策")
    if not idempotency_key:
        raise ValidationError_("缺少 Idempotency-Key")
    normalized = _validate_l1_policy(values)
    existing = await conn.fetchrow(
        """
        SELECT id, version, policy_hash, single_position_limit
          FROM risk.investment_policies
         WHERE tenant_id=$1 AND idempotency_key=$2
        """,
        ctx.tenant_id,
        idempotency_key,
    )
    if existing:
        if existing["policy_hash"] != normalized["policyHash"]:
            raise VersionConflictError("同一幂等键对应了不同政策内容")
        return await _preview_dto(conn, ctx.tenant_id, existing)

    next_version = int(
        await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM risk.investment_policies WHERE tenant_id=$1",
            ctx.tenant_id,
        )
    )
    row = await conn.fetchrow(
        """
        INSERT INTO risk.investment_policies
            (tenant_id, max_drawdown_limit, single_position_limit,
             cash_target_pct, investment_horizon_months, alert_budget_per_day,
             benchmark_code, default_silence_minutes, policy_status, version,
             policy_hash, previewed_at, idempotency_key)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'PREVIEW',$9,$10,now(),$11)
        RETURNING id, version, policy_hash, single_position_limit
        """,
        ctx.tenant_id,
        normalized["maxDrawdown"],
        normalized["maxPosition"],
        normalized["minCash"],
        normalized["horizonMonths"],
        normalized["maxAlertsPerDay"],
        normalized["benchmark"],
        normalized["silenceMinutes"],
        next_version,
        normalized["policyHash"],
        idempotency_key,
    )
    await write_audit(
        conn,
        tenant_id=ctx.tenant_id,
        actor_type="USER",
        actor_id=ctx.user_id,
        action="RISK_POLICY_PREVIEWED",
        resource_type="risk.investment_policies",
        resource_id=str(row["id"]),
        details={"version": next_version, "policy_hash": normalized["policyHash"]},
    )
    return await _preview_dto(conn, ctx.tenant_id, row)


async def _preview_dto(conn, tenant_id: str, row) -> dict:
    current_limit = await conn.fetchval(
        """
        SELECT single_position_limit FROM risk.investment_policies
         WHERE tenant_id=$1 AND policy_status='CONFIRMED'
         ORDER BY version DESC LIMIT 1
        """,
        tenant_id,
    )
    impacted = await conn.fetchval(
        """
        WITH latest_nav AS (
            SELECT CASE WHEN COUNT(*) = COUNT(total_nav)
                        THEN SUM(total_nav) END total_nav
              FROM (
                SELECT DISTINCT ON (account_id) account_id,total_nav
                  FROM ledger.account_nav_snapshots
                 WHERE tenant_id=$1 ORDER BY account_id,as_of DESC
            ) n
        ), latest_positions AS (
            SELECT DISTINCT ON (account_id,instrument_id) market_value
              FROM ledger.position_snapshots
             WHERE tenant_id=$1 ORDER BY account_id,instrument_id,as_of DESC
        )
        SELECT COUNT(*) FROM latest_positions, latest_nav
         WHERE market_value IS NOT NULL AND total_nav > 0
           AND market_value / total_nav * 100 > $2
        """,
        tenant_id,
        row["single_position_limit"],
    )
    impacted_count = int(impacted or 0)
    return {
        "draftId": str(row["id"]),
        "expectedVersion": int(row["version"]),
        "previousMaxPosition": fmt_money(current_limit) if current_limit is not None else None,
        "nextMaxPosition": fmt_money(row["single_position_limit"]),
        "impactedHoldings": impacted_count,
        "newAlerts": impacted_count,
        "policyHash": row["policy_hash"],
        "status": "PREVIEW",
    }


async def confirm_policy(
    conn: asyncpg.Connection,
    ctx: TenantContext,
    *,
    draft_id: str,
    expected_version: int,
    policy_hash: str,
    password: str,
) -> dict:
    """Reauthenticate and atomically promote a PREVIEW to CONFIRMED."""
    if ctx.tenant_role not in {"OWNER", "ADMIN"}:
        raise ForbiddenError("只有所有者或管理员可以确认风险政策")
    draft = await conn.fetchrow(
        "SELECT * FROM risk.investment_policies WHERE tenant_id=$1 AND id=$2 FOR UPDATE",
        ctx.tenant_id,
        draft_id,
    )
    if draft is None:
        raise NotFoundError("风险政策预览不存在")
    if draft["policy_status"] == "CONFIRMED":
        if draft["policy_hash"] != policy_hash:
            raise VersionConflictError("政策哈希不匹配")
        return {"id": str(draft["id"]), "version": draft["version"], "status": "CONFIRMED", "policyHash": draft["policy_hash"]}
    if (
        draft["policy_status"] != "PREVIEW"
        or draft["version"] != expected_version
        or draft["policy_hash"] != policy_hash
    ):
        raise VersionConflictError("政策预览已变化，请重新预览")
    password_hash = await conn.fetchval(
        "SELECT password_hash FROM identity.profiles WHERE id=$1", ctx.user_id
    )
    if not verify_password(password, password_hash):
        raise AuthRequiredError("重新验证口令失败")

    current = await conn.fetchrow(
        """
        SELECT id, version FROM risk.investment_policies
         WHERE tenant_id=$1 AND policy_status='CONFIRMED'
         ORDER BY version DESC LIMIT 1 FOR UPDATE
        """,
        ctx.tenant_id,
    )
    if current:
        await conn.execute(
            """
            UPDATE risk.investment_policies
               SET policy_status='RETIRED', effective_to=CURRENT_DATE,
                   version=version+1
             WHERE id=$1
            """,
            current["id"],
        )
    row = await conn.fetchrow(
        """
        UPDATE risk.investment_policies
           SET policy_status='CONFIRMED', version=version+1,
               confirmed_at=now(), confirmed_by=$2,
               confirmation_provenance='USER_CONFIRMED'
         WHERE id=$1
        RETURNING id, version, policy_status, policy_hash
        """,
        draft_id,
        ctx.user_id,
    )
    await write_audit(
        conn,
        tenant_id=ctx.tenant_id,
        actor_type="USER",
        actor_id=ctx.user_id,
        action="RISK_POLICY_CONFIRMED",
        resource_type="risk.investment_policies",
        resource_id=str(row["id"]),
        details={"version": row["version"], "policy_hash": row["policy_hash"]},
    )
    return {
        "id": str(row["id"]),
        "version": row["version"],
        "status": row["policy_status"],
        "policyHash": row["policy_hash"],
    }


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
    if any(row["total_nav"] is None for row in navs):
        raise DataUnavailableError(
            "组合估值不完整，无法安全计算仓位、现金比例和回撤检查",
            retryable=False,
        )
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
    complete_history = [row for row in history if row["total_nav"] is not None]
    if (
        drawdown_limit
        and len(complete_history) == len(history)
        and len(complete_history) >= 2
    ):
        peak = Decimal("-inf")
        max_drawdown = Decimal("0.00")
        for row in complete_history:
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
