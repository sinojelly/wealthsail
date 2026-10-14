"""MINE-scoped research, opt-in monitors, and private product-only notices."""

import json
from datetime import timedelta

from backend.errors import NotFoundError, ValidationError_
from backend.investment.decision import (
    PIT_VERSION,
    VERSION,
    semantic_hash,
    timestamp,
    number,
)


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def scope_key(scope):
    return semantic_hash(
        {
            key: str(scope[key]) if scope.get(key) is not None else None
            for key in (
                "direction_id",
                "index_id",
                "instrument_id",
                "fund_code",
                "fund_market",
            )
        }
    )


async def resolve_instrument(conn, instrument_id, fund_code=None, fund_market=None):
    if instrument_id:
        return await conn.fetchval(
            """SELECT i.id::text FROM reference.instruments i WHERE i.id::text=$1 AND lifecycle_status='ACTIVE'
              AND asset_type IN ('EXCHANGE_FUND','OFF_EXCHANGE_FUND') AND ($3::text IS NULL OR i.primary_market=$3)
              AND ($2::text IS NULL OR EXISTS(SELECT 1 FROM reference.research_fund_catalog f WHERE f.instrument_id=i.id AND f.fund_code=$2 AND ($3::text IS NULL OR f.market=$3)))""",
            str(instrument_id),
            fund_code,
            fund_market,
        )
    if not fund_code:
        return None
    rows = await conn.fetch(
        """SELECT DISTINCT instrument_id::text FROM reference.research_fund_catalog
      WHERE fund_code=$1 AND ($2::text IS NULL OR market=$2) AND instrument_id IS NOT NULL""",
        fund_code,
        fund_market,
    )
    ids = {str(row["instrument_id"]) for row in rows}
    return next(iter(ids)) if len(ids) == 1 else None


_MINE = """SELECT DISTINCT a.id AS account_id,a.base_currency FROM identity.accounts a
 JOIN identity.account_members m ON m.tenant_id=a.tenant_id AND m.account_id=a.id
 JOIN identity.tenant_members t ON t.tenant_id=m.tenant_id AND t.user_id=m.user_id AND t.status='ACTIVE'
 WHERE a.tenant_id=$1 AND m.user_id=$2 AND a.status='ACTIVE'
 AND m.relationship IN ('BENEFICIAL_OWNER','JOINT_OWNER')
 AND m.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
 AND (m.effective_to IS NULL OR m.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date)"""


async def mine_portfolio(conn, tenant_id, user_id, now):
    args = (tenant_id, user_id, now)
    navs = await conn.fetch(
        f"""WITH mine AS ({_MINE}) SELECT mine.account_id,mine.base_currency,n.*
      FROM mine LEFT JOIN LATERAL(SELECT s.as_of,s.total_nav,s.cash_balance,s.positions_market_value,s.data_quality_status,
         s.valuation_coverage_pct,s.unpriced_position_count FROM ledger.account_nav_snapshots s
         WHERE s.tenant_id=$1 AND s.account_id=mine.account_id AND s.as_of<=($3 AT TIME ZONE 'Asia/Shanghai')::date
           AND s.created_at<=$3 ORDER BY s.as_of DESC,s.created_at DESC LIMIT 1)n ON TRUE""",
        *args,
    )
    positions = await conn.fetch(
        f"""WITH mine AS ({_MINE}),latest AS (
      SELECT DISTINCT ON(p.account_id,p.instrument_id) p.* FROM ledger.position_snapshots p JOIN mine ON mine.account_id=p.account_id
      WHERE p.tenant_id=$1 AND p.as_of<=($3 AT TIME ZONE 'Asia/Shanghai')::date AND p.created_at<=$3
      ORDER BY p.account_id,p.instrument_id,p.as_of DESC,p.created_at DESC)
      SELECT p.*,i.trading_currency AS currency,ARRAY(SELECT DISTINCT f.index_id FROM reference.fund_index_links f
        JOIN reference.research_indices idx ON idx.id=f.index_id AND idx.verified_at<=$3
        WHERE f.instrument_id=p.instrument_id AND f.verified_at<=$3
          AND f.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
          AND (f.effective_to IS NULL OR f.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date)) AS index_ids
      FROM latest p JOIN reference.instruments i ON i.id=p.instrument_id""",
        *args,
    )
    policy = await conn.fetchrow(
        """SELECT * FROM risk.investment_policies WHERE tenant_id=$1 AND account_id IS NULL AND policy_status='CONFIRMED'
      AND effective_from<=($2 AT TIME ZONE 'Asia/Shanghai')::date AND (effective_to IS NULL OR effective_to>=($2 AT TIME ZONE 'Asia/Shanghai')::date)
      AND created_at<=$2 AND confirmed_at<=$2 ORDER BY version DESC LIMIT 1""",
        tenant_id,
        now,
    )
    return (
        [dict(row) for row in navs],
        [dict(row) for row in positions],
        dict(policy) if policy else None,
    )


