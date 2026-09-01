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
import hashlib
import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import asyncpg

from backend.common.database import Pool, tenant_transaction
from backend.common.formatting import fmt_money
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
from backend.market import service as market_service
from backend.portfolio.service import get_positions, get_summary
from backend.reports import fund_1430, pre_market, repository, shared_market_brief

logger = logging.getLogger("wealthsail.reports")

SESSION_META: dict[str, dict[str, str]] = {
    "PRE_MARKET": {"title": "盘前简报", "subtitle": "隔夜市场、今日事件与持仓风险"},
    "INTRADAY": {"title": "盘中跟踪", "subtitle": "持仓异动与风险预算触点"},
    "FUND_1430": {
        "title": "基金 2:30 报告",
        "subtitle": "场外基金估值、覆盖率与截止时间",
    },
    "POST_MARKET": {
        "title": "盘后交互复盘",
        "subtitle": "计划—决策—执行闭环与证据回看",
    },
}

_ALLOWED_ITEM_TYPES = {"FACT", "RISK", "ACTION_PLAN", "TODO", "FUND_ESTIMATE", "ALERT"}
_ALLOWED_SEVERITIES = {"INFO", "WARNING", "CRITICAL"}


def canonical_context_hash(context: dict) -> str:
    """Content-address a report input snapshot using canonical JSON."""
    payload = json.dumps(
        context,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
                "evidence": _parse_json_list(it["evidence"]),
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
                    str(h["supersedes_report_id"])
                    if h["supersedes_report_id"]
                    else None
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
    *,
    job_run_id: str | None = None,
    actor_type: str = "USER",
    force: bool = False,
    delivery_user_id: str | None = None,
    trigger_audit_action: str | None = None,
    trigger_audit_details: dict | None = None,
) -> dict:
    ctx.require_roles("OWNER", "ADMIN", "EDITOR")
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
    if latest is not None and not force:
        age = (started_at - latest["created_at"]).total_seconds()
        if age < IDEMPOTENT_WINDOW_SECONDS:
            async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
                return await get_report_detail(conn, ctx.tenant_id, str(latest["id"]))

    # Phase 2: PRE_MARKET and FUND_1430 are deterministic. The other sessions
    # keep personalized generation for holdings and a shared brief otherwise.
    if session == "PRE_MARKET":
        generation_mode = "PRE_MARKET_DETERMINISTIC"
    elif session == "FUND_1430":
        generation_mode = "FUND_1430_DETERMINISTIC"
    else:
        generation_mode = (
            "NO_HOLDINGS_SHARED" if not context["positions"] else "PERSONALIZED"
        )
    shared_brief_id: str | None = None
    shared_cache_hit = False
    shared_context_hash: str | None = None
    push_content: str | None = None
    if generation_mode == "PRE_MARKET_DETERMINISTIC":
        draft = pre_market.build_report(context)
        push_content = pre_market.format_push_content(context)
        model_result = None
        error_class = None
    elif generation_mode == "FUND_1430_DETERMINISTIC":
        draft = fund_1430.build_report(context)
        push_content = fund_1430.format_push_content(context)
        model_result = None
        error_class = None
    elif generation_mode == "NO_HOLDINGS_SHARED":
        shared_result = await shared_market_brief.get_or_generate(
            pool, trade_date, context
        )
        draft = shared_result.draft
        model_result = shared_result.model_result
        error_class = shared_result.error_class
        shared_brief_id = shared_result.id
        shared_context_hash = shared_result.context_hash
        shared_cache_hit = shared_result.cache_hit
        push_content = shared_market_brief.format_push_content(draft, session=session)
    else:
        # The notification preview is deterministic and grounded in the same
        # persisted context as the report. It never adds a second model call.
        push_content = _format_personalized_push_content(context, session=session)
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
        draft = _attach_deterministic_fund_items(session, draft, context)

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
                    job_run_id=job_run_id,
                    actor_type=actor_type,
                    generation_mode=generation_mode,
                    shared_brief_id=shared_brief_id,
                    shared_cache_hit=shared_cache_hit,
                    shared_context_hash=shared_context_hash,
                    push_content=push_content,
                    delivery_user_id=delivery_user_id,
                    trigger_audit_action=trigger_audit_action,
                    trigger_audit_details=trigger_audit_details,
                )
        except asyncpg.UniqueViolationError as exc:
            last_error = exc
            logger.info("report version conflict (attempt %d), retrying", attempt + 1)
            continue
    raise asyncpg.UniqueViolationError(
        f"report version conflict after {MAX_VERSION_RETRIES} attempts"
    ) from last_error


