"""Current local NAV context for matched products, with no provider I/O."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re

from backend.funds.nav_history import local
from backend.investment.discovery_limits import (
    OPPORTUNITY_FUNDS_PER_TOPIC,
    OPPORTUNITY_TOPIC_LIMIT,
)
from backend.investment.live_fund_plans import _code, refresh_report_fund_plans
from backend.investment.opportunity_scoring import high_zone, score_plan


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def price_position(plan, now):
    """Describe a technical location; it is neither valuation nor buy permission."""
    result = {
        "status": "DATA_PENDING",
        "label": "净值数据待同步",
        "reasons": [],
        "actionable": False,
    }
    refresh = plan.get("priceRefresh") or {}
    enough = (
        plan.get("historyQuality") == "READY"
        or refresh.get("reason") == "LOCAL_PRICE_INTERVAL_UNAVAILABLE"
    )
    try:
        age = local(now).date() - date.fromisoformat(plan.get("asOf"))
    except (ValueError, TypeError):
        age = timedelta(days=999)
    current = _number(plan.get("currentPrice"))
    if (
        not enough
        or age < timedelta(0)
        or age > timedelta(days=10)
        or current is None
        or current <= 0
    ):
        result["reasons"] = [refresh.get("reason") or "LOCAL_NAV_EVIDENCE_INCOMPLETE"]
        return result
    result["asOf"] = plan["asOf"]
    tracks = [row for row in plan.get("tracks", []) if isinstance(row, dict)]
    percentile = _number((plan.get("indicators") or {}).get("navPercentile1y"))
    overheated = (
        plan.get("inSellZone") is True
        or plan.get("entryStatusTone") == "amber"
        and bool(re.search(r"高位|追高|卖出", str(plan.get("entryStatus") or "")))
        or percentile is not None
        and percentile >= 90
        or any(row.get("inSellZone") is True or high_zone(plan, row) for row in tracks)
    )
    if overheated:
        result.update(
            status="OVERHEATED",
            label="短期高位／防追高",
            reasons=["TECHNICAL_HIGH_ZONE"],
        )
    elif refresh.get("state") not in {None, "READY"}:
        result["reasons"] = [
            refresh.get("reason") or "LOCAL_PRICE_INTERVAL_UNAVAILABLE"
        ]
    elif any(
        row.get("priceState") == "READY"
        and row.get("inBuyZone") is True
        and not row.get("inSellZone")
        for row in tracks
    ):
        result.update(
            status="ENTRY_ZONE",
            label="进入技术参考区间",
            reasons=["TECHNICAL_ENTRY_ZONE_ONLY"],
        )
    else:
        result.update(
            status="NEUTRAL", label="中性观察", reasons=["OUTSIDE_TECHNICAL_ENTRY_ZONE"]
        )
    return result


async def enrich_fund_price_positions(conn, recalls, packet, now):
    """Batch only displayed identities, including products outside the NAV pool."""
    now = local(now)
    result = deepcopy(recalls)
    products = {}
    for recalled in result.values():
        for field in ("funds", "indirectFunds"):
            for fund in recalled.get(field, []):
                plan = {
                    **fund,
                    "label": fund.get("name", fund.get("fundCode")),
                    "priceBasis": "UNIT_NAV",
                    "unit": "CNY/份",
                    "currency": "CNY",
                }
                if code := _code(plan):
                    products.setdefault(code, plan)
    maximum = OPPORTUNITY_TOPIC_LIMIT * OPPORTUNITY_FUNDS_PER_TOPIC * 2
    if len(products) > maximum:
        raise ValueError("displayed fund price enrichment exceeds its budget")
    if not products:
        return result
    # Use the existing chart-cohort selection, continuity and freshness checks.
    refreshed = await refresh_report_fund_plans(
        conn,
        {"discoveryActionableZones": list(products.values())},
        now,
        packet=packet,
    )
    plans = {row["fundCode"]: row for row in refreshed["discoveryActionableZones"]}
    for recalled in result.values():
        for field in ("funds", "indirectFunds"):
            for fund in recalled.get(field, []):
                plan = plans.get(fund.get("fundCode"), {})
                if (plan.get("subjectKind"), plan.get("subjectId")) != (
                    fund.get("subjectKind"),
                    fund.get("subjectId"),
                ):
                    plan = {}
                position = price_position(plan, now)
                fund.update(pricePosition=position, canBuyNow=False, actionable=False)
                gaps = list(fund.get("missingEvidence", []))
                if position["status"] == "DATA_PENDING":
                    fund.update(
                        quantitativeScore=None, confirmedNav=None, referenceTracks=[]
                    )
                    if "LOCAL_NAV_EVIDENCE_INCOMPLETE" not in gaps:
                        gaps.append("LOCAL_NAV_EVIDENCE_INCOMPLETE")
                else:
                    scored = score_plan(plan, packet, now)
                    fund.update(
                        confirmedNav={
                            "value": plan["currentPrice"],
                            "asOf": plan["asOf"],
                            "retrievedAt": plan.get("retrievedAt"),
                            "sourceUri": plan.get("sourceUri"),
                        },
                        referenceTracks=deepcopy(plan.get("tracks", [])),
                        quantitativeScore=scored.get("opportunityScore"),
                        scoreMethodVersion=scored.get("scoreMethodVersion"),
                        scoreBreakdown=deepcopy(scored.get("scoreBreakdown", {})),
                        quantitativeRiskPenaltyApplied=-scored.get(
                            "scoreBreakdown", {}
                        ).get("industryCyclePenalty", 0),
                    )
                    gaps = [
                        gap
                        for gap in gaps
                        if gap
                        not in {
                            "LOCAL_QUANTITATIVE_EVIDENCE_MISSING",
                            "QUANTITATIVE_SCORE_UNAVAILABLE",
                            "STALE_QUANTITATIVE_EVIDENCE",
                            "LOCAL_NAV_EVIDENCE_INCOMPLETE",
                        }
                    ]
                    if fund["quantitativeScore"] is None:
                        gaps.append("QUANTITATIVE_SCORE_UNAVAILABLE")
                # Never carry a previous score's risk adjustment into no-data results.
                if fund.get("quantitativeScore") is None:
                    fund.pop("quantitativeRiskPenaltyApplied", None)
                    fund.pop("unadjustedQuantitativeScore", None)
                fund["missingEvidence"] = list(dict.fromkeys(gaps))
    return result