async def latest_validation(conn, instrument_id, now):
    row = await conn.fetchrow(
        """SELECT result,provenance FROM research.opportunity_strategy_validations
      WHERE instrument_id::text=$1 AND method_version=$2 AND evaluated_at<=$3 AND available_at<=$3 AND retrieved_at<=$3
      ORDER BY available_at DESC,retrieved_at DESC,id DESC LIMIT 1""",
        str(instrument_id),
        PIT_VERSION,
        now,
    )
    return {**decode(row["result"]), **decode(row["provenance"])} if row else None


async def save_validation(conn, instrument_id, dataset, report, now):
    from backend.investment.decision import validate_pit

    if report != validate_pit(dataset):
        raise ValidationError_("验证报告与受控输入计算不一致")
    if report.get("windowEnd"):
        used = {}
        for point in dataset.get("points", []):
            if point["date"] <= report["windowEnd"]:
                prior = used.get(point["date"])
                if prior is None or (
                    timestamp(point["retrievedAt"]),
                    timestamp(point["availableAt"]),
                ) < (timestamp(prior["retrievedAt"]), timestamp(prior["availableAt"])):
                    used[point["date"]] = point
        if any(
            timestamp(row[key]) > now
            for row in used.values()
            for key in ("decisionAt", "availableAt", "retrievedAt")
        ):
            raise ValidationError_("验证窗口包含评估时尚不可得的观察或成交时点")
    provenance = {
        key: dataset.get(key)
        for key in (
            "sourceMode",
            "pointInTimeVerified",
            "sourceUri",
            "configVersion",
            "seriesKind",
            "costsVerified",
            "transactionMechanismVerified",
            "returnBasisVerified",
        )
    }
    return await conn.fetchval(
        """INSERT INTO research.opportunity_strategy_validations(instrument_id,method_version,dataset_hash,config_hash,
      evaluated_at,available_at,retrieved_at,result,provenance) VALUES($1,$2,$3,$4,$5,$5,$5,$6::jsonb,$7::jsonb)
      ON CONFLICT DO NOTHING RETURNING id""",
        instrument_id,
        PIT_VERSION,
        report.get("datasetHash") or semantic_hash(dataset),
        report.get("configHash")
        or semantic_hash(
            {"method": PIT_VERSION, "config": dataset.get("configVersion")}
        ),
        now,
        json.dumps(report, default=str, ensure_ascii=False),
        json.dumps(provenance, default=str, ensure_ascii=False),
    )


