"""Public, report-clock projections of server-computed fund research prices."""

from copy import deepcopy
from decimal import Decimal
import re
from uuid import UUID


def public_quantitative_scan(value, now):
    """Copy only complete original coordinates; never recalculate their prices."""
    from backend.macro.alpha_research import _price, _program_plan
    from backend.macro.daily_research import _pick, _public_actionable_plan, _stamp

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid quantitative scan")
    stamp = _stamp(value.get("asOf"))
    rows = value.get("items")
    if (
        value.get("methodVersion") not in {"fund-quant-scan-v1", "fund-quant-scan-v2"}
        or stamp is None
        or stamp > now
        or not isinstance(rows, list)
        or len(rows) > 46
    ):
        raise ValueError("invalid quantitative scan version or time")
    items, seen, identities = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        code, kind, subject = (
            row.get("fundCode"),
            row.get("subjectKind"),
            row.get("subjectId"),
        )
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"[0-9]{6}", code)
            or row.get("market") != "FUND_CN"
            or kind not in {"fund", "nav"}
            or row.get("historyQuality") != "READY"
            or kind == "nav"
            and subject != code
        ):
            continue
        if kind == "fund":
            try:
                UUID(subject)
            except (ValueError, TypeError, AttributeError):
                continue
        if code in seen or (kind, subject) in identities:
            raise ValueError("ambiguous quantitative fund identity")
        seen.add(code)
        identities.add((kind, subject))
        plan = _public_actionable_plan(row, subject, now, subject_kind=kind)
        if plan is None:
            continue
        plan.update(_pick(row, ("fundCode", "market", "retrievedAt", "availableAt")))
        valid_tracks = []
        for track in plan["tracks"]:
            if track.get("priceState") != "READY":
                continue
            try:
                _program_plan(plan, track["track"], now, require_theme=False)
                for key in ("stopLossPrice", "takeProfitT1", "takeProfitT2"):
                    _price(track.get(key))
            except (TypeError, KeyError, ValueError):
                continue
            valid_tracks.append(track)
        signals = []
        for track in valid_tracks:
            matching = [
                signal
                for signal in row.get("signals", [])
                if isinstance(signal, dict) and signal.get("track") == track["track"]
            ]
            if len(matching) != 1:
                raise ValueError("ambiguous quantitative track signal")
            signal = matching[0]
            current, low, high = (
                _price(raw)
                for raw in (
                    plan["currentPrice"],
                    track["entryZoneMin"],
                    track["entryZoneMax"],
                )
            )
            in_zone = low <= current <= high
            # Match the frozen scanner's four-decimal percentage without using
            # the signal as authority for either prices or technical conditions.
            expected = round(
                (float(low) - float(current)) / float(current) * 100
                if current < low
                else (float(current) - float(high)) / float(current) * 100
                if current > high
                else 0,
                4,
            )
            distance = signal.get("distanceToEntryPct")
            if (
                signal.get("triggerState") != track["state"]
                or type(signal.get("inEntryZone")) is not bool
                or signal["inEntryZone"] != in_zone
                or isinstance(distance, bool)
                or not isinstance(distance, (int, float))
                or not Decimal(str(distance)).is_finite()
                or abs(Decimal(str(distance)) - Decimal(str(expected)))
                > Decimal("0.0001")
            ):
                raise ValueError("quantitative signal differs from original prices")
            candidate = track["state"] == "CANDIDATE"
            ready = (
                candidate
                and in_zone
                and not any(
                    item.get("entryReady") is False
                    or item.get("entryBlocked") is True
                    or item.get("inSellZone") is True
                    or item.get("subscriptionRestricted") is True
                    for item in (row, track)
                )
            )
            if value["methodVersion"] == "fund-quant-scan-v2" and (
                type(signal.get("candidate")) is not bool
                or type(signal.get("entryReady")) is not bool
                or signal["candidate"] != candidate
                or signal["entryReady"] != ready
            ):
                raise ValueError(
                    "quantitative entry readiness differs from technical state"
                )
            signals.append(
                {
                    **_pick(
                        signal,
                        ("track", "triggerState", "inEntryZone", "distanceToEntryPct"),
                    ),
                    **(
                        {"candidate": candidate, "entryReady": ready}
                        if value["methodVersion"] == "fund-quant-scan-v2"
                        else {}
                    ),
                    **_pick(
                        track,
                        (
                            "inSellZone",
                            "entryBlocked",
                            "entryBlockReason",
                            "entryStatus",
                            "entryStatusTone",
                        ),
                    ),
                }
            )
        if not signals:
            continue
        # Retain both original tracks, including any explicitly unavailable one,
        # so the reading layer cannot mistake a missing condition for a buy point.
        plan["signals"] = signals
        sector = row.get("discoverySector")
        if isinstance(sector, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", sector):
            plan["discoverySector"] = sector
        items.append(plan)
    return {
        "methodVersion": value["methodVersion"],
        "asOf": value["asOf"],
        "minimumHistorySamples": 220,
        "items": items,
        "coverage": {
            "pricedFundCount": len(items),
            "targetHistoryFundCount": sum(
                isinstance(item.get("historySampleCount"), int)
                and not isinstance(item.get("historySampleCount"), bool)
                and item["historySampleCount"] >= 220
                for item in items
            ),
        },
    }


def report_with_quantitative_scan(report, packet, now):
    """An old READY report may read only its own dispatch's public scan."""
    from backend.macro.daily_research import _stamp, apply_entry_guards

    if not isinstance(report, dict):
        return report
    result = deepcopy(report)
    stamp = _stamp(report.get("asOf"))
    if stamp is None or stamp > now or not isinstance(packet, dict):
        return result
    if "quantitativeScan" not in result:
        try:
            scan = public_quantitative_scan(packet.get("quantitativeScan"), stamp)
        except (ValueError, TypeError):
            scan = None
        if scan is not None:
            result["quantitativeScan"] = scan
    from backend.investment.opportunity_scoring import rank_report
    from backend.macro.model_projection import readable_fund_codes, known_company_names

    codes = {
        row.get("fundCode")
        for row in (result.get("quantitativeScan") or {}).get("items", [])
    }
    return readable_fund_codes(
        rank_report(apply_entry_guards(result, packet, stamp), packet),
        codes,
        known_company_names(packet),
    )
