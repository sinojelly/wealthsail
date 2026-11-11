"""Reprice response copies from local public NAV, leaving research versions intact."""

from collections import OrderedDict
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import hashlib
import json
from uuid import UUID

from backend.funds.nav_history import (
    full_history_proven,
    local,
    obj,
    recently_checked,
    select_history_view,
    stamp as nav_stamp,
    valid_points,
)
from backend.indicators.actionable_zones import (
    VERSION,
    compute_actionable_price_plan,
    apply_industry_cycle,
    _price as nav_display_price,
)
from backend.funds.nav_window import NAV_RESEARCH_MAX_SAMPLES, NAV_RESEARCH_MIN_SAMPLES
from backend.investment.opportunity_scoring import SCORE_FIELDS
from backend.investment.discovery_limits import RESEARCH_SELECTION_LIMIT
from backend.investment.theme_graph import _disclosed_nav_quality, _history_quality
from backend.macro.daily_research import apply_entry_guards

_UNITS = {"CNY", "CNY/份", "元/份", "CNY/unit"}
_PLAN_CACHE = OrderedDict()
_PLAN_CACHE_LIMIT = 256
_PLAN_ARRAYS = (
    "actionablePlans",
    "actionableZones",
    "themeActionableZones",
    "discoveryActionableZones",
)
_PRICES = (
    "entryZoneMin",
    "entryZoneMax",
    "stopLossPrice",
    "takeProfitT1",
    "takeProfitT2",
    "sellZoneMin",
    "sellZoneMax",
    "estimatedProfitMinPct",
    "estimatedProfitMaxPct",
    "currentToSellMaxPct",
)
_STATUS = (
    "technicalTriggered",
    "inBuyZone",
    "entryReady",
    "entryBlocked",
    "entryBlockReason",
    "entryStatus",
    "entryStatusTone",
    "inSellZone",
    "buyZoneAnchor",
)
_STALE = (
    *_PRICES,
    *_STATUS,
    *SCORE_FIELDS,
    "signals",
    "priceRevisionStatus",
    "priceRevisionNote",
    "priceRevisionBasis",
    "priceRevisionSourceIds",
    "rewardRiskRatio",
    "relativeStrength",
    "industryAdjustmentBase",
    "industryRiskAdjustment",
    "industryRiskWarning",
    "riskAdjustmentVersion",
)


def _code(plan):
    if not isinstance(plan, dict) or plan.get("priceBasis") != "UNIT_NAV":
        return None
    kind, subject = plan.get("subjectKind"), plan.get("subjectId")
    code = plan.get("fundCode") or (subject if kind == "nav" else None)
    if (
        kind not in {"nav", "fund"}
        or not isinstance(code, str)
        or re.fullmatch(r"[0-9]{6}", code) is None
        or plan.get("unit") not in _UNITS
        or plan.get("currency", "CNY") != "CNY"
        or plan.get("market") not in {None, "FUND_CN"}
        or re.search(r"美元|港币|港元|外币|现汇|现钞", str(plan.get("label") or ""))
    ):
        return None
    if kind == "nav":
        return code if subject == code else None
    try:
        UUID(str(subject))
    except (TypeError, ValueError, AttributeError):
        return None
    return code if plan.get("market") == "FUND_CN" else None


def _plans(report):
    for field in _PLAN_ARRAYS:
        yield from (row for row in report.get(field, []) if isinstance(row, dict))
    for direction in report.get("valueDirections", []):
        if isinstance(direction, dict):
            for field in ("plans", "fundPlans", "quantitativeEvidence"):
                yield from (
                    row for row in direction.get(field, []) if isinstance(row, dict)
                )
    for field in ("mentionedFunds",):
        yield from (row for row in report.get(field, []) if isinstance(row, dict))
    for field in ("researchTopics", "topicCatalog"):
        for topic in report.get(field, []):
            if isinstance(topic, dict):
                for plans in ("plans", "fundPlans"):
                    yield from (
                        row for row in topic.get(plans, []) if isinstance(row, dict)
                    )
    yield from (
        row
        for row in (report.get("quantitativeScan") or {}).get("items", [])
        if isinstance(row, dict)
    )