async def _build_context(
    conn: asyncpg.Connection,
    tenant_id: str,
    session: str,
    *,
    audience_user_id: str | None = None,
) -> dict:
    summary = await get_summary(conn, tenant_id, now_shanghai())
    # Historical zero-quantity snapshots are not active holdings. Filtering
    # here keeps fully closed portfolios in the no-holdings report mode.
    positions = _active_positions(await get_positions(conn, tenant_id))
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
    fund_estimates: list[dict] = []
    if session == "FUND_1430":
        rows = await conn.fetch(
            """
            WITH latest_positions AS (
                SELECT DISTINCT ON (account_id, instrument_id)
                    account_id, instrument_id, quantity
                FROM ledger.position_snapshots
                WHERE tenant_id = $1
                ORDER BY account_id, instrument_id, as_of DESC
            ), held_funds AS (
                SELECT instrument_id
                FROM latest_positions
                GROUP BY instrument_id
                HAVING sum(quantity) > 0
            )
            SELECT DISTINCT ON (o.subject_id)
                o.subject_id AS instrument_id, COALESCE(a.ticker, '') AS symbol,
                i.canonical_name AS name, o.value, o.lower_value, o.upper_value,
                o.quality_status, o.as_of, o.snapshot_id, o.metadata
            FROM market.market_observations o
            JOIN held_funds h ON h.instrument_id = o.subject_id
            JOIN reference.instruments i ON i.id = o.subject_id
            LEFT JOIN LATERAL (
                SELECT alias_value AS ticker
                FROM reference.instrument_aliases
                WHERE instrument_id=o.subject_id AND alias_type='TICKER'
                  AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
                ORDER BY effective_from DESC LIMIT 1
            ) a ON TRUE
            WHERE o.field_code = 'ESTIMATED_NAV'
            ORDER BY o.subject_id, o.as_of DESC, o.observed_at DESC
            """,
            tenant_id,
        )
        fund_estimates = [
            {
                "instrumentId": str(row["instrument_id"]),
                "symbol": row["symbol"],
                "name": row["name"],
                "value": str(row["value"]) if row["value"] is not None else None,
                "lowerValue": (
                    str(row["lower_value"]) if row["lower_value"] is not None else None
                ),
                "upperValue": (
                    str(row["upper_value"]) if row["upper_value"] is not None else None
                ),
                "quality": row["quality_status"],
                "asOf": row["as_of"],
                "snapshotId": str(row["snapshot_id"]) if row["snapshot_id"] else None,
                "metadata": _parse_jsonb(row["metadata"]),
            }
            for row in rows
        ]
    pre_market_context: dict = {}
    fund_1430_context: dict = {}
    if session == "PRE_MARKET":
        instrument_ids = [
            str(position["instrumentId"])
            for position in positions
            if position.get("instrumentId")
        ]
        report_date = now_shanghai().date()
        corporate_actions = await repository.get_pre_market_corporate_actions(
            conn, instrument_ids, report_date
        )
        cutoff_rules = await repository.get_pre_market_cutoff_rules(
            conn, instrument_ids, report_date
        )
        pre_market_context = {
            # No governed providers exist for these fields yet. Their explicit
            # empty values are rendered as "暂无" instead of inferred facts.
            "usIndices": [],
            "a50Future": None,
            "economicEvents": [],
            "subscriptionRedemptionChanges": [],
            "corporateActions": corporate_actions,
            "fundCutoffRules": cutoff_rules,
        }
    if session == "FUND_1430":
        fund_ids = [
            str(position["instrumentId"])
            for position in positions
            if position.get("instrumentId") and position.get("type") == "场外基金"
        ]
        cutoff_rules = await repository.get_pre_market_cutoff_rules(
            conn, fund_ids, now_shanghai().date()
        )
        fund_1430_context = {"fundCutoffRules": cutoff_rules}
    result = {
        "session": session,
        "summary": summary,
        "positions": positions,
        "policy": dict(policy) if policy else None,
        "fundEstimates": fund_estimates,
        "watchlist": [],
        # Read-only persisted snapshot; this never performs provider I/O.
        "marketSentiment": await market_service.get_market_sentiment(conn),
    }
    if audience_user_id is not None:
        result["watchlist"] = await repository.get_user_watchlist_context(
            conn, audience_user_id
        )
    if session == "PRE_MARKET":
        result["preMarket"] = pre_market_context
    if session == "FUND_1430":
        result["fund1430"] = fund_1430_context
    return result


