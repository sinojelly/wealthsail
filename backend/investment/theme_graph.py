"""Curated thematic relationships and public, same-fund NAV coordinates."""

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.funds.nav_window import (
    NAV_HISTORY_FETCH_CALENDAR_DAYS,
    NAV_RESEARCH_MAX_SAMPLES,
    NAV_RESEARCH_MIN_SAMPLES,
    NAV_SYNC_TARGET_SAMPLES,
    two_year_nav_points,
)
from backend.indicators.actionable_zones import compute_actionable_price_plan

THEMES = {
    "HAINAN_FTP",
    "PUDONG_DEV",
    "GEO_CRUDE_OIL",
    "AI_MEMORY_HBM",
    "COMMERCIAL_SPACE",
}
FUND_KINDS = {"ETF_LINK_FUND", "ACTIVE_FUND", "OFF_EXCHANGE_FUND", "LOF"}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _local(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("NAV history requires an aware clock")
    return value.astimezone(SHANGHAI)


def _coordinate(value):
    if value is None or isinstance(value, bool):
        raise ValueError("invalid confirmed NAV")
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("invalid confirmed NAV")
    return result


def _history_quality(history, points, now):
    """Certify against stored sessions, never a weekday or elapsed-age guess."""
    proof = history.get("calendarProof")
    if (
        history.get("overseasCalendarRequired") is True
        or isinstance(proof, dict)
        and proof.get("overseasCalendarRequired") is True
    ):
        return _disclosed_nav_quality(history, points, now)
    cutoff = now.date() if now.time() >= time(18) else now.date() - timedelta(days=1)
    if not isinstance(proof, dict) or proof.get("market") != "SSE":
        return (
            "UNVERIFIED",
            "缺少已存逐日交易日历，不能确认最新净值是否为当前应有观察。",
        )
    try:
        checked = date.fromisoformat(proof["checkedThrough"])
        calendar = {}
        for row in proof["days"]:
            day = date.fromisoformat(row["date"])
            if type(row.get("isTradingDay")) is not bool or day in calendar:
                raise ValueError("invalid calendar fact")
            calendar[day] = row["isTradingDay"]
        first, last = (
            date.fromisoformat(points[0]["date"]),
            date.fromisoformat(points[-1]["date"]),
        )
        end = max(cutoff, last)
        if (
            checked < end
            or first > cutoff
            or any(
                first + timedelta(days=i) not in calendar
                for i in range((end - first).days + 1)
            )
        ):
            return (
                "UNVERIFIED",
                "已存交易日历覆盖不完整，不能认证当前净值日期或历史连续性。",
            )
        observed = {date.fromisoformat(row["date"]) for row in points}
        if any(calendar[day] is not True for day in observed):
            return "CONFLICT", "净值日期与已存交易日历冲突，不能生成当前区间。"
        expected = {
            day
            for day, trading in calendar.items()
            if first <= day <= last and trading is True
        }
        if observed != expected:
            return "CONFLICT", "已核验交易日内缺少实际净值观察，未跨缺口连接价格历史。"
        current = max(
            (
                day
                for day, trading in calendar.items()
                if first <= day <= cutoff and trading is True
            ),
            default=None,
        )
        if current != last:
            return (
                "STALE",
                "最新确认净值尚未覆盖当前应有交易日；保留真实历史价格，等待发布。",
            )
        return "READY", None
    except (KeyError, TypeError, ValueError):
        return "UNVERIFIED", "已存交易日历事实无效，不能认证当前净值日期。"


def _disclosed_nav_quality(history, points, now):
    """A complete disclosed series supports dated research, not today's estimate."""
    from backend.integrations.nav_history_proof import nav_points_digest

    proof = history.get("providerHistoryProof")
    unknown = (
        "UNVERIFIED",
        "缺少同基金完整披露净值证明；境外日历尚未接入，等待来源补全。",
    )
    if not isinstance(proof, dict) or not isinstance(points, list) or not points:
        return unknown
    try:
        retrieved = _local(datetime.fromisoformat(proof["retrievedAt"]))
        history_retrieved = _local(datetime.fromisoformat(history["retrievedAt"]))
        start, end = (
            date.fromisoformat(proof[key]) for key in ("queryStart", "queryEnd")
        )
        first, last = (date.fromisoformat(points[i]["date"]) for i in (0, -1))
        fields = (
            "totalCount",
            "receivedRowCount",
            "uniquePointCount",
            "pointCount",
            "pageCount",
            "requiredPages",
        )
        if any(type(proof.get(key)) is not int or proof[key] <= 0 for key in fields):
            return unknown
        allowed = {
            ("eastmoney-nav-history-v1", "https://api.fund.eastmoney.com/f10/lsjz"),
            (
                "akshare-unit-nav-history-v1",
                f"https://fund.eastmoney.com/pingzhongdata/{history['fundCode']}.js",
            ),
            ("akshare-unit-nav-history-v1", "https://api.fund.eastmoney.com/f10/lsjz"),
        }
        if (
            (proof.get("methodVersion"), proof.get("sourceUri")) not in allowed
            or proof.get("fundCode") != history["fundCode"]
            or proof.get("complete") is not True
            or not start <= first <= last <= end <= now.date()
            or end < retrieved.date()
            or proof["pointCount"] != len(points)
            or proof["uniquePointCount"] < len(points)
            or proof["totalCount"] < proof["uniquePointCount"]
            or proof["receivedRowCount"] < proof["totalCount"]
            or proof["pageCount"] < proof["requiredPages"]
            or proof.get("firstDate") != first.isoformat()
            or proof.get("lastDate") != last.isoformat()
            or proof.get("pointsDigest") != nav_points_digest(points)
            or not retrieved <= history_retrieved <= now
        ):
            return unknown
        if (now.date() - last).days > 10 or now - retrieved > timedelta(hours=36):
            return (
                "STALE",
                "最新披露净值或来源核查已过期，需重新同步后计算当前研究参考。",
            )
        return (
            "READY",
            "依据最新披露净值计算研究参考；境外日历尚未接入，存在跨市场发布延迟，不是当日估值。",
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return unknown


async def _calendar_proof(conn, history, now):
    points = history.get("points") or []
    if not points or not isinstance(points[0], dict):
        return None
    try:
        first = date.fromisoformat(points[0]["date"])
    except (TypeError, ValueError, KeyError):
        return None
    rows = await conn.fetch(
        """SELECT calendar_date,is_trading_day,
        (EXISTS(SELECT 1 FROM reference.theme_asset_graph g
          WHERE g.market='FUND_CN' AND g.asset_code=$3
          AND g.metadata->>'overseasCalendarRequired'='true')
        OR EXISTS(SELECT 1 FROM reference.discovery_fund_universe u
          WHERE u.market='FUND_CN' AND u.fund_code=$3 AND u.overseas_calendar_required)
        OR EXISTS(SELECT 1 FROM reference.research_fund_catalog c
          WHERE c.market='FUND_CN' AND c.fund_code=$3
            AND (c.fund_type ILIKE '%QDII%' OR c.fund_name ILIKE '%QDII%')))
        AS overseas_calendar_required
        FROM reference.trading_calendars
        WHERE market='SSE' AND calendar_date BETWEEN $1 AND $2 ORDER BY calendar_date""",
        first,
        now.date(),
        history.get("fundCode"),
    )
    return {
        "market": "SSE",
        "checkedThrough": now.date().isoformat(),
        "overseasCalendarRequired": any(
            row.get("overseas_calendar_required") is True for row in rows
        ),
        "days": [
            {"date": str(row["calendar_date"]), "isTradingDay": row["is_trading_day"]}
            for row in rows
        ],
    }


async def read_themes(conn, now, theme_code=None):
    if theme_code is not None and theme_code not in THEMES:
        raise ValueError("unknown public theme")
    rows = await conn.fetch(
        """SELECT g.*,c.instrument_id FROM reference.theme_asset_graph g
        LEFT JOIN reference.research_fund_catalog c ON c.fund_code=g.asset_code AND c.market=g.market
        WHERE ($1::text IS NULL OR g.theme_code=$1) ORDER BY g.theme_code,g.asset_kind,g.asset_code""",
        theme_code,
    )
    result = []
    for row in rows:
        metadata = row.get("metadata") or {}
        metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
        result.append(
            {
                "themeCode": row["theme_code"],
                "themeLabel": row["theme_label"],
                "assetKind": row["asset_kind"],
                "assetCode": row["asset_code"],
                "assetName": row["asset_name"],
                "market": row["market"],
                "relationshipType": row["relationship_type"],
                "sourceUri": row["source_uri"],
                "instrumentId": str(row["instrument_id"])
                if row.get("instrument_id")
                else None,
                "metadata": {
                    "chainRole": str(metadata.get("chainRole") or "关联需核查")[:200],
                    "verificationScope": str(
                        metadata.get("verificationScope") or "RESEARCH_RELATIONSHIP"
                    )[:80],
                    "holdingsVerified": False,
                    "highPositionPct": None,
                    "overseasCalendarRequired": metadata.get("overseasCalendarRequired")
                    is True,
                },
            }
        )
    return result


def nav_plan(history, *, now, subject_kind="nav", subject_id=None, label=None):
    now = _local(now)
    try:
        code = history["fundCode"]
        day = date.fromisoformat(history["asOf"])
        retrieved = _local(datetime.fromisoformat(history["retrievedAt"]))
        available = _local(datetime.fromisoformat(history["availableAt"]))
        uri = urlsplit(history["sourceUri"])
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"\d{6}", code)
            or day > now.date()
            or retrieved > now
            or available > now
            or history.get("priceBasis") != "UNIT_NAV"
        ):
            return None
        if (
            uri.scheme != "https"
            or uri.hostname != "fund.eastmoney.com"
            or uri.port not in {None, 443}
            or uri.username
            or uri.password
            or uri.query
            or uri.fragment
            or uri.path != f"/{code}.html"
        ):
            return None
        if (
            subject_kind not in {"nav", "fund"}
            or subject_kind == "nav"
            and subject_id not in {None, code}
        ):
            return None
        if subject_kind == "fund":
            UUID(str(subject_id))
        raw = history.get("points")
        if not isinstance(raw, list) or not raw or len(raw) > NAV_SYNC_TARGET_SAMPLES:
            return None
        points = []
        previous = None
        for point in raw:
            stamp = date.fromisoformat(point["date"])
            if stamp > day or previous is not None and stamp <= previous:
                return None
            points.append(
                {
                    "date": stamp.isoformat(),
                    "close": format(_coordinate(point["close"]), "f"),
                    **(
                        {"returnPct": point["returnPct"]}
                        if "returnPct" in point
                        else {}
                    ),
                }
            )
            previous = stamp
        if previous != day:
            return None
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None
    # Validate the full stored sequence against its provenance before slicing;
    # provider digests bind the retained samples, not the research subset.
    quality, reason = _history_quality(history, points, now)
    research_points = two_year_nav_points(points)
    plan = compute_actionable_price_plan(
        research_points,
        subject_kind=subject_kind,
        subject_id=subject_id or code,
        label=label or code,
        price_basis="UNIT_NAV",
        unit="CNY",
        as_of=day,
        source_uri=history["sourceUri"],
        market="FUND_CN",
    )
    plan.update(
        fundCode=code,
        retrievedAt=retrieved.isoformat(),
        availableAt=available.isoformat(),
        publishedAt=None,
        timeBasis="PROVIDER_NAV_DATE",
        historyQuality=quality,
        historySampleCount=len(research_points),
        historyStoredSampleCount=len(points),
        historyTargetSamples=NAV_RESEARCH_MAX_SAMPLES,
        historyCoverage="SUFFICIENT"
        if len(research_points) >= NAV_RESEARCH_MIN_SAMPLES
        else "SHORT_HISTORY",
        orderPriceAvailable=False,
    )
    if quality == "READY" and (
        history.get("overseasCalendarRequired") is True
        or (history.get("calendarProof") or {}).get("overseasCalendarRequired") is True
    ):
        plan["priceTimeBasis"] = "LATEST_DISCLOSED_NAV"
        plan["timeBasis"] = "LATEST_DISCLOSED_NAV"
        for track in plan["tracks"]:
            track["reasons"].append(reason)
    if quality != "READY":
        for track in plan["tracks"]:
            track.update(
                state="INSUFFICIENT",
                candidate=False,
                technicalTriggered=False,
                entryReady=False,
                inBuyZone=False,
                priceState=quality,
                entryZoneMin=None,
                entryZoneMax=None,
                stopLossPrice=None,
                takeProfitT1=None,
                takeProfitT2=None,
                estimatedProfitMinPct=None,
                estimatedProfitMaxPct=None,
                currentToSellMaxPct=None,
                **(
                    {"sellZoneMin": None, "sellZoneMax": None, "inSellZone": False}
                    if "inSellZone" in track
                    else {}
                ),
            )
            for field in ("entryStatus", "entryStatusTone", "entryBlockReason"):
                track.pop(field, None)
            track["reasons"].append(reason)
    return plan