def _reference_defaults(plan):
    """Complete coordinate metadata only for an already resolved CNY identity."""
    if plan.get("market") == "FUND_CN" and plan.get("subjectKind") in {"nav", "fund"}:
        candidate = {"priceBasis": "UNIT_NAV", "unit": "CNY/份", **plan}
        if _code(candidate):
            plan.setdefault("priceBasis", "UNIT_NAV")
            plan.setdefault("unit", "CNY/份")
    return plan


def fund_reference_contract(plan):
    """Full deterministic plan plus one compact set of chart/price coordinates."""
    if not isinstance(plan, dict) or not isinstance(plan.get("tracks"), list):
        return None
    result = {key: deepcopy(value) for key, value in plan.items() if key != "actionableZones"}
    selected = plan.get("selectedTrack", plan.get("track"))
    tracks = result["tracks"]
    priced = [
        row for row in tracks
        if row.get("entryZoneMin") is not None and row.get("entryZoneMax") is not None
    ]
    track = next((row for row in priced if row.get("track") == selected), None)
    track = track or next((row for row in priced if row.get("priceState") == "READY"), None)
    track = track or next(iter(priced), {})
    result.update(
        buyZone={"min": track.get("entryZoneMin"), "max": track.get("entryZoneMax")},
        sellZone={"min": track.get("sellZoneMin", track.get("takeProfitT1")),
                  "max": track.get("sellZoneMax", track.get("takeProfitT2"))},
        stopLoss=track.get("stopLossPrice"),
        targets=[track[key] for key in ("takeProfitT1", "takeProfitT2") if track.get(key) is not None],
    )
    return result


def _histories(value):
    if isinstance(value, list):
        for row in value:
            history = obj(row)
            if history:
                yield history
    elif isinstance(value, dict):
        if "fundCode" in value:
            yield value
        else:
            for rows in value.values():
                yield from _histories(rows)


def _safe_history(history):
    return (
        history.get("currency", "CNY") == "CNY"
        and history.get("unit", "CNY") in _UNITS
        and not re.search(
            r"美元|港币|港元|外币|现汇|现钞",
            str(history.get("fundName") or history.get("label") or ""),
        )
    )


def _saved_curve_history(plan, code, now):
    """Reuse only a consistent server-plan snapshot, never a declared URL alone."""
    tracks = plan.get("tracks")
    if not (
        plan.get("methodVersion") == VERSION
        and _code(plan) == code
        and isinstance(tracks, list)
        and len(tracks) == 2
        and all(isinstance(row, dict) and isinstance(row.get("track"), str) for row in tracks)
        and {row["track"] for row in tracks} == {"LEFT_VALUE", "RIGHT_TREND"}
    ):
        return None
    series = plan.get("series")
    if not isinstance(series, dict):
        zones = plan.get("actionableZones")
        series = zones.get("series") if isinstance(zones, dict) else None
    if not (
        isinstance(series, dict)
        and series.get("fundCode") == code
        and series.get("priceBasis") == "UNIT_NAV"
        and series.get("unit") in _UNITS
        and isinstance(series.get("points"), list)
        and all(isinstance(row, dict) for row in series["points"])
        and plan.get("asOf") == series.get("asOf")
        and plan.get("sourceUri") == series.get("sourceUri")
    ):
        return None
    retrieved, available, series_retrieved = (
        nav_stamp(value)
        for value in (plan.get("retrievedAt"), plan.get("availableAt"), series.get("retrievedAt"))
    )
    refresh = plan.get("priceRefresh")
    refresh = refresh if isinstance(refresh, dict) else {}
    calculated = nav_stamp(refresh.get("calculatedAt"))
    if not (
        retrieved is not None and available is not None and calculated is not None
        and series_retrieved == retrieved <= available <= calculated <= now
        and refresh.get("methodVersion") == VERSION
        and refresh.get("navAsOf") == series["asOf"]
    ):
        return None
    history = {
        "fundCode": code, "priceBasis": "UNIT_NAV", "unit": series["unit"],
        "currency": "CNY", "sourceUri": series.get("sourceUri"),
        "asOf": series.get("asOf"), "retrievedAt": series.get("retrievedAt"),
        "availableAt": plan["availableAt"],
        "points": [{"date": row.get("date"), "close": row.get("value")} for row in series["points"]],
        # The snapshot proves recorded coordinates, not a new source recheck or
        # complete live coverage. Keep reference prices without granting entry.
        "coverageReason": "LOCAL_NAV_CONTINUITY_UNVERIFIED",
    }
    points = valid_points(history, code, now)
    window = plan.get("inputWindow")
    window = window if isinstance(window, dict) else {}
    if not (
        points and len(points) == len(series["points"])
        and points[-1]["date"] == history["asOf"]
        and [row["date"] for row in history["points"]] == [row["date"] for row in points]
        and type(window.get("sampleCount")) is int and window["sampleCount"] == len(points)
        and window.get("start") == points[0]["date"]
        and window.get("end") == points[-1]["date"]
    ):
        return None
    try:
        # UNIT_NAV plans display four decimal places; bind the saved quote to
        # that same deterministic rounding of the curve's actual last point.
        price = Decimal(str(plan.get("currentPrice")))
        if not price.is_finite() or price <= 0 or price != Decimal(nav_display_price(points[-1]["close"])):
            return None
    except (InvalidOperation, TypeError, ValueError):
        return None
    try:
        change = plan.get("currentChangePct")
        if not isinstance(change, bool):
            change = Decimal(str(change))
            if change.is_finite() and change > -100:
                # A distribution changes unit NAV independently of its
                # disclosed daily return. Keep the saved same-quote disclosure.
                history["points"][-1]["returnPct"] = str(change)
    except (InvalidOperation, TypeError, ValueError):
        pass
    return history


