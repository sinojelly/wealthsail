from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from uuid import UUID

from backend.investment import repository
from backend.investment.schemas import ScreenQuery
from backend.errors import NotFoundError
from backend.dependencies import now_shanghai
from backend.market.research_history import published_day, expected_trading_day
from backend.indicators.value_evidence import evaluate
from copy import deepcopy
from backend.macro.evidence_repository import read_evidence as read_macro_evidence
from backend.macro.research import enrich_item, research_context, direction_research


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _macro_timestamp(value, now):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=now.tzinfo)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=now.tzinfo)
    raise ValueError("invalid macro timestamp")


def _macro_available(row, now):
    try:
        return all(
            row.get(field) is None or _macro_timestamp(row[field], now) <= now
            for field in ("as_of", "observed_at", "retrieved_at", "published_at")
        )
    except (ValueError, TypeError):
        return False


def _macro_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and math.isfinite(float(number)) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def macro_dto(row, *, now=None):
    now = now or now_shanghai()
    verified = (
        row.get("published_at") is not None and row.get("quality_status") == "FRESH"
    )
    value = _macro_number(row.get("value"))
    state = "NOT_COLLECTED" if value is None else "READY" if verified else "UNVERIFIED"
    if value is None and row.get("value") is not None:
        state = "CONFLICT"
    if row.get("quality_status") in {"CONFLICT", "UNAVAILABLE"}:
        value, state = None, row["quality_status"]
    published = row.get("published_at")
    if not _macro_available(row, now):
        value, state = None, "NOT_COLLECTED"
        row = {
            **row,
            "as_of": None,
            "retrieved_at": None,
            "source_name": None,
            "source_uri": None,
            "revision": None,
        }
        published = None
    elif verified and value is not None:
        max_age = row.get("max_age_days")
        if not isinstance(max_age, int) or max_age <= 0:
            state = "UNVERIFIED"
        elif now - _macro_timestamp(published, now) > timedelta(days=max_age):
            state = "STALE"
    return {
        "code": row["field_code"],
        "label": row.get("label", row["field_code"]),
        "dimension": row.get("dimension"),
        "maxAgeDays": row.get("max_age_days"),
        "value": value,
        "asOf": _iso(row.get("as_of")),
        "publishedAt": _iso(published),
        "retrievedAt": _iso(row.get("retrieved_at")),
        "source": row.get("source_name"),
        "sourceUri": row.get("source_uri"),
        "unit": row.get("unit"),
        "revision": row.get("revision"),
        "state": state,
        "assessment": None,
    }