def _active_positions(positions: list[dict]) -> list[dict]:
    """Exclude historical zero/negative snapshots from report-mode selection."""
    return [
        position
        for position in positions
        if Decimal(str(position.get("quantity") or "0")) > ZERO
    ]


def _safe_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _position_label(position: dict) -> str:
    name = str(position.get("name") or "未命名标的")
    compact_name = name if len(name) <= 12 else f"{name[:11]}…"
    symbol = _compact_symbol(position.get("symbol"))
    return f"{compact_name}({symbol})"


def _compact_symbol(value: object, limit: int = 9) -> str:
    """Keep long identifiers recognisable without consuming a push line."""
    symbol = str(value or "--")
    if len(symbol) <= limit:
        return symbol
    side = (limit - 1) // 2
    return f"{symbol[:side]}…{symbol[-side:]}"


def _clip_push_line(value: str, limit: int = 72) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1].rstrip('；，。')}…"


def _personalized_summary(summary: dict) -> str:
    daily_rate = _safe_decimal(summary.get("dailyPnlRate"))
    daily_pnl = _safe_decimal(summary.get("dailyPnl"))
    if daily_rate is not None:
        performance = f"组合当日 {daily_rate:+.2f}%"
    elif daily_pnl is not None:
        performance = f"组合当日涨跌幅暂不可用（盈亏 {daily_pnl:+.2f} 元）"
    else:
        performance = "组合当日涨跌幅暂不可用"
    return f"{performance}；主要贡献标的暂无法可靠归因。"


def _personalized_health(context: dict) -> tuple[str, list[dict], list[dict]]:
    positions = context.get("positions") or []
    stale = [
        position
        for position in positions
        if str(position.get("quality") or "") == "STALE"
    ]
    policy = context.get("policy")
    limit = (
        _safe_decimal(policy.get("single_position_limit"))
        if policy is not None
        else None
    )
    overweight: list[dict] = []
    if limit is not None:
        overweight = sorted(
            (
                position
                for position in positions
                if (_safe_decimal(position.get("weight")) or ZERO) > limit
            ),
            key=lambda position: _safe_decimal(position.get("weight")) or ZERO,
            reverse=True,
        )

    parts: list[str] = []
    if stale:
        # Preserve the friendly name for a single stale holding, but switch to
        # compact symbols when several holdings need attention.  This keeps the
        # overweight holding and its policy threshold visible in the same
        # WeCom line instead of letting the generic line clip hide them.
        if len(stale) == 1:
            labels = _position_label(stale[0])
        else:
            labels = "、".join(
                _compact_symbol(position.get("symbol")) for position in stale[:3]
            )
        extra = f"等{len(stale)}只" if len(stale) > 3 else ""
        parts.append(f"估值过期：{labels}{extra}")
    else:
        parts.append("无估值过期持仓")
    if overweight and limit is not None:
        position = overweight[0]
        weight = _safe_decimal(position.get("weight")) or ZERO
        symbol = _compact_symbol(position.get("symbol"))
        parts.append(f"仓位偏高：{symbol} {weight:.2f}%＞{limit:.2f}%")
    elif limit is None:
        parts.append("未配置单一持仓上限")
    else:
        parts.append("无仓位超限")
    return "；".join(parts) + "。", stale, overweight


