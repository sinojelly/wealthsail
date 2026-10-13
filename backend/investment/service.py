from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from uuid import UUID

from backend.investment import repository
from backend.investment.schemas import ScreenQuery
from backend.errors import NotFoundError
from backend.dependencies import now_shanghai
from backend.market.research_history import published_day


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


def macro_dto(row):
    now = now_shanghai()
    verified = (
        row.get("published_at") is not None and row.get("quality_status") == "FRESH"
    )
    value = _macro_number(row.get("value"))
    state = "NOT_COLLECTED" if value is None else "READY" if verified else "UNVERIFIED"
    if value is None and row.get("value") is not None:
        state = "CONFLICT"
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


async def market_context(conn, months=36):
    if type(months) is not int or not 1 <= months <= 120:
        raise ValueError("macro history months must be between 1 and 120")
    now = now_shanghai()
    month_number = now.year * 12 + now.month - months
    start = date(month_number // 12, month_number % 12 + 1, 1)
    catalogue = [dict(row) for row in await repository.macro(conn)]
    histories = {}
    for record in await repository.macro_history(conn, start, now):
        row = dict(record)
        if not _macro_available(row, now) or row.get("as_of") is None:
            continue
        period = (
            _macro_timestamp(row["as_of"], now)
            .astimezone(now.tzinfo)
            .date()
            .replace(day=1)
        )
        if period < start or period > now.date():
            continue
        key = row["field_code"], period
        if key not in histories or (row.get("revision") or 0) > (
            histories[key].get("revision") or 0
        ):
            histories[key] = row
    items = []
    for catalog_row in catalogue:
        points = []
        for (code, period), row in sorted(histories.items()):
            if (
                code != catalog_row["field_code"]
                or _macro_number(row.get("value")) is None
            ):
                continue
            dto = macro_dto({**catalog_row, **row})
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
        item = macro_dto(catalog_row)
        if points:
            latest = points[-1]
            row = histories[
                (catalog_row["field_code"], date.fromisoformat(latest["asOf"]))
            ]
            item = macro_dto({**catalog_row, **row})
        change, trend = None, "INSUFFICIENT"
        if len(points) >= 2:
            previous, latest = points[-2:]
            old_day, new_day = (
                date.fromisoformat(previous["asOf"]),
                date.fromisoformat(latest["asOf"]),
            )
            old_source = previous["sourceUri"] or previous["source"]
            new_source = latest["sourceUri"] or latest["source"]
            comparable = (
                new_day.year * 12 + new_day.month - old_day.year * 12 - old_day.month
                == 1
                and old_source is not None
                and old_source == new_source
                and previous["unit"] is not None
                and previous["unit"] == latest["unit"]
            )
            change = latest["value"] - previous["value"] if comparable else None
            trend = "OBSERVED" if comparable else "GAP"
        items.append(
            {
                **item,
                "history": points,
                "historyMonths": months,
                "sampleCount": len(points),
                "periodChange": change,
                "trendState": trend,
            }
        )
    return {
        "items": items,
        "months": months,
        "windowStart": start.isoformat(),
        "windowEnd": now.date().isoformat(),
        "assessment": None,
        "state": "READY"
        if items and all(x["state"] == "READY" for x in items)
        else "UNVERIFIED",
    }


async def directions(conn):
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
        }
        for r in await repository.directions(conn)
    ]


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
    return {
        "instrumentId": str(r["instrument_id"]) if r["instrument_id"] else None,
        "fundCode": r["fund_code"],
        "fundName": r["fund_name"],
        "fundType": r["fund_type"],
        "tradeForm": r["trade_form"],
        "market": r["market"],
        "currency": r["currency"],
        "latestValue": r.get("latest_nav"),
        "latestDate": _iso(r.get("latest_nav_date")),
        "navRangePosition": r.get("current_percentile"),
        "updatedAt": _iso(r.get("updated_at")),
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
        loc = (
            await repository.location(conn, row["instrument_id"])
            if row["instrument_id"]
            else None
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


async def analysis(conn, kind, subject_id):
    row = await conn.fetchrow(
        """
        SELECT result,as_of,input_hash,source_uri,points,retrieved_at,series_kind
        FROM market.investment_analysis_snapshots
        WHERE subject_kind=$1 AND subject_key=$2 ORDER BY retrieved_at DESC LIMIT 1
    """,
        kind,
        subject_id,
    )
    if row is None:
        return {
            "state": "NOT_COLLECTED",
            "score": None,
            "indicators": {},
            "reasons": [],
            "points": [],
        }
    result = row["result"]
    if isinstance(result, str):
        result = json.loads(result)
    if result.get("score") is not None:
        now = now_shanghai()
        cutoff = published_day(now.date(), result.get("publicationHour"), now)
        expected = await conn.fetchval(
            """SELECT max(calendar_date)
          FROM reference.trading_calendars WHERE market=$1 AND is_trading_day
            AND calendar_date <= $2""",
            result.get("market"),
            cutoff,
        )
        if expected is None or row["as_of"] < expected:
            result = {
                **result,
                "score": None,
                "state": "STALE" if expected else "UNVERIFIED",
            }
    points = row.get("points", [])
    return {
        **result,
        "asOf": _iso(row["as_of"]),
        "inputHash": row["input_hash"],
        "source": row["source_uri"],
        "points": json.loads(points) if isinstance(points, str) else points,
        "retrievedAt": _iso(row.get("retrieved_at")),
        "seriesKind": row.get("series_kind"),
    }


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
            attempt_count=0,started_at=NULL,finished_at=NULL,error_class=NULL,lease_token=NULL,
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
