"""Durable public batches and source-specific projections; no private queries."""

from copy import deepcopy
from datetime import timedelta
import json
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from backend.macro.market_repository import _day, _number, _stamp

SHANGHAI = ZoneInfo("Asia/Shanghai")
METHOD = "alpha-public-v1"
ARRAYS = ("observations", "catalysts", "odds", "launches", "fundHistories")
LABELS = {
    "CN_T10": ("中国10年期国债收益率（东方财富）", "DAILY", "%", "AKSHARE_CN_T10"),
    "M1_YOY": ("M1同比（东方财富报告口径）", "MONTHLY", "%", "AKSHARE_MONEY_SUPPLY"),
    "M2_YOY": ("M2同比（东方财富）", "MONTHLY", "%", "AKSHARE_MONEY_SUPPLY"),
    "M1_M2_GAP": (
        "M1−M2同比剪刀差",
        "MONTHLY",
        "percentage_points",
        "AKSHARE_MONEY_SUPPLY",
    ),
    "SHIBOR_7D": ("SHIBOR一周利率（东方财富）", "DAILY", "%", "AKSHARE_SHIBOR_7D"),
    "TSF_STOCK_YOY": (
        "社融存量同比（央行报告官方转载）",
        "MONTHLY",
        "%",
        "GOV_SH_PBOC_TSF",
    ),
}
for _city, _name, _source in (
    ("HAIKOU", "海口", "HAINAN"),
    ("SANYA", "三亚", "HAINAN"),
    ("SHANGHAI", "上海", "MAINLAND"),
    ("SHENZHEN", "深圳", "MAINLAND"),
):
    for _kind, _kind_name in (("NEW", "新房"), ("SECOND", "二手房")):
        LABELS[f"HOUSE_{_city}_{_kind}_MOM"] = (
            f"{_name}{_kind_name}价格环比",
            "MONTHLY",
            "%",
            f"AKSHARE_HOUSE_{_source}",
        )


def local(now):
    stamp = _stamp(now)
    if stamp is None:
        raise ValueError("alpha collection requires aware time")
    return stamp.astimezone(SHANGHAI)


def empty_public():
    return {
        "methodVersion": METHOD,
        "asOf": None,
        **{key: [] for key in ARRAYS},
        "sourceResults": {},
        "themeNavSourceResults": {},
    }


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return deepcopy(value) if isinstance(value, dict) else {}


async def claim_alpha_batch(conn, now, *, debug_request_id=None):
    stamp = local(now)
    if debug_request_id is not None:
        return await conn.fetchrow(
            """INSERT INTO market.alpha_public_batches(run_date,claimed_at,status,run_mode,debug_request_id)
            VALUES($1,$2,'CLAIMED','DEBUG',$3)
            ON CONFLICT(debug_request_id) WHERE run_mode='DEBUG' DO NOTHING RETURNING *""",
            stamp.date(),
            stamp,
            debug_request_id,
        )
    return await conn.fetchrow(
        """INSERT INTO market.alpha_public_batches(run_date,claimed_at,status)
        VALUES($1,$2,'CLAIMED') ON CONFLICT(run_date) WHERE run_mode='SCHEDULED' DO NOTHING RETURNING *""",
        stamp.date(),
        stamp,
    )


async def finish_alpha_batch(conn, identity, now, status, payload):
    if status not in {"READY", "PARTIAL", "FAILED"}:
        raise ValueError("invalid alpha batch terminal status")
    await conn.execute(
        """UPDATE market.alpha_public_batches SET status=$2,completed_at=$3,payload=$4::jsonb
        WHERE id=$1 AND status='CLAIMED' AND claimed_at<=$3""",
        identity,
        status,
        local(now),
        json.dumps(payload, ensure_ascii=False, allow_nan=False),
    )


