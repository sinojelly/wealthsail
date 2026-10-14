"""Immutable official evidence; short database claims never span provider calls."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
_JOB = "MACRO_OFFICIAL_EVIDENCE"
_UNITS = {"CPI_YOY": "%", "PPI_YOY": "%", "M2_YOY": "%", "PMI": ""}


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if (
            not isinstance(result, datetime)
            or result.tzinfo is None
            or result.utcoffset() is None
        ):
            raise ValueError("evidence timestamp must include timezone")
        return result.astimezone(_TZ)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid evidence timestamp") from exc


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def normalize_record(kind, row, now):
    now = _timestamp(now)
    uri = urlsplit(row["sourceUri"])
    host = (uri.hostname or "").lower()
    allowed = ("stats.gov.cn", "pbc.gov.cn", "federalreserve.gov")
    if (
        uri.scheme != "https"
        or uri.username
        or uri.password
        or uri.port not in (None, 443)
        or not any(host == root or host.endswith("." + root) for root in allowed)
    ):
        raise ValueError("official source URI is not allowed")
    source_uri = urlunsplit(uri._replace(fragment=""))
    retrieved = _timestamp(row["retrievedAt"])
    if retrieved > now:
        raise ValueError("future evidence retrieval")
    published = (
        _timestamp(row["publishedAt"]) if row.get("publishedAt") is not None else None
    )
    if published and published > now:
        raise ValueError("future actual publication")
    result = dict(
        source_uri=source_uri,
        publisher=str(row["publisher"]).strip(),
        source_key=row.get("sourceKey", "OFFICIAL"),
        retrieved_at=retrieved,
        content_hash=str(row["contentHash"]),
    )
    if not result["publisher"] or not result["content_hash"]:
        raise ValueError("official provenance missing")
    if kind in {"releases", "schedules"}:
        code = row["code"]
        period = row["referencePeriod"]
        if (
            code not in _UNITS
            or not isinstance(period, str)
            or not re.fullmatch(r"\d{4}-\d{2}", period)
        ):
            raise ValueError("invalid indicator or period")
        period = date.fromisoformat(period + "-01")
        result.update(code=code, reference_period=period)
        if kind == "releases":
            try:
                value = Decimal(str(row["value"]))
                if (
                    isinstance(row["value"], bool)
                    or not value.is_finite()
                    or abs(value) >= Decimal("1e18")
                    or row["unit"] != _UNITS[code]
                    or period > now.date()
                ):
                    raise ValueError("invalid macro value, unit or period")
            except (InvalidOperation, TypeError) as exc:
                raise ValueError("invalid macro value") from exc
            if (
                not (host == "stats.gov.cn" or host.endswith(".stats.gov.cn"))
                and code != "M2_YOY"
            ):
                raise ValueError("indicator publisher mismatch")
            if code == "M2_YOY" and not (
                host == "pbc.gov.cn" or host.endswith(".pbc.gov.cn")
            ):
                raise ValueError("indicator publisher mismatch")
            result.update(value=value, unit=row["unit"], published_at=published)
        else:
            if not (host == "stats.gov.cn" or host.endswith(".stats.gov.cn")):
                raise ValueError("release calendar publisher mismatch")
            result["scheduled_at"] = _timestamp(row["scheduledAt"])
    elif kind == "events":
        if row["sourceType"] != "OFFICIAL_FACT":
            raise ValueError("official event type required")
        result.update(
            event_key=str(row["eventKey"]),
            title=str(row["title"]).strip(),
            category=str(row["category"]),
            source_type=row["sourceType"],
            published_at=published,
            summary=str(row.get("summary", ""))[:300],
            direction_ids=sorted(set(row.get("directionIds", []))),
            evidence=row.get("evidence", {}),
        )
        if not result["event_key"] or not result["title"]:
            raise ValueError("event identity missing")
    else:
        raise ValueError("unknown evidence kind")
    canonical = {
        key: value
        for key, value in result.items()
        if key not in {"retrieved_at", "content_hash"}
    }
    # Numeric spelling must not manufacture a revision.
    if "value" in canonical:
        canonical["value"] = str(canonical["value"].normalize())
    result["canonical_hash"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    return result


async def append_evidence(conn, data, now):
    counts = {"releases": 0, "schedules": 0, "events": 0, "rejected": 0}
    tables = {
        "releases": "macro_release_evidence",
        "schedules": "macro_release_schedules",
        "events": "macro_events",
    }
    async with conn.transaction():
        for kind, table in tables.items():
            for record in data.get(kind, []):
                try:
                    row = normalize_record(kind, record, now)
                    directions = row.pop("direction_ids", [])
                    if directions:
                        known = await conn.fetch(
                            "SELECT id FROM reference.research_directions WHERE id=ANY($1::text[])",
                            directions,
                        )
                        if {item["id"] for item in known} != set(directions):
                            raise ValueError("unknown event direction")
                except (KeyError, TypeError, ValueError):
                    counts["rejected"] += 1
                    continue
                identity = (
                    (row["event_key"],)
                    if kind == "events"
                    else (row["code"], row["reference_period"], row["source_uri"])
                )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"macro-evidence:{kind}:{identity}",
                )
                where = (
                    "event_key=$1"
                    if kind == "events"
                    else "code=$1 AND reference_period=$2 AND source_uri=$3"
                )
                latest = await conn.fetchrow(
                    f"SELECT revision,canonical_hash FROM market.{table} WHERE {where} ORDER BY revision DESC LIMIT 1",
                    *identity,
                )
                if latest and latest["canonical_hash"] == row["canonical_hash"]:
                    continue
                row["revision"] = latest["revision"] + 1 if latest else 1
                if kind == "events":
                    row["evidence"] = json.dumps(
                        row["evidence"], ensure_ascii=False, default=str
                    )
                columns = list(row)
                params = ",".join(
                    f"${n + 1}" + ("::jsonb" if column == "evidence" else "")
                    for n, column in enumerate(columns)
                )
                inserted = await conn.fetchval(
                    f"INSERT INTO market.{table}({','.join(columns)}) VALUES({params}) ON CONFLICT DO NOTHING RETURNING id",
                    *row.values(),
                )
                if inserted:
                    counts[kind] += 1
                    for direction in directions:
                        await conn.execute(
                            "INSERT INTO market.macro_event_direction_links(event_id,direction_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
                            inserted,
                            direction,
                        )
    return counts


async def read_evidence(conn, now):
    now = _timestamp(now)
    releases = await conn.fetch(
        """SELECT DISTINCT ON(code,reference_period,source_uri) *
      FROM market.macro_release_evidence WHERE retrieved_at<=$1 AND (published_at IS NULL OR published_at<=$1)
      AND reference_period<=($1 AT TIME ZONE 'Asia/Shanghai')::date
      ORDER BY code,reference_period,source_uri,revision DESC,retrieved_at DESC""",
        now,
    )
    schedules = await conn.fetch(
        """SELECT DISTINCT ON(code,reference_period,source_uri) *
      FROM market.macro_release_schedules WHERE retrieved_at<=$1
      ORDER BY code,reference_period,source_uri,revision DESC,retrieved_at DESC""",
        now,
    )
    events = await conn.fetch(
        """SELECT e.*,ARRAY(SELECT l.direction_id FROM market.macro_event_direction_links l
      WHERE l.event_id=e.id ORDER BY l.direction_id) AS direction_ids FROM (
      SELECT DISTINCT ON(event_key) * FROM market.macro_events
      WHERE retrieved_at<=$1 AND (published_at IS NULL OR published_at<=$1)
      ORDER BY event_key,revision DESC,retrieved_at DESC) e
      WHERE COALESCE(e.published_at,e.retrieved_at)>=$1-INTERVAL '30 days'
      ORDER BY e.published_at DESC NULLS LAST,e.retrieved_at DESC,e.id LIMIT 20""",
        now,
    )
    return {
        "releases": [dict(row) for row in releases],
        "schedules": [dict(row) for row in schedules],
        "events": [{**dict(row), "evidence": _json(row["evidence"])} for row in events],
    }


def refresh_due(now, previous, schedules, releases):
    now = _timestamp(now)
    if previous is None:
        return {"trigger": "BOOT", "release_keys": [], "retry_until": None}
    result = _json(previous["result"])
    recorded = _timestamp(previous["recorded_at"])
    retry_until = _timestamp(result["retryUntil"]) if result.get("retryUntil") else None
    release_periods = {
        (row["code"], str(row["reference_period"])[:7]) for row in releases
    }
    prior_keys = {str(key) for key in _json(previous.get("release_keys", []))}
    due = [
        row
        for row in schedules
        if row["scheduled_at"] + timedelta(minutes=15)
        <= now
        < row["scheduled_at"] + timedelta(hours=24)
        and (row["code"], str(row["reference_period"])[:7]) not in release_periods
        and str(row["id"]) not in prior_keys
    ]
    if due:
        return {
            "trigger": "RELEASE",
            "release_keys": [str(row["id"]) for row in due],
            "retry_until": min(
                row["scheduled_at"] + timedelta(hours=24) for row in due
            ),
        }
    if (
        result.get("status") not in {"COMPLETED", "SKIPPED"}
        and retry_until
        and now < retry_until
    ):
        return (
            {
                "trigger": "RETRY",
                "release_keys": _json(previous.get("release_keys", [])),
                "retry_until": retry_until,
            }
            if now >= recorded + timedelta(hours=1)
            else None
        )
    if now >= recorded + timedelta(hours=6) or retry_until and now >= retry_until:
        return {
            "trigger": "REGULAR",
            "release_keys": [],
            "retry_until": None,
            "retry_exhausted": bool(
                result.get("retryExhausted") or retry_until and now >= retry_until
            ),
        }
    return None


async def claim_refresh(conn, now, *, force=False):
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _JOB)
        active = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM market.macro_evidence_attempts c
          WHERE c.job_key=$1 AND c.phase='CLAIMED' AND c.recorded_at<=$2 AND c.lease_until>$2
          AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts t WHERE t.run_id=c.run_id AND t.phase IN ('FINISHED','CANCELLED')))""",
            _JOB,
            now,
        )
        if active:
            return None
        previous = await conn.fetchrow(
            """SELECT * FROM market.macro_evidence_attempts
          WHERE job_key=$1 AND phase IN ('FINISHED','CANCELLED') AND recorded_at<=$2
          ORDER BY recorded_at DESC,id DESC LIMIT 1""",
            _JOB,
            now,
        )
        evidence = await read_evidence(conn, now)
        due = (
            {"trigger": "FORCE", "release_keys": [], "retry_until": None}
            if force
            else refresh_due(
                now,
                dict(previous) if previous else None,
                evidence["schedules"],
                evidence["releases"],
            )
        )
        if not due:
            return None
        run_id = uuid4()
        await conn.execute(
            """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,lease_until,trigger,release_keys)
          VALUES($1,$2,'CLAIMED',$3,$3,$4,$5,$6::jsonb)""",
            run_id,
            _JOB,
            now,
            now + timedelta(minutes=10),
            due["trigger"],
            json.dumps(due["release_keys"]),
        )
        targets = [
            {"code": row["code"], "reference_period": row["reference_period"]}
            for row in evidence["schedules"]
            if str(row["id"]) in due["release_keys"]
        ]
        return {"id": run_id, "started_at": now, "release_targets": targets, **due}


async def finish_refresh(conn, claim, now, result, *, cancelled=False):
    await conn.execute(
        """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,trigger,release_keys,result)
      VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb) ON CONFLICT DO NOTHING""",
        claim["id"],
        _JOB,
        "CANCELLED" if cancelled else "FINISHED",
        claim.get("started_at", now),
        now,
        claim["trigger"],
        json.dumps(claim["release_keys"]),
        json.dumps(result, ensure_ascii=False, default=str),
    )
