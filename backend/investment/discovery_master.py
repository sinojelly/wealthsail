"""Worker-only, durable preparation of the public market-wide fund master."""

import asyncio
from datetime import datetime, timedelta
import json
import math
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.integrations.fund_master import FundMasterEntry
from backend.jobs.fund_master_sync import upsert_fund_master_entries

SOURCE_URI = "https://fund.eastmoney.com/js/fundcode_search.js"
METHOD_VERSION = "eastmoney-fund-master-v1"
MINIMUM_ENTRIES = 10000
MAXIMUM_ENTRIES = 100000
MAXIMUM_BYTES = 12_000_000
_JOB = "DISCOVERY_FUND_MASTER"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class ClockRegression(ValueError):
    """A regressed wall clock cannot establish a master refresh watermark."""


def _local(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("fund master requires an aware clock")
    return value.astimezone(_SHANGHAI)


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _validate_entries(entries, previous_count=0):
    if (
        not isinstance(entries, list)
        or not MINIMUM_ENTRIES <= len(entries) <= MAXIMUM_ENTRIES
    ):
        raise ValueError("fund master does not meet full-list coverage bounds")
    if len(entries) < math.ceil(previous_count * 0.8):
        raise ValueError("fund master response unexpectedly shrank")
    seen = set()
    for row in entries:
        if (
            not isinstance(row, FundMasterEntry)
            or not re.fullmatch(r"[0-9]{6}", row.fund_code)
            or row.fund_code in seen
            or not row.fund_name.strip()
            or len(row.fund_name) > 200
            or not row.fund_type.strip()
            or len(row.fund_type) > 100
            or row.pinyin_abbr is not None
            and len(row.pinyin_abbr) > 200
            or row.pinyin_full is not None
            and len(row.pinyin_full) > 1000
        ):
            raise ValueError("invalid or ambiguous full-list fund identity")
        seen.add(row.fund_code)


def parse_master_payload(raw):
    """Parse the upstream used by AKShare fund_name_em; never execute JS."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAXIMUM_BYTES:
        raise ValueError("fund master response exceeds its byte bound")
    match = re.fullmatch(
        r"\s*var\s+r\s*=\s*(\[.*\])\s*;\s*", raw.lstrip("\ufeff"), re.DOTALL
    )
    if match is None:
        raise ValueError("fund master response is not the expected JSON assignment")
    try:
        rows = json.loads(match[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid fund master JSON array") from exc
    if not isinstance(rows, list) or len(rows) > MAXIMUM_ENTRIES:
        raise ValueError("invalid fund master row count")
    entries = []
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) != 5
            or any(not isinstance(cell, str) for cell in row)
        ):
            raise ValueError("fund master response columns changed")
        code, abbr, name, kind, full = (cell.strip() for cell in row)
        entries.append(
            FundMasterEntry(code, name, kind or "UNKNOWN", abbr or None, full or None)
        )
    _validate_entries(entries)
    return entries


async def fetch_discovery_master(*, client=None):
    """One public streamed request, with byte and wall-clock deadlines."""
    if client is None:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as owned:
            return await fetch_discovery_master(client=owned)
    async with asyncio.timeout(30):
        async with client.stream("GET", SOURCE_URI) as response:
            response.raise_for_status()
            size = response.headers.get("content-length")
            if size is not None and (not size.isdigit() or int(size) > MAXIMUM_BYTES):
                raise ValueError("fund master response exceeds its byte bound")
            chunks, length = [], 0
            async for chunk in response.aiter_bytes():
                length += len(chunk)
                if length > MAXIMUM_BYTES:
                    raise ValueError("fund master response exceeds its byte bound")
                chunks.append(chunk)
        return parse_master_payload(b"".join(chunks).decode("utf-8-sig"))


def master_refresh_due(now, *, success, previous, catalog_count):
    now = _local(now)
    if success:
        result = _object(success.get("result"))
        recorded = _local(success["recorded_at"])
        total = result.get("totalEntries")
        if (
            result.get("status") == "SUCCESS"
            and type(total) is int
            and total >= MINIMUM_ENTRIES
            and catalog_count >= max(MINIMUM_ENTRIES, math.ceil(total * 0.8))
            and recorded <= now < recorded + timedelta(days=7)
        ):
            return "FRESH"
    if previous:
        recorded = _local(previous["recorded_at"])
        if _object(previous.get("result")).get(
            "status"
        ) != "SUCCESS" and recorded <= now < recorded + timedelta(hours=1):
            return "COOLDOWN"
    return "DUE"


async def _claim_master(pool, now):
    async with system_connection(pool) as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _JOB)
            # A backwards clock must not hide an already leased claim; only a
            # terminal record visible at the current instant releases it.
            if await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM market.macro_evidence_attempts c
                  WHERE c.job_key=$1 AND c.phase='CLAIMED' AND c.lease_until>$2
                    AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts f
                      WHERE f.run_id=c.run_id AND f.phase IN ('FINISHED','CANCELLED')
                        AND f.recorded_at<=$2))""",
                _JOB,
                now,
            ):
                return {"status": "SKIPPED", "reason": "ACTIVE_CLAIM"}
            count = await conn.fetchval("SELECT count(*) FROM reference.fund_master")
            previous = await conn.fetchrow(
                """SELECT recorded_at,result FROM market.macro_evidence_attempts
                  WHERE job_key=$1 AND phase IN ('FINISHED','CANCELLED') AND recorded_at<=$2
                  ORDER BY recorded_at DESC,id DESC LIMIT 1""",
                _JOB,
                now,
            )
            success = await conn.fetchrow(
                """SELECT recorded_at,result FROM market.macro_evidence_attempts
                  WHERE job_key=$1 AND phase='FINISHED' AND result->>'status'='SUCCESS'
                    AND recorded_at<=$2 ORDER BY recorded_at DESC,id DESC LIMIT 1""",
                _JOB,
                now,
            )
            due = master_refresh_due(
                now, success=success, previous=previous, catalog_count=count
            )
            if due != "DUE":
                return {
                    "status": "SKIPPED",
                    "reason": due,
                    "catalogCount": count,
                    **(
                        {"lastSuccessAt": _local(success["recorded_at"]).isoformat()}
                        if success
                        else {}
                    ),
                }
            identity = uuid4()
            await conn.execute(
                """INSERT INTO market.macro_evidence_attempts
                  (run_id,job_key,phase,started_at,recorded_at,lease_until,trigger,release_keys)
                  VALUES($1,$2,'CLAIMED',$3,$3,$4,'RESEARCH_PREFLIGHT','[]'::jsonb)""",
                identity,
                _JOB,
                now,
                now + timedelta(minutes=2),
            )
            return {"claimId": identity, "startedAt": now, "catalogCount": count}


