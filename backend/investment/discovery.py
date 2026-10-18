"""Bounded public product discovery, independent of actor-selected filters."""

import re
import json
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from backend.common.database import system_connection


async def read_discovery_assets(conn, now, themes):
    rows = await conn.fetch(
        """SELECT c.instrument_id,c.fund_code,c.fund_name,c.market,
        count(*) OVER(PARTITION BY c.market,c.fund_code) AS mapping_count,
        u.sector AS discovery_sector,u.priority AS seed_priority,
        u.source_uri AS identity_source_uri,u.verified_at AS identity_verified_at,
        (c.fund_type ILIKE '%QDII%' OR c.fund_name ILIKE '%QDII%'
          OR COALESCE(u.overseas_calendar_required,false)) AS overseas_calendar_required
        FROM reference.research_fund_catalog c
        LEFT JOIN reference.discovery_fund_universe u ON u.market=c.market AND u.fund_code=c.fund_code
          AND u.enabled AND u.verified_at<=$1
        WHERE c.market='FUND_CN' AND c.fund_code~'^[0-9]{6}$'
          AND c.trade_form IN ('FUND','LOF')
        ORDER BY CASE WHEN u.fund_code IS NOT NULL THEN -1 WHEN EXISTS(SELECT 1 FROM reference.research_product_index_links p
            WHERE p.fund_code=c.fund_code AND p.market=c.market
              AND p.relationship_type IN ('TRACKS','ETF_LINK') AND p.verified_at<=$1
              AND p.effective_from<=($1 AT TIME ZONE 'Asia/Shanghai')::date
              AND (p.effective_to IS NULL OR p.effective_to>=($1 AT TIME ZONE 'Asia/Shanghai')::date)) THEN 0
          WHEN EXISTS(SELECT 1 FROM reference.theme_asset_graph g
            WHERE g.asset_code=c.fund_code AND g.market=c.market) THEN 1
          WHEN EXISTS(SELECT 1 FROM market.investment_analysis_snapshots s
            WHERE s.subject_kind='fund' AND s.subject_key=c.instrument_id::text
              AND s.available_at<=$1 AND s.retrieved_at<=$1) THEN 2 ELSE 3 END,
          u.priority NULLS LAST,c.fund_code,c.instrument_id NULLS LAST LIMIT 96""",
        now,
    )
    assets = {}
    ambiguous = {
        (row.get("market"), row.get("fund_code"))
        for row in rows
        if row.get("mapping_count", 1) != 1
    }
    for row in rows:
        code, market = row.get("fund_code"), row.get("market")
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"\d{6}", code)
            or market != "FUND_CN"
            or (market, code) in ambiguous
        ):
            continue
        identifier = str(row["instrument_id"]) if row.get("instrument_id") else code
        assets.setdefault(
            (market, code),
            {
                "subjectKind": "fund" if row.get("instrument_id") else "nav",
                "subjectId": identifier,
                "fundCode": code,
                "market": market,
                "label": str(row.get("fund_name") or code)[:200],
                "overseasCalendarRequired": row.get("overseas_calendar_required")
                is True,
            },
        )
        if row.get("discovery_sector"):
            assets[(market, code)].update(
                discoverySector=row["discovery_sector"],
                identitySourceUri=row.get("identity_source_uri"),
                identityVerifiedAt=str(row.get("identity_verified_at")),
            )
    for row in themes:
        if row.get("market") != "FUND_CN" or row.get("assetKind") not in {
            "ETF_LINK_FUND",
            "ACTIVE_FUND",
            "LOF",
            "OFF_EXCHANGE_FUND",
        }:
            continue
        code = row.get("assetCode")
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
            continue
        key = ("FUND_CN", code)
        if key in ambiguous:
            continue
        overseas = (row.get("metadata") or {}).get("overseasCalendarRequired") is True
        if key in assets:
            assets[key]["overseasCalendarRequired"] |= overseas
        else:
            assets[key] = {
                "subjectKind": "fund" if row.get("instrumentId") else "nav",
                "subjectId": row.get("instrumentId") or code,
                "fundCode": code,
                "market": "FUND_CN",
                "label": row.get("assetName") or code,
                "overseasCalendarRequired": overseas,
            }
    return _industry_order(_public_universe(list(assets.values())))


def _industry_order(assets):
    """One per industry first; retain eight slots for the wider public catalog."""
    groups, expansion = {}, []
    for asset in assets:
        sector = asset.get("discoverySector")
        if sector:
            groups.setdefault(sector, []).append(asset)
        else:
            expansion.append(asset)
    representatives = [group[0] for group in groups.values()]
    rest = [
        group[i]
        for i in range(1, max((len(g) for g in groups.values()), default=0))
        for group in groups.values()
        if i < len(group)
    ]
    ordered = [
        *representatives[:24],
        *expansion[:8],
        *representatives[24:],
        *rest,
        *expansion[8:],
    ]
    return ordered


