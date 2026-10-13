"""Immutable index valuation and fund-cost evidence collected by the Worker."""

import hashlib
import json
from datetime import date, timedelta
from uuid import UUID

from backend.common.database import system_connection
from backend.integrations.investment_fundamentals import (
    fetch_fund_costs,
    fetch_index_valuation,
)
from backend.market.research_history import calendar_market, published_day


def _object(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _missing_valuation(state, reason, config):
    return {
        "state": state,
        "provider": config.get("provider"),
        "symbol": config.get("symbol"),
        "sourceUri": config.get("sourceUri"),
        "reasons": [reason],
        **{
            key: {
                "state": state,
                "metric": metric,
                "points": [],
                "sourceUri": config.get("sourceUri"),
                "historyStart": None,
                "historyEnd": None,
                "sampleCount": 0,
                "reasons": [reason],
            }
            for key, metric in (("pe", "PE_TTM"), ("pb", "PB"))
        },
    }


async def _persist(conn, kind, subject_id, data_kind, result, now):
    latest_days = [result.get(metric, {}).get("historyEnd") for metric in ("pe", "pb")]
    dates = [date.fromisoformat(day) for day in latest_days if day]
    as_of = min(dates) if data_kind == "VALUATION" and dates else None
    await conn.execute(
        """INSERT INTO market.investment_fundamental_snapshots
          (subject_kind,subject_key,data_kind,input_hash,source_uri,as_of,retrieved_at,result)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
          ON CONFLICT(subject_kind,subject_key,data_kind,input_hash) DO NOTHING""",
        kind,
        subject_id,
        data_kind,
        _hash(result),
        result.get("sourceUri") or "unknown:investment-fundamentals",
        as_of,
        now,
        json.dumps(result, ensure_ascii=False),
    )


async def _index_valuation(pool, subject_id, subject, as_of, now, *, allow_cache):
    config = _object(subject.get("valuation"))
    configuration_hash = _hash(config)
    end = None
    verified = (
        config.get("verified") is True
        and isinstance(config.get("sourceUri"), str)
        and bool(config["sourceUri"].strip())
        and type(config.get("publishHour")) is int
        and 0 <= config["publishHour"] <= 23
    )
    if verified:
        cutoff = published_day(as_of, config["publishHour"], now)
        if cutoff is not None:
            async with system_connection(pool) as conn:
                end = await conn.fetchval(
                    """SELECT max(calendar_date) FROM reference.trading_calendars
                      WHERE market=$1 AND is_trading_day AND calendar_date<=$2""",
                    calendar_market(subject.get("market")),
                    cutoff,
                )
    if allow_cache and end is not None:
        async with system_connection(pool) as conn:
            row = await conn.fetchrow(
                """SELECT result FROM market.investment_fundamental_snapshots
                  WHERE subject_kind='index' AND subject_key=$1 AND data_kind='VALUATION'
                    AND result->>'configurationHash'=$2 AND result->>'expectedAsOf'=$3
                    AND retrieved_at>=$4 AND retrieved_at<=$5
                  ORDER BY retrieved_at DESC,id DESC LIMIT 1""",
                subject_id,
                configuration_hash,
                end.isoformat(),
                now.replace(hour=0, minute=0, second=0, microsecond=0),
                now,
            )
        if row is not None:
            return _object(row["result"])
    if end is None:
        result = _missing_valuation(
            "UNVERIFIED", "指数估值来源、披露时间或交易日历尚未核验。", config
        )
    else:
        try:
            result = await fetch_index_valuation(
                {**subject, "valuation": config}, end - timedelta(days=365 * 5), end
            )
        except Exception as exc:  # noqa: BLE001 -- valuation must not block price research
            result = _missing_valuation(
                "UNAVAILABLE", f"指数估值来源不可用：{type(exc).__name__}。", config
            )
    result = {
        **result,
        "configurationHash": configuration_hash,
        "valuationConfig": config,
        "expectedAsOf": end.isoformat() if end else None,
        "retrievedAt": now.isoformat(),
        "mapped": True,
        "indexId": subject_id,
    }
    async with system_connection(pool) as conn:
        await _persist(conn, "index", subject_id, "VALUATION", result, now)
    return result


async def refresh_fundamentals(pool, kind, subject_id, subject, as_of, *, now):
    """Collect facts independently; names never create fund/index mappings."""
    if kind not in ("index", "fund"):
        raise ValueError("unsupported investment fundamental subject")
    if kind == "index":
        valuation = await _index_valuation(
            pool, subject_id, subject, as_of, now, allow_cache=False
        )
        return {
            "valuation": valuation,
            "costs": None,
            "mapped": True,
            "indexId": subject_id,
            "valuationConfig": valuation["valuationConfig"],
        }
    config = _object(_object(subject.get("metadata")).get("research"))
    source_verified = (
        config.get("sourceVerified") is True
        and isinstance(config.get("sourceUri"), str)
        and bool(config["sourceUri"].strip())
    )
    try:
        costs = await fetch_fund_costs(subject["code"])
    except Exception as exc:  # noqa: BLE001 -- isolate operating-fee provider failure
        costs = {
            "state": "UNAVAILABLE",
            "managementRate": None,
            "custodyRate": None,
            "salesServiceRate": None,
            "totalRate": None,
            "sourceUri": f"https://fundf10.eastmoney.com/jjfl_{subject['code']}.html",
            "reasons": [f"基金费用来源不可用：{type(exc).__name__}。"],
        }
    if costs["state"] == "READY" and not source_verified:
        costs = {
            **costs,
            "state": "UNVERIFIED",
            "reasons": [
                *costs.get("reasons", []),
                "基金研究来源尚未核验，费用仅作为待核实事实。",
            ],
        }
    costs = {
        **costs,
        "sourceVerified": source_verified,
        "retrievedAt": now.isoformat(),
    }
    async with system_connection(pool) as conn:
        await _persist(conn, "fund", subject_id, "COSTS", costs, now)
        indices = await conn.fetch(
            """SELECT DISTINCT i.id,i.code,i.market,i.valuation FROM reference.fund_index_links f
              JOIN reference.research_indices i ON i.id=f.index_id
              WHERE f.instrument_id=$1 AND f.effective_from<=$2
                AND (f.effective_to IS NULL OR f.effective_to>=$2)
                AND f.verified_at<=$3 AND i.verified_at IS NOT NULL AND i.verified_at<=$3
              ORDER BY i.id""",
            UUID(subject_id),
            as_of,
            now,
        )
    mapped = len(indices) == 1
    index_id = indices[0]["id"] if mapped else None
    if mapped:
        valuation = await _index_valuation(
            pool, index_id, dict(indices[0]), as_of, now, allow_cache=True
        )
        valuation_config = _object(indices[0]["valuation"])
    else:
        valuation_config = {}
        valuation = {
            **_missing_valuation(
                "UNVERIFIED", "没有唯一、有效且已核验的基金跟踪指数关系。", {}
            ),
            "expectedAsOf": None,
            "retrievedAt": now.isoformat(),
        }
    valuation = {
        **valuation,
        "mapped": mapped,
        "indexId": index_id,
        "valuationConfig": valuation_config,
    }
    async with system_connection(pool) as conn:
        await _persist(conn, "fund", subject_id, "VALUATION", valuation, now)
    return {
        "valuation": valuation,
        "costs": costs,
        "mapped": mapped,
        "indexId": index_id,
        "valuationConfig": valuation_config,
    }