def _personalized_actions(
    context: dict,
    stale: list[dict],
    overweight: list[dict],
) -> str:
    actions: list[str] = []
    if stale:
        symbol = _compact_symbol(stale[0].get("symbol") or "该标的")
        actions.append(f"等待{symbol}估值更新后再决策")
    policy = context.get("policy")
    limit = (
        _safe_decimal(policy.get("single_position_limit"))
        if policy is not None
        else None
    )
    if overweight and limit is not None:
        symbol = _compact_symbol(overweight[0].get("symbol") or "该标的")
        actions.append(f"{symbol}暂停增加暴露，按{limit:.2f}%上限评估再平衡")
    if not actions:
        actions.append("维持观察，按既定风险规则复核持仓")
    return (
        "；".join(f"{index + 1}) {action}" for index, action in enumerate(actions[:2]))
        + "。"
    )


def _personalized_market_signal(sentiment: dict) -> str:
    up = _safe_decimal(sentiment.get("totalUp"))
    down = _safe_decimal(sentiment.get("totalDown"))
    score = _safe_decimal(sentiment.get("sentimentScore"))
    if up is None or down is None:
        return "市场广度数据暂不可用，等待 Worker 快照更新。"
    if up > down * Decimal("1.2"):
        breadth = "偏强"
    elif down > up * Decimal("1.2"):
        breadth = "偏弱"
    else:
        breadth = "均衡"
    freshness = (
        ""
        if sentiment.get("qualityStatus") == "FRESH"
        else f"截至{sentiment.get('tradeDate') or '未知日期'}（非实时），"
    )
    score_text = f"，情绪 {score:.2f}" if score is not None else ""
    return (
        f"{freshness}上涨 {int(up)} 家、下跌 {int(down)} 家，"
        f"市场广度{breadth}{score_text}。"
    )


def _format_personalized_push_content(context: dict, *, session: str) -> str:
    """Render a four-line, model-free holding summary for WeCom."""
    del session  # The four-section contract is shared by every report session.
    active_context = {
        **context,
        "positions": _active_positions(context.get("positions") or []),
    }
    health, stale, overweight = _personalized_health(active_context)
    lines = [
        f"1. 一句话总结：{_personalized_summary(active_context.get('summary') or {})}",
        f"2. 持仓健康度：{health}",
        f"3. 具体行动建议：{_personalized_actions(active_context, stale, overweight)}",
        "4. 今日市场信号："
        f"{_personalized_market_signal(active_context.get('marketSentiment') or {})}",
    ]
    # Allocate more room to health details while keeping the complete message
    # below 300 characters (the three newlines bring the maximum to 298).
    line_limits = (70, 80, 75, 70)
    return "\n".join(
        _clip_push_line(line, limit)
        for line, limit in zip(lines, line_limits, strict=True)
    )


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
        item_type = str(item.get("item_type", "FACT"))
        severity = str(item.get("severity", "INFO"))
        if item_type not in _ALLOWED_ITEM_TYPES:
            raise ValueError(f"LLM item_type 非法: {item_type}")
        if severity not in _ALLOWED_SEVERITIES:
            raise ValueError(f"LLM severity 非法: {severity}")
        normalized.append(
            {
                "item_type": item_type,
                "severity": severity,
                "title": str(item.get("title", "")).strip(),
                "body": str(item.get("body", "")).strip(),
                # Model text is an interpretation until mapped to persisted
                # evidence_refs; never promote it to a fresh market fact.
                "quality": "UNAVAILABLE",
            }
        )
    normalized = [it for it in normalized if it["title"] and it["body"]]
    if not normalized:
        raise ValueError("LLM 输出缺少 items")
    return {
        "summary": summary,
        "items": normalized,
        "quality": "UNAVAILABLE",
        "status": "PARTIAL",
        "policy_check_status": "WARNING",
    }


