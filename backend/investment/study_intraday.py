"""Refresh public same-fund estimates after actor-scoped database reads finish."""

import asyncio
from copy import deepcopy
from decimal import Decimal
import logging
import re
from uuid import UUID

from backend.dependencies import now_shanghai
from backend.investment.study_price_watch import (
    _number,
    _source_uri,
    _stamp,
    _trading_time,
    _usable,
    price_watch,
)

logger = logging.getLogger("wealthsail.study_intraday")


async def resolve_fund_scopes(conn, items, now):
    """Fill legacy read-model identities from unique current aliases, without writes."""
    targets, instrument_ids, codes = {}, set(), set()
    for index, item in enumerate(items):
        scope = item.get("scope") or {}
        if scope.get("fundCode") and scope.get("fundMarket"):
            continue
        code, instrument = scope.get("fundCode"), scope.get("instrumentId")
        if isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code):
            codes.add(code)
        if instrument:
            try:
                instrument_ids.add(UUID(str(instrument)))
            except (ValueError, TypeError):
                pass
        if code or instrument:
            targets[index] = scope
    if not targets or not (instrument_ids or codes):
        return items
    rows = await conn.fetch(
        """SELECT DISTINCT i.id AS instrument_id,a.alias_value AS fund_code,
          i.primary_market AS market
        FROM reference.instruments i JOIN reference.instrument_aliases a ON a.instrument_id=i.id
        WHERE i.lifecycle_status='ACTIVE'
          AND i.asset_type IN ('EXCHANGE_FUND','OFF_EXCHANGE_FUND')
          AND a.alias_type='TICKER' AND a.alias_value ~ '^[0-9]{6}$'
          AND a.effective_from<=$3 AND (a.effective_to IS NULL OR a.effective_to>=$3)
          AND (i.id=ANY($1::uuid[]) OR a.alias_value=ANY($2::text[]))""",
        sorted(instrument_ids),
        sorted(codes),
        _stamp(now).date(),
    )
    candidates = {
        (str(row["instrument_id"]), row["fund_code"], row["market"])
        for row in rows
        if row["market"]
    }
    output = list(items)
    for index, scope in targets.items():
        matches = {
            (instrument, code, market)
            for instrument, code, market in candidates
            if (
                not scope.get("instrumentId")
                or str(scope["instrumentId"]) == instrument
            )
            and (not scope.get("fundCode") or scope["fundCode"] == code)
            and (not scope.get("fundMarket") or scope["fundMarket"] == market)
        }
        if len(matches) != 1:
            continue
        instrument, code, market = next(iter(matches))
        resolved = {"instrumentId": instrument, "fundCode": code, "fundMarket": market}
        output[index] = {**items[index], "scope": {**scope, **resolved}}
    return output


def _current_quote(observation, plan, now):
    """An exchange ETF quote is never relabelled as a linked fund's unit NAV."""
    if (
        observation.symbol != plan.get("fundCode")
        or observation.subject_type != "FUND"
        or observation.field_code != "ESTIMATED_NAV"
        or observation.quality_status != "ESTIMATED"
        or _number(observation.value) is None
        or not _source_uri(observation.origin_uri)
    ):
        return None
    stamp = _stamp(observation.as_of)
    if stamp is None:
        return None
    metadata = observation.metadata or {}
    base = _number(metadata.get("confirmedNav"))
    change = (observation.value / base - Decimal("1")) * 100 if base else None
    quote = {
        "subjectKind": plan.get("subjectKind"),
        "subjectId": plan.get("subjectId"),
        "fundCode": observation.symbol,
        "priceBasis": "UNIT_NAV",
        "unit": observation.unit,
        "value": str(observation.value),
        "asOf": stamp.isoformat(),
        "checkedAt": now.isoformat(),
        "retrievedAt": now.isoformat(),
        "quality": observation.quality_status,
        "sourceUri": observation.origin_uri,
        "usable": True,
        "tradingDayConfirmed": True,
        "changePct": format(change.quantize(Decimal("0.01")), "f")
        if change is not None
        else None,
        "methodVersion": observation.method_version,
        "explanation": metadata.get("explanation")
        or "同基金盘中估算净值，最终以基金公司披露净值为准。",
    }
    return quote if _usable(quote, plan, now, intraday=True) else None


