"""Structured model reasoning selects deterministic public price coordinates."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import math

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

THEMES = {
    "HAINAN_FTP",
    "PUDONG_DEV",
    "GEO_CRUDE_OIL",
    "AI_MEMORY_HBM",
    "COMMERCIAL_SPACE",
}


class ActionableProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    themeCode: Literal[
        "HAINAN_FTP", "PUDONG_DEV", "GEO_CRUDE_OIL", "AI_MEMORY_HBM", "COMMERCIAL_SPACE"
    ]
    subjectKind: Literal["index", "fund", "nav"]
    subjectId: str = Field(min_length=1, max_length=100)
    track: Literal["LEFT_VALUE", "RIGHT_TREND"]
    priceRevisionStatus: Literal[
        "INITIAL", "UPGRADED", "DOWNGRADED", "TAKE_PROFIT_ALERT"
    ]
    priceRevisionNote: str = Field(min_length=1, max_length=1000)
    calibrationRuleKeys: list[str] = Field(max_length=16)
    calibrationNote: str = Field(max_length=1000)


def _identity(row):
    return row.get("themeCode"), row.get("subjectKind"), row.get("subjectId")


def _price(value):
    if value is None or isinstance(value, bool):
        raise ValueError("invalid program price")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or not math.isfinite(float(number)):
            raise ValueError("invalid program price")
        return number
    except (TypeError, InvalidOperation, OverflowError) as exc:
        raise ValueError("invalid program price") from exc


def _program_plan(plan, track_name, now, *, require_theme=True):
    from backend.macro.daily_research import _url, _visible

    if not isinstance(plan, dict) or not _visible(plan, now):
        raise ValueError("unavailable program plan")
    try:
        day = date.fromisoformat(plan["asOf"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid program date") from exc
    if (
        day > now.date()
        or not plan.get("methodVersion")
        or require_theme
        and _identity(plan)[0] not in THEMES
    ):
        raise ValueError("unknown program plan")
    _url(plan.get("sourceUri"))
    kind, basis, unit = (
        plan.get("subjectKind"),
        plan.get("priceBasis"),
        plan.get("unit"),
    )
    if kind in {"nav", "fund"}:
        if basis != "UNIT_NAV" or unit not in {"CNY", "CNY/份", "元/份", "CNY/unit"}:
            raise ValueError("unsupported fund program coordinate")
    elif (
        kind != "index"
        or basis not in {"INDEX_POINTS", "INDEX_PRICE"}
        or unit not in {"points", "index_point", "指数点"}
    ):
        raise ValueError("unsupported index program coordinate")
    current = _price(plan.get("currentPrice"))
    tracks = [
        item
        for item in plan.get("tracks", [])
        if isinstance(item, dict) and item.get("track") == track_name
    ]
    if len(tracks) != 1:
        raise ValueError("ambiguous program track")
    track = tracks[0]
    if track.get("state") not in {"CANDIDATE", "WATCH"}:
        raise ValueError("unavailable program track")
    low, high = _price(track.get("entryZoneMin")), _price(track.get("entryZoneMax"))
    if low > high:
        raise ValueError("invalid program range")
    stop, first, second = (
        track.get(key) for key in ("stopLossPrice", "takeProfitT1", "takeProfitT2")
    )
    if stop is not None and _price(stop) >= low:
        raise ValueError("invalid program stop")
    if first is not None and _price(first) <= high:
        raise ValueError("invalid program target")
    if second is not None and (first is None or _price(second) <= _price(first)):
        raise ValueError("invalid program target order")
    return day, current, track


def validate_actionable_plans(proposals, packet, now):
    anchors = {_identity(row): row for row in packet.get("themeActionableZones", [])}
    rules = {row["key"]: row for row in packet.get("strategyMemoryRules", [])}
    previous = {
        (*_identity(row), row.get("selectedTrack", row.get("track"))): row
        for row in packet.get("previousActionablePlans", [])
    }
    output, seen = [], set()
    for raw in proposals:
        row = ActionableProposal.model_validate(raw).model_dump()
        key = (*_identity(row), row["track"])
        if key in seen:
            raise ValueError("duplicate model plan")
        seen.add(key)
        anchor = anchors.get(_identity(row))
        if not anchor:
            raise ValueError("unknown or future price anchor")
        day, current, track = _program_plan(anchor, row["track"], now)
        if any(
            key not in rules or rules[key].get("themeCode") != row["themeCode"]
            for key in row["calibrationRuleKeys"]
        ):
            raise ValueError("unknown calibration rule")
        old = previous.get(key)
        if row["priceRevisionStatus"] == "INITIAL" and old:
            raise ValueError("initial plan has previous version")
        if row["priceRevisionStatus"] != "INITIAL" and not old:
            raise ValueError("revision has no previous version")
        if old:
            old_day, _, old_track = _program_plan(old, row["track"], now)
            if old_day > day:
                raise ValueError("previous price anchor is later than current")
        if row["priceRevisionStatus"] == "TAKE_PROFIT_ALERT":
            try:
                proven = (
                    old.get("priceBasis") == anchor.get("priceBasis")
                    and old.get("unit") == anchor.get("unit")
                    and old.get("sourceUri") == anchor.get("sourceUri")
                    and current >= _price(old_track["takeProfitT1"])
                )
            except (KeyError, ValueError, InvalidOperation, TypeError):
                proven = False
            if not proven:
                raise ValueError("take profit lacks same-basis observed price proof")
        output.append(
            {
                **deepcopy(anchor),
                **row,
                "selectedTrack": row["track"],
                "orderPriceAvailable": False,
            }
        )
    return output


def extend_public_packet(bundle, themes, anchors, rules, previous, now):
    """Only explicitly public source fields and server-selected identities travel."""
    from backend.macro.daily_research import (
        _pick,
        _url,
        _visible,
        _texts,
        _public_actionable_plan,
    )

    public = {
        "asOf": bundle.get("asOf"),
        "sourceResults": {},
        "observations": [],
        "catalysts": [],
        "odds": [],
        "launches": [],
    }
    for key, row in bundle.get("sourceResults", {}).items():
        public["sourceResults"][key] = _pick(
            row, ("status", "errorClass", "records", "retrievedAt", "retryAfter")
        )
        public["sourceResults"][key]["limitations"] = _texts(row.get("limitations"), 8)
        if isinstance(row.get("coverage"), dict):
            public["sourceResults"][key]["coverage"] = _pick(
                row["coverage"],
                (
                    "scannedCount",
                    "retainedCount",
                    "pages",
                    "windowStart",
                    "windowEnd",
                    "oldestPublishedAt",
                    "truncated",
                    "stopReason",
                ),
            )
    fields = {
        "observations": (
            "code",
            "referenceDate",
            "frequency",
            "value",
            "unit",
            "sourceKey",
            "sourceUri",
            "sourceName",
            "publishedAt",
            "retrievedAt",
            "availableAt",
            "timeBasis",
            "methodVersion",
        ),
        "catalysts": (
            "id",
            "title",
            "content",
            "phase",
            "importance",
            "factVerification",
            "asOf",
            "publishedAt",
            "retrievedAt",
            "availableAt",
            "sourceKey",
            "sourceUri",
        ),
        "odds": (
            "marketId",
            "question",
            "outcome",
            "price",
            "impliedProbabilityPct",
            "priceKind",
            "quoteTimestamp",
            "sourceUpdatedAt",
            "marketEndAt",
            "observedAt",
            "asOf",
            "retrievedAt",
            "availableAt",
            "sourceKey",
            "sourceUri",
        ),
        "launches": (
            "id",
            "name",
            "phase",
            "scheduledAt",
            "windowStart",
            "windowEnd",
            "asOf",
            "retrievedAt",
            "availableAt",
            "sourceKey",
            "sourceUri",
        ),
    }
    for name, keys in fields.items():
        rows = bundle.get(name, [])
        if name == "observations":
            from backend.macro.alpha_repository import LABELS

            grouped = {code: [] for code in LABELS}
            for row in rows:
                if (
                    isinstance(row, dict)
                    and row.get("code") in grouped
                    and _visible(row, now)
                ):
                    grouped[row["code"]].append(row)
            # Global slicing starves alphabetically earlier series; each known
            # indicator retains its own bounded, chronological history instead.
            rows = [
                row
                for code in sorted(grouped)
                for row in sorted(
                    grouped[code], key=lambda item: str(item.get("referenceDate") or "")
                )[-24:]
            ]
        else:
            rows = rows[: 300 if name == "catalysts" else 40]
        for row in rows:
            try:
                if not _visible(row, now):
                    continue
                item = _pick(row, keys)
                if name == "catalysts":
                    item["title"] = str(item.get("title") or "")[:200]
                    item["content"] = str(item.get("content") or "")[:400]
                if row.get("sourceUri"):
                    item["sourceUri"] = _url(row["sourceUri"])
                if name == "observations":
                    metadata = row.get("metadata") or {}
                    if isinstance(metadata, dict):
                        item["metadata"] = _pick(
                            metadata,
                            (
                                "definitionKey",
                                "definitionVersion",
                                "definitionNote",
                                "calculation",
                                "originalIndex",
                                "indexBasis",
                                "transformation",
                                "notATradePrice",
                            ),
                        )
                        components = metadata.get("components")
                        if isinstance(components, dict):
                            item["metadata"]["components"] = _pick(
                                components, ("M1_YOY", "M2_YOY")
                            )
                public[name].append(item)
            except (ValueError, TypeError):
                continue
    allowed = {
        (
            row["themeCode"],
            "fund" if row.get("instrumentId") else "nav",
            row.get("instrumentId") or row["assetCode"],
        )
        for row in themes
        if row["assetKind"]
        in {"ETF_LINK_FUND", "ACTIVE_FUND", "OFF_EXCHANGE_FUND", "LOF"}
    }
    clean_anchors = []
    for row in anchors[:24]:
        if _identity(row) not in allowed:
            continue
        item = _public_actionable_plan(
            {**row, "subjectKind": "index"}, row["subjectId"], now
        )
        if item:
            item.update(
                themeCode=row["themeCode"],
                subjectKind=row["subjectKind"],
                fundCode=row.get("fundCode"),
                retrievedAt=row.get("retrievedAt"),
            )
            clean_anchors.append(item)
    prior = []
    for row in ((previous or {}).get("report") or {}).get("actionablePlans", [])[:24]:
        if _identity(row) not in allowed or not _visible(row, now):
            continue
        item = _public_actionable_plan(
            {**row, "subjectKind": "index"}, row["subjectId"], now
        )
        if item:
            item.update(
                _pick(
                    row,
                    (
                        "themeCode",
                        "subjectKind",
                        "selectedTrack",
                        "track",
                        "priceRevisionStatus",
                        "priceRevisionNote",
                        "calibrationNote",
                    ),
                )
            )
            prior.append(item)
    return {
        "alphaPublic": public,
        "themes": themes,
        "themeActionableZones": clean_anchors,
        "previousActionablePlans": prior,
        "strategyMemoryRules": rules,
    }