async def read_alpha_public(conn, now, *, debug_request_id=None):
    stamp = local(now)
    exact = " AND debug_request_id=$3" if debug_request_id is not None else ""
    arguments = (
        (stamp.date(), stamp, debug_request_id)
        if debug_request_id is not None
        else (stamp.date(), stamp)
    )
    latest = await conn.fetchrow(
        """SELECT * FROM market.alpha_public_batches
        WHERE run_date<=$1 AND claimed_at<=$2"""
        + exact
        + " ORDER BY run_date DESC,claimed_at DESC,id DESC LIMIT 1",
        *arguments,
    )
    ready = await conn.fetchrow(
        """SELECT * FROM market.alpha_public_batches WHERE run_date<=$1
        AND claimed_at<=$2 AND completed_at<=$2 AND status IN ('READY','PARTIAL')
        """
        + exact
        + " ORDER BY run_date DESC,completed_at DESC,id DESC LIMIT 1",
        *arguments,
    )
    result = empty_public()
    if (
        ready
        and _stamp(ready.get("completed_at")) is not None
        and _stamp(ready["completed_at"]) <= stamp
        and _stamp(ready.get("claimed_at")) is not None
        and _stamp(ready["claimed_at"]) <= stamp
        and _day(ready.get("run_date")) is not None
        and _day(ready["run_date"]) <= stamp.date()
    ):
        payload = _object(ready.get("payload"))
        for key in ARRAYS:
            result[key] = payload.get(key) if isinstance(payload.get(key), list) else []
        for key in ("sourceResults", "themeNavSourceResults"):
            result[key] = payload.get(key) if isinstance(payload.get(key), dict) else {}
        result["methodVersion"] = payload.get("methodVersion", METHOD)
        result["asOf"] = payload.get("asOf")
        result["collectionAsOf"] = _stamp(ready["completed_at"]).isoformat()
        result["cacheRunDate"] = _day(ready["run_date"]).isoformat()
        result["batchId"] = str(ready["id"])
    else:
        ready = None
        result.update(collectionAsOf=None, cacheRunDate=None)
    attempt = None
    if (
        latest
        and _stamp(latest.get("claimed_at")) is not None
        and _stamp(latest["claimed_at"]) <= stamp
        and _day(latest.get("run_date")) is not None
        and _day(latest["run_date"]) <= stamp.date()
    ):
        completed = _stamp(latest.get("completed_at"))
        terminal_visible = completed is not None and completed <= stamp
        state = latest.get("status") if terminal_visible else "CLAIMED"
        attempt = {
            "runDate": _day(latest["run_date"]).isoformat(),
            "claimedAt": _stamp(latest["claimed_at"]).isoformat(),
            "completedAt": completed.isoformat() if terminal_visible else None,
            "status": state,
        }
        if terminal_visible and state == "FAILED":
            error = _object(latest.get("payload")).get("errorClass")
            if isinstance(error, str) and error.isidentifier() and len(error) <= 80:
                attempt["errorClass"] = error
    today = attempt and attempt["runDate"] == stamp.date().isoformat()
    result["collectionState"] = (
        ("COLLECTING" if attempt["status"] == "CLAIMED" else attempt["status"])
        if today
        else "STALE"
        if ready
        else "NOT_COLLECTED"
    )
    result["lastAttempt"] = attempt
    return result


def _basis(row):
    metadata = row.get("metadata") or {}
    version = metadata.get("definitionVersion") or "ALPHA_REPORTED_V1"
    if row["code"] in {"M1_YOY", "M1_M2_GAP"} and version == "EASTMONEY_REPORTED":
        day = _day(row.get("referenceDate"))
        version += ":2025_ONWARD" if day and day.year >= 2025 else ":BEFORE_2025"
    return (
        row.get("sourceKey"),
        row.get("sourceUri"),
        metadata.get("definitionKey") or row["code"],
        version,
        row.get("frequency"),
        row.get("unit"),
    )


def _valid_source(row, expected):
    try:
        parts = urlsplit(row.get("sourceUri") or "")
        host = (
            "jrj.sh.gov.cn" if expected == "GOV_SH_PBOC_TSF" else "data.eastmoney.com"
        )
        return (
            row.get("sourceKey") == expected
            and parts.scheme == "https"
            and parts.hostname == host
            and not parts.username
            and not parts.password
            and parts.port in {None, 443}
        )
    except ValueError:
        return False