async def _local_histories(conn, codes, now):
    # Match the chart's latest eight retrievals from each source exactly.
    rows = await conn.fetch(
        """WITH chart AS (
          SELECT fund_code,payload,
            row_number() OVER(PARTITION BY fund_code ORDER BY retrieved_at DESC,id DESC) AS recent_rank
          FROM market.fund_nav_chart_histories WHERE fund_code=ANY($1::text[])
            AND retrieved_at<=$2 AND available_at<=$2
        ), research AS (
          SELECT fund_code,payload,
            row_number() OVER(PARTITION BY fund_code ORDER BY retrieved_at DESC,id DESC) AS recent_rank
          FROM market.fund_research_nav_histories WHERE fund_code=ANY($1::text[])
            AND retrieved_at<=$2 AND available_at<=$2
        ) SELECT fund_code,payload FROM chart WHERE recent_rank<=8
          UNION ALL SELECT fund_code,payload FROM research WHERE recent_rank<=8""",
        codes,
        now,
    )
    for row in rows:
        history = obj(row.get("payload"))
        if (
            history.get("fundCode") in codes
            and row.get("fund_code") == history["fundCode"]
        ):
            yield history


def _chart_points(view, histories, now):
    points = [{"date": row["date"], "close": row["unitNav"]} for row in view["points"]]
    # A distribution can change raw unit NAV while the provider's disclosed
    # daily return stays positive; use its return only for this exact source.
    for history in histories:
        if history.get("sourceUri") != view.get("sourceUri") or history.get(
            "retrievedAt"
        ) != view.get("retrievedAt"):
            continue
        if not valid_points(history, view["fundCode"], now):
            continue
        raw = {
            row.get("date"): row
            for row in history.get("points", [])
            if isinstance(row, dict)
        }
        if not points or history.get("asOf", points[-1]["date"]) != view.get("asOf"):
            continue
        for point in points:
            source = raw.get(point["date"], {})
            try:
                if (
                    Decimal(str(source.get("close"))) == Decimal(point["close"])
                    and "returnPct" in source
                ):
                    point["returnPct"] = source["returnPct"]
            except (InvalidOperation, TypeError, ValueError):
                continue
        break
    return points


