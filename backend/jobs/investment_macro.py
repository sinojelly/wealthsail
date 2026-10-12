"""Persist observed macro facts; provider period dates are not publication dates."""
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid5, NAMESPACE_URL

from backend.common.database import system_connection
from backend.integrations.market_data import fetch_economic_calendar
from backend.market import repository


def normalize_macro(items):
    rows = []
    codes = {"中国CPI同比": "CPI_YOY", "中国制造业PMI": "PMI"}
    for item in items:
        try:
            code = codes[item["name"]]
            value = Decimal(str(item["value"]))
            period = datetime.strptime(str(item["date"]), "%Y-%m").date()
            if not value.is_finite():
                continue
        except (KeyError, ValueError, InvalidOperation):
            continue
        rows.append({"fieldCode": code, "value": value, "period": period,
                     "unit": item.get("unit", ""), "publishedAt": None, "quality": "STALE"})
    return rows


async def sync_macro(pool, now):
    items = normalize_macro(await fetch_economic_calendar())
    items = [item for item in items if item["period"] <= now.date()]
    if not items:
        return {"observations": 0}
    async with system_connection(pool) as conn:
        async with conn.transaction():
            await repository.upsert_data_source(conn, name="AKSHARE_RESEARCH_MACRO", source_type="MACRO")
            source_id = await repository.get_source_id(conn, "AKSHARE_RESEARCH_MACRO")
            snapshot_id = await repository.insert_source_snapshot(conn, source_id=source_id,
                retrieval_key=f"research-macro:{now.isoformat()}", observations=items,
                quality_status="STALE", retrieved_at=now)
            for item in items:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"research-macro:{item['fieldCode']}")
                period = datetime.combine(item["period"], datetime.min.time(), tzinfo=now.tzinfo)
                await repository.insert_observations(conn, [("MACRO_SERIES", str(uuid5(NAMESPACE_URL, f"wealthsail:macro:{item['fieldCode']}")),
                    item["fieldCode"], item["value"], None, None, item["unit"], "CNY", period, now, snapshot_id,
                    source_id, "STALE", "research-macro-v1", json.dumps({"publicationVerified": False}))])
    return {"observations": len(items)}