def _point(row, label, now, bundle):
    _, frequency, unit, expected = label
    day, value = _day(row.get("referenceDate")), _number(row.get("value"))
    state, reason = "OBSERVED", None
    if (
        day is None
        or day > now.date()
        or any(
            _stamp(row.get(key)) is None or _stamp(row[key]) > now
            for key in ("retrievedAt", "availableAt")
        )
        or row.get("publishedAt") is not None
        and (_stamp(row["publishedAt"]) is None or _stamp(row["publishedAt"]) > now)
    ):
        state, reason = (
            "UNVERIFIED",
            "观察日期或可得/抓取时刻无效、未知或晚于研究截止。",
        )
    elif value is None or row.get("frequency") != frequency or row.get("unit") != unit:
        state, reason = "CONFLICT", "数值、频率或单位与指标口径不一致。"
    elif not _valid_source(row, expected):
        state, reason = "UNVERIFIED", "标的指标与供应商来源尚未对应核验。"
    elif (bundle.get("sourceResults", {}).get(expected) or {}).get(
        "status"
    ) != "SUCCESS":
        state, reason = (
            (bundle.get("sourceResults", {}).get(expected) or {}).get("status")
            or "UNAVAILABLE",
            "本源最近采集未成功，未将旧值认证为当前事实。",
        )
    elif now.date() > day + timedelta(
        days=5 if frequency == "DAILY" else 45
    ) or bundle.get("collectionState") in {"FAILED", "COLLECTING", "STALE"}:
        state, reason = "STALE", "保留实际历史观察；当前采集或指标观察期已陈旧。"
    basis = _basis(row)
    valid_value = value if state in {"OBSERVED", "STALE"} else None
    return {
        "date": day.isoformat() if day else None,
        "value": float(valid_value) if valid_value is not None else None,
        "state": state,
        "reason": reason,
        "definitionKey": basis[2],
        "definitionVersion": basis[3],
        "unit": row.get("unit"),
        "sourceUri": row.get("sourceUri"),
        "sourceName": row.get("sourceName"),
        "sourceKey": row.get("sourceKey"),
        "retrievedAt": row.get("retrievedAt"),
        "availableAt": row.get("availableAt"),
        "publishedAt": row.get("publishedAt"),
        "timeBasis": row.get("timeBasis"),
        "_basis": basis,
        "_number": valid_value,
        "_raw": row,
    }


def _changes(points, frequency, code):
    unit = "bp" if frequency == "DAILY" and code in {"CN_T10", "SHIBOR_7D"} else "pp"
    result = {
        "sampleCount": 0,
        "previousValue": None,
        "periodChange": None,
        "changeUnit": unit,
        "change3Months": None,
        "change6Months": None,
    }
    if not points or points[-1]["_number"] is None:
        return result
    latest = points[-1]
    comparable = [
        point
        for point in points
        if point["_basis"] == latest["_basis"] and point["_number"] is not None
    ]
    result["sampleCount"] = len(comparable)
    if len(points) >= 2:
        previous = points[-2]
        prior, current = _day(previous["date"]), _day(latest["date"])
        adjacent = (
            prior is not None
            and current is not None
            and (
                (current - prior).days == 1
                if frequency == "DAILY"
                else current.year * 12 + current.month - prior.year * 12 - prior.month
                == 1
            )
        )
        if adjacent and previous in comparable:
            result["previousValue"] = previous["value"]
            delta = (latest["_number"] - previous["_number"]) * (
                100 if unit == "bp" else 1
            )
            number = _number(delta)
            result["periodChange"] = float(number) if number is not None else None
    if frequency == "MONTHLY":
        months = {
            _day(point["date"]).year * 12 + _day(point["date"]).month: point
            for point in comparable
        }
        end = _day(latest["date"]).year * 12 + _day(latest["date"]).month
        for distance in (3, 6):
            if all(period in months for period in range(end - distance, end + 1)):
                delta = _number(latest["_number"] - months[end - distance]["_number"])
                result[f"change{distance}Months"] = (
                    float(delta) if delta is not None else None
                )
    return result