def _continuity_proven(code, view, histories, now):
    for history in histories:
        if history.get("sourceUri") != view.get("sourceUri") or history.get(
            "retrievedAt"
        ) != view.get("retrievedAt"):
            continue
        points = valid_points(history, code, now)
        if not points or points[-1]["date"] != view.get("asOf"):
            continue
        coordinates = {row["date"]: Decimal(row["close"]) for row in points}
        if any(
            coordinates.get(row["date"]) != Decimal(row["unitNav"])
            for row in view["points"]
        ):
            continue
        # Match disclosed-NAV freshness limits before accepting any proof:
        # a complete source can still have no recent NAV disclosure.
        nav_age = now.date() - date.fromisoformat(points[-1]["date"])
        if nav_age.days > 10 or not recently_checked(history, now):
            return False
        if full_history_proven(history, points, now):
            return True
        if _disclosed_nav_quality(history, points, now)[0] == "READY":
            return True
        if _history_quality(history, points, now)[0] == "READY":
            return True
        return False
    return False


def _signals(plan):
    current = plan.get("currentPrice")
    signals = []
    for track in plan.get("tracks", []):
        try:
            price, low, high = (
                Decimal(str(raw))
                for raw in (current, track["entryZoneMin"], track["entryZoneMax"])
            )
            if not 0 < low <= high or price <= 0:
                continue
        except (KeyError, InvalidOperation, ValueError, TypeError):
            continue
        in_zone = low <= price <= high
        distance = (
            (low - price) / price
            if price < low
            else (price - high) / price
            if price > high
            else Decimal(0)
        )
        ready = (
            track.get("state") == "CANDIDATE"
            and track.get("priceState") == "READY"
            and in_zone
            and track.get("entryReady") is not False
            and not any(
                row.get("entryBlocked") is True
                or row.get("inSellZone") is True
                or row.get("subscriptionRestricted") is True
                for row in (plan, track)
            )
        )
        signals.append(
            {
                "track": track["track"],
                "triggerState": track["state"],
                "candidate": track["state"] == "CANDIDATE",
                "inEntryZone": in_zone,
                "entryReady": ready,
                "distanceToEntryPct": round(float(distance * 100), 4),
                **{
                    field: deepcopy(track[field]) for field in _STATUS if field in track
                },
            }
        )
        signals[-1]["entryReady"] = ready
    return signals


def _cached_nav_plan(code, view, points, benchmark):
    """Cache immutable indicator math; freshness and entry guards run every read."""
    benchmark_points = (benchmark or {}).get("points")
    # Observation identities alone cannot detect in-place provider revisions.
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "points": points,
                "benchmark": benchmark_points,
                "source": view.get("sourceUri"),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    key = (
        code,
        view.get("asOf"),
        view.get("retrievedAt"),
        len(points),
        VERSION,
        (benchmark or {}).get("asOf"),
        fingerprint,
    )
    cached = _PLAN_CACHE.get(key)
    if cached is not None:
        _PLAN_CACHE.move_to_end(key)
        return deepcopy(cached)
    plan = compute_actionable_price_plan(
        points,
        subject_kind="nav",
        subject_id=code,
        label=code,
        price_basis="UNIT_NAV",
        unit="CNY/份",
        market="FUND_CN",
        source_uri=view.get("sourceUri") if points else None,
        benchmark_points=benchmark_points,
    )
    _PLAN_CACHE[key] = deepcopy(plan)
    _PLAN_CACHE.move_to_end(key)
    while len(_PLAN_CACHE) > _PLAN_CACHE_LIMIT:
        _PLAN_CACHE.popitem(last=False)
    return plan