async def product_quality(conn, instrument_id, now):
    row = await conn.fetchrow(
        "SELECT metadata FROM reference.instruments WHERE id::text=$1",
        str(instrument_id),
    )
    quality = (decode(row["metadata"]) if row else {}).get("opportunityQuality") or {}
    unknown = {"state": "UNKNOWN", "reason": "产品质量来源、时点及已确认约束尚不完整。"}
    try:
        if (
            quality.get("sourceVerified") is not True
            or quality.get("configConfirmed") is not True
            or not quality.get("sourceUri")
            or not quality.get("configVersion")
        ):
            return {"productGate": unknown, "tradabilityGate": unknown}
        if (
            not now - timedelta(days=45) <= timestamp(quality["retrievedAt"]) <= now
            or timestamp(quality["availableAt"]) > now
        ):
            return {"productGate": unknown, "tradabilityGate": unknown}
        fields = (
            "totalFeeRate",
            "trackingError",
            "liquidity",
            "premium",
            "maximumTrackingError",
            "minimumLiquidity",
            "maximumAbsPremium",
        )
        v = {key: number(quality.get(key)) for key in fields}
        if any(value is None for value in v.values()) or any(
            v[key] < 0 for key in fields if key != "premium"
        ):
            return {"productGate": unknown, "tradabilityGate": unknown}
        passed = (
            v["totalFeeRate"] <= number("0.008")
            and v["trackingError"] <= v["maximumTrackingError"]
            and v["liquidity"] >= v["minimumLiquidity"]
            and abs(v["premium"]) <= v["maximumAbsPremium"]
        )
        trade = (
            quality.get("transactionMechanismVerified") is True
            and type(quality.get("tradable")) is bool
        )
        return {
            "configVersion": quality["configVersion"],
            "productGate": {
                "state": "PASS" if passed else "BLOCKED",
                "reason": "已核查费用及已确认产品质量约束。",
            },
            "tradabilityGate": {
                "state": "PASS"
                if trade and quality["tradable"]
                else "BLOCKED"
                if trade
                else "UNKNOWN",
                "reason": "当前交易机制及可交易性核查。",
            },
        }
    except (KeyError, TypeError, ValueError):
        return {"productGate": unknown, "tradabilityGate": unknown}


def validation_matches(report, facts, config_version):
    return bool(
        config_version
        and report.get("sourceUri") == facts.get("source")
        and report.get("seriesKind") == facts.get("seriesKind")
        and report.get("configVersion") == config_version
        and report.get("sourceMode") == "REAL_PIT"
        and all(
            report.get(key) is True
            for key in (
                "pointInTimeVerified",
                "costsVerified",
                "transactionMechanismVerified",
                "returnBasisVerified",
            )
        )
    )


def monitor_dto(row):
    return {
        "id": str(row["id"]),
        "enabled": row["enabled"],
        "directionId": row.get("direction_id"),
        "indexId": row.get("index_id"),
        "instrumentId": str(row["instrument_id"]) if row.get("instrument_id") else None,
        "fundCode": row.get("fund_code"),
        "fundMarket": row.get("fund_market"),
        "enabledAt": row.get("enabled_at"),
    }