async def refresh_tracks(
    items, now, *, trading_day, loader=None, now_provider=now_shanghai
):
    """Each page read refreshes in a proven open SSE session, as one bounded batch.

    Inputs already passed actor RLS. No portfolio, plan, scheduled check or
    historical 14:30 snapshot is written by this presentation-only refresh.
    """
    now = _stamp(now)
    if now is None or trading_day is not True or not _trading_time(now):
        return items
    targets = {}
    for index, item in enumerate(items):
        watch = item.get("priceWatch") or {}
        plan = watch.get("frozenPlan") or {}
        has_plan = bool(plan)
        scope = item.get("scope") or {}
        if not has_plan and scope.get("fundMarket") == "FUND_CN":
            # The validated selection scope supplies only quote identity. It
            # does not create a frozen price plan or a trigger comparison.
            plan = {
                "fundCode": scope.get("fundCode"),
                "subjectKind": "nav",
                "subjectId": scope.get("fundCode"),
                "priceBasis": "UNIT_NAV",
                "unit": "元/份",
                "asOf": now.date().isoformat(),
            }
        code = plan.get("fundCode")
        if (
            isinstance(code, str)
            and re.fullmatch(r"[0-9]{6}", code)
            and plan.get("subjectKind") in {"nav", "fund"}
            and plan.get("priceBasis") == "UNIT_NAV"
        ):
            targets[index] = (watch, plan, has_plan)
    if not targets:
        return items
    if loader is None:
        from backend.integrations.market_data import fetch_fund_intraday_estimates

        loader = fetch_fund_intraday_estimates
    try:
        observations = await asyncio.wait_for(
            loader(sorted({plan["fundCode"] for _, plan, _ in targets.values()})),
            timeout=8,
        )
    except Exception as exc:  # A quote outage must not hide the frozen study.
        logger.warning("Page-open fund estimate unavailable: %s", type(exc).__name__)
        return items
    finished = _stamp(now_provider())
    if (
        finished is None
        or finished.date() != now.date()
        or finished < now
        or not _trading_time(finished)
    ):
        return items
    by_code = {observation.symbol: observation for observation in observations}
    output = list(items)
    for index, (watch, plan, has_plan) in targets.items():
        observation = by_code.get(plan["fundCode"])
        quote = (
            _current_quote(observation, plan, finished)
            if observation is not None
            else None
        )
        if quote is None:
            continue
        if not has_plan:
            output[index] = {**items[index], "currentEstimate": quote}
            continue
        refreshed = price_watch(
            plan,
            confirmed=watch.get("confirmedNav"),
            estimate=watch.get("estimate1430"),
            current_estimate=quote,
            holding=watch.get("holding"),
            now=finished,
            entry_safety=watch.get("entrySafety"),
        )
        output[index] = {**items[index], "priceWatch": {**deepcopy(watch), **refreshed}}
    return output


async def refresh_watch_context(
    data, now, *, trading_day, loader=None, now_provider=now_shanghai
):
    """Use one quote request for manually tracked funds and imported holdings."""
    items = data.get("items") or []
    holdings = data.get("heldFunds") or []
    suppressed = set(data.get("suppressedFundKeys") or [])
    held_items = []
    for fund in holdings:
        confirmed = fund.get("confirmedNav") or {}
        held_items.append(
            {
                "scope": {
                    "fundCode": fund.get("fundCode"),
                    "fundMarket": fund.get("fundMarket"),
                    "instrumentId": fund.get("instrumentId"),
                },
                "priceWatch": {
                    "frozenPlan": {
                        "subjectKind": "nav",
                        "subjectId": fund.get("fundCode"),
                        "fundCode": fund.get("fundCode")
                        if fund.get("fundMarket") == "FUND_CN"
                        else None,
                        "priceBasis": "UNIT_NAV",
                        "unit": "元/份",
                        "asOf": confirmed.get("asOf") or now.date().isoformat(),
                    }
                },
            }
        )
    combined = [*items, *held_items]
    active_indices = []
    for index, item in enumerate(combined):
        plan = (item.get("priceWatch") or {}).get("frozenPlan") or {}
        scope = item.get("scope") or {}
        code = plan.get("fundCode") or scope.get("fundCode")
        market = scope.get("fundMarket") or plan.get("market") or "FUND_CN"
        instrument = scope.get("instrumentId")
        if not suppressed.intersection(
            {f"{market}:{code}", f"{market}:instrument:{instrument}"}
        ):
            active_indices.append(index)
    active = await refresh_tracks(
        [combined[index] for index in active_indices],
        now,
        trading_day=trading_day,
        loader=loader,
        now_provider=now_provider,
    )
    refreshed = list(combined)
    for index, item in zip(active_indices, active, strict=True):
        refreshed[index] = item
    return {
        **data,
        "items": refreshed[: len(items)],
        "heldFunds": [
            {
                **fund,
                "currentEstimate": (item.get("priceWatch") or {}).get(
                    "currentEstimate"
                ),
            }
            for fund, item in zip(holdings, refreshed[len(items) :], strict=True)
        ],
    }
