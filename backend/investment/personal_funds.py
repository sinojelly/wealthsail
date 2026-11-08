"""Actor-owned fund inventory and prices read exclusively from persisted facts.

This module never refreshes a provider, queues collection, or changes a study.
Program price zones are display references and confer no investment eligibility.
"""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import re

from backend.funds.nav_history import local, obj, select_history_view
from backend.investment import study_repository
from backend.investment.fund_watch import list_watch_context
from backend.investment.live_fund_plans import (
    _local_histories,
    _safe_history,
    refresh_report_fund_plans,
)
from backend.investment.study_intraday import resolve_fund_scopes
from backend.investment.study_price_watch import (
    _number,
    _source_uri,
    _stamp,
    _trading_time,
)
from backend.macro.research_mentions import mentioned_fund_codes


def merge_fund_inventory(rows):
    """Union exact market identities, retaining all relationship provenance."""
    result = {}
    for row in rows:
        code, market, instrument = (
            row.get("fundCode"),
            row.get("fundMarket"),
            row.get("instrumentId"),
        )
        if not market or (not code and not instrument):
            continue
        key = f"{market}:{code}" if code else f"{market}:instrument:{instrument}"
        target = result.setdefault(
            key,
            {
                **deepcopy(row),
                "key": key,
                "relationships": [],
                "investmentEligible": False,
            },
        )
        target["relationships"] = sorted(
            set(target["relationships"] + row["relationships"])
        )
        if instrument:
            target["instrumentId"] = instrument
        if row.get("comments"):
            target.setdefault("comments", [])
            for comment in row["comments"]:
                if comment not in target["comments"]:
                    target["comments"].append(deepcopy(comment))
    return list(result.values())


