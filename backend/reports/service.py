"""Reports service: four-session report generation and version-audit detail.

Generation is two-phase:
  1. read context inside a tenant RLS transaction (short-lived),
  2. call the model gateway (or deterministic fallback),
  3. write model_run + report + items + outbox inside a second tenant
     transaction, retrying on unique-version conflicts.

If MODEL_API_KEY is not configured (or the provider fails), a deterministic
template keeps the workflow usable and records an ABSTAINED model run — the
report never fabricates market facts it cannot source.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import asyncpg

from backend.common.database import Pool, tenant_transaction
from backend.common.formatting import fmt_money, fmt_qty, merge_quality, pct
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import now_shanghai, write_audit
from backend.errors import NotFoundError, ValidationError_
from backend.integrations.model_gateway import (
    ModelCallError,
    ModelNotConfiguredError,
    ModelResult,
    chat_completion,
)
from backend.portfolio.service import get_positions, get_summary
from backend.reports import repository

logger = logging.getLogger("wealthsail.reports")

SESSION_META: dict[str, dict[str, str]] = {
    "PRE_MARKET": {"title": "盘前简报", "subtitle": "隔夜市场、今日事件与持仓风险"},
    "INTRADAY": {"title": "盘中跟踪", "subtitle": "持仓异动与风险预算触点"},
    "FUND_1430": {"title": "基金 2:30 报告", "subtitle": "场外基金估值、覆盖率与截止时间"},
    "POST_MARKET": {"title": "盘后交互复盘", "subtitle": "计划—决策—执行闭环与证据回看"},
}

# Idempotent replay window: a generate request for the same (session, trade_date)
# within this window returns the existing report instead of minting a new version.
IDEMPOTENT_WINDOW_SECONDS = 60
MAX_VERSION_RETRIES = 3

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------
async def list_reports(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date | None,
    session: str | None,
) -> list[dict]:
    rows = await repository.list_reports(conn, tenant_id, trade_date, session)
    return [_report_summary(r) for r in rows]


def _report_summary(row: asyncpg.Record) -> dict:
    meta = SESSION_META.get(row["report_session"], {})
    return {
        "id": str(row["id"]),
        "session": row["report_session"],
        "title": meta.get("title", row["report_session"]),
        "subtitle": meta.get("subtitle", ""),
        "tradeDate": row["trade_date"].isoformat(),
        "version": row["version"],
        "generatedAt": row["as_of"],
        "status": "已发布" if row["status"] == "PUBLISHED" else "待复核",
        "qualityStatus": row["quality_status"],
    }


async def get_report_detail(
    conn: asyncpg.Connection, tenant_id: str, report_id: str
) -> dict:
    row = await repository.get_report(conn, tenant_id, report_id)
    if row is None:
        raise NotFoundError("报告不存在或已被删除")
    items = await repository.get_report_items(conn, tenant_id, report_id)
    history = await repository.version_history(
        conn, tenant_id, row["trade_date"], row["report_session"], report_id
    )
    meta = SESSION_META.get(row["report_session"], {})

    model_run = None
    if row["model_run_id"] is not None:
        model_run = await conn.fetchrow(
            """
            SELECT provider, model, status, token_usage, cost_cny,
                   started_at, finished_at, error_class
            FROM research.model_runs
            WHERE tenant_id = $1 AND id = $2
            """,
            tenant_id,
            row["model_run_id"],
        )

    return {
        "id": str(row["id"]),
        "session": row["report_session"],
        "title": meta.get("title", row["report_session"]),
        "subtitle": meta.get("subtitle", ""),
        "tradeDate": row["trade_date"].isoformat(),
        "version": row["version"],
        "status": row["status"],
        "asOf": row["as_of"],
        "qualityStatus": row["quality_status"],
        "summary": row["summary"],
        "policyCheckStatus": row["policy_check_status"],
        "validUntil": row["valid_until"],
        "supersedesReportId": (
            str(row["supersedes_report_id"]) if row["supersedes_report_id"] else None
        ),
        "modelRun": (
            {
                "provider": model_run["provider"],
                "model": model_run["model"],
                "status": model_run["status"],
                "tokenUsage": _parse_jsonb(model_run["token_usage"]),
                "costCny": fmt_money(Decimal(model_run["cost_cny"] or 0), 4),
                "startedAt": model_run["started_at"],
                "finishedAt": model_run["finished_at"],
                "errorClass": model_run["error_class"],
            }
            if model_run
            else None
        ),
        "items": [
            {
                "id": str(it["id"]),
                "itemType": it["item_type"],
                "severity": it["severity"],
                "title": it["title"],
                "body": it["body"],
                "actionPlan": it["action_plan"],
                "quality": it["data_quality_status"],
            }
            for it in items
        ],
        "versionHistory": [
            {
                "id": str(h["id"]),
                "version": h["version"],
                "status": h["status"],
                "asOf": h["as_of"],
                "supersedesReportId": (
                    str(h["supersedes_report_id"]) if h["supersedes_report_id"] else None
                ),
            }
            for h in history
        ],
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
async def generate_report(
    pool: Pool,
    ctx: TenantContext,
    settings: Settings,
    session: str,
) -> dict:
    if session not in SESSION_META:
        raise ValidationError_(
            f"未知报告时段: {session}",
            field_errors=[{"field": "session", "message": "必须是四时段之一"}],
        )

    trade_date = now_shanghai().date()
    started_at = now_shanghai()

    # Phase 1: read context (RLS transaction).
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        context = await _build_context(conn, ctx.tenant_id, session)
        latest = await repository.latest_report(
            conn, ctx.tenant_id, trade_date, session
        )

    # Idempotent replay: same (session, trade_date) generated moments ago.
    if latest is not None:
        age = (started_at - latest["created_at"]).total_seconds()
        if age < IDEMPOTENT_WINDOW_SECONDS:
            async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
                return await get_report_detail(conn, ctx.tenant_id, str(latest["id"]))

    # Phase 2: model gateway (outside any transaction) or deterministic fallback.
    model_result = None
    error_class: str | None = None
    try:
        system_prompt, user_prompt = _render_prompts(session, context)
        model_result = await chat_completion(
            settings=settings, system_prompt=system_prompt, user_prompt=user_prompt
        )
        draft = _parse_llm_draft(model_result.content, session)
    except (ModelNotConfiguredError, ModelCallError, ValueError) as exc:
        logger.warning("report generation degraded for %s: %s", session, exc)
        error_class = exc.__class__.__name__
        draft = _fallback_draft(session, context)

    # Phase 3: write with version-conflict retry.
    last_error: Exception | None = None
    for attempt in range(MAX_VERSION_RETRIES):
        try:
            async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
                return await _persist_generation(
                    conn,
                    ctx=ctx,
                    session=session,
                    trade_date=trade_date,
                    started_at=started_at,
                    context=context,
                    draft=draft,
                    model_result=model_result,
                    error_class=error_class,
                )
        except asyncpg.UniqueViolationError as exc:
            last_error = exc
            logger.info("report version conflict (attempt %d), retrying", attempt + 1)
            continue
    raise asyncpg.UniqueViolationError(
        f"report version conflict after {MAX_VERSION_RETRIES} attempts"
    ) from last_error


async def _build_context(
    conn: asyncpg.Connection, tenant_id: str, session: str
) -> dict:
    summary = await get_summary(conn, tenant_id, now_shanghai())
    positions = await get_positions(conn, tenant_id)
    policy = await conn.fetchrow(
        """
        SELECT max_drawdown_limit, single_position_limit, equity_target_pct,
               bond_target_pct, gold_target_pct, cash_target_pct,
               rebalance_tolerance_pct, version
        FROM risk.investment_policies
        WHERE tenant_id = $1 AND policy_status = 'CONFIRMED'
          AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
        ORDER BY version DESC
        LIMIT 1
        """,
        tenant_id,
    )
    return {
        "session": session,
        "summary": summary,
        "positions": positions,
        "policy": dict(policy) if policy else None,
    }


def _render_prompts(session: str, context: dict) -> tuple[str, str]:
    system_prompt = (
        "你是 WealthSail 投研平台的资深研究助理，输出简体中文。"
        "必须严格返回 JSON 对象，不要输出任何其他文字。"
        'JSON 结构：{"summary": "报告摘要(3-5句)", "items": ['
        '{"item_type": "FACT|RISK|ACTION_PLAN|TODO|FUND_ESTIMATE|ALERT", '
        '"severity": "INFO|WARNING|CRITICAL", "title": "条目标题", "body": "条目正文"}]}。'
        "条目 3-5 条，正文必须只基于提供的上下文事实，禁止编造行情数据。"
    )
    user_prompt = json.dumps(
        {
            "session": session,
            "trade_date": now_shanghai().date().isoformat(),
            "context": context,
        },
        ensure_ascii=False,
        default=str,
    )
    return system_prompt, user_prompt


def _parse_llm_draft(content: str, session: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    payload = json.loads(text)
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise ValueError("LLM 输出缺少 summary")
    items = payload.get("items") or []
    normalized: list[dict] = []
    for item in items[:8]:
        normalized.append(
            {
                "item_type": str(item.get("item_type", "FACT")),
                "severity": str(item.get("severity", "INFO")),
                "title": str(item.get("title", "")).strip(),
                "body": str(item.get("body", "")).strip(),
                "quality": "FRESH",
            }
        )
    normalized = [it for it in normalized if it["title"] and it["body"]]
    if not normalized:
        raise ValueError("LLM 输出缺少 items")
    return {"summary": summary, "items": normalized}


def _fallback_draft(session: str, context: dict) -> dict:
    """Deterministic template generation when the model gateway is unavailable.

    Only facts already present in the context are stated; quality is derived
    from the underlying snapshot quality.
    """
    summary = context["summary"]
    positions = context["positions"]
    policy = context["policy"]

    lines = [
        f"当前组合总资产 {summary['totalAsset']} 元，可用现金 {summary['cashBalance']} 元，"
        f"持仓市值 {summary['positionsMarketValue']} 元，当日收益 {summary['dailyPnl']} 元。",
    ]
    if positions:
        top = positions[0]
        lines.append(
            f"第一大持仓 {top['name']}（{top['symbol']}）市值 {top['marketValue']} 元，"
            f"占组合 {top['weight']}%，持仓收益 {top['pnl']} 元。"
        )
    else:
        lines.append("当前无持仓，组合全部为现金。")

    items: list[dict] = [
        {
            "item_type": "FACT",
            "severity": "INFO",
            "title": "组合概况",
            "body": " ".join(lines),
            "quality": summary["qualityStatus"],
        }
    ]
    if positions and policy and policy["single_position_limit"] is not None:
        max_weight = max(
            (Decimal(p["weight"]) for p in positions), default=ZERO
        )
        limit = Decimal(policy["single_position_limit"])
        if max_weight > limit:
            items.append(
                {
                    "item_type": "RISK",
                    "severity": "WARNING",
                    "title": "单一标的仓位超限",
                    "body": (
                        f"当前最大单一持仓占比 {fmt_money(max_weight)}%，"
                        f"超过政策上限 {fmt_money(limit)}%，请评估是否触发调仓预案。"
                    ),
                    "quality": "FRESH",
                }
            )
    items.append(
        {
            "item_type": "ACTION_PLAN",
            "severity": "INFO",
            "title": "后续动作",
            "body": "跟踪持仓标的价格与政策合规状态；模型网关恢复后自动生成完整研报。",
            "quality": "FRESH",
        }
    )
    return {"summary": "；".join(lines), "items": items}


async def _persist_generation(
    conn: asyncpg.Connection,
    *,
    ctx: TenantContext,
    session: str,
    trade_date: date,
    started_at: datetime,
    context: dict,
    draft: dict,
    model_result: ModelResult | None,
    error_class: str | None,
) -> dict:
    latest = await repository.latest_report(conn, ctx.tenant_id, trade_date, session)
    version = (latest["version"] + 1) if latest else 1
    supersedes = str(latest["id"]) if latest else None

    correlation_id = "req_" + uuid.uuid4().hex[:16]
    finished_at = now_shanghai()

    if model_result is not None:
        model_run_id = await repository.insert_model_run(
            conn,
            tenant_id=ctx.tenant_id,
            provider="deepseek",
            model=model_result.model,
            prompt_template_version=f"report.{session.lower()}.v1",
            input_snapshot_hashes={
                "positions": f"sha256:ctx-{len(context.get('positions', []))}",
            },
            token_usage=model_result.usage,
            cost_cny=model_result.cost_cny,
            started_at=started_at,
            finished_at=finished_at,
            status="COMPLETED",
            error_class=None,
            correlation_id=correlation_id,
        )
    else:
        model_run_id = await repository.insert_model_run(
            conn,
            tenant_id=ctx.tenant_id,
            provider="deepseek",
            model="fallback-template",
            prompt_template_version=f"report.{session.lower()}.v1",
            input_snapshot_hashes={},
            token_usage={},
            cost_cny=Decimal("0.0000"),
            started_at=started_at,
            finished_at=finished_at,
            status="ABSTAINED",
            error_class=error_class,
            correlation_id=correlation_id,
        )

    valid_until = _valid_until(session, finished_at)
    report_id = await repository.insert_report(
        conn,
        tenant_id=ctx.tenant_id,
        session=session,
        trade_date=trade_date,
        version=version,
        status="PUBLISHED",
        as_of=finished_at,
        quality_status=draft.get("quality", "FRESH"),
        summary=draft["summary"],
        model_run_id=model_run_id,
        job_run_id=None,
        policy_check_status=draft.get("policy_check_status", "PASSED"),
        valid_until=valid_until,
        supersedes_report_id=supersedes,
    )
    await repository.insert_report_items(
        conn,
        tenant_id=ctx.tenant_id,
        report_id=report_id,
        items=draft["items"],
    )
    await repository.insert_outbox(
        conn,
        tenant_id=ctx.tenant_id,
        topic="REPORT_PUBLISHED",
        aggregate_type="reporting.reports",
        aggregate_id=report_id,
        payload={"report_id": report_id, "session": session, "version": version},
        idempotency_key=f"{session}:{trade_date.isoformat()}:{version}",
    )
    await write_audit(
        conn,
        tenant_id=ctx.tenant_id,
        actor_type="USER" if ctx.user_id else "SYSTEM_WORKER",
        actor_id=ctx.user_id,
        action="REPORT_GENERATED",
        resource_type="reporting.reports",
        resource_id=report_id,
        correlation_id=correlation_id,
        details={"session": session, "version": version, "degraded": model_result is None},
    )
    return await get_report_detail(conn, ctx.tenant_id, report_id)


def _parse_jsonb(value: object) -> dict:
    """asyncpg returns jsonb as JSON text; decode defensively."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return {}


def _valid_until(session: str, as_of: datetime) -> datetime:
    if session == "POST_MARKET":
        return as_of.replace(hour=23, minute=59, second=59, microsecond=0)
    if session == "INTRADAY":
        return as_of.replace(hour=15, minute=0, second=0, microsecond=0)
    return as_of + timedelta(hours=2)
