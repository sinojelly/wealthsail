"""Frequency-aware market facts; collection time never rewrites release time."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from statistics import median
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from backend.integrations.fred_macro import FRED_CATALOG, FRED_HOST

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
CATALOG = {
    "CN_T10": ("中国10年期国债收益率", "DAILY", "%", "CHINABOND"),
    "US_T10_NOMINAL": ("美国10年期名义国债收益率", "DAILY", "%", "UST_NOMINAL"),
    "US_T10_REAL": ("美国10年期实际国债收益率", "DAILY", "%", "UST_REAL"),
    "M1_YOY": ("M1同比（2025新口径）", "MONTHLY", "%", "PBOC_M1"),
    "TSF_STOCK_YOY": ("社融存量同比", "MONTHLY", "%", "PBOC_TSF"),
    "CORP_MEDLONG_LOANS_YTD": ("企业中长期贷款累计新增", "YTD", "万亿元", "PBOC_LOANS"),
    "DR007": ("DR007资金价格", "DAILY", "%", "CFETS"),
    "A50_CHANGE_PCT": ("A50盘前变化", "DAILY", "%", "PRE_MARKET"),
    "US_SP500_CHANGE_PCT": ("标普500隔夜变化", "DAILY", "%", "PRE_MARKET"),
    "US_NASDAQ_CHANGE_PCT": ("纳斯达克隔夜变化", "DAILY", "%", "PRE_MARKET"),
}
CATALOG.update({code: row for code, row in FRED_CATALOG.items() if code not in CATALOG})
US_YIELDS = {"US_T10_NOMINAL", "US_T10_REAL", "US_T2_NOMINAL", "US_FED_FUNDS"}
_NY_SERIES = set(FRED_CATALOG)


def _stamp(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        return (
            result
            if isinstance(result, datetime)
            and result.tzinfo
            and result.utcoffset() is not None
            else None
        )
    except (ValueError, TypeError):
        return None


def _number(value):
    try:
        result = Decimal(str(value))
        return (
            result
            if not isinstance(value, bool)
            and result.is_finite()
            and math.isfinite(float(result))
            else None
        )
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def _day(value):
    try:
        return (
            value
            if isinstance(value, date) and not isinstance(value, datetime)
            else date.fromisoformat(str(value)[:10])
        )
    except (ValueError, TypeError):
        return None


def _metadata(row):
    value = row.get("metadata", {})
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
        return decoded if isinstance(decoded, dict) else {}
    except (ValueError, TypeError):
        return {}


def _visible(row, now):
    day = _day(row.get("reference_date"))
    zone = NEW_YORK if row.get("code") in _NY_SERIES else SHANGHAI
    if not day or day > now.astimezone(zone).date():
        return False
    return all(
        _stamp(row.get(key)) is not None and _stamp(row[key]) <= now
        for key in ("retrieved_at", "available_at")
    ) and (
        row.get("published_at") is None
        or _stamp(row["published_at"]) is not None
        and _stamp(row["published_at"]) <= now
    )


def permission_allowed(permission, now):
    if not permission or permission.get("status") != "APPROVED":
        return False
    verified = _stamp(permission.get("verifiedAt", permission.get("verified_at")))
    expires = permission.get("validUntil", permission.get("valid_until"))
    return bool(
        permission.get("purpose")
        and permission.get("evidenceUri", permission.get("evidence_uri"))
        and verified
        and verified <= now
        and (expires is None or _stamp(expires) is not None and _stamp(expires) > now)
    )


def _definition(row):
    metadata = _metadata(row)
    return (
        metadata.get("definitionKey"),
        metadata.get("definitionVersion"),
        row.get("unit"),
        row.get("method_version"),
        row.get("frequency"),
    )


def _has_definition(metadata):
    return all(
        isinstance(metadata.get(key), str) and metadata[key].strip()
        for key in ("definitionKey", "definitionVersion")
    )


def _conflict(row, frequency, unit):
    return (
        row.get("frequency") != frequency
        or row.get("unit") != unit
        or _number(row.get("value")) is None
        or not _has_definition(_metadata(row))
        or _metadata(row).get("qualityStatus") in {"CONFLICT", "UNAVAILABLE"}
    )


def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _changes(points, latest, frequency, *, code):
    is_yield = code in US_YIELDS or code in {"CN_T10", "DR007"}
    result = {
        "previousValue": None,
        "periodChange": None,
        "change5Samples": None,
        "change20Samples": None,
        "change3Months": None,
        "change6Months": None,
        "changeUnit": "bp"
        if frequency == "DAILY" and is_yield
        else "pp"
        if frequency in {"MONTHLY", "DAILY"}
        else None,
        "median": None,
        "percentile": None,
        "sampleCount": sum(
            not _conflict(p, frequency, CATALOG[code][2]) for p in points
        ),
    }
    if latest is None or frequency == "YTD":
        return result
    basis = _definition(latest)
    if not basis[0] or not basis[1]:
        return result
    comparable = [
        p
        for p in points
        if _definition(p) == basis and not _conflict(p, frequency, CATALOG[code][2])
    ]
    result["sampleCount"] = len(comparable)
    if not comparable or _day(comparable[-1]["reference_date"]) != _day(
        latest["reference_date"]
    ):
        return result
    value = _number(latest["value"])
    if frequency == "DAILY":
        # A different-definition point between endpoints breaks a comparable run.
        for distance, field in (
            (1, "previousValue"),
            (5, "change5Samples"),
            (20, "change20Samples"),
        ):
            segment = points[-(distance + 1) :]
            if len(segment) == distance + 1 and all(p in comparable for p in segment):
                result[field] = (
                    _number(segment[0]["value"])
                    if distance == 1
                    else (value - _number(segment[0]["value"]))
                    * (100 if is_yield else 1)
                )
        minimum = 252
    else:
        months = {
            _day(p["reference_date"]).year * 12 + _day(p["reference_date"]).month: p
            for p in comparable
        }
        current = (
            _day(latest["reference_date"]).year * 12
            + _day(latest["reference_date"]).month
        )
        for distance, field in (
            (1, "previousValue"),
            (3, "change3Months"),
            (6, "change6Months"),
        ):
            if all(m in months for m in range(current - distance, current + 1)):
                result[field] = (
                    _number(months[current - distance]["value"])
                    if distance == 1
                    else value - _number(months[current - distance]["value"])
                )
        minimum = 12
    if result["previousValue"] is not None:
        result["periodChange"] = (value - result["previousValue"]) * (
            100 if frequency == "DAILY" and is_yield else 1
        )
    if len(points) >= minimum and len(comparable) == len(points):
        values = [_number(p["value"]) for p in points]
        result["median"] = median(values)
        result["percentile"] = (
            Decimal(100)
            * (
                sum(v < value for v in values)
                + Decimal("0.5") * sum(v == value for v in values)
            )
            / len(values)
        )
    # Finite inputs can overflow a float DTO after subtraction / bp scaling.
    for key in result:
        if key not in {"changeUnit", "sampleCount"}:
            result[key] = _number(result[key])
    if code == "US_BRENT":
        result["changeUnit"] = "USD/barrel"
    return result


def series_context(rows, now, *, permissions=None, source_results=None):
    if _stamp(now) is None:
        raise ValueError("opportunity series requires aware time")
    permissions, source_results = permissions or {}, source_results or {}
    selected = {}
    for record in rows:
        row = dict(record)
        if row.get("code") not in CATALOG or not _visible(row, now):
            continue
        if row["code"] in _NY_SERIES and _day(
            row["reference_date"]
        ) < _years_before(now.astimezone(NEW_YORK).date(), 3):
            continue
        key = row["code"], _day(row["reference_date"])
        rank = (_stamp(row["retrieved_at"]), row.get("revision") or 0)
        if key not in selected or rank > selected[key][0]:
            selected[key] = rank, row
    output = []
    for code, (label, frequency, unit, source_key) in CATALOG.items():
        points = [r for (key, _), (_, r) in sorted(selected.items()) if key == code]
        latest = points[-1] if points else None
        state, value = "NOT_COLLECTED", None
        blocked = source_key in {"CHINABOND", "CFETS"} and not permission_allowed(
            permissions.get(source_key), now
        )
        if blocked:
            state, points, latest = "PERMISSION_REQUIRED", [], None
        elif latest:
            value = _number(latest.get("value"))
            if _conflict(latest, frequency, unit):
                value, state = None, "CONFLICT"
            else:
                day = _day(latest["reference_date"])
                daily_ttl = 1 if source_key == "PRE_MARKET" else 5
                stale = (
                    now.astimezone(
                        NEW_YORK if code in _NY_SERIES else SHANGHAI
                    ).date()
                    > (
                        day + timedelta(days=daily_ttl)
                        if frequency == "DAILY"
                        else day.replace(day=monthrange(day.year, day.month)[1])
                        + timedelta(days=45)
                    )
                    or _metadata(latest).get("qualityStatus") == "STALE"
                )
                state = (
                    "STALE"
                    if stale
                    or source_results.get(source_key, {}).get("status") == "FAILED"
                    else "READY"
                )
        valid_latest = latest if value is not None else None
        output.append(
            {
                "code": code,
                "label": label,
                "frequency": frequency,
                "unit": unit,
                "value": value,
                "state": state,
                "asOf": _day(latest["reference_date"]).isoformat() if latest else None,
                "sourceUri": latest.get("source_uri")
                if latest
                else {
                    "CHINABOND": "https://yield.chinabond.com.cn/",
                    "CFETS": "https://www.chinamoney.com.cn/",
                }.get(source_key),
                "sourceName": latest.get("source_name") if latest else None,
                "publishedAt": latest.get("published_at") if latest else None,
                "retrievedAt": latest.get("retrieved_at") if latest else None,
                "availableAt": latest.get("available_at") if latest else None,
                "timeBasis": latest.get("time_basis") if latest else None,
                "methodVersion": latest.get("method_version") if latest else None,
                "metadata": _metadata(latest) if latest else {},
                "changes": _changes(points, valid_latest, frequency, code=code),
                "history": [
                    {
                        "date": _day(p["reference_date"]).isoformat(),
                        "value": None
                        if _conflict(p, frequency, unit)
                        else _number(p.get("value")),
                        "state": "CONFLICT"
                        if _conflict(p, frequency, unit)
                        else "STALE"
                        if _metadata(p).get("qualityStatus") == "STALE"
                        else "OBSERVED",
                        "definitionKey": _metadata(p).get("definitionKey"),
                        "definitionVersion": _metadata(p).get("definitionVersion"),
                        "unit": p.get("unit"),
                        "sourceUri": p.get("source_uri"),
                        "sourceName": p.get("source_name"),
                        "retrievedAt": p.get("retrieved_at"),
                        "availableAt": p.get("available_at"),
                        "publishedAt": p.get("published_at"),
                        "timeBasis": p.get("time_basis"),
                    }
                    for p in points
                ],
            }
        )
    return output


async def read_permissions(conn):
    return {
        row["source_key"]: dict(row)
        for row in await conn.fetch(
            "SELECT * FROM reference.opportunity_source_permissions"
        )
    }


async def read_series(conn, now):
    return await conn.fetch(
        """SELECT DISTINCT ON(code,reference_date) * FROM market.opportunity_series_observations
      WHERE retrieved_at<=$1 AND available_at<=$1 AND (published_at IS NULL OR published_at<=$1)
      AND reference_date<=($1 AT TIME ZONE 'Asia/Shanghai')::date
      ORDER BY code,reference_date,retrieved_at DESC,revision DESC""",
        now,
    )


async def read_collection(conn, now):
    row = await conn.fetchrow(
        """SELECT a.result,a.recorded_at,a.phase,a.lease_until FROM market.macro_evidence_attempts a
      WHERE a.job_key='OPPORTUNITY_MARKET_EVIDENCE' AND a.recorded_at<=$1
      AND (a.phase IN ('FINISHED','CANCELLED') OR (a.phase='CLAIMED' AND a.lease_until>$1
        AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts t WHERE t.run_id=a.run_id AND t.phase IN ('FINISHED','CANCELLED') AND t.recorded_at<=$1)))
      ORDER BY a.recorded_at DESC,a.id DESC LIMIT 1""",
        now,
    )
    if not row:
        return {
            "collectedAt": None,
            "nextCheckAt": None,
            "collectionState": "NOT_COLLECTED",
            "sourceResults": {},
            "sourceAttemptAt": {},
        }
    terminal = row
    if row.get("phase") == "CLAIMED":
        terminal = await conn.fetchrow(
            """SELECT result,recorded_at,phase FROM market.macro_evidence_attempts
            WHERE job_key='OPPORTUNITY_MARKET_EVIDENCE' AND phase IN ('FINISHED','CANCELLED') AND recorded_at<=$1
            ORDER BY recorded_at DESC,id DESC LIMIT 1""",
            now,
        )
    raw = terminal["result"] if terminal else {}
    value = json.loads(raw) if isinstance(raw, str) else dict(raw)
    # Empty cancellation outcomes do not prove that an earlier failed source
    # recovered. Read each source's last terminal result without a history cap.
    outcomes = value.get("sourceResults") or {}
    sources = {
        key: result
        for key, result in outcomes.items()
        if key != "PUBLIC_SOURCES" and isinstance(result, dict)
    }
    source_attempts = {key: None for key in sources}
    source_rows = await conn.fetch(
        """WITH attempts AS (
            SELECT id,recorded_at,
              CASE WHEN jsonb_typeof(result->'sourceResults')='object'
                THEN result->'sourceResults' ELSE '{}'::jsonb END AS outcomes
            FROM market.macro_evidence_attempts
            WHERE job_key='OPPORTUNITY_MARKET_EVIDENCE'
              AND phase IN ('FINISHED','CANCELLED') AND recorded_at<=$1
          ), candidates AS (
            SELECT a.id,a.recorded_at,s.key AS source_key,s.value AS outcome,0 AS precedence
            FROM attempts a CROSS JOIN LATERAL jsonb_each(a.outcomes) s
            WHERE s.key<>'PUBLIC_SOURCES' AND jsonb_typeof(s.value)='object'
            UNION ALL
            SELECT a.id,a.recorded_at,s.source_key,
              a.outcomes->'PUBLIC_SOURCES' AS outcome,1 AS precedence
            FROM attempts a CROSS JOIN unnest(ARRAY[
              'UST_NOMINAL','UST_REAL','PBOC_M1','PBOC_TSF','PBOC_LOANS',
              'CPIAUCSL','CPILFESL','PPIACO','DFF','DGS2','DCOILBRENTEU'
            ]) AS s(source_key)
            WHERE a.outcomes->'PUBLIC_SOURCES'->>'status'='FAILED'
          )
          SELECT DISTINCT ON(source_key) source_key,outcome AS result,recorded_at
          FROM candidates
          ORDER BY source_key,recorded_at DESC,id DESC,precedence DESC""",
        now,
    )
    for source in source_rows:
        result = source["result"]
        sources[source["source_key"]] = (
            json.loads(result) if isinstance(result, str) else dict(result)
        )
        attempted = _stamp(source.get("recorded_at"))
        source_attempts[source["source_key"]] = (
            attempted if attempted is not None and attempted <= now else None
        )
    collected = terminal.get("recorded_at") if terminal else None
    deadline = _stamp(value.get("retryUntil"))
    next_check = row.get("lease_until") if row.get("phase") == "CLAIMED" else None
    if next_check is None and collected:
        next_check = collected + timedelta(hours=24)
        if deadline:
            next_check = (
                deadline
                if now >= deadline
                else min(
                    collected + timedelta(hours=1)
                    if value.get("status") not in {"COMPLETED", "SKIPPED"}
                    else next_check,
                    deadline,
                )
            )
    return {
        **value,
        "sourceResults": sources,
        "sourceAttemptAt": source_attempts,
        "collectedAt": collected,
        "nextCheckAt": next_check,
        "collectionState": row.get("phase", "UNKNOWN"),
    }


def normalize_series(record, now, *, permissions=None):
    code = record["code"]
    if code not in CATALOG or _stamp(now) is None:
        raise ValueError("unknown series or time")
    _, frequency, unit, source_key = CATALOG[code]
    if source_key in {"CHINABOND", "CFETS"} and not permission_allowed(
        (permissions or {}).get(source_key), now
    ):
        raise ValueError("source permission required")
    parsed = urlsplit(record["sourceUri"])
    roots = (
        ("treasury.gov", FRED_HOST)
        if code.startswith("US_T10")
        else (FRED_HOST,)
        if code in FRED_CATALOG
        else ("pbc.gov.cn",)
        if source_key.startswith("PBOC")
        else ("chinabond.com.cn",)
        if source_key == "CHINABOND"
        else ("chinamoney.com.cn",)
        if source_key == "CFETS"
        else ("sinajs.cn", "sina.com.cn", "eastmoney.com")
    )
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not any(
            parsed.hostname == r or (parsed.hostname or "").endswith("." + r)
            for r in roots
        )
    ):
        raise ValueError("untrusted series source")
    metadata = record.get("metadata", {})
    if (
        not isinstance(metadata, dict)
        or not _has_definition(metadata)
        or not record.get("methodVersion")
        or not record.get("sourceName")
    ):
        raise ValueError("missing series provenance")
    row = {
        "code": code,
        "reference_date": _day(record["referenceDate"]),
        "frequency": frequency,
        "unit": unit,
        "value": _number(record.get("value")),
        "source_uri": record["sourceUri"],
        "source_name": record["sourceName"],
        "published_at": _stamp(record.get("publishedAt")),
        "retrieved_at": _stamp(record["retrievedAt"]),
        "available_at": _stamp(record["availableAt"]),
        "time_basis": record["timeBasis"],
        "method_version": record["methodVersion"],
        "metadata": dict(metadata),
    }
    if not _visible(row, now) or (
        record.get("publishedAt") is not None and row["published_at"] is None
    ):
        raise ValueError("unavailable or future series fact")
    if row["time_basis"] == "RETRIEVAL_ONLY":
        if (
            row["available_at"] != row["retrieved_at"]
            or row["published_at"] is not None
        ):
            raise ValueError("retrieval-only availability mismatch")
    elif row["time_basis"] == "OFFICIAL_PUBLICATION":
        if row["published_at"] is None or row["available_at"] < row["published_at"]:
            raise ValueError("official availability requires publication")
    else:
        raise ValueError("unknown availability basis")
    if row["retrieved_at"] < row["available_at"]:
        raise ValueError("availability after retrieval")
    if frequency in {"MONTHLY", "YTD"} and row["reference_date"].day != 1:
        raise ValueError("monthly period requires month start")
    if (
        record.get("unit") != unit
        or record.get("frequency") != frequency
        or row["value"] is None
    ):
        row["value"] = None
        row["metadata"]["qualityStatus"] = "CONFLICT"
    canonical = {
        key: value
        for key, value in row.items()
        if key not in {"retrieved_at", "available_at"}
    }
    if canonical["value"] is not None:
        canonical["value"] = str(canonical["value"].normalize())
    row["canonical_hash"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()
    return row


async def append_series(conn, records, now, *, permissions=None):
    inserted, rejected = 0, 0
    async with conn.transaction():
        for record in records:
            try:
                row = normalize_series(record, now, permissions=permissions)
            except (KeyError, TypeError, ValueError, OverflowError):
                rejected += 1
                continue
            identity = (
                row["code"],
                row["reference_date"],
                row["source_uri"],
                row["method_version"],
            )
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"opportunity-series:{identity}",
            )
            latest = await conn.fetchrow(
                """SELECT revision,canonical_hash FROM market.opportunity_series_observations
              WHERE code=$1 AND reference_date=$2 AND source_uri=$3 AND method_version=$4 ORDER BY revision DESC LIMIT 1""",
                *identity,
            )
            if latest and latest["canonical_hash"] == row["canonical_hash"]:
                continue
            row["revision"] = (latest["revision"] if latest else 0) + 1
            row["metadata"] = json.dumps(
                row["metadata"], default=str, ensure_ascii=False
            )
            columns = list(row)
            parameters = ",".join(
                f"${i + 1}" + ("::jsonb" if col == "metadata" else "")
                for i, col in enumerate(columns)
            )
            await conn.execute(
                f"INSERT INTO market.opportunity_series_observations({','.join(columns)}) VALUES({parameters})",
                *row.values(),
            )
            inserted += 1
    return {"observations": inserted, "rejected": rejected}
