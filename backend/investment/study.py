"""Frozen personal research and deterministic observations, not trade authority."""

from __future__ import annotations

import calendar
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VERSION = "study-v1"
TZ = ZoneInfo("Asia/Shanghai")
CATALOG = {
    "PMI": {"label": "制造业PMI", "unit": "指数点", "maxAgeDays": 75},
    "CPI_YOY": {"label": "CPI同比", "unit": "%", "maxAgeDays": 75},
    "PPI_YOY": {"label": "PPI同比", "unit": "%", "maxAgeDays": 75},
    "M2_YOY": {"label": "M2同比", "unit": "%", "maxAgeDays": 75},
    "US_T10_NOMINAL": {"label": "美国10年名义利率", "unit": "%", "maxAgeDays": 10},
    "US_T10_REAL": {"label": "美国10年实际利率", "unit": "%", "maxAgeDays": 10},
    "DCOILBRENTEU": {"label": "布伦特现货", "unit": "USD/barrel", "maxAgeDays": 10},
    "GASDESW": {"label": "美国零售柴油", "unit": "USD/gallon", "maxAgeDays": 21},
    "INDEX_MA200_GAP": {"label": "指数偏离MA200", "unit": "%", "maxAgeDays": 14},
    "INDEX_MOMENTUM63": {"label": "指数63期动量", "unit": "%", "maxAgeDays": 14},
    "INDEX_PE": {"label": "指数滚动PE", "unit": "倍", "maxAgeDays": 14},
}
INDICES = {"hs300", "a500", "zz500"}


class StudyCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    metric: str
    subject_key: str | None = Field(default=None, alias="subjectKey")
    operator: Literal["GT", "GTE", "LT", "LTE"]
    threshold: Decimal
    effect: Literal["STRENGTHEN", "WEAKEN", "TRIGGER", "INVALIDATE"] = "TRIGGER"
    unit: str | None = None

    @field_validator("threshold", mode="before")
    @classmethod
    def finite_threshold(cls, value):
        if isinstance(value, bool) or len(str(value)) > 40:
            raise ValueError("finite numeric threshold required")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("finite numeric threshold required") from exc
        if not number.is_finite() or abs(number) > Decimal("1e12"):
            raise ValueError("finite bounded threshold required")
        return number

    @model_validator(mode="after")
    def validate_identity(self):
        if self.metric not in CATALOG:
            raise ValueError("unsupported research metric")
        if self.metric.startswith("INDEX_"):
            if self.subject_key not in INDICES:
                raise ValueError("a verified index identity is required")
        elif self.subject_key is not None:
            raise ValueError("this public series has no index subject")
        expected = CATALOG[self.metric]["unit"]
        if self.unit is not None and self.unit != expected:
            raise ValueError("condition unit does not match metric")
        self.unit = expected
        return self


