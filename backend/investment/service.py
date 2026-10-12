from datetime import datetime, timedelta

from backend.investment import repository
from backend.investment.schemas import ScreenQuery


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def macro_dto(row):
    verified = row.get("published_at") is not None and row.get("quality_status") == "FRESH"
    value = row.get("value")
    state = "READY" if verified else "UNVERIFIED" if value is not None else "NOT_COLLECTED"
    published = row.get("published_at")
    if isinstance(published, datetime) and datetime.now(published.tzinfo)-published > timedelta(days=45):
        state = "STALE"
    return {"code": row["field_code"], "label": row.get("label", row["field_code"]),
            "dimension": row.get("dimension"), "value": value, "asOf": _iso(row.get("as_of")),
            "publishedAt": _iso(published), "retrievedAt": _iso(row.get("retrieved_at")),
            "source": row.get("source_name"), "revision": row.get("revision"),
            "state": state, "assessment": None}


async def market_context(conn):
    items = [macro_dto(dict(row)) for row in await repository.macro(conn)]
    return {"items": items, "assessment": None, "state": "READY" if items and all(x["state"] == "READY" for x in items) else "UNVERIFIED"}


async def directions(conn):
    return [{"id": r["id"], "parentId": r["parent_id"], "marketId": r["market_id"],
             "styleId": r["style_id"], "marketLabel": r["market_label"], "label": r["label"],
             "framework": r["framework"], "indexCount": r["index_count"],
             "state": "NOT_EVALUATED", "assessment": None} for r in await repository.directions(conn)]


async def indices(conn, direction_id):
    return [{"id": r["id"], "code": r["code"], "name": r["name"], "market": r["market"],
             "exposure": r["exposure"], "fundCount": r["fund_count"], "state": "NOT_COLLECTED"}
            for r in await repository.indices(conn, direction_id)]


def fund_dto(r):
    return {"instrumentId": str(r["instrument_id"]) if r["instrument_id"] else None,
            "fundCode": r["fund_code"], "fundName": r["fund_name"], "fundType": r["fund_type"],
            "tradeForm": r["trade_form"], "market": r["market"], "currency": r["currency"],
            "latestValue": r.get("latest_nav"), "latestDate": _iso(r.get("latest_nav_date")),
            "navRangePosition": r.get("current_percentile"), "updatedAt": _iso(r.get("updated_at")),
            "technicalScore": None, "dataState": "UNAVAILABLE" if r.get("data_source") == "UNAVAILABLE" else "RANGE_ONLY" if r.get("latest_nav") is not None else "NOT_COLLECTED"}


async def screen(conn, query):
    rows, total = await repository.screen(conn, query)
    return {"items": [fund_dto(dict(row)) for row in rows], "total": total,
            "page": query.page, "pageSize": query.page_size}


async def search(conn, keyword):
    keyword = keyword.strip()
    if not keyword:
        return []
    indices_rows = await repository.search_indices(conn, keyword)
    result = [{"kind": "index", "id": r["id"], "code": r["code"], "name": r["name"],
               "indexId": r["id"], "directionId": r["direction_id"], "marketId": r["market_id"], "styleId": r["style_id"]}
              for r in indices_rows]
    rows, _ = await repository.screen(conn, ScreenQuery(keyword=keyword, pageSize=10))
    for row in rows:
        item = fund_dto(dict(row))
        loc = await repository.location(conn, row["instrument_id"]) if row["instrument_id"] else None
        result.append({**item, "kind": "fund", "id": item["instrumentId"] or f"FUND_CN:{item['fundCode']}",
                       "code": item["fundCode"], "name": item["fundName"],
                       "indexId": loc["index_id"] if loc else None, "directionId": loc["direction_id"] if loc else None,
                       "marketId": loc["market_id"] if loc else None, "styleId": loc["style_id"] if loc else None})
    return result[:20]
