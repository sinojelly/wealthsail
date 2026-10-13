"""Persist monthly macro histories without inferring provider release dates."""

import hashlib
import json
from datetime import datetime
from itertools import groupby
from uuid import NAMESPACE_URL, uuid5

from backend.common.database import system_connection
from backend.integrations.investment_macro import fetch_macro_history, normalize_macro
from backend.market import repository


async def sync_macro(pool, now):
    start = now.date().replace(year=now.year - 5, day=1)
    items = normalize_macro(
        await fetch_macro_history(start, now.date()), start=start, end=now.date()
    )
    if not items:
        return {"observations": 0}
    async with system_connection(pool) as conn:
        async with conn.transaction():
            await repository.upsert_data_source(
                conn, name="AKSHARE_RESEARCH_MACRO", source_type="MACRO"
            )
            source_id = await repository.get_source_id(conn, "AKSHARE_RESEARCH_MACRO")
            for source_uri, points in groupby(
                sorted(items, key=lambda row: row["sourceUri"]),
                key=lambda row: row["sourceUri"],
            ):
                points = list(points)
                canonical = json.dumps(
                    points, sort_keys=True, ensure_ascii=False, default=str
                )
                source_as_of = datetime.combine(
                    max(point["period"] for point in points),
                    datetime.min.time(),
                    tzinfo=now.tzinfo,
                )
                snapshot_id = await conn.fetchval(
                    """INSERT INTO market.source_snapshots
                      (source_id,retrieval_key,origin_uri,as_of,retrieved_at,content_hash,quality_status)
                      VALUES($1,$2,$3,$4,$5,$6,'STALE') RETURNING id""",
                    source_id,
                    f"research-macro:{source_uri}:{now.isoformat()}",
                    source_uri,
                    source_as_of,
                    now,
                    "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                )
                rows = []
                for item in points:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        f"research-macro:{item['fieldCode']}",
                    )
                    period = datetime.combine(
                        item["period"], datetime.min.time(), tzinfo=now.tzinfo
                    )
                    rows.append(
                        (
                            "MACRO_SERIES",
                            str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"wealthsail:macro:{item['fieldCode']}",
                                )
                            ),
                            item["fieldCode"],
                            item["value"],
                            None,
                            None,
                            item["unit"],
                            "CNY",
                            period,
                            now,
                            snapshot_id,
                            source_id,
                            "STALE",
                            "research-macro-history-v2",
                            json.dumps(
                                {
                                    "publicationVerified": False,
                                    "period": item["period"].strftime("%Y-%m"),
                                    "sourceUri": source_uri,
                                }
                            ),
                        )
                    )
                await repository.insert_observations(conn, rows)
    return {"observations": len(items)}