async def _finish_master(pool, claim, now, result, *, cancelled=False):
    async with system_connection(pool) as conn:
        await conn.execute(
            """INSERT INTO market.macro_evidence_attempts
              (run_id,job_key,phase,started_at,recorded_at,trigger,release_keys,result)
              VALUES($1,$2,$3,$4,$5,'RESEARCH_PREFLIGHT','[]'::jsonb,$6::jsonb) ON CONFLICT DO NOTHING""",
            claim["claimId"],
            _JOB,
            "CANCELLED" if cancelled else "FINISHED",
            claim["startedAt"],
            now,
            json.dumps(result, ensure_ascii=False),
        )


async def ensure_discovery_fund_master(
    pool, now, *, fetcher=None, now_provider=now_shanghai
):
    """Bootstrap or weekly-refresh the master before Worker NAV preparation."""
    now = _local(now)
    try:
        claim = await _claim_master(pool, now)
    except Exception as exc:  # noqa: BLE001 -- existing identities remain usable on storage failure
        return {"status": "FAILED", "errorClass": type(exc).__name__}
    if "claimId" not in claim:
        return claim
    result = {
        "status": "FAILED",
        "sourceUri": SOURCE_URI,
        "methodVersion": METHOD_VERSION,
        "catalogCountBefore": claim["catalogCount"],
    }
    cancelled = False
    retrieved = None
    try:
        entries = await asyncio.wait_for(
            (fetcher or fetch_discovery_master)(), timeout=30
        )
        _validate_entries(entries, claim["catalogCount"])
        retrieved = _local(now_provider())
        if retrieved < now:
            raise ClockRegression("fund master clock regressed before persistence")
        async with system_connection(pool) as conn:
            await asyncio.wait_for(
                upsert_fund_master_entries(conn, entries), timeout=45
            )
        result.update(
            status="SUCCESS",
            totalEntries=len(entries),
            retrievedAt=retrieved.isoformat(),
            unknownFundTypeCount=sum(row.fund_type == "UNKNOWN" for row in entries),
        )
    except asyncio.CancelledError:
        cancelled = True
        result["errorClass"] = "CancelledError"
        raise
    except Exception as exc:  # noqa: BLE001 -- never erase or replace the previous catalog on failure
        result["errorClass"] = type(exc).__name__
    finally:
        finished = _local(now_provider())
        if finished < now or retrieved is not None and finished < retrieved:
            result.update(status="FAILED", errorClass="ClockRegression")
        # An earlier completion instant must not be replaced with the claim's
        # future time. Keep the durable claim until the real clock/lease allows
        # another attempt; already persisted identities remain intact.
        if finished >= now:
            result["completedAt"] = finished.isoformat()
            cleanup = asyncio.create_task(
                _finish_master(pool, claim, finished, result, cancelled=cancelled)
            )
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
    return result
