"""08:35 user-scoped pre-market report: holdings, watchlist and reminders.

Content is deterministic and built only from facts that belong to the audienced
member: their own positions under ``AssetScope(kind="MINE")``, their watchlist,
and the corporate actions or fund cutoffs that apply to those instruments. The
global overnight picture belongs to the 08:30 macro report and is deliberately
not repeated here. There is no model call in this phase.
"""

from __future__ import annotations

from backend.common.database import tenant_transaction
from backend.common.tenancy import TenantContext
from backend.portfolio.scope import AssetScope
from backend.reports.pre_market import (
    ACTION_LABELS,
    cutoff_text,
    holding_impact_text,
    instrument_label,
    safe_decimal,
)

_MISSING = "暂无"
_DISCLAIMER = "以上仅为市场信息与投资思考，不构成投资建议。"
_SECTION_TITLES = ("持仓今日影响", "关注标的动态", "今日操作提醒")


def _watchlist_text(context: dict) -> str:
    """Summarize watched instruments from their latest persisted quote."""
    watched = context.get("watchlist") or []
    if not watched:
        return _MISSING
    parts: list[str] = []
    for item in watched:
        if not isinstance(item, dict):
            continue
        label = instrument_label(item)
        change = safe_decimal(item.get("changePct"))
        price = safe_decimal(item.get("price"))
        if change is None and price is None:
            parts.append(f"{label}暂无行情")
            continue
        move_text = f"{change:+.2f}%" if change is not None else _MISSING
        price_text = f"{price:.4f}" if price is not None else _MISSING
        quality = str(item.get("quality") or _MISSING)
        parts.append(
            f"{label}最新价{price_text}、涨跌{move_text}（数据质量：{quality}）"
        )
    if not parts:
        return _MISSING
    actions = _watched_actions(context)
    action_text = "；".join(actions) if actions else _MISSING
    return f"{'；'.join(parts)}。今日公司行动：{action_text}。"


def _watched_actions(context: dict) -> list[str]:
    watched_ids = {
        str(item.get("instrumentId"))
        for item in (context.get("watchlist") or [])
        if isinstance(item, dict) and item.get("instrumentId")
    }
    if not watched_ids:
        return []
    held_ids = {
        str(position.get("instrumentId"))
        for position in (context.get("positions") or [])
        if isinstance(position, dict) and position.get("instrumentId")
    }
    actions: list[str] = []
    for action in (context.get("preMarket") or {}).get("corporateActions") or []:
        if not isinstance(action, dict):
            continue
        instrument_id = str(action.get("instrumentId") or "")
        if instrument_id not in watched_ids or instrument_id in held_ids:
            # Holdings already carry their action in the impact section.
            continue
        action_label = ACTION_LABELS.get(
            str(action.get("actionType") or ""), "公司行动"
        )
        actions.append(f"{instrument_label(action)}今日{action_label}")
    return actions


def _impact_quality(pre: dict) -> str:
    return "FRESH" if pre.get("corporateActions") else "UNAVAILABLE"


def _watchlist_quality(context: dict) -> str:
    watched = context.get("watchlist") or []
    if not watched:
        return "UNAVAILABLE"
    if _watched_actions(context):
        return "FRESH"
    has_quote = any(
        isinstance(item, dict)
        and (
            safe_decimal(item.get("price")) is not None
            or safe_decimal(item.get("changePct")) is not None
        )
        for item in watched
    )
    return "FRESH" if has_quote else "UNAVAILABLE"


def build_report(context: dict) -> dict:
    """Build the three-section personalized brief without any model I/O."""
    pre = context.get("preMarket") or {}
    # 持仓影响需要 positions 做归因，因此传整个 context 而非仅 preMarket。
    impact = holding_impact_text(context)
    watchlist = _watchlist_text(context)
    reminder = cutoff_text(pre)
    sections = (
        (_SECTION_TITLES[0], impact, "RISK", _impact_quality(pre)),
        (_SECTION_TITLES[1], watchlist, "FACT", _watchlist_quality(context)),
        (
            _SECTION_TITLES[2],
            reminder,
            "ACTION_PLAN",
            "FRESH" if pre.get("fundCutoffRules") else "UNAVAILABLE",
        ),
    )
    qualities = [item_quality for *__, item_quality in sections]
    overall = "UNAVAILABLE"
    for candidate in ("FRESH", "ESTIMATED", "STALE"):
        if candidate in qualities:
            overall = candidate
            break
    return {
        "summary": "\n".join(
            f"{index}. {title}：{body}"
            for index, (title, body, _item_type, _item_quality) in enumerate(
                sections, start=1
            )
        ),
        "items": [
            {
                "item_type": item_type,
                "severity": "WARNING" if item_quality == "UNAVAILABLE" else "INFO",
                "title": title,
                "body": body,
                "quality": item_quality,
            }
            for title, body, item_type, item_quality in sections
        ],
        "quality": overall,
        # Watchlist quotes and cutoff rules are the only sourced inputs; the
        # section stays explicitly partial rather than claiming completeness.
        "status": "PARTIAL",
        "policy_check_status": "WARNING",
    }


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("；，。") + "…"


def format_push_content(context: dict) -> str:
    """Build a three-line WeCom summary capped at 200 characters."""
    pre = context.get("preMarket") or {}
    draft = build_report(context)
    rules = pre.get("fundCutoffRules") or []
    reminder_text = _MISSING
    if rules:
        first_rule = rules[0]
        cutoff = str(first_rule.get("cutoffTime") or "")[:5]
        if cutoff:
            reminder_text = (
                f"{instrument_label(first_rule, include_symbol=False)}"
                f"{cutoff}截止参考，以渠道状态为准"
            )
    return "\n".join(
        (
            f"持仓：{_clip(str(draft['items'][0]['body']), 70)}",
            f"关注：{_clip(str(draft['items'][1]['body']), 70)}",
            f"提醒：{_clip(reminder_text, 40)}",
        )
    )


async def generate_pre_market_personalized(
    pool,
    ctx: TenantContext,
    settings,
    *,
    job_run_id: str | None = None,
) -> dict | None:
    """Generate one member's 08:35 report, or None when there is nothing to say.

    A member with neither active positions nor a watchlist has no personal
    facts to report, so the phase skips them and they keep only the shared
    08:30 macro brief.
    """
    # Imported lazily: backend.reports.service imports this module.
    from backend.reports import service

    scope = AssetScope(kind="MINE", owner_user_id=ctx.user_id)
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        context = await service.build_report_context(
            conn,
            ctx.tenant_id,
            "PRE_MARKET",
            audience_user_id=ctx.user_id,
            asset_scope=scope,
        )
    if not context["positions"] and not context["watchlist"]:
        return None
    return await service.generate_report(
        pool,
        ctx,
        settings,
        "PRE_MARKET",
        job_run_id=job_run_id,
        actor_type="SYSTEM_WORKER" if job_run_id else "USER",
        report_variant="PRE_MARKET_PERSONALIZED",
        audience_user_id=ctx.user_id,
        delivery_user_id=ctx.user_id,
        context=context,
    )