async def refresh_report_fund_plans(
    conn, report, now, *, histories=None, packet=None, include_series=False,
    tenant_id=None, user_id=None, repair_nav=True,
):
    """Refresh every research reference from recorded public NAV coordinates.

    Saved-report reads set repair_nav=False and never await a provider. Explicit
    repair callers can use the narrow authenticated history persistence RPC;
    research status and audit coverage never filter the underlying products.
    """
    if not isinstance(report, dict):
        return report
    from backend.macro.industry_cycle import expire_report_industry_risks
    from backend.common.report_chart_series import freeze_chart_series, freeze_report_chart_series

    now = local(now)
    if not repair_nav:
        report = freeze_report_chart_series(report)
    result = expire_report_industry_risks(report, now)
    if tenant_id is not None and user_id is not None:
        from backend.investment.research_fund_references import resolve_report_fund_references

        result = await resolve_report_fund_references(conn, result, packet, now)
    plans = [(plan, code) for plan in _plans(result) if (code := _code(_reference_defaults(plan)))]
    if not plans:
        return result
    codes = list(dict.fromkeys(code for _, code in plans))
    prepared = None
    if repair_nav and tenant_id is not None and user_id is not None:
        from backend.funds.nav_history import ensure_nav_histories

        prepared = await ensure_nav_histories(
            conn, codes, now, tenant_id=tenant_id, user_id=user_id,
            provider_budget=len(codes),
        )
        now = local(prepared["asOf"])
    candidates = {code: [] for code in codes}
    supplied = [
        *_histories(histories),
        *_histories((packet or {}).get("fundHistories", [])),
        *_histories((prepared or {}).get("histories", [])),
    ]
    for offset in range(0, len(codes) if prepared is None else 0, RESEARCH_SELECTION_LIMIT):
        supplied.extend(
            [
                row
                async for row in _local_histories(
                    conn, codes[offset : offset + RESEARCH_SELECTION_LIMIT], now
                )
            ]
        )
    for history in supplied:
        if history.get("fundCode") in candidates and _safe_history(history):
            candidates[history["fundCode"]].append(history)
    calculated = {}
    for code in codes:
        view = select_history_view(code, code, "2y", candidates[code], now)
        if not repair_nav and not view["points"]:
            for original, plan_code in plans:
                if plan_code == code and (saved := _saved_curve_history(original, code, now)):
                    candidates[code].append(saved)
                    view = select_history_view(code, code, "2y", candidates[code], now)
                    break
        points = _chart_points(view, candidates[code], now)
        enough = len(points) >= NAV_RESEARCH_MIN_SAMPLES
        # The engine keeps short-window references separate from 120-sample
        # historical anchors. Preserve its real NAV and calculable coordinates.
        plan = _cached_nav_plan(
            code,
            view,
            points,
            (packet or {}).get("relativeStrengthBenchmark"),
        )
        state = (
            "UNAVAILABLE"
            if not points
            else "READY"
            if enough and view["state"] == "READY"
            else "PARTIAL"
        )
        reason = (
            "LOCAL_NAV_HISTORY_INSUFFICIENT"
            if points and not enough
            else "LOCAL_NAV_HISTORY_UNAVAILABLE"
            if not points
            else view["coverage"].get("reason")
        )
        if state == "READY" and not _continuity_proven(
            code, view, candidates[code], now
        ):
            state, reason = "PARTIAL", "LOCAL_NAV_CONTINUITY_UNVERIFIED"
        if state == "READY" and not any(
            track.get("priceState") == "READY" for track in plan["tracks"]
        ):
            state, reason = "PARTIAL", "LOCAL_PRICE_INTERVAL_UNAVAILABLE"
        if state != "READY":
            for track in plan["tracks"]:
                track.update(entryReady=False, inBuyZone=False)
                if state == "PARTIAL" and track["priceState"] == "READY":
                    track["priceState"] = "PARTIAL"
                if reason:
                    track["reasons"].append(reason)
        plan.update(
            historyQuality=state,
            historySampleCount=len(points),
            historyTargetSamples=NAV_RESEARCH_MAX_SAMPLES,
            retrievedAt=view.get("retrievedAt") if points else None,
            availableAt=next(
                (
                    history.get("availableAt")
                    for history in candidates[code]
                    if points
                    and history.get("sourceUri") == view.get("sourceUri")
                    and history.get("retrievedAt") == view.get("retrievedAt")
                    and history.get("asOf", view.get("asOf")) == view.get("asOf")
                ),
                None,
            ),
            historyCoverage="SUFFICIENT" if enough else "SHORT_HISTORY",
            priceRefresh={
                "state": state,
                "calculatedAt": now.isoformat(),
                "navAsOf": plan["asOf"],
                "methodVersion": VERSION,
                **({"reason": reason} if reason else {}),
            },
        )
        if prepared:
            plan["navSync"] = deepcopy(prepared.get("refresh", {}).get(code, {}))
            confirmed = prepared.get("latestConfirmed", {}).get(code) or {}
            try:
                known_date = date.fromisoformat(str(confirmed.get("asOf"))[:10])
                plan_date = date.fromisoformat(str(plan.get("asOf"))[:10])
            except (TypeError, ValueError):
                known_date = plan_date = None
            if (confirmed.get("quality") != "ESTIMATED" and plan_date is not None
                    and known_date is not None and plan_date < known_date <= now.date()):
                state, reason = "PARTIAL", "NEWER_CONFIRMED_NAV_AVAILABLE"
                plan["historyQuality"] = state
                plan["priceRefresh"].update(state=state, reason=reason)
                for track in plan.get("tracks", []):
                    track.update(priceState="PARTIAL", entryReady=False, inBuyZone=False)
        if include_series:
            series = {
                "state": state if state != "READY" else view["state"],
                "fundCode": code,
                "asOf": view.get("asOf"),
                "unit": "CNY/份",
                "priceBasis": "UNIT_NAV",
                "sourceUri": view.get("sourceUri"),
                "retrievedAt": view.get("retrievedAt"),
                "points": [{"date": row["date"], "value": row["close"]} for row in points],
            }
            plan["series"] = series if repair_nav else freeze_chart_series(series)
        calculated[code] = plan
    for original, code in plans:
        previous_as_of, previous_method, previous_source = (
            original.get("asOf"),
            original.get("methodVersion"),
            original.get("sourceUri"),
        )
        identity = {
            key: deepcopy(original[key])
            for key in ("subjectKind", "subjectId", "label", "unit", "selectedTrack", "track")
            if key in original
        }
        cycle = deepcopy(original.get("industryCycle"))
        for key in _STALE:
            original.pop(key, None)
        original.pop("actionableZones", None)
        updated = calculated.get(code)
        if updated is None:
            continue
        original.update(
            apply_industry_cycle(updated, cycle)
            if isinstance(cycle, dict)
            else deepcopy(updated)
        )
        original.update(identity, fundCode=code)
        original.setdefault("researchAsOf", previous_as_of)
        original.setdefault("researchMethodVersion", previous_method)
        original.setdefault("researchSourceUri", previous_source)
        selected = original.get("selectedTrack", original.get("track"))
        track = next(
            (row for row in original["tracks"] if row["track"] == selected), None
        )
        if track:
            original.update(
                {
                    key: deepcopy(track[key])
                    for key in (*_STATUS, *_PRICES)
                    if key in track
                }
            )
        original["signals"] = _signals(original)
    if isinstance(result.get("quantitativeScan"), dict):
        scan = result["quantitativeScan"]
        scan.setdefault("researchAsOf", scan.get("asOf"))
        scan.update(
            asOf=now.isoformat(),
            methodVersion="fund-quant-scan-v2",
            minimumHistorySamples=NAV_RESEARCH_MIN_SAMPLES,
        )
    result = apply_entry_guards(result, packet or {}, now)
    if include_series:
        from backend.investment.opportunity_fund_prices import price_position

        for original in _plans(result):
            if _code(original) and isinstance(original.get("tracks"), list):
                original["pricePosition"] = price_position(original, now)
                original["confirmedNav"] = {
                    "value": original["currentPrice"], "asOf": original.get("asOf"),
                    "retrievedAt": original.get("retrievedAt"), "sourceUri": original.get("sourceUri"),
                    "changePct": original.get("currentChangePct"), "quality": original.get("historyQuality"),
                } if original.get("currentPrice") is not None else None
                original["actionableZones"] = fund_reference_contract(original)
    if isinstance(result.get("quantitativeScan"), dict):
        from backend.investment.quantitative_scan import public_quantitative_scan

        scan = result["quantitativeScan"]
        try:
            scan["coverage"] = public_quantitative_scan(scan, now)["coverage"]
        except (TypeError, ValueError):
            scan["coverage"] = {"pricedFundCount": 0, "targetHistoryFundCount": 0}
    return result