def _now(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("timezone-aware study clock required")
    return value.astimezone(TZ)


def _stamp(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return _now(parsed)
    except (ValueError, TypeError):
        return None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and abs(number) <= Decimal("1e18") else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _day(value):
    try:
        value = str(value)
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            return date(year, month, calendar.monthrange(year, month)[1])
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _json(value):
    return json.loads(
        json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)
    )


def _strings(values, limit=8):
    return (
        [
            text[:2000]
            for text in values[:limit]
            if isinstance(text, str) and text.strip()
        ]
        if isinstance(values, list)
        else []
    )


def freeze_hypothesis(origin, *, now, horizon_days=30, scope=None):
    now = _now(now)
    if type(horizon_days) is not int or not 1 <= horizon_days <= 365:
        raise ValueError("observation horizon must be 1..365 calendar days")
    event = origin.get("event") or origin
    conditions = [
        StudyCondition.model_validate(row).model_dump(by_alias=True, mode="json")
        for row in origin.get("conditions", [])[:8]
    ]
    snapshot = {
        "methodVersion": VERSION,
        "phase": event.get("phase"),
        "title": str(event.get("title") or origin.get("title") or "当前方向研究")[:200],
        "thesis": str(
            event.get("summary")
            or origin.get("summary")
            or "保留当前观察，继续核对研究条件。"
        )[:2000],
        "transmission": _strings(
            event.get("channels") or origin.get("transmission") or []
        ),
        "supports": _strings(origin.get("supports") or []),
        "counterFactors": _strings(
            origin.get("counterFactors")
            or [
                row.get("counter")
                for row in event.get("scenarios", [])
                if isinstance(row, dict)
            ]
        ),
        "watchFor": _strings(event.get("watchFor") or origin.get("watchFor") or []),
        "invalidationConditions": _strings(
            event.get("invalidationConditions")
            or origin.get("invalidationConditions")
            or []
        ),
        "scenarios": [
            {
                key: str(row.get(key) or "")[:2000]
                for key in ("condition", "impact", "counter")
            }
            for row in event.get("scenarios", [])[:3]
            if isinstance(row, dict)
        ],
        "sources": [
            {
                key: deepcopy(row[key])
                for key in (
                    "id",
                    "url",
                    "title",
                    "quote",
                    "publishedDate",
                    "publishedAt",
                    "retrievedAt",
                    "verification",
                    "contentHash",
                )
                if key in row
            }
            for row in origin.get("sources", [])[:16]
        ],
        "origin": {
            "originType": origin.get("originType", "SELECTION"),
            **{
                key: origin.get(key) for key in ("reportRunId", "eventId", "reportAsOf")
            },
        },
        "scope": {
            key: value
            for key, value in (
                scope or origin.get("scope") or origin.get("selection") or {}
            ).items()
            if key
            in {"directionId", "indexId", "instrumentId", "fundCode", "fundMarket"}
            and value is not None
        },
        "createdAt": now.isoformat(),
        "expiresOn": (now.date() + timedelta(days=horizon_days)).isoformat(),
        "conditions": conditions,
        "observations": [
            public_observation(row) for row in origin.get("observations", [])[:32]
        ],
    }
    snapshot["benchmarkIndexId"] = (
        snapshot["scope"].get("indexId")
        if snapshot["scope"].get("indexId") in INDICES
        else None
    )
    return _json(snapshot)


def public_observation(row):
    result = {
        key: row[key]
        for key in (
            "metric",
            "subjectKey",
            "label",
            "unit",
            "asOf",
            "retrievedAt",
            "availableAt",
            "publishedAt",
            "sourceUri",
            "verification",
            "state",
            "frequency",
        )
        if key in row
    }
    value = _number(row.get("value"))
    result["value"] = (
        ("0" if value == 0 else format(value.normalize(), "f"))
        if value is not None
        else None
    )
    return _json(result)


def _key(row):
    return row.get("metric"), row.get("subjectKey")


def _fact(row, now):
    code = row.get("metric")
    if (
        code not in CATALOG
        or row.get("state") != "READY"
        or row.get("verification") != "VERIFIED"
    ):
        return None
    number, day, retrieved = (
        _number(row.get("value")),
        _day(row.get("asOf")),
        _stamp(row.get("retrievedAt")),
    )
    if (
        number is None
        or day is None
        or day > now.date()
        or (now.date() - day).days > CATALOG[code]["maxAgeDays"]
    ):
        return None
    if row.get("unit") != CATALOG[code]["unit"] or retrieved is None or retrieved > now:
        return None
    for field in ("availableAt", "publishedAt"):
        if row.get(field) is not None and (
            _stamp(row[field]) is None or _stamp(row[field]) > now
        ):
            return None
    uri = urlsplit(row.get("sourceUri") or "")
    if uri.scheme != "https" or not uri.hostname or uri.username or uri.password:
        return None
    if code.startswith("INDEX_") and row.get("subjectKey") not in INDICES:
        return None
    return number


def _indexed(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(_key(row), []).append(row)
    return grouped


def _one(rows):
    if not rows:
        return None
    signatures = {
        (
            str(row.get("value")),
            row.get("asOf"),
            row.get("unit"),
            row.get("state"),
            row.get("verification"),
        )
        for row in rows
    }
    return rows[0] if len(signatures) == 1 else None


def evaluate_conditions(version, previous, current, *, now):
    now = _now(now)
    conditions = [
        StudyCondition.model_validate(row) for row in version.get("conditions", [])
    ]
    current = [public_observation(row) for row in current[:32]]
    facts = _indexed(current)
    previous_facts = _indexed((previous or {}).get("observations") or [])
    prior_results = {
        row["id"]: row for row in (previous or {}).get("conditionResults", [])
    }
    expired = _day(version.get("expiresOn")) is not None and now.date() > _day(
        version["expiresOn"]
    )
    results, effects = [], []
    operations = {
        "GT": lambda a, b: a > b,
        "GTE": lambda a, b: a >= b,
        "LT": lambda a, b: a < b,
        "LTE": lambda a, b: a <= b,
    }
    for index, condition in enumerate(conditions):
        identity = f"condition-{index + 1}"
        key = condition.metric, condition.subject_key
        prior_row = prior_results.get(identity, {})
        row = _one(facts.get(key))
        old = prior_row.get("lastKnownObservation") or _one(previous_facts.get(key))
        value = _fact(row, now) if row else None
        regressed = (
            old is not None
            and row is not None
            and _day(old.get("asOf")) is not None
            and (
                _day(row.get("asOf")) is None
                or _day(row.get("asOf")) < _day(old["asOf"])
            )
        )
        result = (
            "NOT_DUE"
            if expired
            else "UNKNOWN"
            if value is None or regressed
            else "TRUE"
            if operations[condition.operator](value, condition.threshold)
            else "FALSE"
        )
        prior = prior_row.get("result")
        if prior not in {"TRUE", "FALSE"}:
            prior = prior_row.get("lastKnownResult")
        if (
            prior in {"TRUE", "FALSE"}
            and result in {"TRUE", "FALSE"}
            and result != prior
        ):
            if result == "TRUE":
                effects.append(condition.effect)
            elif condition.effect == "STRENGTHEN":
                effects.append("WEAKEN")
        results.append(
            {
                "id": identity,
                "metric": condition.metric,
                "subjectKey": condition.subject_key,
                "result": result,
                "previousResult": prior,
                "value": float(value) if value is not None and not regressed else None,
                "unit": condition.unit,
                "asOf": row.get("asOf") if row else None,
                "reason": "OBSERVATION_PERIOD_ENDED"
                if expired
                else "EVIDENCE_UNAVAILABLE"
                if result == "UNKNOWN"
                else "BASELINE"
                if prior not in {"TRUE", "FALSE"}
                else "PREDECLARED_CONDITION",
                "effect": condition.effect,
                "lastKnownResult": result if result in {"TRUE", "FALSE"} else prior,
                "lastKnownObservation": row if result in {"TRUE", "FALSE"} else old,
            }
        )
    quality = (
        "MANUAL_REVIEW_REQUIRED"
        if not conditions
        else "UNKNOWN"
        if any(row["result"] == "UNKNOWN" for row in results)
        else "READY"
    )
    state = (previous or {}).get("state", "OBSERVING")
    if expired:
        state = "ENDED"
    elif state != "INVALIDATED":
        for effect, mapped in (
            ("INVALIDATE", "INVALIDATED"),
            ("WEAKEN", "WEAKENED"),
            ("TRIGGER", "TRIGGERED"),
            ("STRENGTHEN", "STRENGTHENED"),
        ):
            if effect in effects:
                state = mapped
                break
    hash_input = {
        "methodVersion": VERSION,
        "conditions": [
            row.model_dump(by_alias=True, mode="json") for row in conditions
        ],
        "observations": sorted(
            [
                {
                    key: row.get(key)
                    for key in (
                        "metric",
                        "subjectKey",
                        "value",
                        "unit",
                        "asOf",
                        "sourceUri",
                        "verification",
                        "state",
                    )
                }
                for row in current
            ],
            key=lambda row: (
                row.get("metric") or "",
                row.get("subjectKey") or "",
                str(row.get("value")),
            ),
        ),
        "expired": expired,
        "quality": quality,
    }
    digest = hashlib.sha256(
        json.dumps(hash_input, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    return {
        "methodVersion": VERSION,
        "state": state,
        "quality": quality,
        "baseline": previous is None,
        "hasChange": previous is None or previous.get("inputHash") != digest,
        "inputHash": digest,
        "checkedAt": now.isoformat(),
        "stateAsOf": now.isoformat()
        if effects or previous is None or expired
        else previous.get("stateAsOf"),
        "observations": current,
        "conditionResults": results,
    }


def review_evidence(version, baseline, current, trades, *, now):
    now = _now(now)
    before, after = _indexed(baseline), _indexed(current)
    comparisons = []
    for key in sorted(before.keys() | after.keys(), key=str):
        old, row = _one(before.get(key)), _one(after.get(key))
        # Frozen observations are evaluated at their original retrieval time;
        # this never relabels historical evidence as current or proves PIT.
        old_clock = _stamp(old.get("retrievedAt")) if old else None
        frozen_at = _stamp(version.get("createdAt"))
        initial = (
            _fact(old, old_clock)
            if old and old_clock and frozen_at and old_clock <= frozen_at <= now
            else None
        )
        latest = _fact(row, now) if row else None
        comparable = (
            initial is not None
            and latest is not None
            and _day(row["asOf"]) >= _day(old["asOf"])
        )
        comparisons.append(
            {
                "metric": key[0],
                "subjectKey": key[1],
                "initialValue": float(initial) if initial is not None else None,
                "currentValue": float(latest) if latest is not None else None,
                "initialAsOf": old.get("asOf") if old else None,
                "currentAsOf": row.get("asOf") if row else None,
                "unit": CATALOG.get(key[0], {}).get("unit"),
                "change": float(latest - initial) if comparable else None,
                "state": "COMPARABLE" if comparable else "UNKNOWN",
            }
        )
    confirmed = [
        {
            key: row[key]
            for key in (
                "fillId",
                "side",
                "tradeDate",
                "instrumentId",
                "quantity",
                "price",
                "fees",
                "currency",
                "linkedAt",
            )
            if key in row
        }
        for row in trades
        if row.get("status") == "CONFIRMED" and row.get("accessVerified") is True
    ]
    return {
        "methodVersion": VERSION,
        "asOf": now.isoformat(),
        "observations": comparisons,
        "transactions": _json(confirmed),
        "performance": {
            "state": "UNKNOWN",
            "netReturn": None,
            "excessReturn": None,
            "maxDrawdown": None,
            "opportunityCost": None,
            "reasons": [
                "缺少完整持有期现金流、分红、成本及可比基准证据，不能认证净收益或超额收益。"
            ],
        },
        "executionTimePrecision": "DAY",
        "causalAttribution": "UNVERIFIED",
    }