async def collect_theme_nav_histories(
    pool,
    now,
    *,
    fetcher=None,
    now_provider=now_shanghai,
    asset_rows=None,
    clock=monotonic,
    minimum_points=0,
):
    from backend.integrations.market_data import fetch_fund_nav_history

    if minimum_points:
        from backend.integrations.fund_nav_fallback import (
            fetch_fund_nav_history_with_fallback,
        )

        fetch_fund_nav_history = fetch_fund_nav_history_with_fallback

    deadline = clock() + 240 if asset_rows is not None else None
    if asset_rows is None:
        async with system_connection(pool) as conn:
            rows = await conn.fetch("""SELECT asset_code,bool_or(COALESCE(metadata->>'overseasCalendarRequired'='true',false)) AS overseas_calendar_required
        FROM reference.theme_asset_graph WHERE asset_kind IN ('ETF_LINK_FUND','ACTIVE_FUND','OFF_EXCHANGE_FUND','LOF') GROUP BY asset_code ORDER BY asset_code LIMIT 12""")
    else:
        selected = {}
        for asset in asset_rows:
            if not isinstance(asset, dict):
                continue
            code = asset.get("asset_code")
            if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
                continue
            if code not in selected:
                if len(selected) >= 46:
                    continue
                selected[code] = {
                    "asset_code": code,
                    "overseas_calendar_required": False,
                }
            selected[code]["overseas_calendar_required"] |= (
                asset.get("overseas_calendar_required") is True
            )
        rows = list(selected.values())
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:

        async def collect_one(row):
            async with semaphore:
                code = row["asset_code"]
                remaining = deadline - clock() if deadline is not None else 45
                if remaining <= 0:
                    return None, {
                        "status": "UNAVAILABLE",
                        "records": 0,
                        "reason": "BUDGET_EXHAUSTED",
                    }
                try:
                    observations = await asyncio.wait_for(
                        (fetcher or fetch_fund_nav_history)(
                            code,
                            now.date()
                            - timedelta(days=NAV_HISTORY_FETCH_CALENDAR_DAYS),
                            now.date(),
                            client,
                            page_size=100,
                            max_pages=32,
                            **(
                                {"minimum_points": minimum_points}
                                if minimum_points and fetcher is None
                                else {}
                            ),
                        ),
                        timeout=min(45, remaining),
                    )
                    by_day = {}
                    for obs in observations:
                        value, stamp = Decimal(str(obs.value)), obs.as_of
                        if (
                            obs.symbol == code
                            and obs.subject_type == "FUND"
                            and obs.field_code == "NAV"
                            and obs.quality_status in {"FRESH", "STALE", "REVISED"}
                            and value.is_finite()
                            and value > 0
                            and stamp.tzinfo
                            and stamp <= now
                        ):
                            old = by_day.get(stamp.date())
                            if old and Decimal(old["close"]) != value:
                                raise ValueError(
                                    "conflicting same-day unit NAV observations"
                                )
                            by_day[stamp.date()] = {
                                "date": stamp.date().isoformat(),
                                "close": str(value),
                                **(
                                    {"returnPct": obs.metadata["returnPct"]}
                                    if (obs.metadata or {}).get("returnPct") is not None
                                    else {}
                                ),
                            }
                    points = [by_day[day] for day in sorted(by_day)][
                        -NAV_SYNC_TARGET_SAMPLES:
                    ]
                    if not points:
                        return None, {"status": "UNAVAILABLE", "records": 0}
                    finished = max(now, now_provider())
                    history = {
                        "fundCode": code,
                        "asOf": points[-1]["date"],
                        "sourceUri": f"https://fund.eastmoney.com/{code}.html",
                        "retrievedAt": finished.isoformat(),
                        "availableAt": finished.isoformat(),
                        "priceBasis": "UNIT_NAV",
                        "points": points,
                        "overseasCalendarRequired": row.get(
                            "overseas_calendar_required"
                        )
                        is True,
                    }
                    proofs = [
                        (obs.metadata or {}).get("providerHistoryProof")
                        for obs in observations
                        if (obs.metadata or {}).get("providerHistoryProof")
                    ]
                    if proofs and all(proof == proofs[0] for proof in proofs):
                        history["providerHistoryProof"] = deepcopy(proofs[0])
                    try:
                        async with system_connection(pool) as conn:
                            history["calendarProof"] = await _calendar_proof(
                                conn, history, _local(finished)
                            )
                    except Exception:  # noqa: BLE001 -- facts survive absent calendar authority
                        history["calendarProof"] = None
                    return history, {
                        "status": "SUCCESS",
                        "records": len(points),
                        "retrievedAt": finished.isoformat(),
                    }
                except Exception as exc:  # noqa: BLE001 -- isolate a public provider failure
                    return None, {"status": "FAILED", "errorClass": type(exc).__name__}

        # gather preserves input order even when independent providers finish out
        # of order, and cancellation propagates to all in-flight subprocesses.
        results = await asyncio.gather(*(collect_one(row) for row in rows))
    return {
        "fundHistories": [history for history, _ in results if history is not None],
        "themeNavSourceResults": {
            row["asset_code"]: status for row, (_, status) in zip(rows, results)
        },
    }


