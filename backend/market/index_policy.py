"""Conservative index research availability, never a historical PIT proof."""

import hashlib
import json
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from backend.market.research_history import expected_trading_day

VERSION = "csindex-retrieval-v1"
POLICY = {
    "provider": "CSINDEX",
    "timeBasis": "RETRIEVAL_ONLY",
    "cutoffStrategy": "PREVIOUS_COMPLETE_SESSION",
    "methodVersion": VERSION,
}
_TZ = ZoneInfo("Asia/Shanghai")


def object_value(value):
    return json.loads(value) if isinstance(value, str) else value or {}


def source_uri(code):
    return f"https://www.csindex.com.cn/zh-CN/indices/index-detail/{code}"


def configured(subject):
    return object_value(subject.get("collection_policy")).get("provider") == "CSINDEX"


def research_status(result):
    result = result or {}
    quality, trend = result.get("dataQuality"), result.get("trendQuality")
    if quality in (None, "READY") and trend in (None, "READY"):
        return "COMPLETED"
    if quality == "CONFLICT" and trend == "READY":
        return "PARTIAL"
    return "FAILED"


def trusted(subject, now):
    verified = subject.get("verified_at")
    return bool(
        verified
        and verified <= now
        and subject.get("market") in (None, "SSE", "SZSE")
        and subject.get("code") in {"000300", "000510", "000905"}
        and subject.get("provider_symbol") == subject["code"]
        and subject.get("source_uri") == source_uri(subject["code"])
        and object_value(subject.get("collection_policy")) == POLICY
    )


def config_hash(subject):
    data = {
        key: subject.get(key)
        for key in ("code", "market", "provider_symbol", "source_uri", "publish_hour")
    }
    data["collection_policy"] = object_value(subject.get("collection_policy"))
    data["sourceVerified"] = bool(subject.get("verified_at"))
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def expected_session(conn, now, start=None):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("index research requires an aware clock")
    cutoff = now.astimezone(_TZ).date() - timedelta(days=1)
    expected = await expected_trading_day(conn, "SSE", cutoff, start)
    if expected and start is None and expected < cutoff:
        expected = await expected_trading_day(conn, "SSE", cutoff, expected)
    return expected


async def read_collection(conn, index_id, now):
    row = await conn.fetchrow(
        """SELECT c.phase,c.recorded_at,c.lease_until,c.result,t.result AS last_result,t.recorded_at AS last_recorded_at
      FROM market.macro_evidence_attempts c LEFT JOIN LATERAL (
        SELECT result,recorded_at FROM market.macro_evidence_attempts t WHERE t.job_key=$1
          AND t.phase='FINISHED' AND t.result->>'status' IN ('COMPLETED','PARTIAL','FAILED') AND t.recorded_at<=$2
          ORDER BY t.recorded_at DESC,t.id DESC LIMIT 1) t ON true
      WHERE c.job_key=$1 AND c.recorded_at<=$2 AND (
        (c.phase='FINISHED' AND c.result->>'status' IN ('COMPLETED','PARTIAL','FAILED')) OR
        (c.phase='CLAIMED' AND c.lease_until>$2 AND NOT EXISTS(
          SELECT 1 FROM market.macro_evidence_attempts t WHERE t.run_id=c.run_id AND t.phase IN ('FINISHED','CANCELLED'))))
      ORDER BY c.recorded_at DESC,c.id DESC LIMIT 1""",
        f"INDEX_RESEARCH:{index_id}",
        now,
    )
    if row is None:
        return None
    result = object_value(row["result"])
    active = row["phase"] == "CLAIMED"
    last = object_value(row.get("last_result")) or (result if not active else {})
    last_recorded = row.get("last_recorded_at") or (
        row["recorded_at"] if not active else None
    )
    status = "RUNNING" if active else result.get("status", "UNKNOWN")
    due = (
        row["lease_until"]
        if active
        else row["recorded_at"] + timedelta(hours=24 if status == "COMPLETED" else 1)
    )
    output = {
        "attemptedAt": row["recorded_at"].isoformat(),
        "collectedAt": None if active else row["recorded_at"].isoformat(),
        "nextCheckAt": due.isoformat(),
        "collectionState": status,
        "status": status,
        "lastResultStatus": last.get("status"),
        "lastResultAt": last_recorded.isoformat() if last_recorded else None,
    }
    error = result.get("errorClass") or last.get("errorClass")
    if isinstance(error, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error):
        output["errorClass"] = error
    http_status = result.get("httpStatus", last.get("httpStatus"))
    if type(http_status) is int and 100 <= http_status <= 599:
        output["httpStatus"] = http_status
    return output