async def validate_scope(conn, scope, now):
    if not any(
        scope.get(key)
        for key in ("direction_id", "index_id", "instrument_id", "fund_code")
    ):
        raise ValidationError_("请先选择研究方向、指数或基金")
    if scope.get("direction_id") and not await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM reference.research_directions WHERE id=$1)",
        scope["direction_id"],
    ):
        raise NotFoundError("研究方向不存在")
    if scope.get("index_id") and not await conn.fetchval(
        """SELECT EXISTS(SELECT 1 FROM reference.research_indices i WHERE i.id=$1
      AND ($2::text IS NULL OR EXISTS(SELECT 1 FROM reference.direction_index_links d WHERE d.index_id=i.id AND d.direction_id=$2)))""",
        scope["index_id"],
        scope.get("direction_id"),
    ):
        raise NotFoundError("指数不属于所选研究方向")
    identity = await resolve_instrument(
        conn,
        scope.get("instrument_id"),
        scope.get("fund_code"),
        scope.get("fund_market"),
    )
    if scope.get("instrument_id") and identity is None:
        raise NotFoundError("基金标的不存在")
    if identity and (scope.get("index_id") or scope.get("direction_id")):
        valid = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM reference.fund_index_links f JOIN reference.research_indices i ON i.id=f.index_id AND i.verified_at<=$4
          WHERE f.instrument_id::text=$1 AND f.verified_at<=$4 AND f.effective_from<=($4 AT TIME ZONE 'Asia/Shanghai')::date
          AND (f.effective_to IS NULL OR f.effective_to>=($4 AT TIME ZONE 'Asia/Shanghai')::date)
          AND ($2::text IS NULL OR f.index_id=$2) AND ($3::text IS NULL OR EXISTS(SELECT 1 FROM reference.direction_index_links d WHERE d.index_id=f.index_id AND d.direction_id=$3)))""",
            identity,
            scope.get("index_id"),
            scope.get("direction_id"),
            now,
        )
        if not valid:
            raise NotFoundError("基金方向/指数映射未核验")


async def set_monitor(conn, tenant_id, user_id, scope, enabled, now):
    await validate_scope(conn, scope, now)
    key = scope_key(scope)
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        f"opportunity-monitor:{tenant_id}:{user_id}:{key}",
    )
    row = await conn.fetchrow(
        """INSERT INTO risk.opportunity_condition_monitors(tenant_id,user_id,direction_id,index_id,instrument_id,fund_code,fund_market,scope_key,enabled,enabled_at)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT(tenant_id,user_id,scope_key) DO UPDATE SET enabled=EXCLUDED.enabled,
      enabled_at=CASE WHEN opportunity_condition_monitors.enabled IS DISTINCT FROM EXCLUDED.enabled THEN EXCLUDED.enabled_at ELSE opportunity_condition_monitors.enabled_at END RETURNING *""",
        tenant_id,
        user_id,
        scope.get("direction_id"),
        scope.get("index_id"),
        scope.get("instrument_id"),
        scope.get("fund_code"),
        scope.get("fund_market"),
        key,
        enabled,
        now,
    )
    return monitor_dto(dict(row))


async def list_monitors(conn, tenant_id, user_id):
    return [
        monitor_dto(dict(row))
        for row in await conn.fetch(
            "SELECT * FROM risk.opportunity_condition_monitors WHERE tenant_id=$1 AND user_id=$2 ORDER BY created_at DESC",
            tenant_id,
            user_id,
        )
    ]


async def list_notices(
    conn,
    tenant_id,
    user_id,
    *,
    direction_id=None,
    index_id=None,
    instrument_id=None,
    fund_code=None,
    fund_market=None,
    exact_scope=False,
):
    filters = (
        """AND m.direction_id IS NOT DISTINCT FROM $3::text AND m.index_id IS NOT DISTINCT FROM $4::text
      AND m.instrument_id::text IS NOT DISTINCT FROM $5::text AND m.fund_code IS NOT DISTINCT FROM $6::text
      AND m.fund_market IS NOT DISTINCT FROM $7::text"""
        if exact_scope
        else ""
    )
    args = (
        (
            tenant_id,
            user_id,
            direction_id,
            index_id,
            str(instrument_id) if instrument_id else None,
            fund_code,
            fund_market,
        )
        if exact_scope
        else (tenant_id, user_id)
    )
    rows = await conn.fetch(
        f"""SELECT n.* FROM risk.opportunity_condition_notices n JOIN risk.opportunity_condition_monitors m ON m.id=n.monitor_id AND m.tenant_id=n.tenant_id AND m.user_id=n.user_id
      WHERE n.tenant_id=$1 AND n.user_id=$2 {filters}
      ORDER BY n.created_at DESC,n.id DESC LIMIT 20""",
        *args,
    )
    return [
        {
            "id": str(row["id"]),
            "title": row["title"],
            "summary": row["summary"],
            "severity": row["severity"],
            "createdAt": row["created_at"],
            "readAt": row["read_at"],
        }
        for row in rows
    ]


async def ack_notice(conn, tenant_id, user_id, notice_id, now):
    row = await conn.fetchrow(
        "UPDATE risk.opportunity_condition_notices SET read_at=COALESCE(read_at,$4) WHERE tenant_id=$1 AND user_id=$2 AND id=$3 RETURNING id,read_at",
        tenant_id,
        user_id,
        notice_id,
        now,
    )
    if not row:
        raise NotFoundError("提醒不存在")
    return {"id": str(row["id"]), "readAt": row["read_at"]}


def conditions(result):
    item = result["decision"]
    return {
        "state": item["state"],
        "marketState": item["marketState"],
        "personalState": item["personalState"],
        "gates": sorted((row["id"], row["state"]) for row in item.get("gates", [])),
    }


def condition_fingerprint(result):
    return semantic_hash(conditions(result))


def transition_fingerprint(prior, result):
    return semantic_hash(
        {
            "episode": str(prior.get("id") or prior.get("as_of")),
            "before": conditions(decode(prior["result"])),
            "after": conditions(result),
        }
    )


async def record_monitor_result(conn, monitor, result, now):
    owner = (
        monitor["tenant_id"],
        monitor["user_id"],
        monitor["id"],
        monitor["enabled_at"],
    )
    key = (
        f"opportunity-monitor:{owner[0]}:{owner[1]}:{monitor['scope_key']}"
        if monitor.get("scope_key")
        else str(owner[2])
    )
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
    active = await conn.fetchrow(
        """SELECT m.enabled_at FROM risk.opportunity_condition_monitors m
      JOIN identity.tenant_members actor ON actor.tenant_id=m.tenant_id AND actor.user_id=m.user_id AND actor.status='ACTIVE'
      WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.id=$3 AND m.enabled IS TRUE AND m.enabled_at=$4""",
        *owner,
    )
    if not active:
        return {"baseline": False, "notice": False, "skipped": True}
    prior = await conn.fetchrow(
        "SELECT id,condition_fingerprint,result,as_of FROM risk.opportunity_decision_snapshots WHERE tenant_id=$1 AND user_id=$2 AND monitor_id=$3 AND enabled_at=$4 AND as_of<=$5 ORDER BY as_of DESC,created_at DESC,id DESC LIMIT 1",
        *owner,
        now,
    )
    fingerprint = condition_fingerprint(result)
    if prior and prior["condition_fingerprint"] == fingerprint:
        return {"baseline": False, "notice": False}
    safe = {"decision": result["decision"], "portfolio": result["portfolio"]}
    snapshot = await conn.fetchval(
        """INSERT INTO risk.opportunity_decision_snapshots(tenant_id,user_id,monitor_id,enabled_at,as_of,method_version,input_hash,condition_fingerprint,result)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb) ON CONFLICT DO NOTHING RETURNING id""",
        *owner,
        now,
        VERSION,
        semantic_hash(
            {
                "conditions": conditions(result),
                "portfolioAsOf": result["portfolio"].get("asOf"),
            }
        ),
        fingerprint,
        json.dumps(safe, default=str, ensure_ascii=False),
    )
    if not prior:
        return {"baseline": True, "notice": False}
    if prior["condition_fingerprint"] == fingerprint or snapshot is None:
        return {"baseline": False, "notice": False}
    budget, silence = (
        result["portfolio"].get("noticeBudget"),
        result["portfolio"].get("silenceMinutes"),
    )
    if (
        type(budget) is not int
        or budget <= 0
        or type(silence) is not int
        or silence < 0
    ):
        return {
            "baseline": False,
            "notice": False,
            "reason": "CONFIRMED_NOTICE_BUDGET_REQUIRED",
        }
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        f"opportunity-notice-budget:{owner[0]}:{owner[1]}",
    )
    limits = await conn.fetchrow(
        """SELECT count(*) FILTER(WHERE created_at>=(date_trunc('day',$3 AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')) AS notice_count,
      max(created_at) FILTER(WHERE monitor_id=$4 AND enabled_at=$5) AS last_notice
      FROM risk.opportunity_condition_notices WHERE tenant_id=$1 AND user_id=$2 AND created_at<=$3""",
        owner[0],
        owner[1],
        now,
        owner[2],
        owner[3],
    )
    if limits["notice_count"] >= budget or (
        limits["last_notice"] is not None
        and now < limits["last_notice"] + timedelta(minutes=silence)
    ):
        return {"baseline": False, "notice": False, "reason": "BUDGET_OR_SILENCE"}
    before, after = conditions(decode(prior["result"])), conditions(result)
    state_labels = {
        "WAIT": "等待",
        "ENTRY_CANDIDATE": "建仓研究候选",
        "HOLD_REVIEW": "持仓观察",
        "POSITION_REVIEW": "持仓风险复核",
    }
    notice = await conn.fetchval(
        """INSERT INTO risk.opportunity_condition_notices(tenant_id,user_id,monitor_id,snapshot_id,enabled_at,fingerprint,title,summary,severity,created_at,before_conditions,after_conditions)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb) ON CONFLICT DO NOTHING RETURNING id""",
        owner[0],
        owner[1],
        owner[2],
        snapshot,
        owner[3],
        transition_fingerprint(dict(prior), result),
        "研究条件发生变化",
        f"{state_labels.get(before['state'], '证据待核查')} → {state_labels.get(after['state'], '证据待核查')}；请查看更新证据与缺口。",
        "CRITICAL" if after["state"] == "POSITION_REVIEW" else "WATCH",
        now,
        json.dumps(before, ensure_ascii=False),
        json.dumps(after, ensure_ascii=False),
    )
    return {"baseline": False, "notice": notice is not None}
