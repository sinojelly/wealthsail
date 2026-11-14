"""Durable, versioned discovery identities. Request readers never run matching.

The archive worker may retry this optional projection independently of economic
archival. Purchase, price, score and execution facts always come from live reads.
"""

import json
import logging
import re
from uuid import UUID, uuid4

from backend.common.database import system_connection
from backend.macro.research_diagnostics import safe_error_class

logger = logging.getLogger("wealthsail.opportunity_fund_cache")
CACHE_BYTES = 8 * 1024 * 1024
TOPIC_LIMIT = 72
CANDIDATES_PER_TOPIC = 96
LEASE_SECONDS = 120
MAX_ATTEMPTS = 8
_TOPIC = re.compile(r"topic:[a-f0-9]{32}")
_CODE = re.compile(r"[0-9]{6}")
_FUND_FIELDS = {
    "fundCode", "name", "subjectKind", "subjectId", "market", "identityVerifiedAt",
    "overseasCalendarRequired", "associationLevel", "matchLevel", "matchReason",
    "sourceUri", "fundGroupKey", "alternativeShareClasses", "salesChannel",
    "narrativeMention", "primaryTopicMatch",
}
_ITEM_FIELDS = {"topicId", "status", "funds", "indirectFunds", "unavailableReason", "message"}


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def prepare_cached_candidates(discovered, topic_ids):
    """Project a bounded identity allowlist, excluding all mutable decisions."""
    ids = set(topic_ids)
    if (len(ids) != len(topic_ids) or len(ids) > TOPIC_LIMIT
            or any(not isinstance(key, str) or not _TOPIC.fullmatch(key) for key in ids)
            or not isinstance(discovered, dict) or set(discovered) != ids):
        raise ValueError("invalid cached topic identity")
    result = {}
    for key, value in discovered.items():
        if not isinstance(value, dict) or value.get("topicId") != key:
            raise ValueError("invalid cached topic body")
        item = {field: value[field] for field in _ITEM_FIELDS if field in value}
        if any(not isinstance(text, (str, type(None))) or isinstance(text, str) and len(text) > 2000
               for field, text in item.items() if field not in {"funds", "indirectFunds"}):
            raise ValueError("invalid cached topic metadata")
        count = 0
        for field in ("funds", "indirectFunds"):
            funds = value.get(field, [])
            if not isinstance(funds, list):
                raise ValueError("invalid cached funds")
            count += len(funds)
            if count > CANDIDATES_PER_TOPIC:
                raise ValueError("cached funds exceed topic budget")
            item[field] = []
            for fund in funds:
                if not isinstance(fund, dict) or not _CODE.fullmatch(str(fund.get("fundCode", ""))):
                    raise ValueError("invalid cached fund identity")
                projected = {name: fund[name] for name in _FUND_FIELDS if name in fund}
                for name, text in projected.items():
                    if name == "alternativeShareClasses":
                        if not isinstance(text, list) or len(text) > 4:
                            raise ValueError("invalid alternative shares")
                        safe_shares = []
                        for share in text:
                            if isinstance(share, str) and _CODE.fullmatch(share):
                                safe_shares.append(share)
                            elif isinstance(share, dict) and _CODE.fullmatch(str(share.get("fundCode", ""))):
                                safe = {k: v for k, v in share.items() if k in {"fundCode", "name", "shareClass"}}
                                if any(not isinstance(v, str) or len(v) > 200 for v in safe.values()):
                                    raise ValueError("invalid alternative share metadata")
                                safe_shares.append(safe)
                            else:
                                raise ValueError("invalid alternative share identity")
                        projected[name] = safe_shares
                    elif not isinstance(text, (str, bool, type(None))) or isinstance(text, str) and len(text) > 2000:
                        raise ValueError("invalid cached fund metadata")
                item[field].append(projected)
        result[key] = item
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # PostgreSQL jsonb renders additional whitespace; budget its canonical form too.
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > CACHE_BYTES:
        raise ValueError("cached funds exceed run byte budget")
    return json.loads(encoded)