def _fallback_draft(session: str, context: dict) -> dict:
    """Deterministic template generation when the model gateway is unavailable.

    Only facts already present in the context are stated; quality is derived
    from the underlying snapshot quality.
    """
    summary = context["summary"]
    positions = context["positions"]
    policy = context["policy"]
    context_quality = summary.get("qualityStatus", "UNAVAILABLE")

    total_asset = summary.get("totalAsset")
    known_value = summary.get("knownValue")
    if total_asset is None:
        known_suffix = (
            f"（已知价值 {known_value} 元）" if known_value is not None else ""
        )
        total_text = f"当前组合总资产不可完整估值{known_suffix}"
    else:
        total_text = f"当前组合总资产 {total_asset} 元"
    cash_text = (
        f"可用现金 {summary['cashBalance']} 元"
        if summary.get("cashBalance") is not None
        else "可用现金暂不可用"
    )
    market_value_text = (
        f"持仓市值 {summary['positionsMarketValue']} 元"
        if summary.get("positionsMarketValue") is not None
        else "持仓市值暂不可完整估值"
    )
    daily_pnl_text = (
        f"当日收益 {summary['dailyPnl']} 元"
        if summary.get("dailyPnl") is not None
        else "当日收益暂不可用"
    )
    lines = [f"{total_text}，{cash_text}，{market_value_text}，{daily_pnl_text}。"]
    if positions:
        top = positions[0]
        top_market_value = (
            f"市值 {top['marketValue']} 元"
            if top.get("marketValue") is not None
            else "市值暂不可用"
        )
        top_weight = (
            f"占组合 {top['weight']}%"
            if top.get("weight") is not None
            else "占比暂不可计算"
        )
        top_pnl = (
            f"持仓收益 {top['pnl']} 元"
            if top.get("pnl") is not None
            else "持仓收益暂不可计算"
        )
        lines.append(
            f"第一大持仓 {top['name']}（{top['symbol']}）{top_market_value}，"
            f"{top_weight}，{top_pnl}。"
        )
    else:
        lines.append("当前无持仓，组合全部为现金。")

    items: list[dict] = [
        {
            "item_type": "FACT",
            "severity": "INFO",
            "title": "组合概况",
            "body": " ".join(lines),
            "quality": context_quality,
        }
    ]
    known_weights = [
        Decimal(p["weight"]) for p in positions if p.get("weight") is not None
    ]
    if known_weights and policy and policy["single_position_limit"] is not None:
        max_weight = max(known_weights, default=ZERO)
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
                    "quality": context_quality,
                }
            )
    items.append(
        {
            "item_type": "ACTION_PLAN",
            "severity": "INFO",
            "title": "后续动作",
            "body": "跟踪持仓标的价格与政策合规状态；模型网关恢复后自动生成完整研报。",
            "quality": context_quality,
        }
    )
    return {
        "summary": "；".join(lines),
        "items": items,
        "quality": context_quality,
        "status": "ABSTAIN" if context_quality == "UNAVAILABLE" else "PARTIAL",
        "policy_check_status": "WARNING",
    }