def theme_plans(themes, bundle, now):
    histories = {row.get("fundCode"): row for row in bundle.get("fundHistories", [])}
    plans = []
    for asset in themes:
        if asset["assetKind"] not in FUND_KINDS:
            continue
        history = histories.get(asset["assetCode"])
        if not history:
            continue
        history = {
            **history,
            "overseasCalendarRequired": (asset.get("metadata") or {}).get(
                "overseasCalendarRequired"
            )
            is True
            or history.get("overseasCalendarRequired") is True,
        }
        plan = nav_plan(
            history,
            now=now,
            subject_kind="fund" if asset.get("instrumentId") else "nav",
            subject_id=asset.get("instrumentId") or asset["assetCode"],
            label=asset["assetName"],
        )
        if plan:
            plans.append(
                {
                    **deepcopy(plan),
                    "themeCode": asset["themeCode"],
                    "relationshipType": asset["relationshipType"],
                }
            )
    return plans


async def cached_nav_analysis(conn, code, now, *, subject_kind="nav", subject_id=None):
    from backend.macro.alpha_repository import read_alpha_public

    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        return None
    bundle = await read_alpha_public(conn, now)
    rows = await conn.fetch(
        """SELECT payload FROM market.fund_research_nav_histories
        WHERE fund_code=$1 AND retrieved_at<=$2 AND available_at<=$2
          AND payload->>'asOf'<=$3
        ORDER BY payload->>'asOf' DESC,retrieved_at DESC,id DESC LIMIT 1""",
        code,
        now,
        now.date().isoformat(),
    )
    candidates = list(bundle.get("fundHistories", []))
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                continue
        if isinstance(payload, dict):
            candidates.append(payload)
    valid = [
        history
        for history in candidates
        if isinstance(history, dict)
        and history.get("fundCode") == code
        and nav_plan(history, now=now, subject_kind=subject_kind, subject_id=subject_id)
    ]
    if not valid:
        return None
    history = deepcopy(
        sorted(
            valid,
            key=lambda row: (
                row["asOf"],
                _local(datetime.fromisoformat(row["retrievedAt"])),
            ),
        )[-1]
    )
    try:
        history["calendarProof"] = await _calendar_proof(conn, history, _local(now))
    except Exception:  # noqa: BLE001 -- display history without certifying current prices
        history["calendarProof"] = None
    plan = nav_plan(history, now=now, subject_kind=subject_kind, subject_id=subject_id)
    if not plan:
        return None
    changes = {
        "basis": "UNIT_NAV_CHANGE",
        "unit": "decimal_fraction",
        "sampleCount": len(history["points"]),
        "missingWindows": [],
        "reasons": [],
    }
    for window in (63, 252):
        points = history["points"]
        changes[f"change{window}"] = (
            float(
                Decimal(points[-1]["close"]) / Decimal(points[-window - 1]["close"]) - 1
            )
            if len(points) > window
            else None
        )
        if len(points) <= window:
            changes["missingWindows"].append(window)
            changes["reasons"].append(
                f"不足 {window + 1} 个实际单位净值观察，无法计算 {window} 期净值变动。"
            )
    from backend.indicators.performance import analyze as analyze_performance

    return {
        "state": plan["historyQuality"],
        "dataQuality": plan["historyQuality"],
        "score": None,
        "indicators": plan["indicators"],
        "points": deepcopy(history["points"]),
        "reasons": [
            "确认单位净值研究坐标；不含假定分红再投资收益。",
            *changes["reasons"],
        ],
        "asOf": plan["asOf"],
        "source": plan["sourceUri"],
        "retrievedAt": plan["retrievedAt"],
        "availableAt": history["availableAt"],
        "seriesKind": "UNIT_NAV",
        "performance": analyze_performance(
            history["points"], series_kind="UNIT_NAV", quality=plan["historyQuality"]
        ),
        "navChanges": changes,
        "actionableZones": plan,
    }