async def read_cached_topic_funds(conn, run_id, validated_at, topics, now):
    """SELECT an exact visible snapshot/version/revision, or report a cache miss."""
    from backend.investment.opportunity_fund_recall import RECALL_MATCH_VERSION

    row = await conn.fetchrow(
        """SELECT c.candidates FROM research.opportunity_fund_cache c
        JOIN research.research_topic_snapshots s ON s.run_id=c.run_id AND s.id=c.snapshot_id
        JOIN reference.opportunity_fund_catalog_revision v ON v.singleton
        WHERE c.run_id=$1 AND c.validated_at=$2 AND s.validated_at=$2
          AND c.content_digest=s.content_digest AND c.match_version=$3
          AND c.catalog_revision=v.revision AND c.catalog_day=CURRENT_DATE
          AND c.status='READY' AND c.updated_at<=$4""",
        UUID(str(run_id)), validated_at, RECALL_MATCH_VERSION, now,
    )
    if row is None:
        return None
    try:
        return prepare_cached_candidates(
            _object(row["candidates"]),
            [topic["topicId"] for topic in topics if topic.get("status") != "QUARANTINED"],
        )
    except (ValueError, TypeError, KeyError):
        # A bad optional cache must never authorize fallback request-time matching.
        return None


async def read_topic_fund_cache_status(conn, run_id, validated_at, now):
    """Read safe retry state for this exact current cache; a stale row is pending."""
    from backend.investment.opportunity_fund_recall import RECALL_MATCH_VERSION

    value = await conn.fetchval(
        """SELECT c.status FROM research.opportunity_fund_cache c
        JOIN research.research_topic_snapshots s ON s.run_id=c.run_id AND s.id=c.snapshot_id
        JOIN reference.opportunity_fund_catalog_revision v ON v.singleton
        WHERE c.run_id=$1 AND c.validated_at=$2 AND s.validated_at=$2
          AND c.content_digest=s.content_digest AND c.match_version=$3
          AND c.catalog_revision=v.revision AND c.catalog_day=CURRENT_DATE
          AND c.updated_at<=$4""",
        UUID(str(run_id)), validated_at, RECALL_MATCH_VERSION, now,
    )
    return value if value in {"PENDING", "COMPUTING", "READY", "FAILED"} else None


async def claim_topic_fund_cache(conn, *, run_id=None):
    """Backfill newest research first; only database time authorizes a lease."""
    from backend.investment.opportunity_fund_recall import RECALL_MATCH_VERSION

    await conn.execute(
        """INSERT INTO research.opportunity_fund_cache
        (run_id,snapshot_id,content_digest,validated_at,match_version,catalog_revision,catalog_day)
        SELECT s.run_id,s.id,s.content_digest,s.validated_at,$2,v.revision,CURRENT_DATE
        FROM research.research_topic_snapshots s
        CROSS JOIN reference.opportunity_fund_catalog_revision v
        LEFT JOIN research.opportunity_fund_cache c ON c.run_id=s.run_id
        WHERE ($1::uuid IS NULL OR s.run_id=$1)
          AND (c.run_id IS NULL OR c.match_version<>$2 OR c.catalog_revision<>v.revision
            OR c.catalog_day<>CURRENT_DATE OR c.content_digest<>s.content_digest)
          AND (c.status IS DISTINCT FROM 'COMPUTING' OR c.lease_expires_at<=clock_timestamp())
        ORDER BY s.validated_at DESC,s.created_at DESC,s.run_id LIMIT 8
        ON CONFLICT(run_id) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id,
          content_digest=EXCLUDED.content_digest,validated_at=EXCLUDED.validated_at,
          match_version=EXCLUDED.match_version,catalog_revision=EXCLUDED.catalog_revision,
          catalog_day=EXCLUDED.catalog_day,status='PENDING',attempts=0,candidates='{}',
          lease_token=NULL,lease_expires_at=NULL,last_error_class=NULL,
          next_attempt_at=clock_timestamp(),updated_at=clock_timestamp()
        WHERE opportunity_fund_cache.status<>'COMPUTING'
          OR opportunity_fund_cache.lease_expires_at<=clock_timestamp()""",
        UUID(str(run_id)) if run_id is not None else None, RECALL_MATCH_VERSION,
    )
    await conn.execute(
        """UPDATE research.opportunity_fund_cache SET status='FAILED',lease_token=NULL,
        lease_expires_at=NULL,last_error_class='TimeoutError',updated_at=clock_timestamp()
        WHERE status='COMPUTING' AND attempts>=8 AND lease_expires_at<=clock_timestamp()
          AND ($1::uuid IS NULL OR run_id=$1)""",
        UUID(str(run_id)) if run_id is not None else None,
    )
    row = await conn.fetchrow(
        """WITH due AS (SELECT run_id FROM research.opportunity_fund_cache
        WHERE ($1::uuid IS NULL OR run_id=$1) AND attempts<8
          AND ((status='PENDING' AND next_attempt_at<=clock_timestamp())
            OR (status='COMPUTING' AND lease_expires_at<=clock_timestamp()))
        ORDER BY validated_at DESC,next_attempt_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1)
        UPDATE research.opportunity_fund_cache c SET status='COMPUTING',attempts=c.attempts+1,
          lease_token=$2,lease_expires_at=clock_timestamp()+$3*interval '1 second',
          updated_at=clock_timestamp()
        FROM due WHERE c.run_id=due.run_id RETURNING c.*""",
        UUID(str(run_id)) if run_id is not None else None, uuid4(), LEASE_SECONDS,
    )
    return dict(row) if row else None