async def _inventory(conn, tenant_id, user_id, now, *, include_research_mentions):
    owned = await list_watch_context(conn, tenant_id, user_id, now)
    rows = [{**row, "relationships": ["HELD"]} for row in owned["heldFunds"]]
    tracks, offset = [], 0
    while True:
        page = await study_repository.list_tracks(
            conn, tenant_id, user_id, now, limit=100, offset=offset
        )
        tracks.extend(page["items"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            break
    tracks = await resolve_fund_scopes(conn, tracks, now)
    suppressed = set(owned["suppressedFundKeys"])
    for track in tracks:
        scope = track.get("scope") or {}
        code, market, instrument = (
            scope.get("fundCode"),
            scope.get("fundMarket"),
            scope.get("instrumentId"),
        )
        if not track["enabled"] or track["status"] == "ENDED" or not market:
            continue
        if (
            f"{market}:{code}" in suppressed
            or f"{market}:instrument:{instrument}" in suppressed
        ):
            continue
        rows.append(
            {
                "fundCode": code,
                "fundMarket": market,
                "instrumentId": instrument,
                "label": track["title"],
                "relationships": ["TRACKED"],
            }
        )
    watched = await conn.fetch(
        """SELECT symbol,asset_name,asset_type FROM identity.watchlist
        WHERE user_id=$1 AND added_at<=$2 AND asset_type IN ('FUND','ETF') ORDER BY added_at,symbol""",
        user_id,
        now,
    )
    candidate_codes = {
        row["symbol"] for row in watched if re.fullmatch(r"[0-9]{6}", row["symbol"])
    }
    candidate_codes.update(
        row["fundCode"]
        for row in rows
        if isinstance(row.get("fundCode"), str)
        and re.fullmatch(r"[0-9]{6}", row["fundCode"])
    )
    report = {}
    mentions = []
    if include_research_mentions:
        saved = await conn.fetchrow(
            """SELECT t.payload FROM research.daily_research_runs r
            JOIN research.daily_research_results t USING(run_id)
            WHERE t.status='READY' AND (
              (r.run_mode='SCHEDULED' AND r.debug_request_id IS NULL) OR
              (r.run_mode='MANUAL' AND EXISTS (
                SELECT 1 FROM research.daily_research_refresh_requests q
                WHERE q.id=r.debug_request_id AND q.tenant_id=$2 AND q.user_id=$3
                  AND q.request_mode='MANUAL' AND q.status='READY'
                  AND q.report_run_id=r.run_id AND q.batch_id=r.source_batch_id
                  AND q.requested_at<=$1 AND q.started_at<=r.dispatched_at
                  AND r.dispatched_at<=q.completed_at AND t.recorded_at<=q.completed_at
                  AND q.completed_at<=$1)))
              AND r.dispatched_at<=$1 AND t.recorded_at<=$1
            ORDER BY r.dispatched_at DESC,t.recorded_at DESC,r.run_id DESC LIMIT 1""",
            now,
            tenant_id,
            user_id,
        )
        report = obj(saved["payload"]) if saved else {}
        mentions = mentioned_fund_codes(report)
        # Explicit verified projection also covers structured-only mentions.
        mentions += [
            row for row in report.get("mentionedFunds", []) if isinstance(row, dict)
        ]
        for direction in report.get("valueDirections", []):
            if isinstance(direction, dict):
                for plan in direction.get("plans", []):
                    if (
                        isinstance(plan, dict)
                        and plan.get("market", "FUND_CN") == "FUND_CN"
                    ):
                        mentions.append(
                            {
                                "fundCode": plan.get("fundCode")
                                or (
                                    plan.get("subjectId")
                                    if plan.get("subjectKind") == "nav"
                                    else None
                                ),
                                "directionKeys": [direction.get("key")],
                            }
                        )
        candidate_codes.update(
            row.get("fundCode")
            for row in mentions
            if isinstance(row.get("fundCode"), str)
            and re.fullmatch(r"[0-9]{6}", row["fundCode"])
        )
    catalog = (
        await conn.fetch(
            """SELECT fund_code,fund_name,currency FROM reference.research_fund_catalog
        WHERE fund_code=ANY($1::text[]) AND market='FUND_CN' AND trade_form IN ('FUND','LOF')""",
            sorted(candidate_codes),
        )
        if candidate_codes
        else []
    )
    identities = {}
    for item in catalog:
        identities.setdefault(item["fund_code"], []).append(item)
    safe = {
        code: values[0]
        for code, values in identities.items()
        if len(values) == 1
        and values[0]["currency"] == "CNY"
        and not re.search(r"美元|港币|港元|外币|现汇|现钞", values[0]["fund_name"])
    }
    for item in watched:
        if item["asset_type"] == "FUND":
            identity = safe.get(item["symbol"])
            rows.append(
                {
                    "fundCode": item["symbol"],
                    "fundMarket": "FUND_CN",
                    "label": identity["fund_name"] if identity else item["asset_name"],
                    "relationships": ["WATCHLIST"],
                }
            )
    etfs = [row for row in watched if row["asset_type"] == "ETF"]
    if etfs:
        aliases = await conn.fetch(
            """SELECT DISTINCT a.alias_value AS fund_code,i.id AS instrument_id,i.primary_market AS market
            FROM reference.instrument_aliases a JOIN reference.instruments i ON i.id=a.instrument_id
            WHERE a.alias_type='TICKER' AND a.alias_value=ANY($1::text[])
              AND i.asset_type='EXCHANGE_FUND' AND i.lifecycle_status='ACTIVE'
              AND a.effective_from<=$2 AND (a.effective_to IS NULL OR a.effective_to>=$2)""",
            [row["symbol"] for row in etfs],
            now.date(),
        )
        for item in etfs:
            matches = [row for row in aliases if row["fund_code"] == item["symbol"]]
            exact = matches[0] if len(matches) == 1 else None
            rows.append(
                {
                    "fundCode": item["symbol"],
                    "fundMarket": exact["market"] if exact else "UNKNOWN",
                    "instrumentId": str(exact["instrument_id"]) if exact else None,
                    "label": item["asset_name"],
                    "relationships": ["WATCHLIST"],
                }
            )
    for mention in mentions:
        code = mention.get("fundCode")
        if code not in safe:
            continue
        directions = [
            row
            for row in report.get("valueDirections", [])
            if isinstance(row, dict)
            and row.get("key") in mention.get("directionKeys", [])
        ]
        rows.append(
            {
                "fundCode": code,
                "fundMarket": "FUND_CN",
                "label": safe[code]["fund_name"],
                "relationships": ["RESEARCH_MENTIONED"],
                "comments": [
                    {
                        "title": row.get("title"),
                        "thesis": row.get("thesis"),
                        "risks": deepcopy(row.get("risks") or []),
                    }
                    for row in directions
                ],
            }
        )
    for row in rows:
        row["localNavEligible"] = bool(
            row["fundMarket"] == "FUND_CN"
            and row.get("fundCode") in safe
            and not re.search(r"美元|港币|港元|外币|现汇|现钞", row["label"])
        )
    return merge_fund_inventory(rows)


async def _persisted_estimates(conn, codes, now):
    """Read same-fund estimates with their actual acquisition clock; never fetch."""
    if not codes:
        return {}
    rows = await conn.fetch(
        """SELECT DISTINCT ON(a.alias_value) a.alias_value AS fund_code,o.value,o.unit,
          o.as_of,o.observed_at,o.metadata,s.origin_uri,c.is_trading_day
        FROM market.market_observations o
        JOIN reference.instruments i ON i.id=o.subject_id AND i.primary_market='FUND_CN'
        JOIN reference.instrument_aliases a ON a.instrument_id=i.id AND a.alias_type='TICKER'
          AND a.alias_value=ANY($1::text[]) AND a.effective_from<=($2 AT TIME ZONE 'Asia/Shanghai')::date
          AND (a.effective_to IS NULL OR a.effective_to>=($2 AT TIME ZONE 'Asia/Shanghai')::date)
        JOIN market.source_snapshots s ON s.id=o.snapshot_id AND s.retrieved_at<=$2
        LEFT JOIN reference.trading_calendars c ON c.market='SSE'
          AND c.calendar_date=($2 AT TIME ZONE 'Asia/Shanghai')::date
        WHERE o.subject_type='FUND' AND o.field_code='ESTIMATED_NAV' AND o.quality_status='ESTIMATED'
          AND o.currency='CNY' AND o.as_of<=$2 AND o.observed_at<=$2 AND o.created_at<=$2
        ORDER BY a.alias_value,o.as_of DESC,o.observed_at DESC,o.id DESC""",
        codes,
        now,
    )
    output = {}
    for row in rows:
        code = row["fund_code"]
        stamp, retrieved = _stamp(row["as_of"]), _stamp(row["observed_at"])
        if (
            code not in codes
            or stamp is None
            or retrieved is None
            or stamp.date() != now.date()
            or stamp > retrieved
            or stamp > now
            or retrieved > now
            or not _number(row["value"])
            or row["unit"] not in {"CNY", "CNY/份", "元/份", "CNY/unit"}
        ):
            continue
        source = _source_uri(row["origin_uri"])
        base = _number(obj(row["metadata"]).get("confirmedNav"))
        output[code] = {
            "subjectKind": "nav",
            "subjectId": code,
            "fundCode": code,
            "value": str(row["value"]),
            "asOf": stamp.isoformat(),
            "retrievedAt": retrieved.isoformat(),
            "checkedAt": retrieved.isoformat(),
            "priceBasis": "UNIT_NAV",
            "unit": row["unit"],
            "quality": "ESTIMATED",
            "sourceUri": source,
            "tradingDayConfirmed": row["is_trading_day"] is True,
            "sourceVerification": "PUBLIC_SOURCE" if source else "LOCAL_RECORD_ONLY",
            "explanation": "本地Worker记录，原公开来源未保存/待核。"
            if not source
            else "已保存的同基金估算净值，最终以基金公司披露为准。",
            "usable": bool(
                source
                and row["is_trading_day"] is True
                and _trading_time(stamp)
                and _trading_time(now)
                and now - stamp <= timedelta(minutes=30)
            ),
            "changePct": str(
                ((Decimal(str(row["value"])) / base - 1) * 100).quantize(
                    Decimal("0.01")
                )
            )
            if base
            else None,
        }
    return output


async def personal_funds_reference(
    conn, tenant_id, user_id, now, *, include_research_mentions=True
):
    now = local(now)
    funds = await _inventory(
        conn,
        tenant_id,
        user_id,
        now,
        include_research_mentions=include_research_mentions,
    )
    codes = sorted(
        {
            row["fundCode"]
            for row in funds
            if row["fundMarket"] == "FUND_CN"
            and row.get("localNavEligible") is True
            and isinstance(row.get("fundCode"), str)
            and re.fullmatch(r"[0-9]{6}", row["fundCode"])
        }
    )
    histories = (
        [row async for row in _local_histories(conn, codes, now) if _safe_history(row)]
        if codes
        else []
    )
    skeletons = [
        {
            "subjectKind": "nav",
            "subjectId": code,
            "fundCode": code,
            "market": "FUND_CN",
            "label": next(
                row["label"]
                for row in funds
                if row.get("fundCode") == code and row["fundMarket"] == "FUND_CN"
            ),
            "priceBasis": "UNIT_NAV",
            "unit": "CNY/份",
            "tracks": [],
        }
        for code in codes
    ]
    refreshed = await refresh_report_fund_plans(
        conn, {"actionablePlans": skeletons}, now, histories=histories
    )
    plans = {row["fundCode"]: row for row in refreshed["actionablePlans"]}
    estimates = await _persisted_estimates(conn, codes, now)
    for fund in funds:
        code = fund.get("fundCode")
        eligible = fund.pop("localNavEligible", False)
        plan = deepcopy(plans.get(code)) if eligible else None
        view = (
            select_history_view(
                code,
                fund["label"],
                "all",
                [row for row in histories if row["fundCode"] == code],
                now,
            )
            if plan
            else None
        )
        if view:
            view["refresh"] = {"state": "IDLE", "runId": None, "pollAfterMs": None}
        missing = []
        if not eligible:
            missing.append(
                "LOCAL_NAV_IDENTITY_UNVERIFIED"
                if fund["fundMarket"] == "FUND_CN"
                else "LOCAL_NAV_MARKET_UNSUPPORTED"
            )
        if not view or not view["points"]:
            missing.append("LOCAL_NAV_HISTORY_UNAVAILABLE")
        if not plan or (plan.get("priceRefresh") or {}).get("state") != "READY":
            missing.append("LOCAL_PRICE_PLAN_INCOMPLETE")
        estimate = estimates.get(code)
        if not estimate or estimate.get("unavailableReason"):
            missing.append(
                (estimate or {}).get("unavailableReason", "LOCAL_ESTIMATE_UNAVAILABLE")
            )
            estimate = None
        elif estimate.get("sourceVerification") == "LOCAL_RECORD_ONLY":
            missing.append("LOCAL_ESTIMATE_SOURCE_UNVERIFIED")
        confirmed = None
        if view and view["points"]:
            confirmed = {
                "subjectKind": "nav",
                "subjectId": code,
                "fundCode": code,
                "value": view["points"][-1]["unitNav"],
                "asOf": view["asOf"],
                "retrievedAt": view["retrievedAt"],
                "sourceUri": view["sourceUri"],
                "priceBasis": "UNIT_NAV",
                "unit": view["unit"],
                "quality": view["state"],
                "usable": view["state"] == "READY",
                "changePct": plan.get("currentChangePct"),
            }
        if plan:
            plan.update(
                investmentEligible=False,
                qualificationStatus="OBSERVATION_ONLY",
                entryReady=False,
            )
            for track in plan.get("tracks", []):
                track["entryReady"] = False
        fund.update(
            plan=plan,
            history=view,
            confirmedNav=confirmed,
            currentEstimate=estimate if eligible else None,
            missingReasons=missing,
            investmentEligible=False,
        )
    return {
        "version": "personal-funds-reference-v1",
        "asOf": now.isoformat(),
        "source": "LOCAL_DATABASE_ONLY",
        "readOnly": True,
        "funds": funds,
    }