def _attach_deterministic_fund_items(session: str, draft: dict, context: dict) -> dict:
    """Replace model-authored fund estimates with persisted calculator output."""
    if session != "FUND_1430":
        return draft
    items = [
        item
        for item in draft.get("items", [])
        if item.get("item_type") != "FUND_ESTIMATE"
    ]
    estimates = context.get("fundEstimates") or []
    if not estimates:
        items.append(
            {
                "item_type": "FUND_ESTIMATE",
                "severity": "WARNING",
                "title": "基金估值数据不可用",
                "body": "当前没有可审计的 14:30 估值观测，系统未生成基金净值预测。",
                "quality": "UNAVAILABLE",
            }
        )
    for estimate in estimates:
        metadata = estimate.get("metadata") or {}
        quality = estimate.get("quality") or "UNAVAILABLE"
        name = estimate.get("name") or estimate.get("symbol") or "场外基金"
        symbol = estimate.get("symbol") or "--"
        if quality == "ESTIMATED" and estimate.get("value") is not None:
            body = (
                f"估值 {estimate['value']}，区间 {estimate.get('lowerValue')}–"
                f"{estimate.get('upperValue')} 元；可用披露持仓与实时行情覆盖率 "
                f"{metadata.get('coveragePct', '--')}%，持仓披露日 "
                f"{metadata.get('disclosedOn', '--')}，最近正式净值 "
                f"{metadata.get('confirmedNav', '--')}。{metadata.get('explanation', '')}"
            )
            severity = "INFO"
        else:
            body = metadata.get("explanation") or (
                "正式净值、持仓披露或成分行情不足，系统已弃权。"
            )
            severity = "WARNING"
        item = {
            "item_type": "FUND_ESTIMATE",
            "subject_id": estimate.get("instrumentId"),
            "severity": severity,
            "title": f"{name}（{symbol}）14:30 估值",
            "body": body,
            "quality": quality,
        }
        if estimate.get("snapshotId"):
            item["_evidence"] = {
                "source_snapshot_id": estimate["snapshotId"],
                "claim_type": "VALUATION",
                "link_claim_type": "FACT",
                "as_of": estimate["asOf"],
                "excerpt": body,
                "quality": quality,
            }
        items.append(item)

    result = {**draft, "items": items}
    result["quality"] = _database_quality(result.get("quality"))
    if any(item.get("quality") == "UNAVAILABLE" for item in items):
        result["status"] = "PARTIAL" if estimates else "ABSTAIN"
    return result


def _database_quality(value: object) -> str:
    quality = str(value or "UNAVAILABLE").upper()
    return quality if quality in {"FRESH", "ESTIMATED", "STALE"} else "UNAVAILABLE"