def _public_universe(rows):
    """Only unambiguous public OTC identities cross the research boundary."""
    products, identities, bad_codes, bad_identities = {}, {}, set(), set()
    for row in rows:
        if not isinstance(row, dict) or row.get("market") != "FUND_CN":
            continue
        code, kind, subject = (
            row.get("fundCode"),
            row.get("subjectKind"),
            row.get("subjectId"),
        )
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            continue
        if row.get("mapping_count", row.get("mappingCount", 1)) != 1:
            bad_codes.add(code)
            continue
        if not isinstance(subject, str) or kind not in {"fund", "nav"}:
            continue
        if kind == "nav" and subject != code:
            continue
        if kind == "fund":
            try:
                UUID(subject)
            except (ValueError, TypeError):
                continue
        identity = kind, subject
        if (
            code in products
            and (products[code]["subjectKind"], products[code]["subjectId"]) != identity
        ):
            bad_codes.add(code)
        if identity in identities and identities[identity] != code:
            bad_identities.add(identity)
        identities.setdefault(identity, code)
        products.setdefault(
            code,
            {
                "subjectKind": kind,
                "subjectId": subject,
                "fundCode": code,
                "market": "FUND_CN",
                "label": str(row.get("label") or code)[:200],
                "overseasCalendarRequired": row.get("overseasCalendarRequired") is True,
            },
        )
        sector = row.get("discoverySector")
        if isinstance(sector, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", sector):
            products[code].update(
                discoverySector=sector,
                identitySourceUri=row.get("identitySourceUri"),
                identityVerifiedAt=row.get("identityVerifiedAt"),
            )
    return [
        row
        for code, row in products.items()
        if code not in bad_codes
        and (row["subjectKind"], row["subjectId"]) not in bad_identities
    ][:108]


def discovery_packet(universe, anchors, previous, now):
    from backend.macro.daily_research import (
        _public_actionable_plan,
        _pick,
        _visible,
        _texts,
    )

    universe = _public_universe(universe)
    products = {
        (row.get("subjectKind"), row.get("subjectId")): row
        for row in universe
        if row.get("market") == "FUND_CN"
    }
    allowed = set(products)
    allowed |= {("index", code) for code in ("hs300", "a500", "zz500")}

    def clean(plan):
        if not isinstance(plan, dict):
            return None
        identity = plan.get("subjectKind"), plan.get("subjectId")
        if identity not in allowed:
            return None
        product = products.get(identity)
        if product:
            if plan.get("fundCode") not in {None, product["fundCode"]} or any(
                plan.get(field) not in {None, product["market"]}
                for field in ("market", "fundMarket")
            ):
                return None
        result = _public_actionable_plan(
            plan, identity[1], now, subject_kind=identity[0]
        )
        if result:
            result.update(
                _pick(
                    plan,
                    (
                        "fundCode",
                        "themeCode",
                        "retrievedAt",
                        "availableAt",
                        "historyQuality",
                        "selectedTrack",
                        "track",
                        "priceRevisionStatus",
                        "priceRevisionNote",
                        "calibrationNote",
                        "valueDirectionKey",
                        "valueDirectionTitle",
                    ),
                )
            )
            if product:
                result.update(
                    market="FUND_CN",
                    fundCode=product["fundCode"],
                    label=product["label"],
                )
        return result

    unique = {}
    for anchor in anchors[:112]:
        item = clean(anchor)
        if item:
            # The builder appends this run's recomputation after old public
            # anchors. An authoritative unavailable result also replaces an
            # old candidate, so an obsolete buy zone cannot remain active.
            unique[(item["subjectKind"], item["subjectId"])] = item
    prior = []
    from backend.macro.value_directions import MAX_VALUE_DIRECTIONS

    for row in ((previous or {}).get("report") or {}).get("valueDirections", [])[
        :MAX_VALUE_DIRECTIONS
    ]:
        if (
            not isinstance(row, dict)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", str(row.get("key") or ""))
            or not _visible(row, now)
        ):
            continue
        prior.append(
            {
                **_pick(
                    row,
                    (
                        "key",
                        "title",
                        "thesis",
                        "signal",
                    ),
                ),
                "sourceIds": _texts(row.get("sourceIds"))[:8],
                "macroCodes": _texts(row.get("macroCodes"))[:8],
                "watchFor": _texts(row.get("watchFor"))[:4],
                "quantitativeFundCodes": _texts(
                    row.get("quantitativeFundCodes"), width=6
                )[:2],
                "quantitativeEvidence": [
                    {
                        **_pick(
                            item,
                            (
                                "subjectKind",
                                "subjectId",
                                "fundCode",
                                "label",
                                "asOf",
                                "sourceUri",
                                "historyQuality",
                                "priceTimeBasis",
                                "historySampleCount",
                            ),
                        ),
                    }
                    for item in (row.get("quantitativeEvidence") or [])[:2]
                    if isinstance(item, dict)
                ],
                **{
                    field: [
                        {
                            "summary": str(item.get("summary") or "")[:240],
                            "sourceIds": _texts(item.get("sourceIds"))[:8],
                        }
                        for item in (row.get(field) or [])[:2]
                        if isinstance(item, dict)
                    ]
                    for field in ("catalysts", "risks")
                },
                "plans": [
                    item for plan in row.get("plans", [])[:2] if (item := clean(plan))
                ],
            }
        )
    result = {
        "discoveryUniverse": universe[:108],
        "discoveryActionableZones": list(unique.values()),
        "previousValueDirections": prior,
    }
    result["quantitativeScan"] = _quantitative_scan(result, now)
    return result


def _quantitative_scan(public, now):
    """Independent server scan; a qualifying condition is not an in-zone price."""
    items = []
    sectors = {
        row["fundCode"]: row.get("discoverySector")
        for row in public["discoveryUniverse"]
    }
    for plan in public["discoveryActionableZones"]:
        code = plan.get("fundCode")
        if not code or plan.get("historyQuality") != "READY":
            continue
        signals = []
        for track in plan.get("tracks", []):
            if track.get("state") not in {"CANDIDATE", "WATCH"}:
                continue
            try:
                current, low, high = (
                    float(value)
                    for value in (
                        plan["currentPrice"],
                        track["entryZoneMin"],
                        track["entryZoneMax"],
                    )
                )
                if not 0 < low <= high or current <= 0:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            distance = (
                (low - current) / current
                if current < low
                else (current - high) / current
                if current > high
                else 0
            )
            signals.append(
                {
                    "track": track["track"],
                    "triggerState": track["state"],
                    "inEntryZone": low <= current <= high,
                    "distanceToEntryPct": round(distance * 100, 4),
                }
            )
        if not signals:
            continue
        item = deepcopy(plan)
        item["signals"] = signals
        if sectors.get(code):
            item["discoverySector"] = sectors[code]
        items.append(item)
    return {
        "methodVersion": "fund-quant-scan-v1",
        "asOf": now.isoformat(),
        "minimumHistorySamples": 220,
        "items": items,
        "coverage": {
            "pricedFundCount": len(items),
            "targetHistoryFundCount": sum(
                item.get("historySampleCount", 0) >= 220 for item in items
            ),
        },
    }


async def _cached_histories(conn, codes, now):
    if not codes:
        return {}
    rows = await conn.fetch(
        """SELECT DISTINCT ON(fund_code) fund_code,payload
        FROM market.fund_research_nav_histories WHERE fund_code=ANY($1::text[])
          AND retrieved_at<=$2 AND available_at<=$2 AND payload->>'asOf'<=$3
        ORDER BY fund_code,payload->>'asOf' DESC,retrieved_at DESC,id DESC""",
        codes,
        now,
        now.date().isoformat(),
    )
    result = {}
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                continue
        if (
            isinstance(payload, dict)
            and payload.get("fundCode") in codes
            and row.get("fund_code", payload.get("fundCode")) == payload["fundCode"]
        ):
            result[payload["fundCode"]] = payload
    return result


def _history_rank(history):
    from backend.macro.daily_research import _stamp

    stamp = _stamp(history.get("retrievedAt"))
    return str(history.get("asOf") or ""), stamp.timestamp() if stamp else 0


async def collect_discovery_nav_histories(pool, now):
    from backend.investment.theme_graph import (
        read_themes,
        collect_theme_nav_histories,
        _calendar_proof,
        nav_plan,
        _disclosed_nav_quality,
    )

    async with system_connection(pool) as conn:
        themes = await read_themes(conn, now)
        assets = await read_discovery_assets(conn, now, themes)
        cached = await _cached_histories(
            conn, [row["fundCode"] for row in assets[:32]], now
        )
    reused, needed, statuses = [], [], {}
    for asset in assets[:32]:
        history = cached.get(asset["fundCode"])
        if not isinstance(history, dict) or not isinstance(history.get("points"), list):
            needed.append(asset)
            continue
        try:
            same_day = (
                datetime.fromisoformat(history["retrievedAt"])
                .astimezone(now.tzinfo)
                .date()
                == now.date()
            )
        except (KeyError, TypeError, ValueError):
            same_day = False
        complete_short = (
            same_day
            and _disclosed_nav_quality(history, history.get("points", []), now)[0]
            == "READY"
        )
        if same_day and (len(history.get("points", [])) >= 220 or complete_short):
            async with system_connection(pool) as conn:
                history = deepcopy(history)
                history["calendarProof"] = await _calendar_proof(conn, history, now)
            plan = nav_plan(history, now=now)
            if plan and plan.get("historyQuality") == "READY":
                reused.append(history)
                statuses[asset["fundCode"]] = {
                    "status": "SUCCESS",
                    "records": len(history["points"]),
                    "retrievedAt": history["retrievedAt"],
                    "cache": "REUSED_CONFIRMED",
                }
                continue
        needed.append(asset)
    rows = [
        {
            "asset_code": row["fundCode"],
            "overseas_calendar_required": row["overseasCalendarRequired"],
        }
        for row in needed
    ]
    collected = await collect_theme_nav_histories(
        pool, now, asset_rows=rows, minimum_points=220
    )
    async with system_connection(pool) as conn:
        for history in collected["fundHistories"]:
            await conn.execute(
                """INSERT INTO market.fund_research_nav_histories
                (fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$3,$4::jsonb)""",
                history["fundCode"],
                datetime.fromisoformat(history["retrievedAt"]),
                datetime.fromisoformat(history["availableAt"]),
                json.dumps(history),
            )
    order = {row["fundCode"]: i for i, row in enumerate(assets[:32])}
    return {
        "fundHistories": sorted(
            [*reused, *collected["fundHistories"]],
            key=lambda row: order[row["fundCode"]],
        ),
        "themeNavSourceResults": {**statuses, **collected["themeNavSourceResults"]},
    }


async def build_discovery_packet(conn, packet, bundle, themes, previous, now):
    from copy import deepcopy
    from backend.investment.theme_graph import nav_plan, _calendar_proof
    from backend.investment import service

    universe = await read_discovery_assets(conn, now, themes)
    histories = await _cached_histories(
        conn, [row["fundCode"] for row in universe[:32]], now
    )
    for history in bundle.get("fundHistories", []):
        code = history.get("fundCode")
        if code not in histories or _history_rank(history) >= _history_rank(
            histories[code]
        ):
            histories[code] = history
    anchors = [
        *packet.get("actionableZones", []),
        *packet.get("themeActionableZones", []),
    ]
    for asset in universe[:32]:
        history = histories.get(asset["fundCode"])
        if not history:
            if asset["subjectKind"] == "fund":
                stored = await service.analysis(conn, "fund", asset["subjectId"])
                candidate = (
                    stored.get("actionableZones") if isinstance(stored, dict) else None
                )
                if (
                    isinstance(candidate, dict)
                    and candidate.get("subjectKind") == "fund"
                    and candidate.get("subjectId") == asset["subjectId"]
                    and candidate.get("priceBasis") == "UNIT_NAV"
                    and (candidate.get("historyQuality") or stored.get("dataQuality"))
                    == "READY"
                ):
                    anchors.append(deepcopy(candidate))
            continue
        history = deepcopy(history)
        history["overseasCalendarRequired"] = (
            asset["overseasCalendarRequired"]
            or history.get("overseasCalendarRequired") is True
        )
        history["calendarProof"] = await _calendar_proof(conn, history, now)
        plan = nav_plan(
            history,
            now=now,
            subject_kind=asset["subjectKind"],
            subject_id=asset["subjectId"],
            label=asset["label"],
        )
        if plan:
            anchors.append(plan)
    result = discovery_packet(universe, anchors, previous, now)
    selected = [row["fundCode"] for row in universe[:32]]
    priced = {
        row.get("fundCode")
        for row in result["discoveryActionableZones"]
        if row.get("historyQuality") == "READY"
        and any(
            track.get("entryZoneMin") is not None for track in row.get("tracks", [])
        )
    }
    result["discoveryCoverage"] = {
        "universeCount": len(universe),
        "selectedFundCodes": selected,
        "historyFundCodes": [
            code for code in selected if histories.get(code, {}).get("points")
        ],
        "pricedFundCodes": [code for code in selected if code in priced],
        "sectorCodes": list(
            dict.fromkeys(
                row["discoverySector"]
                for row in universe[:32]
                if row.get("discoverySector")
            )
        ),
        "selectionLimit": 32,
    }
    return result