def merge_alpha_series(existing_rows, bundle, now):
    stamp = local(now)
    result = deepcopy(list(existing_rows))
    grouped = {code: [] for code in LABELS}
    for raw in (bundle or {}).get("observations", []):
        if isinstance(raw, dict) and raw.get("code") in LABELS:
            grouped[raw["code"]].append(dict(raw))
    for code, label in LABELS.items():
        title, frequency, unit, source = label
        points = sorted(
            (_point(row, label, stamp, bundle or {}) for row in grouped[code]),
            key=lambda point: (
                point["date"] or "",
                _stamp(point.get("retrievedAt")).timestamp()
                if _stamp(point.get("retrievedAt")) is not None
                else float("-inf"),
            ),
        )
        unique = {}
        for point in points:
            identity = point["date"], point["_basis"]
            previous = unique.get(identity)
            if (
                previous is not None
                and _stamp(previous["retrievedAt"]) == _stamp(point["retrievedAt"])
                and previous["_number"] != point["_number"]
            ):
                point.update(
                    state="CONFLICT",
                    value=None,
                    _number=None,
                    reason="同一日期、来源、定义与抓取时点存在冲突观察，未选择其中一个值。",
                )
            unique[identity] = point
        points = list(unique.values())
        latest = points[-1] if points else None
        source_status = ((bundle or {}).get("sourceResults", {}).get(source) or {}).get(
            "status"
        )
        state = (
            "READY"
            if latest and latest["state"] == "OBSERVED"
            else latest["state"]
            if latest
            else source_status
            if source_status
            in {
                "FAILED",
                "TIMEOUT",
                "RATE_LIMITED",
                "PERMISSION_REQUIRED",
                "UNAVAILABLE",
            }
            else "NOT_COLLECTED"
        )
        metadata = deepcopy((latest["_raw"].get("metadata") or {}) if latest else {})
        metadata.update(
            sourceKey=source,
            definitionKey=latest["definitionKey"] if latest else code,
            definitionVersion=latest["definitionVersion"] if latest else None,
        )
        row = {
            "code": code,
            "label": title,
            "frequency": frequency,
            "unit": unit,
            "value": latest["value"] if latest else None,
            "asOf": latest["date"]
            if latest and latest["state"] not in {"UNVERIFIED", "CONFLICT"}
            else None,
            "state": state,
            "sourceUri": latest.get("sourceUri") if latest else None,
            "sourceName": latest.get("sourceName") if latest else None,
            "publishedAt": latest.get("publishedAt") if latest else None,
            "retrievedAt": latest.get("retrievedAt") if latest else None,
            "availableAt": latest.get("availableAt") if latest else None,
            "timeBasis": latest.get("timeBasis") if latest else None,
            "methodVersion": latest["_raw"].get("methodVersion") if latest else METHOD,
            "metadata": metadata,
            "reason": latest.get("reason")
            if latest
            else "该公开供应商指标尚未取得可用观察；其他来源权限状态保持独立。",
            "history": [
                {key: value for key, value in point.items() if not key.startswith("_")}
                for point in points
            ],
            "changes": _changes(points, frequency, code),
        }
        index = next(
            (index for index, prior in enumerate(result) if prior.get("code") == code),
            None,
        )
        if index is None:
            result.append(row)
        elif result[index].get("state") != "READY" and row["state"] == "READY":
            row["previousEvidence"] = {
                key: result[index].get(key)
                for key in ("state", "sourceUri", "sourceName", "metadata")
            }
            result[index] = row
        elif result[index].get("state") != "READY":
            result[index]["alphaFallback"] = {
                "state": row["state"],
                "reason": row["reason"],
                "sourceKey": source,
            }
    return result