def _delivery_audit_details(
    *,
    session: str,
    version: int,
    degraded: bool,
    delivery_count: int,
    generation_mode: str | None = None,
    shared_brief_id: str | None = None,
    shared_cache_hit: bool | None = None,
) -> dict:
    """Describe report delivery fan-out, including the zero-recipient case."""
    details = {
        "session": session,
        "version": version,
        "degraded": degraded,
        "deliveryCount": delivery_count,
        "deliveryStatus": "QUEUED" if delivery_count else "NO_ACTIVE_BINDING",
    }
    if generation_mode is not None:
        details["generationMode"] = generation_mode
    if generation_mode == "PRE_MARKET_DETERMINISTIC":
        details["generatorVersion"] = "pre-market-deterministic-v1"
    elif generation_mode == "FUND_1430_DETERMINISTIC":
        details["generatorVersion"] = "fund-1430-deterministic-v1"
    if shared_brief_id is not None:
        details["sharedBriefId"] = shared_brief_id
        details["sharedCacheHit"] = bool(shared_cache_hit)
    return details


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
    job_run_id: str | None,
    actor_type: str,
    generation_mode: str,
    shared_brief_id: str | None,
    shared_cache_hit: bool,
    shared_context_hash: str | None,
    push_content: str | None,
    delivery_user_id: str | None = None,
    trigger_audit_action: str | None = None,
    trigger_audit_details: dict | None = None,
) -> dict:
    latest = await repository.latest_report(conn, ctx.tenant_id, trade_date, session)
    version = (latest["version"] + 1) if latest else 1
    supersedes = str(latest["id"]) if latest else None

    correlation_id = "req_" + uuid.uuid4().hex[:16]
    finished_at = now_shanghai()

    if generation_mode in {
        "PRE_MARKET_DETERMINISTIC",
        "FUND_1430_DETERMINISTIC",
    }:
        # Deterministic structured-data generation is not a model execution.
        # Keep model_run_id nullable and record the generator version in the
        # report audit event instead of inflating model-run counts.
        model_run_id = None
    elif model_result is not None:
        model_run_id = await repository.insert_model_run(
            conn,
            tenant_id=ctx.tenant_id,
            provider=(
                "shared-brief"
                if generation_mode == "NO_HOLDINGS_SHARED"
                else "deepseek"
            ),
            model=model_result.model,
            prompt_template_version=(
                "report.no_holdings.v1"
                if generation_mode == "NO_HOLDINGS_SHARED"
                else f"report.{session.lower()}.v1"
            ),
            input_snapshot_hashes={
                (
                    "sharedContext"
                    if generation_mode == "NO_HOLDINGS_SHARED"
                    else "context"
                ): (
                    shared_context_hash
                    if generation_mode == "NO_HOLDINGS_SHARED"
                    else canonical_context_hash(context)
                ),
                **(
                    {"sharedBriefId": shared_brief_id}
                    if shared_brief_id is not None
                    else {}
                ),
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
            prompt_template_version=(
                "report.no_holdings.v1"
                if generation_mode == "NO_HOLDINGS_SHARED"
                else f"report.{session.lower()}.v1"
            ),
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
        status=draft.get("status", "PARTIAL"),
        as_of=finished_at,
        quality_status=_database_quality(draft.get("quality")),
        summary=draft["summary"],
        model_run_id=model_run_id,
        job_run_id=job_run_id,
        policy_check_status=draft.get("policy_check_status", "WARNING"),
        valid_until=valid_until,
        supersedes_report_id=supersedes,
    )
    await repository.insert_report_items(
        conn,
        tenant_id=ctx.tenant_id,
        report_id=report_id,
        items=draft["items"],
    )
    delivery_count = await repository.insert_report_outbox_deliveries(
        conn,
        tenant_id=ctx.tenant_id,
        report_id=report_id,
        session=session,
        version=version,
        trade_date=trade_date.isoformat(),
        content=push_content,
        target_user_id=delivery_user_id,
    )
    delivery_outbox_id = None
    if delivery_user_id is not None:
        delivery_outbox_id = await repository.get_report_outbox_id(
            conn, ctx.tenant_id, report_id, delivery_user_id
        )
    await write_audit(
        conn,
        tenant_id=ctx.tenant_id,
        actor_type=actor_type,
        actor_id=ctx.user_id if actor_type == "USER" else (job_run_id or "worker"),
        action="REPORT_GENERATED",
        resource_type="reporting.reports",
        resource_id=report_id,
        correlation_id=correlation_id,
        details=_delivery_audit_details(
            session=session,
            version=version,
            degraded=error_class is not None,
            delivery_count=delivery_count,
            generation_mode=generation_mode,
            shared_brief_id=shared_brief_id,
            shared_cache_hit=shared_cache_hit,
        ),
    )
    if trigger_audit_action is not None:
        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action=trigger_audit_action,
            resource_type="reporting.reports",
            resource_id=report_id,
            correlation_id=correlation_id,
            details={
                **(trigger_audit_details or {}),
                "session": session,
                "outboxId": delivery_outbox_id,
            },
        )
    report = await get_report_detail(conn, ctx.tenant_id, report_id)
    if trigger_audit_action is not None:
        report["_deliveryOutboxId"] = delivery_outbox_id
    return report


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


def _parse_json_list(value: object) -> list[dict]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _valid_until(session: str, as_of: datetime) -> datetime:
    if session == "POST_MARKET":
        return as_of.replace(hour=23, minute=59, second=59, microsecond=0)
    if session == "INTRADAY":
        return as_of.replace(hour=15, minute=0, second=0, microsecond=0)
    return as_of + timedelta(hours=2)
