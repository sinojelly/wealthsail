"""Structured model reasoning selects deterministic public price coordinates."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from backend.macro.price_revision import (
    PriceRevisionStatus,
    index_previous_plans,
    normalize_price_revision,
    previous_plan,
    same_coordinate,
)

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
    priceRevisionStatus: PriceRevisionStatus
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
    previous = index_previous_plans(
        packet.get("previousActionablePlans", []), list(anchors.values())
    )
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
        old = previous_plan(previous, anchor, row["track"])
        old_track = None
        if same_coordinate(anchor, old):
            old_day, _, old_track = _program_plan(old, row["track"], now)
            if old_day > day:
                raise ValueError("previous price anchor is later than current")
        if row["priceRevisionStatus"] == "TAKE_PROFIT_ALERT":
            try:
                proven = (
                    old is not None
                    and old_track is not None
                    and old.get("priceBasis") == anchor.get("priceBasis")
                    and old.get("unit") == anchor.get("unit")
                    and old.get("sourceUri") == anchor.get("sourceUri")
                    and current >= _price(old_track["takeProfitT1"])
                )
            except (KeyError, ValueError, InvalidOperation, TypeError):
                proven = False
            if not proven:
                raise ValueError("take profit lacks same-basis observed price proof")
        normalize_price_revision(row, anchor, track, old, old_track)
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
        "sectorCatalysts": {},
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
                    "filteredNoiseCount",
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
            "parentSourceId",
            "parentTitle",
            "itemOrdinal",
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
                    from backend.integrations.alpha_collectors import (
                        SECTOR_KEYWORDS,
                        core_catalyst,
                    )

                    item["title"] = str(item.get("title") or "")[:200]
                    item["content"] = str(item.get("content") or "")[:1000]
                    item["sectorBuckets"] = [
                        sector
                        for sector in _texts(row.get("sectorBuckets"))
                        if sector in {*SECTOR_KEYWORDS, "OTHER"}
                    ]
                    item["researchPriority"] = (
                        "CORE_CATALYST"
                        if core_catalyst(
                            item["title"] + " " + item["content"],
                            [
                                sector
                                for sector in item["sectorBuckets"]
                                if sector in SECTOR_KEYWORDS
                            ],
                        )
                        else "MARKET_CLUE"
                    )
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
    for row in public["catalysts"]:
        for sector in row.get("sectorBuckets", []):
            ids = public["sectorCatalysts"].setdefault(sector, [])
            if row.get("id") and row["id"] not in ids and len(ids) < 12:
                ids.append(row["id"])
    theme_identities = {}
    for row in themes:
        if row["assetKind"] not in {
            "ETF_LINK_FUND",
            "ACTIVE_FUND",
            "OFF_EXCHANGE_FUND",
            "LOF",
        }:
            continue
        identity = (
            row["themeCode"],
            "fund" if row.get("instrumentId") else "nav",
            row.get("instrumentId") or row["assetCode"],
        )
        theme_identities.setdefault(identity, set()).add(
            (row.get("market"), row["assetCode"])
        )
    allowed = set(theme_identities)
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
            if row.get("market") is not None:
                item["market"] = row["market"]
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
                        "fundCode",
                        "market",
                        "subjectKind",
                        "selectedTrack",
                        "track",
                        "priceRevisionStatus",
                        "priceRevisionNote",
                        "calibrationNote",
                    ),
                )
            )
            mappings = theme_identities[_identity(row)]
            if len(mappings) == 1:
                market, code = next(iter(mappings))
                if (
                    market == "FUND_CN"
                    and isinstance(code, str)
                    and re.fullmatch(r"[0-9]{6}", code)
                    and row.get("fundCode") in {None, code}
                ):
                    if row.get("market") is None:
                        item["market"] = market
                    if row.get("fundCode") is None:
                        item["fundCode"] = code
            prior.append(item)
    return {
        "alphaPublic": public,
        "themes": themes,
        "themeActionableZones": clean_anchors,
        "previousActionablePlans": prior,
        "strategyMemoryRules": rules,
    }
