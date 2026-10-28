"""Actor-only read model over frozen plans and already persisted market facts."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import json
from uuid import UUID
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
_UNITS = {"CNY", "元", "元/份", "CNY/unit", "CNY/份", "CNY/share"}


def _source_uri(value):
    try:
        uri = urlsplit(value)
        return (
            value
            if uri.scheme == "https"
            and uri.hostname
            and not uri.username
            and not uri.password
            and uri.port in {None, 443}
            else None
        )
    except (ValueError, TypeError):
        return None


def _current_session(calendar, now):
    cutoff = now.date() if now.time() >= time(18) else now.date() - timedelta(days=1)
    days = {row["calendar_date"]: row["is_trading_day"] for row in calendar}
    needed = {cutoff - timedelta(days=i) for i in range(11)}
    if not needed.issubset(days) or any(type(days[day]) is not bool for day in needed):
        return None
    return max((day for day in needed if days[day]), default=None)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def _stamp(value):
    try:
        value = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        return value.astimezone(SHANGHAI) if value.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _day(value):
    if isinstance(value, datetime):
        stamp = _stamp(value)
        return stamp.date() if stamp else None
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _track(frozen):
    if not isinstance(frozen, dict):
        return None
    tracks = [
        row
        for row in frozen.get("tracks", [])
        if isinstance(row, dict)
        and row.get("track") == frozen.get("selectedTrack", frozen.get("track"))
    ]
    if len(tracks) != 1 or tracks[0].get("state") not in {"CANDIDATE", "WATCH"}:
        return None
    row = tracks[0]
    values = [
        _number(row.get(key))
        for key in (
            "stopLossPrice",
            "entryZoneMin",
            "entryZoneMax",
            "takeProfitT1",
            "takeProfitT2",
        )
    ]
    if any(value is None for value in values):
        return None
    stop, low, high, first, second = values
    return row if stop < low <= high < first < second else None


def _trading_time(stamp):
    return time(9, 30) <= stamp.time() <= time(11, 30) or time(
        13
    ) <= stamp.time() < time(15)


def _usable(quote, frozen, now, *, estimate=False, intraday=False):
    if not isinstance(quote, dict) or quote.get("usable") is not True:
        return False
    retrieved, day = _stamp(quote.get("retrievedAt")), _day(quote.get("asOf"))
    if (
        frozen.get("priceBasis") != "UNIT_NAV"
        or frozen.get("unit") not in _UNITS
        or quote.get("priceBasis") != "UNIT_NAV"
        or quote.get("unit") not in _UNITS
        or quote.get("subjectKind") != frozen.get("subjectKind")
        or str(quote.get("subjectId")) != str(frozen.get("subjectId"))
        or quote.get("fundCode") != frozen.get("fundCode")
        or _number(quote.get("value")) is None
        or day is None
        or day > now.date()
        or _day(frozen.get("asOf")) is None
        or day < _day(frozen.get("asOf"))
        or retrieved is None
        or retrieved > now
        or day > retrieved.date()
        or not _source_uri(quote.get("sourceUri"))
    ):
        return False
    if not estimate and not intraday:
        return quote.get("quality") == "READY" and (now.date() - day).days <= 10
    stamp, checked = _stamp(quote.get("asOf")), _stamp(quote.get("checkedAt"))
    if intraday:
        return (
            quote.get("quality") == "ESTIMATED"
            and quote.get("tradingDayConfirmed") is True
            and stamp is not None
            and checked is not None
            and stamp <= checked <= now
            and stamp.date() == now.date()
            and _trading_time(stamp)
            and _trading_time(now)
            and now - stamp <= timedelta(minutes=30)
        )
    return (
        quote.get("quality") == "ESTIMATED"
        and stamp is not None
        and checked is not None
        and stamp <= checked <= now
        and stamp.date() == now.date()
        and time(14) <= stamp.time() < time(15)
        and now.time() < time(15)
        and now - stamp <= timedelta(minutes=30)
    )


_ENTRY_SAFETY_FIELDS = (
    "entryBlocked",
    "entryBlockReason",
    "entryStatus",
    "entryStatusTone",
    "subscriptionStatus",
    "subscriptionRestricted",
    "subscriptionLabel",
    "preferredEntryEligible",
    "isDelayedNav",
)


def entry_permitted(frozen, track, entry_safety=None):
    """Historical geometric flags do not block a later actual price-zone hit."""
    current = entry_safety if isinstance(entry_safety, dict) else frozen
    selected = next(
        (
            row
            for row in current.get("tracks", [])
            if isinstance(row, dict) and row.get("track") == track.get("track")
        ),
        track,
    )
    return not any(
        row.get("entryBlocked") is True
        or row.get("subscriptionRestricted") is True
        or row.get("preferredEntryEligible") is False
        for row in (current, selected)
    )


def _entry_safety_summary(frozen, track, entry_safety=None):
    current = entry_safety if isinstance(entry_safety, dict) else frozen
    selected = next(
        (
            row
            for row in current.get("tracks", [])
            if isinstance(row, dict) and row.get("track") == track.get("track")
        ),
        track,
    )
    result = {
        key: deepcopy(current[key]) for key in _ENTRY_SAFETY_FIELDS if key in current
    }
    result.update(
        {
            key: deepcopy(selected[key])
            for key in _ENTRY_SAFETY_FIELDS
            if key in selected
        }
    )
    for field in ("entryBlockReason", "entryStatus", "entryStatusTone"):
        protected = next(
            (
                row[field]
                for row in (current, selected)
                if row.get("entryBlocked") is True and field in row
            ),
            None,
        )
        if protected is not None:
            result[field] = deepcopy(protected)
    for key in ("entryBlocked", "subscriptionRestricted"):
        if any(row.get(key) is True for row in (current, selected)):
            result[key] = True
    if any(row.get("preferredEntryEligible") is False for row in (current, selected)):
        result["preferredEntryEligible"] = False
    return result


def _observed_percentile(value):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result <= 100 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


async def current_entry_safety(conn, plans, now):
    """Reapply current public safety facts without carrying historical vetoes forward."""
    from backend.jobs.opportunity_market import (
        read_fund_subscription_status,
        normalize_subscription_status,
    )
    from backend.macro.daily_research import apply_entry_guards

    if not plans:
        return []
    now = _stamp(now)
    codes = sorted(
        {
            plan.get("fundCode")
            for plan in plans
            if isinstance(plan.get("fundCode"), str)
        }
    )
    rows = await conn.fetch(
        """SELECT r.public_packet,x.payload FROM research.daily_research_runs r
        JOIN research.daily_research_results x USING(run_id)
        WHERE r.slot_date=$1 AND x.status='READY' AND r.dispatched_at<=$2 AND x.recorded_at<=$2
        ORDER BY x.recorded_at DESC,r.run_id DESC LIMIT 1""",
        now.date(),
        now,
    )
    packet = deepcopy(_object(rows[0].get("public_packet"))) if rows else {}
    payload = _object(rows[0].get("payload")) if rows else {}
    subscriptions = await read_fund_subscription_status(conn, now, codes)
    packet["fundSubscriptionStatus"] = {
        **_object(packet.get("fundSubscriptionStatus")),
        **subscriptions,
    }
    packet_stamp = _stamp(packet.get("asOf"))
    current_public = bool(
        packet_stamp and packet_stamp.date() == now.date() and packet_stamp <= now
    )
    transient = (*_ENTRY_SAFETY_FIELDS, "entryReady", "inBuyZone")

    def clear_historical(value):
        result = deepcopy(value)
        for row in (result, *result.get("tracks", []), *result.get("signals", [])):
            for field in transient:
                row.pop(field, None)
        return result

    def metadata(value):
        return {
            field: deepcopy(value[field])
            for field in _ENTRY_SAFETY_FIELDS
            if field in value
        }

    # Dates/identity are needed for current delayed-NAV shocks. Frozen indicators
    # are historical technical facts, not evidence that a high-NAV veto still holds.
    guarded = [clear_historical(plan) for plan in plans]
    probes = deepcopy(guarded)
    for probe in probes:
        probe.pop("indicators", None)
    checked_frozen = apply_entry_guards({"actionableZones": probes}, packet, now)[
        "actionableZones"
    ]
    for result, checked in zip(guarded, checked_frozen, strict=True):
        result.update(metadata(checked))
        for field in ("tracks", "signals"):
            for target, source in zip(
                result.get(field, []), checked.get(field, []), strict=True
            ):
                target.update(metadata(source))

    public = []
    for value in (packet, payload):
        for field in (
            "discoveryActionableZones",
            "themeActionableZones",
            "actionableZones",
            "actionablePlans",
        ):
            public.extend(row for row in value.get(field, []) if isinstance(row, dict))
        public.extend(
            row
            for row in (value.get("quantitativeScan") or {}).get("items", [])
            if isinstance(row, dict)
        )
        public.extend(
            row
            for direction in value.get("valueDirections", [])
            for row in direction.get("plans", [])
            if isinstance(row, dict)
        )
    checked_public = apply_entry_guards(
        {"actionableZones": [clear_historical(row) for row in public]}, packet, now
    )["actionableZones"]
    for original, result in zip(plans, guarded, strict=True):
        relevant = [
            fact
            for fact in checked_public
            if fact.get("fundCode") == result.get("fundCode")
            and fact.get("market") == "FUND_CN"
            and fact.get("priceBasis") == "UNIT_NAV"
            and fact.get("unit") in _UNITS
        ]
        for fact in relevant:
            if fact.get("entryBlocked") is True:
                result.update(metadata(fact))
        old_rows = (original, *original.get("tracks", []), *original.get("signals", []))
        old_subscription = any(
            row.get("subscriptionRestricted") is True
            or row.get("entryBlockReason") == "SUBSCRIPTION_RESTRICTED"
            or (normalize_subscription_status(row.get("subscriptionStatus")) or {}).get(
                "restricted"
            )
            is True
            for row in old_rows
        )
        reasons = {
            row.get("entryBlockReason")
            for row in old_rows
            if row.get("entryBlocked") is True
            or row.get("preferredEntryEligible") is False
        }
        known_subscription = result.get("fundCode") in subscriptions or (
            current_public
            and normalize_subscription_status(
                packet["fundSubscriptionStatus"].get(result.get("fundCode"))
            )
            is not None
        )
        old_high = "HIGH_NAV_PERCENTILE" in reasons
        known_high = current_public and any(
            _observed_percentile((fact.get("indicators") or {}).get("navPercentile1y"))
            is not None
            and _number((fact.get("indicators") or {}).get("ma20")) is not None
            for fact in relevant
        )
        other_reasons = reasons - {"SUBSCRIPTION_RESTRICTED", "HIGH_NAV_PERCENTILE"}
        # A reasonless subscription veto belongs to the known subscription fact.
        if old_subscription:
            other_reasons.discard(None)
        unknown = (
            (old_subscription and not known_subscription)
            or (old_high and not known_high)
            or (bool(other_reasons) and not current_public)
        )
        selected = next(
            (
                track
                for track in result.get("tracks", [])
                if track.get("track") == result.get("selectedTrack")
            ),
            {},
        )
        if unknown and entry_permitted(result, selected):
            result.update(
                entryBlocked=True,
                entryBlockReason="CURRENT_SAFETY_UNKNOWN",
                entryStatus="当前入场保护事实待核验，暂缓入场",
                entryStatusTone="amber",
                preferredEntryEligible=False,
            )
    return guarded


def price_watch(
    frozen,
    *,
    confirmed=None,
    estimate=None,
    current_estimate=None,
    holding=None,
    now,
    entry_safety=None,
):
    """A reference-line hit never asserts a purchase, execution or realized profit."""
    now = _stamp(now)
    if now is None:
        raise ValueError("price watch requires an aware clock")
    result = {
        "frozenPlan": deepcopy(frozen),
        "confirmedNav": deepcopy(confirmed),
        "estimate1430": deepcopy(estimate),
        "currentEstimate": deepcopy(current_estimate),
        "holding": deepcopy(holding or {"state": "UNKNOWN"}),
        "state": "UNKNOWN",
        "basis": None,
        "asOf": None,
        "reason": "暂无可比较的同基金当前净值，保留冻结计划。",
    }
    track = _track(frozen)
    if track is None:
        result["reason"] = "未冻结完整的单一价格轨道，暂不判定到价。"
        return result
    safety = _entry_safety_summary(frozen, track, entry_safety)
    # Retain the current selected-track context even when all historical vetoes
    # were cleared, so a later page estimate cannot resurrect an old veto.
    result["entrySafety"] = {
        **safety,
        "tracks": [{"track": track["track"], **deepcopy(safety)}],
    }
    confirmed_ok = _usable(confirmed, frozen, now)
    estimate_ok = _usable(estimate, frozen, now, estimate=True)
    current_ok = _usable(current_estimate, frozen, now, intraday=True)
    if result["confirmedNav"] is not None:
        result["confirmedNav"]["usable"] = confirmed_ok
    if result["estimate1430"] is not None:
        result["estimate1430"]["usable"] = estimate_ok
    if result["currentEstimate"] is not None:
        result["currentEstimate"]["usable"] = current_ok
    candidate = (
        current_estimate
        if current_ok
        else estimate
        if estimate_ok
        else confirmed
        if confirmed_ok
        else None
    )
    if candidate is None:
        return result
    value = _number(candidate["value"])
    in_zone = _number(track["entryZoneMin"]) <= value <= _number(track["entryZoneMax"])
    technical_candidate = track.get("state") == "CANDIDATE"
    allowed = entry_permitted(frozen, track, entry_safety)
    result.update(
        basis="ESTIMATE_INTRADAY"
        if current_ok
        else "ESTIMATE_1430"
        if estimate_ok
        else "CONFIRMED_NAV",
        asOf=candidate["asOf"],
        inEntryZone=in_zone,
        technicalCandidate=technical_candidate,
    )
    result["state"] = (
        "STOP_LOSS"
        if value <= _number(track["stopLossPrice"])
        else "TAKE_PROFIT"
        if value >= _number(track["takeProfitT1"])
        else "ENTRY_ZONE"
        if in_zone and technical_candidate and allowed
        else "HOLDING"
        if (holding or {}).get("state") == "CONFIRMED"
        else "OBSERVING"
    )
    result["reason"] = (
        "按当前基金估算净值核对冻结参考线，申赎成交净值尚未确认。"
        if current_ok
        else "按14:30估算值核对冻结参考线，申赎成交净值尚未确认。"
        if estimate_ok
        else "按最新已确认披露净值核对冻结参考线。"
    )
    if in_zone and not technical_candidate:
        result["reason"] += "价格位于参考区间内，冻结轨道的技术条件待触发。"
    if not allowed:
        safety = _entry_safety_summary(frozen, track, entry_safety)
        label = (
            safety.get("subscriptionLabel")
            or safety.get("entryStatus")
            or "当前入场保护生效"
        )
        result["reason"] += f"暂缓入场：{label}；冻结价格坐标保留，卖出风控继续核对。"
    return result


def _confirmed(history, frozen, now, calendar):
    from backend.investment.theme_graph import nav_plan

    history = deepcopy(history)
    if history.get("fundCode") != frozen.get("fundCode"):
        return None
    history["overseasCalendarRequired"] = (
        history.get("overseasCalendarRequired") is True
        or frozen.get("priceTimeBasis") == "LATEST_DISCLOSED_NAV"
    )
    history["calendarProof"] = {
        "market": "SSE",
        "checkedThrough": now.date().isoformat(),
        "overseasCalendarRequired": history["overseasCalendarRequired"],
        "days": [
            {"date": str(row["calendar_date"]), "isTradingDay": row["is_trading_day"]}
            for row in calendar
        ],
    }
    current = nav_plan(
        history,
        now=now,
        subject_kind=frozen.get("subjectKind"),
        subject_id=frozen.get("subjectId"),
        label=frozen.get("label"),
    )
    if current is None or _number(current.get("currentPrice")) is None:
        return None
    return {
        "value": current["currentPrice"],
        "asOf": current["asOf"],
        "retrievedAt": current["retrievedAt"],
        "sourceUri": current.get("sourceUri"),
        "subjectKind": frozen["subjectKind"],
        "subjectId": frozen["subjectId"],
        "fundCode": frozen["fundCode"],
        "priceBasis": "UNIT_NAV",
        "unit": current["unit"],
        "quality": current.get("historyQuality"),
        "usable": current.get("historyQuality") == "READY",
    }


def _estimate(rows, tenant, actor, track_id, version_id, frozen, now):
    selected = []
    for row in rows:
        if (
            str(row.get("tenant_id")) != str(tenant)
            or str(row.get("user_id")) != str(actor)
            or (finished := _stamp(row.get("finished_at"))) is None
            or finished > now
        ):
            continue
        for check in _object(row.get("result")).get("checks", []):
            if (
                not isinstance(check, dict)
                or check.get("trackId") != str(track_id)
                or check.get("versionId") != str(version_id)
                or check.get("state")
                not in {"STOP_LOSS", "TAKE_PROFIT_T1", "IN_ENTRY_ZONE", "WATCH"}
                or _day(row.get("run_date")) != _day(check.get("asOf"))
            ):
                continue
            track = _track(frozen)
            if (
                track is None
                or check.get("selectedTrack") != track["track"]
                or check.get("priceBasis") != frozen.get("priceBasis")
                or check.get("unit") not in _UNITS
                or _day(check.get("planAsOf")) != _day(frozen.get("asOf"))
                or any(
                    _number(check.get(key)) != _number(track.get(key))
                    for key in (
                        "entryZoneMin",
                        "entryZoneMax",
                        "stopLossPrice",
                        "takeProfitT1",
                    )
                )
            ):
                continue
            selected.append(
                {
                    "value": check.get("price"),
                    "asOf": check.get("asOf"),
                    "checkedAt": finished.isoformat(),
                    "retrievedAt": finished.isoformat(),
                    "sourceUri": _source_uri(check.get("marketSourceUri")),
                    "unit": check.get("unit"),
                    "priceBasis": check.get("priceBasis"),
                    "quality": "ESTIMATED",
                    "usable": bool(_source_uri(check.get("marketSourceUri"))),
                    "state": check.get("state"),
                    "runDate": str(row.get("run_date")),
                    "subjectKind": frozen["subjectKind"],
                    "subjectId": frozen["subjectId"],
                    "fundCode": frozen.get("fundCode"),
                }
            )
    return max(selected, key=lambda item: _stamp(item["checkedAt"]), default=None)


async def enrich_tracks(conn, tenant, actor, items, now, *, versions=None):
    """Fixed-count bulk SELECTs under actor RLS, without any provider calls."""
    from backend.investment.discovery import _cached_histories

    now = _stamp(now)
    if now is None:
        raise ValueError("price watch requires an aware clock")
    ids = []
    for item in items:
        try:
            ids.append(UUID(str(item["id"])))
        except (KeyError, TypeError, ValueError):
            pass
    if not ids:
        return [{**item, "expiresOn": None, "priceWatch": None} for item in items]
    if versions is None:
        rows = await conn.fetch(
            """SELECT DISTINCT ON(v.track_id) v.* FROM research.study_versions v
            JOIN research.study_tracks t ON t.id=v.track_id AND t.tenant_id=v.tenant_id AND t.user_id=v.user_id
            WHERE v.tenant_id=$1 AND v.user_id=$2 AND v.track_id=ANY($3::uuid[])
              AND v.created_at<=$4
            ORDER BY v.track_id,v.version DESC,v.id DESC""",
            tenant,
            actor,
            ids,
            now,
        )
        versions = {str(row["track_id"]): dict(row) for row in rows}
    frozen, expiries = {}, {}
    for item in items:
        version = versions.get(str(item.get("id"))) or {}
        if (
            not version
            or version.get("generation", 1) > item.get("generation", 1)
            or _stamp(version.get("created_at", version.get("createdAt"))) is None
            or _stamp(version.get("created_at", version.get("createdAt"))) > now
        ):
            continue
        snapshot = _object(version.get("snapshot"))
        expiry = _day(snapshot.get("expiresOn"))
        expiries[str(item["id"])] = expiry.isoformat() if expiry else None
        plan = snapshot.get("actionablePlan")
        if (
            isinstance(plan, dict)
            and plan.get("subjectKind") in {"fund", "nav"}
            and plan.get("fundCode")
        ):
            frozen[str(item["id"])] = plan
    items = [{**item, "expiresOn": expiries.get(str(item.get("id")))} for item in items]
    if not frozen:
        return [{**item, "priceWatch": None} for item in items]
    histories = await _cached_histories(
        conn, sorted({plan["fundCode"] for plan in frozen.values()}), now
    )
    starts = [
        _day(point.get("date"))
        for h in histories.values()
        for point in h.get("points", [])
        if isinstance(point, dict)
    ]
    starts = [day for day in starts if day is not None and day <= now.date()]
    calendar = await conn.fetch(
        "SELECT calendar_date,is_trading_day FROM reference.trading_calendars WHERE market='SSE' AND calendar_date BETWEEN $1 AND $2 ORDER BY calendar_date",
        min(
            max(min(starts), now.date() - timedelta(days=1000)),
            now.date() - timedelta(days=12),
        )
        if starts
        else now.date() - timedelta(days=12),
        now.date(),
    )
    checks = await conn.fetch(
        """SELECT tenant_id,user_id,run_date,finished_at,result FROM research.alpha_opportunity_runs
        WHERE tenant_id=$1 AND user_id=$2 AND slot='FUND_1430'
          AND status IN ('SENT','NO_ALERT','COMPLETED') AND finished_at<=$3 AND claimed_at<=$3
          AND run_date BETWEEN ($3 AT TIME ZONE 'Asia/Shanghai')::date-7 AND ($3 AT TIME ZONE 'Asia/Shanghai')::date
        ORDER BY run_date DESC,finished_at DESC""",
        tenant,
        actor,
        now,
    )
    instrument_ids = []
    for item in items:
        try:
            instrument_ids.append(
                UUID(str((item.get("scope") or {}).get("instrumentId")))
            )
        except (TypeError, ValueError):
            pass
    positions = []
    if instrument_ids:
        positions = await conn.fetch(
            """WITH latest AS (
              SELECT DISTINCT ON(p.account_id,p.instrument_id) p.instrument_id,p.quantity,p.as_of,p.account_id,p.data_quality_status
              FROM ledger.position_snapshots p JOIN identity.accounts a ON a.id=p.account_id AND a.tenant_id=p.tenant_id AND a.status='ACTIVE'
              JOIN identity.account_members m
                ON m.tenant_id=p.tenant_id AND m.account_id=p.account_id
              WHERE p.tenant_id=$1 AND m.user_id=$2 AND m.relationship IN ('BENEFICIAL_OWNER','JOINT_OWNER')
                AND m.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
                AND (m.effective_to IS NULL OR m.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date)
                AND p.created_at<=$3 AND p.as_of<=($3 AT TIME ZONE 'Asia/Shanghai')::date
                AND p.instrument_id=ANY($4::uuid[])
              ORDER BY p.account_id,p.instrument_id,p.as_of DESC,p.created_at DESC)
            SELECT p.* FROM latest p JOIN LATERAL (
              SELECT data_quality_status,as_of,valuation_coverage_pct,unpriced_position_count FROM ledger.account_nav_snapshots n
              WHERE n.tenant_id=$1 AND n.account_id=p.account_id AND n.created_at<=$3
                AND n.as_of<=($3 AT TIME ZONE 'Asia/Shanghai')::date ORDER BY n.as_of DESC,n.created_at DESC LIMIT 1
            ) n ON n.as_of=p.as_of AND n.data_quality_status='FRESH' AND p.data_quality_status='FRESH'
              AND n.valuation_coverage_pct=100 AND n.unpriced_position_count=0""",
            tenant,
            actor,
            now,
            instrument_ids,
        )
    safety = dict(
        zip(
            frozen,
            await current_entry_safety(conn, list(frozen.values()), now),
            strict=True,
        )
    )
    current_day = _current_session(calendar, now)
    result = []
    for item in items:
        key = str(item.get("id"))
        if key not in frozen:
            result.append({**item, "priceWatch": None})
            continue
        plan, version = frozen[key], versions[key]
        history = histories.get(plan["fundCode"])
        confirmed = _confirmed(history, plan, now, calendar) if history else None
        estimate = _estimate(checks, tenant, actor, key, version["id"], plan, now)
        identity = str((item.get("scope") or {}).get("instrumentId"))
        actual = [
            row
            for row in positions
            if str(row["instrument_id"]) == identity
            and current_day is not None
            and current_day <= row["as_of"] <= now.date()
            and _number(row["quantity"]) is not None
        ]
        holding = (
            {"state": "CONFIRMED", "asOf": str(max(row["as_of"] for row in actual))}
            if actual
            else {"state": "UNKNOWN"}
        )
        result.append(
            {
                **item,
                "priceWatch": price_watch(
                    plan,
                    confirmed=confirmed,
                    estimate=estimate,
                    holding=holding,
                    now=now,
                    entry_safety=safety[key],
                ),
            }
        )
    return result