async def market_context(conn, months=36, *, now=None):
    if type(months) is not int or not 1 <= months <= 120:
        raise ValueError("macro history months must be between 1 and 120")
    now = now or now_shanghai()
    month_number = now.year * 12 + now.month - months
    start = date(month_number // 12, month_number % 12 + 1, 1)
    research_month = now.year * 12 + now.month - 60
    research_start = date(research_month // 12, research_month % 12 + 1, 1)
    query_start = min(start, research_start)
    evidence = await read_macro_evidence(conn, now)
    catalogue = [dict(row) for row in await repository.macro(conn)]
    histories = {}
    for record in await repository.macro_history(conn, query_start, now):
        row = dict(record)
        if not _macro_available(row, now) or row.get("as_of") is None:
            continue
        period = (
            _macro_timestamp(row["as_of"], now)
            .astimezone(now.tzinfo)
            .date()
            .replace(day=1)
        )
        if period < query_start or period > now.date():
            continue
        key = row["field_code"], period
        if key not in histories or (row.get("revision") or 0) > (
            histories[key].get("revision") or 0
        ):
            histories[key] = row
    items = []
    for catalog_row in catalogue:
        points = []
        latest_period, latest_row = None, None
        for (code, period), row in sorted(histories.items()):
            if code != catalog_row["field_code"]:
                continue
            # Latest available revision governs the current fact even if it is
            # unusable. Removing its historical point must not restore old data.
            latest_period, latest_row = period, row
            dto = macro_dto({**catalog_row, **row}, now=now)
            if dto["value"] is None:
                continue
            points.append(
                {
                    "asOf": period.isoformat(),
                    "value": dto["value"],
                    "unit": dto["unit"],
                    "source": dto["source"],
                    "sourceUri": dto["sourceUri"],
                    "state": dto["state"],
                    "publishedAt": dto["publishedAt"],
                    "retrievedAt": dto["retrievedAt"],
                    "revision": dto["revision"],
                }
            )
        item = macro_dto(catalog_row, now=now)
        if latest_row is not None:
            catalogue_key = None
            if catalog_row.get("as_of") is not None and _macro_available(
                catalog_row, now
            ):
                catalogue_key = (
                    _macro_timestamp(catalog_row["as_of"], now)
                    .astimezone(now.tzinfo)
                    .date()
                    .replace(day=1),
                    catalog_row.get("revision") or 0,
                )
            if (
                catalogue_key is None
                or (latest_period, latest_row.get("revision") or 0) >= catalogue_key
            ):
                item = macro_dto({**catalog_row, **latest_row}, now=now)
        item = enrich_item(
            item,
            [p for p in points if p["asOf"] >= research_start.isoformat()],
            evidence,
            now,
        )
        for point in points:
            proof = enrich_item(
                {**item, **point, "code": item["code"]}, [], evidence, now
            )
            point.update(
                valueVerification=proof["valueVerification"],
                publicationStatus=proof["publicationStatus"],
                publishedAt=proof["publishedAt"],
                officialEvidence=proof["officialEvidence"],
            )
            if proof["valueVerification"] in {"CONFLICT", "UNAVAILABLE"}:
                point["state"] = proof["valueVerification"]
        trend = (
            "OBSERVED"
            if item["statistics"]["previousValue"] is not None
            else "GAP"
            if len(points) >= 2
            else "INSUFFICIENT"
        )
        display_points = [p for p in points if p["asOf"] >= start.isoformat()]
        items.append(
            {
                **item,
                "history": display_points,
                "historyMonths": months,
                "sampleCount": len(display_points),
                "trendState": trend,
            }
        )
    events = _macro_events(evidence.get("events", []), now)
    return {
        "items": items,
        "months": months,
        "windowStart": start.isoformat(),
        "windowEnd": now.date().isoformat(),
        "assessment": None,
        "research": research_context(items),
        "events": events,
        "eventState": "AVAILABLE" if events else "NOT_COLLECTED",
        "state": "READY"
        if items and all(x["state"] == "READY" for x in items)
        else "UNVERIFIED",
    }


async def directions(conn):
    rows = await repository.directions(conn)
    context = await market_context(conn)
    return [
        {
            "id": r["id"],
            "parentId": r["parent_id"],
            "marketId": r["market_id"],
            "styleId": r["style_id"],
            "marketLabel": r["market_label"],
            "label": r["label"],
            "framework": r["framework"],
            "indexCount": r["index_count"],
            "state": "NOT_EVALUATED",
            "assessment": None,
            "macroResearch": direction_research(dict(r), context),
            "relatedEvents": [
                e for e in context["events"] if r["id"] in e["directionIds"]
            ],
        }
        for r in rows
    ]


def _macro_events(rows, now):
    from urllib.parse import urlsplit

    events = []
    for row in rows:
        try:
            parsed = urlsplit(row["source_uri"])
            valid_source = parsed.scheme == "https" and any(
                parsed.hostname == domain
                or (parsed.hostname or "").endswith("." + domain)
                for domain in ("stats.gov.cn", "pbc.gov.cn", "federalreserve.gov")
            )
            stamp = _macro_timestamp(
                row.get("published_at") or row["retrieved_at"], now
            )
            if (
                not valid_source
                or row.get("source_type") != "OFFICIAL_FACT"
                or not _macro_available(row, now)
                or not now - timedelta(days=30) <= stamp <= now
            ):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        events.append(
            {
                "id": row["event_key"],
                "title": row["title"],
                "category": row["category"],
                "sourceType": row["source_type"],
                "publisher": row.get("publisher"),
                "sourceUri": row["source_uri"],
                "publishedAt": _iso(row.get("published_at")),
                "retrievedAt": _iso(row.get("retrieved_at")),
                "summary": row.get("summary", ""),
                "directionIds": row.get("direction_ids") or [],
            }
        )
    return sorted(
        events,
        key=lambda e: (
            e["publishedAt"] is not None,
            _macro_timestamp(e["publishedAt"] or e["retrievedAt"], now),
        ),
        reverse=True,
    )[:20]


async def indices(conn, direction_id):
    return [
        {
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "market": r["market"],
            "exposure": r["exposure"],
            "fundCount": r["fund_count"],
            "state": "NOT_COLLECTED",
        }
        for r in await repository.indices(conn, direction_id)
    ]


def fund_dto(r):
    performance = r.get("performance") or {}
    if isinstance(performance, str):
        performance = json.loads(performance)
    point = r.get("latest_point") or {}
    if isinstance(point, str):
        try:
            point = json.loads(point)
        except (TypeError, ValueError):
            point = {}
    if not isinstance(point, dict):
        point = {}
    series = r.get("latest_series_kind")
    basis = {
        "PROVIDER_RETURN_INDEX": "UNIT_NAV",
        "UNIT_NAV": "UNIT_NAV",
        "ETF_HFQ": "ADJUSTED_PRICE",
        "ACCUMULATED_NAV": "ACCUMULATED_NAV",
    }.get(series)
    raw_value = point.get("unitNav" if series == "PROVIDER_RETURN_INDEX" else "close")
    latest_value = _macro_number(raw_value) if basis else None
    latest_date = r.get("latest_series_date")
    if latest_value is None or latest_value <= 0:
        latest_value = _macro_number(r.get("latest_nav"))
        latest_date = r.get("latest_nav_date")
        basis = "ACCUMULATED_NAV" if latest_value is not None else None
    if latest_value is None or latest_value <= 0:
        latest_value, latest_date, basis = None, None, None
    return {
        "instrumentId": str(r["instrument_id"]) if r["instrument_id"] else None,
        "fundCode": r["fund_code"],
        "fundName": r["fund_name"],
        "fundType": r["fund_type"],
        "tradeForm": r["trade_form"],
        "market": r["market"],
        "currency": r["currency"],
        "latestValue": latest_value,
        "latestDate": _iso(latest_date),
        "latestValueBasis": basis,
        "navRangePosition": r.get("current_percentile"),
        "updatedAt": _iso(r.get("updated_at")),
        "performanceState": r.get("performance_state")
        or performance.get("state")
        or "NOT_COLLECTED",
        "valueEligible": r.get("value_eligible") is True,
        "valueState": r.get("value_state") or "INCOMPLETE",
        **{
            key: performance.get(key)
            for key in ("return63", "return252", "maxDrawdown", "volatility20")
        },
        "technicalScore": r.get("technical_score"),
        "technicalState": r.get("technical_state"),
        "historyDays": r.get("history_days", 0),
        "dataState": "READY"
        if r.get("technical_score") is not None
        else r.get("technical_state")
        if r.get("technical_state") is not None
        else "UNAVAILABLE"
        if r.get("data_source") == "UNAVAILABLE"
        else "RANGE_ONLY"
        if r.get("latest_nav") is not None
        else "NOT_COLLECTED",
    }


async def screen(conn, query):
    query = query.model_copy(
        update={"analysis_at": query.analysis_at or datetime.now(timezone.utc)}
    )
    rows, total = await repository.screen(conn, query)
    return {
        "items": [fund_dto(dict(row)) for row in rows],
        "total": total,
        "page": query.page,
        "pageSize": query.page_size,
        "analysisAsOf": _iso(query.analysis_at),
    }


async def search(conn, keyword):
    keyword = keyword.strip()
    if not keyword:
        return []
    indices_rows = await repository.search_indices(conn, keyword)
    result = [
        {
            "kind": "index",
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "indexId": r["id"],
            "directionId": r["direction_id"],
            "marketId": r["market_id"],
            "styleId": r["style_id"],
        }
        for r in indices_rows
    ]
    rows, _ = await repository.screen(conn, ScreenQuery(keyword=keyword, pageSize=10))
    for row in rows:
        item = fund_dto(dict(row))
        loc = await repository.location(
            conn,
            row["instrument_id"],
            fund_code=row["fund_code"],
            market=row["market"],
        )
        result.append(
            {
                **item,
                "kind": "fund",
                "id": item["instrumentId"] or f"FUND_CN:{item['fundCode']}",
                "code": item["fundCode"],
                "name": item["fundName"],
                "indexId": loc["index_id"] if loc else None,
                "directionId": loc["direction_id"] if loc else None,
                "marketId": loc["market_id"] if loc else None,
                "styleId": loc["style_id"] if loc else None,
            }
        )
    return result[:20]


def actionable_plan_for_analysis(result, kind, subject_id, *, label=None, now=None):
    """Use the snapshot's proven current window, independently of old scores."""
    from backend.indicators.actionable_zones import compute_actionable_price_plan

    now = now or now_shanghai()
    points = result.get("points") or []
    quality = result.get("dataQuality", "UNVERIFIED")
    usable = quality == "READY"
    window = result.get("trendWindow") or {}
    if window and result.get("trendQuality") in {"READY", "INSUFFICIENT"}:
        try:
            start, end = (
                date.fromisoformat(str(window[key])) for key in ("start", "end")
            )
            tail = [
                point
                for point in points
                if start <= date.fromisoformat(str(point["date"])[:10]) <= end
            ]
            days = [date.fromisoformat(str(point["date"])[:10]) for point in tail]
            proven = (
                type(window.get("sampleCount")) is int
                and window["sampleCount"] == len(tail) > 0
                and days == sorted(set(days))
                and days[0] == start
                and days[-1] == end
                and end == date.fromisoformat(str(result.get("asOf"))[:10])
            )
            if proven and quality not in {"STALE", "UNVERIFIED", "UNAVAILABLE"}:
                points, usable = tail, True
        except (ValueError, KeyError, TypeError):
            usable = False
    try:
        observed = date.fromisoformat(str(result.get("asOf"))[:10])
        retrieved = result.get("retrievedAt")
        retrieved = (
            datetime.fromisoformat(retrieved)
            if isinstance(retrieved, str)
            else retrieved
        )
        usable = bool(
            usable
            and result.get("source")
            and isinstance(retrieved, datetime)
            and retrieved.tzinfo is not None
            and retrieved.utcoffset() is not None
            and retrieved <= now
            and observed <= now.date()
            and points
            and date.fromisoformat(str(points[-1]["date"])[:10]) == observed
            and all(
                date.fromisoformat(str(point["date"])[:10]) <= observed
                for point in points
            )
        )
    except (ValueError, TypeError, KeyError):
        usable = False
    valuation = result.get("valuation") or {}
    pe = (
        ((result.get("valueEvidence") or {}).get("evidence") or {}).get("pe")
        or valuation.get("pe")
        or {}
    )
    valuation_config = (result.get("fundamentals") or {}).get("valuationConfig") or {}
    valuation_source = str(valuation.get("sourceUri") or "").strip()
    parent_valuation_usable = valuation.get("state") in {"READY", "PARTIAL"} or (
        valuation.get("state") == "STALE"
        and (valuation.get("pb") or {}).get("state") == "STALE"
        and result.get("valuationRequestVerified") is True
        and pe.get("historyEnd") == result.get("asOf")
    )
    pe_current = (
        parent_valuation_usable
        and valuation.get("requestedEnd") == result.get("asOf")
        and pe.get("state") == "READY"
        and bool(valuation_source)
        and pe.get("sourceUri") == valuation_source
        and (
            valuation_config.get("verified") is True
            and valuation_config.get("sourceUri") == valuation_source
            or kind == "index"
            and valuation.get("provider") == "CSINDEX"
            and result.get("source") == valuation_source
        )
        and (kind != "fund" or result.get("valuationMappingVerified") is True)
    )
    plan = compute_actionable_price_plan(
        points,
        subject_kind=kind,
        subject_id=subject_id,
        label=label,
        price_basis="INDEX_POINTS"
        if kind == "index"
        else result.get("seriesKind") or "UNKNOWN",
        unit="points"
        if kind == "index"
        else result.get("currency") or result.get("unit"),
        as_of=result.get("asOf"),
        pe_percentile=pe.get("percentile") if pe_current else None,
        source_uri=result.get("source"),
        market=result.get("market"),
    )
    plan.update(
        retrievedAt=_iso(result.get("retrievedAt")),
        availableAt=_iso(result.get("availableAt")),
        publishedAt=_iso(result.get("publishedAt")),
        timeBasis=result.get("timeBasis"),
        inputHash=result.get("inputHash"),
        historyQuality=quality,
    )
    if not usable:
        reason = "来源、当前观察日或抓取时点尚未核验，未生成当前候选和入场坐标。"
        for item in plan["tracks"]:
            item.update(
                state="INSUFFICIENT",
                priceState=quality
                if quality in {"STALE", "CONFLICT", "UNVERIFIED"}
                else "UNVERIFIED",
            )
            for key in (
                "entryZoneMin",
                "entryZoneMax",
                "stopLossPrice",
                "takeProfitT1",
                "takeProfitT2",
            ):
                item[key] = None
            item["reasons"].append(reason)
        plan["orderPriceAvailable"] = False
    return plan


def _analysis_with_plan(result, kind, subject_id):
    return {
        **result,
        "actionableZones": actionable_plan_for_analysis(result, kind, subject_id),
    }


async def _research_analysis(conn, kind, subject_id):
    from backend.market import index_policy

    row = await conn.fetchrow(
        """
        SELECT result,as_of,input_hash,source_uri,points,retrieved_at,available_at,series_kind
        FROM market.investment_analysis_snapshots
        WHERE subject_kind=$1 AND subject_key=$2 AND retrieved_at<=now() AND available_at<=now()
        ORDER BY retrieved_at DESC,id DESC LIMIT 1
    """,
        kind,
        subject_id,
    )
    if row is None:
        return _analysis_with_plan(
            {
                "state": "NOT_COLLECTED",
                "score": None,
                "indicators": {},
                "reasons": [],
                "points": [],
                **(
                    {
                        "indexCollection": await index_policy.read_collection(
                            conn, subject_id, now_shanghai()
                        )
                    }
                    if kind == "index"
                    else {}
                ),
            },
            kind,
            subject_id,
        )
    result = row["result"]
    if isinstance(result, str):
        result = json.loads(result)
    result = deepcopy(result)
    official = (
        kind == "index"
        and (result.get("collectionPolicy") or {}).get("provider") == "CSINDEX"
    )
    has_research_prices = (
        result.get("score") is not None
        or bool(result.get("performance"))
        or result.get("dataQuality") == "READY"
        or result.get("trendQuality") in {"READY", "INSUFFICIENT"}
    )
    if has_research_prices:
        now = now_shanghai()
        cutoff = published_day(now.date(), result.get("publicationHour"), now)
        stored_points = row.get("points") or []
        stored_points = (
            json.loads(stored_points)
            if isinstance(stored_points, str)
            else stored_points
        )
        start = (
            date.fromisoformat(stored_points[0]["date"])
            if stored_points
            else row["as_of"]
        )
        expected = (
            await index_policy.expected_session(conn, now, start)
            if official
            else await expected_trading_day(
                conn,
                result.get("calendarMarket") or result.get("market"),
                cutoff,
                start,
            )
        )
        if expected is None or row["as_of"] != expected:
            quality = "STALE" if expected and row["as_of"] < expected else "UNVERIFIED"
            result = {
                **result,
                "score": None,
                "state": quality,
                "dataQuality": quality,
                **({"trendQuality": quality} if official else {}),
                **(
                    {
                        "performance": {
                            **result["performance"],
                            "eligible": False,
                            "state": quality,
                        }
                    }
                    if result.get("performance")
                    else {}
                ),
            }
    if has_research_prices:
        if kind == "fund":
            governance = await conn.fetchrow(
                """SELECT i.metadata,
              EXISTS(SELECT 1 FROM reference.fund_index_links f
                JOIN reference.research_indices idx ON idx.id=f.index_id
                WHERE f.instrument_id=i.id AND f.index_id=$2
                  AND f.effective_from<=CURRENT_DATE AND (f.effective_to IS NULL OR f.effective_to>=CURRENT_DATE)
                  AND f.verified_at<=now() AND idx.verified_at<=now() AND idx.valuation=$3::jsonb)
              AND (SELECT count(DISTINCT f.index_id) FROM reference.fund_index_links f
                JOIN reference.research_indices idx ON idx.id=f.index_id
                WHERE f.instrument_id=i.id AND f.effective_from<=CURRENT_DATE
                  AND (f.effective_to IS NULL OR f.effective_to>=CURRENT_DATE)
                  AND f.verified_at<=now() AND idx.verified_at<=now())=1 AS mapping_valid
              FROM reference.instruments i WHERE i.id::text=$1""",
                subject_id,
                result.get("fundamentals", {}).get("indexId"),
                json.dumps(result.get("fundamentals", {}).get("valuationConfig") or {}),
            )
            meta = governance.get("metadata", {}) if governance else {}
            meta = json.loads(meta) if isinstance(meta, str) else meta
            config = meta.get("research", {})
            trusted = (
                config.get("sourceVerified") is True
                and config.get("sourceUri") == row["source_uri"]
                and config.get("publishHour") == result.get("publicationHour")
                and (
                    row.get("series_kind") != "PROVIDER_RETURN_INDEX"
                    or config.get("returnSeriesVerified") is True
                )
            )
        else:
            governance = await conn.fetchrow(
                """SELECT verified_at,source_uri,publish_hour,code,market,provider_symbol,collection_policy
              FROM reference.research_indices WHERE id=$1""",
                subject_id,
            )
            trusted = bool(
                governance
                and governance.get("verified_at")
                and governance.get("source_uri") == row["source_uri"]
                and governance.get("publish_hour") == result.get("publicationHour")
            )
            if official:
                trusted = bool(
                    governance
                    and index_policy.trusted(governance, now_shanghai())
                    and index_policy.config_hash(governance)
                    == result.get("configurationHash")
                    and governance.get("source_uri") == row["source_uri"]
                )
        if not trusted and result.get("dataQuality") != "STALE":
            result.update(score=None, state="UNVERIFIED", dataQuality="UNVERIFIED")
            if result.get("performance"):
                result["performance"].update(eligible=False, state="UNVERIFIED")
        if official:
            collection = await index_policy.read_collection(
                conn, subject_id, now_shanghai()
            )
            if (
                collection
                and collection.get("lastResultStatus") == "FAILED"
                and datetime.fromisoformat(collection["lastResultAt"])
                >= row["retrieved_at"]
            ):
                result.update(score=None, state="UNVERIFIED", dataQuality="UNVERIFIED")
            if result.get("dataQuality") in {"STALE", "UNVERIFIED"}:
                result["trendQuality"] = result["dataQuality"]
                for evidence in (
                    result.get("officialPE"),
                    (result.get("valuation") or {}).get("pe"),
                ):
                    if evidence:
                        evidence.update(
                            state=result.get("dataQuality", "UNVERIFIED"),
                            percentile=None,
                        )
            points = row.get("points", [])
            return _analysis_with_plan(
                {
                    **result,
                    "asOf": _iso(row["as_of"]),
                    "inputHash": row["input_hash"],
                    "source": row["source_uri"],
                    "points": json.loads(points) if isinstance(points, str) else points,
                    "retrievedAt": _iso(row.get("retrieved_at")),
                    "availableAt": _iso(row.get("available_at")),
                    "seriesKind": row.get("series_kind"),
                    "indexCollection": collection,
                },
                kind,
                subject_id,
            )
        facts = result.get("fundamentals") or {}
        costs = deepcopy(facts.get("costs") or {"state": "UNAVAILABLE"})
        valuation = deepcopy(facts.get("valuation") or {"state": "UNAVAILABLE"})
        valuation_request_verified = False
        if costs.get("retrievedAt"):
            retrieved = _macro_timestamp(costs["retrievedAt"], now_shanghai())
            if not now_shanghai() - timedelta(days=45) <= retrieved <= now_shanghai():
                costs["state"] = "STALE"
        else:
            costs["state"] = "UNVERIFIED"
        if facts.get("valuationConfig"):
            now = now_shanghai()
            end = await expected_trading_day(
                conn,
                result.get("calendarMarket") or result.get("market"),
                published_day(
                    now.date(), facts["valuationConfig"].get("publishHour"), now
                ),
            )
            valuation_request_verified = (
                end is not None and valuation.get("requestedEnd") == end.isoformat()
            )
            if end is None or valuation.get("requestedEnd") != end.isoformat():
                valuation["state"] = "STALE" if end else "UNVERIFIED"
        if not trusted:
            valuation["state"] = "UNVERIFIED"
            costs["state"] = "UNVERIFIED"
        result.update(
            costs=costs,
            valuation=valuation,
            valuationRequestVerified=valuation_request_verified,
        )
        if kind == "fund":
            result["valuationMappingVerified"] = bool(
                governance and governance.get("mapping_valid")
            )
            result["valueEvidence"] = evaluate(
                valuation,
                costs,
                result.get("performance"),
                mapped=result["valuationMappingVerified"],
            )
    points = row.get("points", [])
    return _analysis_with_plan(
        {
            **result,
            "asOf": _iso(row["as_of"]),
            "inputHash": row["input_hash"],
            "source": row["source_uri"],
            "points": json.loads(points) if isinstance(points, str) else points,
            "retrievedAt": _iso(row.get("retrieved_at")),
            "availableAt": _iso(row.get("available_at")),
            "seriesKind": row.get("series_kind"),
        },
        kind,
        subject_id,
    )


async def analysis(conn, kind, subject_id):
    result = await _research_analysis(conn, kind, subject_id)
    if kind == "fund":
        from backend.investment.theme_graph import cached_nav_analysis

        code = await conn.fetchval(
            "SELECT fund_code FROM reference.research_fund_catalog WHERE instrument_id::text=$1",
            str(subject_id),
        )
        nav = await cached_nav_analysis(
            conn, code, now_shanghai(), subject_kind="fund", subject_id=str(subject_id)
        )
        if nav and nav["dataQuality"] == "READY":
            result["actionableZones"] = nav["actionableZones"]
    return result


async def enqueue_refresh(conn, tenant_id, body):
    if body.kind == "nav":
        from backend.funds.service import enqueue_fetch

        return await enqueue_fetch(conn, tenant_id=tenant_id, fund_code=body.id)
    from backend.jobs.investment_research import load_subject

    await load_subject(conn, body.kind, body.id)
    key = f"investment-refresh:{body.kind}:{body.id}"
    row = await conn.fetchrow(
        """
      INSERT INTO workflow.job_runs(tenant_id,job_key,job_type,payload,status,available_at)
      VALUES($1,$2,'INVESTMENT_RESEARCH_REFRESH',$3::jsonb,'PENDING',now())
      ON CONFLICT(tenant_id,job_key) DO NOTHING RETURNING id
    """,
        tenant_id,
        key,
        json.dumps(body.model_dump()),
    )
    created = row is not None
    if row is None:
        row = await conn.fetchrow(
            """
          UPDATE workflow.job_runs SET status='PENDING',available_at=now(),next_retry_at=NULL,
            attempt_count=0,started_at=now(),finished_at=NULL,error_class=NULL,lease_token=NULL,
            lease_owner=NULL,lease_until=NULL
          WHERE tenant_id=$1 AND job_key=$2 AND (status='COMPLETED' OR (status='FAILED' AND next_retry_at IS NULL))
          RETURNING id
        """,
            tenant_id,
            key,
        )
        created = row is not None
    if row is None:
        row = await conn.fetchrow(
            "SELECT id FROM workflow.job_runs WHERE tenant_id=$1 AND job_key=$2",
            tenant_id,
            key,
        )
    return {"runId": str(row["id"]), "created": created, "status": "QUEUED"}


async def refresh_status(conn, tenant_id, run_id):
    row = await conn.fetchrow(
        """SELECT status,finished_at,error_class,next_retry_at
      FROM workflow.job_runs WHERE tenant_id=$1 AND id=$2 AND job_type IN ('INVESTMENT_RESEARCH_REFRESH','FUND_NAV_RANGE_FETCH')""",
        tenant_id,
        UUID(run_id),
    )
    if not row:
        raise NotFoundError("更新任务不存在")
    status = "QUEUED" if row["status"] == "PENDING" else row["status"]
    if status == "FAILED" and row["next_retry_at"] is not None:
        status = "QUEUED"
    return {
        "status": status,
        "completedAt": _iso(row["finished_at"]),
        "errorCode": row["error_class"],
    }
