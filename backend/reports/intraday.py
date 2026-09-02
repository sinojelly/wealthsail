"""Condition-triggered INTRADAY (盘中跟踪) alert delivery.

The scheduler still ticks INTRADAY every 5 minutes, but a push is only
generated and delivered when a threshold is breached:

* a single holding's day-change % reaches +/-3%, or
* the portfolio's overall day-change % reaches +/-2%.

When a breach occurs an alert report (variant ``INTRADAY_ALERT``) is produced
whose push content states the trigger condition and the current data. The same
alert is not re-delivered to the same user within 30 minutes; see
``workflow.intraday_alert_throttle`` and :func:`repository.claim_intraday_alert`.

The FUND_1430 (基金 2:30) and other sessions are untouched by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from backend.common.database import Pool, tenant_transaction
from backend.dependencies import now_shanghai
from backend.portfolio.scope import AssetScope
from backend.reports import repository
from backend.reports.service import build_report_context, generate_report

# Single holding day-change % that triggers an alert (absolute).
INTRADAY_SINGLE_THRESHOLD_PCT = Decimal("3")
# Portfolio overall day-change % that triggers an alert (absolute).
INTRADAY_PORTFOLIO_THRESHOLD_PCT = Decimal("2")
# Same user + same alert_key will not re-fire within this window.
INTRADAY_ALERT_DEDUP_WINDOW = timedelta(minutes=30)
# Stored report variant for condition-triggered intraday alerts.
INTRADAY_ALERT_VARIANT = "INTRADAY_ALERT"


@dataclass(frozen=True)
class IntradayTrigger:
    """One breached condition that justifies an intraday alert."""

    kind: str  # "SINGLE_HOLDING" | "PORTFOLIO"
    alert_key: str  # dedup key: "HOLDING:<instrument_id>" or "PORTFOLIO"
    change_pct: Decimal
    threshold_pct: Decimal
    direction: str  # "UP" | "DOWN"
    instrument_id: str | None = None
    symbol: str | None = None
    name: str | None = None


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None


def _portfolio_day_change_pct(summary: dict) -> Decimal | None:
    """Portfolio overall day-change % derived from the dashboard summary.

    ``summary['dailyPnlRate']`` is intentionally left ``None`` (the product
    refuses to publish a fabricated ratio), so derive the day return from the
    absolute daily PnL against the beginning-of-day asset base (current total
    asset minus today's PnL).
    """
    daily = _to_decimal(summary.get("dailyPnl"))
    total = _to_decimal(summary.get("totalAsset"))
    if daily is None or total is None:
        return None
    base = total - daily
    if base == 0:
        return None
    return (daily / base) * 100


def _with_change_pct(position: dict, change_pct: Decimal | None) -> dict:
    enriched = dict(position)
    enriched["changePct"] = str(change_pct) if change_pct is not None else None
    return enriched


def evaluate_triggers(
    positions: list[dict], portfolio_change_pct: Decimal | None
) -> list[IntradayTrigger]:
    """Pure evaluation of which alert conditions are currently breached.

    ``positions`` must each carry a string/numeric ``changePct`` (today's
    day-change %); entries without a usable value are skipped.
    """
    triggers: list[IntradayTrigger] = []
    for position in positions:
        change = _to_decimal(position.get("changePct"))
        if change is None:
            continue
        if abs(change) >= INTRADAY_SINGLE_THRESHOLD_PCT:
            instrument_id = position.get("instrumentId")
            triggers.append(
                IntradayTrigger(
                    kind="SINGLE_HOLDING",
                    alert_key=f"HOLDING:{instrument_id}",
                    change_pct=change,
                    threshold_pct=INTRADAY_SINGLE_THRESHOLD_PCT,
                    direction="UP" if change > 0 else "DOWN",
                    instrument_id=instrument_id,
                    symbol=position.get("symbol"),
                    name=position.get("name"),
                )
            )
    if (
        portfolio_change_pct is not None
        and abs(portfolio_change_pct) >= INTRADAY_PORTFOLIO_THRESHOLD_PCT
    ):
        triggers.append(
            IntradayTrigger(
                kind="PORTFOLIO",
                alert_key="PORTFOLIO",
                change_pct=portfolio_change_pct,
                threshold_pct=INTRADAY_PORTFOLIO_THRESHOLD_PCT,
                direction="UP" if portfolio_change_pct > 0 else "DOWN",
            )
        )
    return triggers


def _fmt_pct(value: Decimal) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def build_intraday_alert_content(
    triggers: list[IntradayTrigger],
    portfolio_change_pct: Decimal | None,
    *,
    as_of: datetime | None = None,
) -> str:
    """Human-readable push body stating the trigger condition and live data."""
    stamp = as_of.strftime("%H:%M") if as_of is not None else ""
    lines = ["📊 盘中异动提醒（条件触发）"]
    for trigger in triggers:
        if trigger.kind == "SINGLE_HOLDING":
            label = trigger.name or trigger.symbol or trigger.instrument_id or "持仓"
            lines.append(
                f"• 持仓 {label} 今日涨跌幅 {_fmt_pct(trigger.change_pct)}"
                f"，超过阈值 ±{INTRADAY_SINGLE_THRESHOLD_PCT:.0f}%"
            )
        else:
            lines.append(
                f"• 组合整体今日涨跌幅 {_fmt_pct(trigger.change_pct)}"
                f"，超过阈值 ±{INTRADAY_PORTFOLIO_THRESHOLD_PCT:.0f}%"
            )
    if stamp:
        lines.append(f"（数据截至 {stamp}）")
    return "\n".join(lines)


async def maybe_generate_intraday_alert(
    pool: Pool,
    ctx,
    settings,
    *,
    job_run_id: str | None = None,
) -> dict | None:
    """Generate and deliver an INTRADAY alert only when a threshold is breached.

    Returns the generated report dict, or ``None`` when no alert fired (no
    breach, or every breach was inside its 30-minute cooldown). Callers map
    ``None`` to a graceful ``SKIPPED`` job outcome instead of a failure.
    """
    asset_scope = AssetScope(kind="MINE", owner_user_id=ctx.user_id)
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        context = await build_report_context(
            conn, ctx.tenant_id, "INTRADAY", asset_scope=asset_scope
        )
        positions = context["positions"]
        summary = context["summary"]

        instrument_ids = [
            str(p["instrumentId"])
            for p in positions
            if p.get("instrumentId") is not None
        ]
        change_map = (
            await repository.latest_day_change_pct(conn, ctx.tenant_id, instrument_ids)
            if instrument_ids
            else {}
        )
        enriched = [
            _with_change_pct(p, change_map.get(str(p["instrumentId"])))
            for p in positions
            if p.get("instrumentId") is not None
        ]
        portfolio_change = _portfolio_day_change_pct(summary)
        triggers = evaluate_triggers(enriched, portfolio_change)
        if not triggers:
            return None

        now = now_shanghai()
        allowed: list[IntradayTrigger] = []
        for trigger in triggers:
            if await repository.claim_intraday_alert(
                conn,
                ctx.tenant_id,
                ctx.user_id,
                trigger.alert_key,
                now,
                INTRADAY_ALERT_DEDUP_WINDOW,
            ):
                allowed.append(trigger)
        if not allowed:
            return None

        content = build_intraday_alert_content(allowed, portfolio_change, as_of=now)

    report = await generate_report(
        pool,
        ctx,
        settings,
        "INTRADAY",
        report_variant=INTRADAY_ALERT_VARIANT,
        audience_user_id=ctx.user_id,
        delivery_user_id=ctx.user_id,
        job_run_id=job_run_id,
        actor_type="SYSTEM_WORKER",
        force=True,
        push_content_override=content,
        asset_scope=asset_scope,
    )
    return report
