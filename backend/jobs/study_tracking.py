"""Check opted-in private studies using existing facts and actor RLS only."""

import json
import logging

from backend.common.database import system_connection, tenant_transaction
from backend.dependencies import now_shanghai
from backend.investment.study_service import check_study

logger = logging.getLogger("wealthsail.study_tracking")


def _otc_price_plan(row):
    snapshot = row.get("latest_snapshot")
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except (TypeError, ValueError):
            snapshot = {}
    return (
        isinstance(snapshot, dict)
        and isinstance(snapshot.get("actionablePlan"), dict)
        and (
            row.get("asset_type") == "OFF_EXCHANGE_FUND"
            or row.get("trade_form") == "LOF"
            or row.get("trade_form") == "FUND"
            and row.get("market") == "FUND_CN"
            or row.get("graph_scope_verified") is True
        )
    )


async def sync_study_tracking(pool, now=None):
    now = now or now_shanghai()
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT t.id,t.tenant_id,t.user_id,t.generation,
            v.snapshot AS latest_snapshot,i.asset_type,c.trade_form,c.market,
            EXISTS(SELECT 1 FROM reference.theme_asset_graph g
              WHERE NULLIF(t.scope->>'instrumentId','') IS NULL
                AND t.scope->>'fundMarket'='FUND_CN' AND g.market='FUND_CN'
                AND g.asset_code=t.scope->>'fundCode'
                AND g.asset_kind IN ('ETF_LINK_FUND','ACTIVE_FUND','LOF')) AS graph_scope_verified
            FROM research.study_tracks t
            JOIN identity.tenant_members m ON m.tenant_id=t.tenant_id AND m.user_id=t.user_id AND m.status='ACTIVE'
            LEFT JOIN LATERAL(SELECT snapshot FROM research.study_versions v
              WHERE v.tenant_id=t.tenant_id AND v.user_id=t.user_id AND v.track_id=t.id
              AND v.created_at<=$1 ORDER BY v.version DESC LIMIT 1) v ON TRUE
            LEFT JOIN LATERAL(SELECT instrument_id,trade_form,market FROM reference.research_fund_catalog c
              WHERE (NULLIF(t.scope->>'instrumentId','') IS NOT NULL
                     AND c.instrument_id::text=t.scope->>'instrumentId')
                 OR (NULLIF(t.scope->>'instrumentId','') IS NULL
                     AND c.fund_code=t.scope->>'fundCode' AND c.market=t.scope->>'fundMarket')
              ORDER BY c.market,c.fund_code LIMIT 1) c ON TRUE
            LEFT JOIN reference.instruments i ON i.id::text=COALESCE(NULLIF(t.scope->>'instrumentId',''),c.instrument_id::text)
            WHERE t.enabled AND t.created_at<=$1 ORDER BY t.created_at,t.id""",
            now,
        )
    stats = {"checked": 0, "stored": 0, "failed": 0}
    for row in rows:
        if _otc_price_plan(row):
            continue
        try:
            async with tenant_transaction(
                pool, row["user_id"], row["tenant_id"]
            ) as conn:
                result = await check_study(
                    conn,
                    row["tenant_id"],
                    row["user_id"],
                    row["id"],
                    row["generation"],
                    now,
                )
            stats["checked"] += 1
            stats["stored"] += result["stored"]
        except Exception as exc:  # noqa: BLE001 -- one actor cannot prevent other private checks
            stats["failed"] += 1
            logger.warning(
                "study_check_failed track_id=%s error_class=%s",
                row["id"],
                type(exc).__name__,
            )
    logger.info("study_tracking_tick checked=%s stored=%s failed=%s", *stats.values())
    return stats