async def finish_topic_fund_cache(conn, claim, *, candidates=None, error_class=None):
    """Fence stale leases and catalog changes after acquiring the row lock."""
    await conn.fetchrow(
        "SELECT run_id FROM research.opportunity_fund_cache WHERE run_id=$1 FOR UPDATE",
        claim["run_id"],
    )
    return await conn.fetchrow(
        """UPDATE research.opportunity_fund_cache c SET status=CASE
          WHEN $3::jsonb IS NOT NULL AND c.catalog_revision=v.revision AND c.catalog_day=CURRENT_DATE THEN 'READY'
          WHEN c.attempts>=8 THEN 'FAILED' ELSE 'PENDING' END,
          candidates=CASE WHEN $3::jsonb IS NOT NULL AND c.catalog_revision=v.revision
            AND c.catalog_day=CURRENT_DATE THEN $3::jsonb ELSE '{}'::jsonb END,
          last_error_class=$4,lease_token=NULL,lease_expires_at=NULL,
          updated_at=clock_timestamp(),next_attempt_at=clock_timestamp()+$5*interval '1 second'
        FROM reference.opportunity_fund_catalog_revision v
        WHERE c.run_id=$1 AND c.lease_token=$2 AND c.status='COMPUTING'
          AND c.lease_expires_at>clock_timestamp() RETURNING c.status""",
        claim["run_id"], claim["lease_token"],
        json.dumps(candidates, ensure_ascii=False, allow_nan=False) if candidates is not None else None,
        safe_error_class(error_class) if error_class else None,
        min(3600, 15 * 2 ** min(claim["attempts"], MAX_ATTEMPTS)),
    )


async def precompute_topic_funds(pool, run_id=None, *, _discover=None):
    """Retry only frozen local discovery. No inference, network or archive mutation."""
    from backend.investment.opportunity_fund_recall import discover_topic_funds

    async with system_connection(pool) as conn:
        async with conn.transaction():
            claim = await claim_topic_fund_cache(conn, run_id=run_id)
    if claim is None:
        return {"claimed": False, "status": "PENDING"}
    try:
        async with system_connection(pool) as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout='60s'")
                rows = await conn.fetch(
                    "SELECT body FROM research.research_topic_entries WHERE snapshot_id=$1 ORDER BY ordinal",
                    claim["snapshot_id"],
                )
                topics = [_object(row["body"]) for row in rows]
                topics = [topic for topic in topics if topic.get("status") != "QUARANTINED"]
                packet = _object(await conn.fetchval(
                    "SELECT public_packet FROM research.daily_research_runs WHERE run_id=$1", claim["run_id"],
                ))
                now = await conn.fetchval("SELECT clock_timestamp()")
                discovered = await (_discover or discover_topic_funds)(conn, topics, packet, now)
                candidates = prepare_cached_candidates(discovered, [topic["topicId"] for topic in topics])
                finished = await finish_topic_fund_cache(conn, claim, candidates=candidates)
    except Exception as exc:  # noqa: BLE001 -- optional local projection, durable retry
        error = safe_error_class(type(exc).__name__)
        async with system_connection(pool) as conn:
            async with conn.transaction():
                finished = await finish_topic_fund_cache(conn, claim, error_class=error)
        logger.warning("opportunity_cache_failed run_id=%s error_class=%s", claim["run_id"], error)
    return {
        "claimed": True,
        "status": finished["status"] if finished else "PENDING",
        "runId": str(claim["run_id"]),
    }
