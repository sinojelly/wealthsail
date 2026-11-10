"""Current NAV coordinates, with authenticated repair for displayed products."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re

from backend.funds.nav_history import local
from backend.investment.discovery_limits import (
    OPPORTUNITY_TOPIC_LIMIT,
)
from backend.investment.live_fund_plans import _code, fund_reference_contract, refresh_report_fund_plans
from backend.investment.opportunity_scoring import high_zone, score_plan
from backend.investment.opportunity_fund_matching import displayed_funds, fund_price_priority


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
    distances = []
    for track in tracks:
        low, high = _number(track.get("entryZoneMin")), _number(track.get("entryZoneMax"))
        if track.get("priceState") == "READY" and low is not None and high is not None and 0 < low <= high:
            distance = max(low - current, current - high, Decimal(0)) / current * 100
            distances.append(distance)
    if distances:
        result["distanceToEntryPct"] = float(min(distances))
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
    elif distances and min(distances) <= 3:
        result.update(
            status="NEAR_ENTRY_ZONE", label="接近技术买区",
            reasons=["WITHIN_THREE_PERCENT_OF_ENTRY_ZONE"],
        )
    else:
        result.update(
            status="NEUTRAL", label="中性观察", reasons=["OUTSIDE_TECHNICAL_ENTRY_ZONE"]
        )
    return result


def _zone_contract(plan):
    """Keep the complete refreshed calculation and expose chart coordinates."""
    return fund_reference_contract(plan)


async def enrich_fund_price_positions(
    conn, recalls, packet, now, *, tenant_id=None, user_id=None, histories=None,
):
    """Batch only displayed identities, including products outside the NAV pool."""
    now = local(now)
    if tenant_id is not None and user_id is not None:
        from backend.funds.nav_history import ensure_nav_histories

        # Rank the entire local candidate catalog first, then repair every
        # displayed product in one bounded provider round and reprice it.
        selected = await enrich_fund_price_positions(conn, recalls, packet, now, histories=histories)
        for recalled in selected.values():
            for field in ("funds", "indirectFunds"):
                if field in recalled:
                    recalled[field] = displayed_funds(recalled[field])
        codes = list(dict.fromkeys(
            fund["fundCode"] for recalled in selected.values()
            for field in ("funds", "indirectFunds") for fund in recalled.get(field, [])
            if _code({**fund, "priceBasis": "UNIT_NAV", "unit": "CNY/份"})
        ))
        if not codes:
            return selected
        prepared = await ensure_nav_histories(
            conn, codes, now, tenant_id=tenant_id, user_id=user_id, provider_budget=len(codes),
        )
        result = await enrich_fund_price_positions(
            conn, selected, packet, prepared["asOf"], histories=prepared["histories"],
        )
        for recalled in result.values():
            for field in ("funds", "indirectFunds"):
                for fund in recalled.get(field, []):
                    plan = fund.get("actionableZones")
                    if not isinstance(plan, dict):
                        continue
                    code = fund.get("fundCode")
                    if code in prepared["refresh"]:
                        plan["navSync"] = deepcopy(prepared["refresh"][code])
                    latest = (prepared.get("latestConfirmed", {}).get(code) or {})
                    try:
                        latest_date = date.fromisoformat(str(latest.get("asOf"))[:10])
                        plan_date = date.fromisoformat(str(plan.get("asOf"))[:10])
                    except (TypeError, ValueError):
                        continue
                    if latest.get("quality") == "ESTIMATED" or not plan_date < latest_date <= local(prepared["asOf"]).date():
                        continue
                    # A failed source repair must not make an older cohort
                    # current merely because that cohort was checked recently.
                    plan["historyQuality"] = "PARTIAL"
                    plan["priceRefresh"].update(state="PARTIAL", reason="NEWER_CONFIRMED_NAV_AVAILABLE")
                    if isinstance(plan.get("series"), dict):
                        plan["series"]["state"] = "PARTIAL"
                    for track in plan.get("tracks", []):
                        track.update(priceState="PARTIAL", entryReady=False, inBuyZone=False)
                    fund.update(
                        pricePosition=price_position(plan, prepared["asOf"]),
                        quantitativeScore=None,
                        referenceTracks=deepcopy(plan.get("tracks", [])),
                    )
                    for key in ("unadjustedQuantitativeScore", "quantitativeRiskPenaltyApplied"):
                        fund.pop(key, None)
                    fund["missingEvidence"] = list(dict.fromkeys([
                        *fund.get("missingEvidence", []), "NEWER_CONFIRMED_NAV_AVAILABLE",
                    ]))
                if field in recalled:
                    recalled[field].sort(key=fund_price_priority)
        return result
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
    # Up to 24 independent product families per topic must be priced before the
    # display limit; a frozen score must not discard today's buy-zone candidate.
    maximum = OPPORTUNITY_TOPIC_LIMIT * 24 * 4
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
        include_series=True,
        histories=histories,
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
                fund.update(pricePosition=position, actionableZones=_zone_contract(plan),
                            canBuyNow=False, actionable=False)
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
                            "changePct": plan.get("currentChangePct"),
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
            if field in recalled:
                recalled[field].sort(key=fund_price_priority)
    return result
