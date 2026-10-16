"""Check opted-in private studies using existing facts and actor RLS only."""

import logging

from backend.common.database import system_connection, tenant_transaction
from backend.dependencies import now_shanghai
from backend.investment.study_service import check_study

logger = logging.getLogger("wealthsail.study_tracking")


async def sync_study_tracking(pool, now=None):
    now = now or now_shanghai()
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT t.id,t.tenant_id,t.user_id,t.generation FROM research.study_tracks t
            JOIN identity.tenant_members m ON m.tenant_id=t.tenant_id AND m.user_id=t.user_id AND m.status='ACTIVE'
            WHERE t.enabled AND t.created_at<=$1 ORDER BY t.created_at,t.id""",
            now,
        )
    stats = {"checked": 0, "stored": 0, "failed": 0}
    for row in rows:
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
