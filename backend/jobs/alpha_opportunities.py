"""Actor-scoped opportunity checks with permanently consumed delivery slots.

No model, trade, ledger or legacy report is invoked here. A committed unique
claim precedes every estimate/provider call and webhook attempt. Failed and
interrupted slots are never automatically replayed.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from backend.common.database import system_connection, tenant_transaction
from backend.dependencies import now_shanghai
from backend.integrations.wecom_bot import send_opportunity_webhook

logger = logging.getLogger("wealthsail.alpha_opportunities")
SHANGHAI = ZoneInfo("Asia/Shanghai")
THEME_LABELS = {
    "HAINAN_FTP": "海南自贸港",
    "PUDONG_DEV": "浦东新区",
    "GEO_CRUDE_OIL": "地缘与原油",
    "AI_MEMORY_HBM": "AI算力与HBM/DRAM",
    "COMMERCIAL_SPACE": "商业航天与卫星互联网",
}
TRACK_LABELS = {"LEFT_VALUE": "左侧价值", "RIGHT_TREND": "右侧趋势"}


def _local(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("opportunity clock must be timezone-aware")
    return now.astimezone(SHANGHAI)


def _body(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def _day(value):
    try:
        if isinstance(value, datetime):
            return _local(value).date()
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _stamp(value):
    try:
        return _local(
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
    except (ValueError, TypeError):
        return None


def _unit(unit, basis):
    if basis == "UNIT_NAV" and unit in {
        "CNY",
        "元",
        "元/份",
        "CNY/unit",
        "CNY/份",
        "CNY/share",
    }:
        return "CNY/份"
    if basis == "PROVIDER_RETURN_INDEX" and unit in {
        "return_index",
        "points",
        "index_point",
        "收益指数",
        "CNY",
        "元",
        "元/份",
        "CNY/unit",
        "CNY/份",
    }:
        return "return_index"
    if basis in {"INDEX_POINTS", "INDEX_PRICE"} and unit in {
        "points",
        "index_point",
        "指数点",
    }:
        return "points"
    return unit


def _scope(settings):
    if not getattr(settings, "alpha_opportunity_enabled", True):
        return None
    try:
        return UUID(str(settings.opportunity_tenant_id)), UUID(
            str(settings.opportunity_user_id)
        )
    except (AttributeError, ValueError, TypeError):
        return None


async def _active(pool, scope):
    async with system_connection(pool) as conn:
        return (
            await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM identity.tenant_members
            WHERE tenant_id=$1 AND user_id=$2 AND status='ACTIVE')""",
                *scope,
            )
            is True
        )


async def _claim(pool, scope, day, slot, now):
    # The single statement commits before the connection is returned. There
    # is deliberately no lease recovery or ON CONFLICT UPDATE path.
    async with system_connection(pool) as conn:
        row = await conn.fetchrow(
            """INSERT INTO research.alpha_opportunity_runs
            (tenant_id,user_id,run_date,slot,status,claimed_at,result)
            VALUES($1,$2,$3,$4,'RUNNING',$5,'{}'::jsonb)
            ON CONFLICT(tenant_id,user_id,run_date,slot) DO NOTHING RETURNING id""",
            *scope,
            day,
            slot,
            now,
        )
    return row["id"] if row else None


async def _finish(pool, scope, run_id, status, now, result):
    async with system_connection(pool) as conn:
        await conn.execute(
            """UPDATE research.alpha_opportunity_runs SET status=$4,finished_at=$5,result=$6::jsonb
            WHERE id=$1 AND tenant_id=$2 AND user_id=$3 AND status='RUNNING'""",
            run_id,
            *scope,
            status,
            now,
            json.dumps(result, ensure_ascii=False, default=str, allow_nan=False),
        )


async def _run_slot(pool, scope, day, slot, now, action, now_provider):
    run_id = await _claim(pool, scope, day, slot, now)
    if run_id is None:
        return {"status": "SKIPPED"}
    try:
        status, result = await action()
    except asyncio.CancelledError:
        await _finish(
            pool,
            scope,
            run_id,
            "FAILED",
            _local(now_provider()),
            {"errorClass": "CancelledError"},
        )
        raise
    except Exception as exc:  # noqa: BLE001 -- one permanent slot, never a retry
        status, result = "FAILED", {"errorClass": type(exc).__name__}
        logger.warning(
            "alpha_slot_failed slot=%s error_class=%s", slot, type(exc).__name__
        )
    await _finish(pool, scope, run_id, status, _local(now_provider()), result)
    return {"status": status, "runId": str(run_id), **result}


async def _read_studies(pool, scope, now):
    async with tenant_transaction(pool, scope[1], scope[0]) as conn:
        rows = await conn.fetch(
            """SELECT t.id AS track_id,t.generation,t.scope,v.id AS version_id,v.snapshot,
            c.instrument_id,COALESCE(c.fund_code,g.asset_code) AS fund_code,
            COALESCE(c.fund_name,g.asset_name) AS fund_name,c.trade_form,
            COALESCE(c.market,g.market) AS market,c.mapping_count,
            g.asset_code IS NOT NULL AS graph_scope_verified,g.asset_kind AS graph_asset_kind,
            i.asset_type,i.metadata AS instrument_metadata
            FROM research.study_tracks t
            JOIN LATERAL(SELECT id,snapshot FROM research.study_versions v
              WHERE v.tenant_id=t.tenant_id AND v.user_id=t.user_id AND v.track_id=t.id
              AND v.created_at<=$3 ORDER BY version DESC LIMIT 1) v ON TRUE
            LEFT JOIN LATERAL(SELECT c.*,(SELECT count(*)
                FROM reference.research_fund_catalog other
                WHERE other.market=c.market AND other.fund_code=c.fund_code) AS mapping_count
              FROM reference.research_fund_catalog c
              WHERE (NULLIF(t.scope->>'instrumentId','') IS NOT NULL
                     AND c.instrument_id::text=t.scope->>'instrumentId')
                 OR (NULLIF(t.scope->>'instrumentId','') IS NULL
                     AND c.fund_code=t.scope->>'fundCode' AND c.market=t.scope->>'fundMarket')
              ORDER BY c.market,c.fund_code LIMIT 1) c ON TRUE
            LEFT JOIN LATERAL(SELECT asset_code,asset_name,asset_kind,market
              FROM reference.theme_asset_graph g
              WHERE NULLIF(t.scope->>'instrumentId','') IS NULL
                AND c.fund_code IS NULL
                AND t.scope->>'fundMarket'='FUND_CN' AND g.market='FUND_CN'
                AND g.asset_code=t.scope->>'fundCode'
                AND g.asset_kind IN ('ETF_LINK_FUND','ACTIVE_FUND','LOF')
              ORDER BY g.theme_code LIMIT 1) g ON TRUE
            LEFT JOIN reference.instruments i ON i.id=c.instrument_id
            WHERE t.tenant_id=$1 AND t.user_id=$2 AND t.enabled AND t.created_at<=$3
            AND jsonb_typeof(v.snapshot->'actionablePlan')='object'
            ORDER BY t.created_at,t.id LIMIT 100""",
            *scope,
            now,
        )
    result = []
    for row in rows:
        row = dict(row)
        row["snapshot"], row["scope"] = (
            _body(row.get("snapshot")),
            _body(row.get("scope")),
        )
        frozen = row["snapshot"].get("actionablePlan")
        expires = _day(row["snapshot"].get("expiresOn"))
        if isinstance(frozen, dict) and (expires is None or expires >= now.date()):
            result.append(row)
    return result


def _mapped_otc(row):
    scope = _body(row.get("scope"))
    if any(
        scope.get(key) and str(scope[key]) != str(row.get(field))
        for key, field in (
            ("fundCode", "fund_code"),
            ("fundMarket", "market"),
            ("instrumentId", "instrument_id"),
        )
    ):
        return False
    graph_known = (
        row.get("graph_scope_verified") is True
        and row.get("mapping_count") in {None, 0}
        and row.get("graph_asset_kind") in {"ETF_LINK_FUND", "ACTIVE_FUND", "LOF"}
        and not scope.get("instrumentId")
        and scope.get("fundCode") == row.get("fund_code")
        and scope.get("fundMarket") == row.get("market") == "FUND_CN"
    )
    return (
        (row.get("mapping_count") == 1 or graph_known)
        and (
            row.get("asset_type") == "OFF_EXCHANGE_FUND"
            or row.get("trade_form") == "LOF"
            or row.get("trade_form") == "FUND"
            and row.get("market") == "FUND_CN"
            or graph_known
        )
        and row.get("market") in {"FUND_CN", "SSE", "SZSE"}
        and str(row.get("fund_code") or "").isdigit()
        and len(str(row.get("fund_code") or "")) == 6
    )


def _identity_matches(row, frozen):
    kind, subject = frozen.get("subjectKind"), frozen.get("subjectId")
    return (
        kind == "fund"
        and str(row.get("instrument_id")) == str(subject)
        or kind == "nav"
        and row.get("fund_code") == subject
        and row["scope"].get("fundMarket") == row.get("market")
    )


def _fresh_estimate(stamp, now):
    stamp = _stamp(stamp)
    return bool(
        stamp is not None
        and stamp.date() == now.date()
        and stamp.time() >= time(14)
        and timedelta(0) <= now - stamp <= timedelta(minutes=30)
    )


def _coverage(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result <= 100 else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def compare_frozen_price(frozen, observation, now, *, entry_safety=None):
    """Compare an auditable same-subject quote without moving frozen targets."""
    now = _local(now)
    output = {
        "state": "UNKNOWN",
        "priceBasis": frozen.get("priceBasis"),
        "reason": "PRICE_NOT_COMPARABLE",
    }
    if not isinstance(observation, dict):
        return output
    value, stamp = _number(observation.get("value")), _stamp(observation.get("asOf"))
    coverage = _coverage(observation.get("coveragePct"))
    lower, upper = (
        _number(observation.get(key)) for key in ("lowerValue", "upperValue")
    )
    valid_range = (
        lower is not None
        and upper is not None
        and value is not None
        and lower <= value <= upper
    )
    output.update(
        lowerValue=str(lower) if valid_range else None,
        upperValue=str(upper) if valid_range else None,
        coveragePct=str(coverage) if coverage is not None else None,
        estimatePriceBasis=observation.get("priceBasis"),
    )
    from backend.investment.study_price_watch import _source_uri

    output["marketSourceUri"] = _source_uri(observation.get("sourceUri"))
    if (
        value is None
        or stamp is None
        or stamp > now
        or stamp.date() != now.date()
        or observation.get("quality") not in {"ESTIMATED", "READY", "FRESH"}
        or observation.get("subjectKind") != frozen.get("subjectKind")
        or str(observation.get("subjectId")) != str(frozen.get("subjectId"))
        or _day(frozen.get("asOf")) is None
        or _day(frozen["asOf"]) > now.date()
    ):
        return output
    if not _fresh_estimate(stamp, now):
        return {**output, "reason": "ESTIMATE_STALE"}
    if observation.get("coveragePct") is not None and (
        coverage is None or coverage < 60
    ):
        return {**output, "reason": "ESTIMATE_COVERAGE_INSUFFICIENT"}
    if (
        observation.get("lowerValue") is not None
        or observation.get("upperValue") is not None
    ) and not valid_range:
        return {**output, "reason": "ESTIMATE_INTERVAL_INVALID"}
    basis = frozen.get("priceBasis")
    if _unit(frozen.get("unit"), basis) is None:
        return output
    if basis != observation.get("priceBasis"):
        pair = observation.get("conversion") or {}
        if not (
            basis == "PROVIDER_RETURN_INDEX"
            and observation.get("priceBasis") == "UNIT_NAV"
            and _unit(frozen.get("unit"), basis) == "return_index"
            and _unit(observation.get("unit"), "UNIT_NAV") == "CNY/份"
            and str(pair.get("subjectId")) == str(frozen.get("subjectId"))
            and pair.get("sourceVerified") is True
            and pair.get("returnSeriesVerified") is True
            and pair.get("dividendConflict") is False
            and _day(pair.get("date")) is not None
            and _day(frozen["asOf"]) <= _day(pair["date"]) < now.date()
            and _number(pair.get("close")) is not None
            and _number(pair.get("unitNav")) is not None
        ):
            return output
        output.update(
            observedNav=str(value),
            conversionAsOf=pair["date"],
            conversionMethod="verified-nav-return-pair/v1",
        )
        value = value / _number(pair["unitNav"]) * _number(pair["close"])
    elif _unit(frozen.get("unit"), basis) != _unit(observation.get("unit"), basis):
        return output
    tracks = [
        item
        for item in frozen.get("tracks", [])
        if item.get("track") == frozen.get("selectedTrack")
    ]
    if len(tracks) != 1:
        return output
    track = tracks[0]
    low, high, stop, first = (
        _number(track.get(key))
        for key in ("entryZoneMin", "entryZoneMax", "stopLossPrice", "takeProfitT1")
    )
    if low is None or high is None or low > high:
        return output
    in_zone = low <= value <= high
    technical_candidate = track.get("state") == "CANDIDATE"
    from backend.investment.study_price_watch import (
        entry_permitted,
        _entry_safety_summary,
    )

    allowed = entry_permitted(frozen, track, entry_safety)
    safety = _entry_safety_summary(frozen, track, entry_safety)
    state = (
        "STOP_LOSS"
        if stop is not None and stop < low and value <= stop
        else "TAKE_PROFIT_T1"
        if first is not None and first > high and value >= first
        else "IN_ENTRY_ZONE"
        if in_zone and technical_candidate and allowed
        else "WATCH"
    )
    return {
        **output,
        "state": state,
        **safety,
        "reason": (
            "ENTRY_PROTECTED"
            if state == "WATCH" and not allowed
            else "TECHNICAL_NOT_TRIGGERED"
            if state == "WATCH" and in_zone and track.get("state") == "WATCH"
            else "TECHNICAL_STATE_UNKNOWN"
            if state == "WATCH" and in_zone and not technical_candidate
            else "FROZEN_COORDINATES"
        ),
        "inEntryZone": in_zone,
        "technicalCandidate": technical_candidate,
        "methodVersion": frozen.get("methodVersion"),
        "selectedTrack": frozen.get("selectedTrack"),
        "planAsOf": frozen.get("asOf"),
        "price": format(value, "f"),
        "asOf": stamp.isoformat(),
        "unit": frozen.get("unit"),
        "entryZoneMin": str(low),
        "entryZoneMax": str(high),
        "stopLossPrice": str(stop) if stop is not None else None,
        "takeProfitT1": str(first) if first is not None else None,
    }


def _text(value, limit=160):
    value = " ".join(str(value or "").split())[:limit]
    return value.translate(
        str.maketrans({"<": "＜", ">": "＞", "[": "【", "]": "】", "@": "＠"})
    )


def _bounded_lines(lines, limit=3900):
    result = []
    for line in lines:
        if len("\n".join([*result, line]).encode("utf-8")) > limit:
            result.append("其余项目请在工作台核对。")
            break
        result.append(line)
    return "\n".join(result)


def _public_coordinate(plan, day, *, funds_only=False):
    """Select typed server coordinates, never model notes or private fields."""
    if not isinstance(plan, dict):
        return None
    direction = plan.get("valueDirectionKey")
    if direction is not None:
        if (
            not isinstance(direction, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", direction)
            or not isinstance(plan.get("valueDirectionTitle"), str)
            or not plan["valueDirectionTitle"].strip()
            or len(plan["valueDirectionTitle"]) > 100
        ):
            return None
    elif plan.get("themeCode") not in THEME_LABELS:
        return None
    kind, basis = plan.get("subjectKind"), plan.get("priceBasis")
    if not isinstance(plan.get("subjectId"), str) or not plan["subjectId"]:
        return None
    if kind in {"fund", "nav"}:
        if basis != "UNIT_NAV" or _unit(plan.get("unit"), basis) != "CNY/份":
            return None
        if kind == "nav" and (
            not re.fullmatch(r"[0-9]{6}", plan["subjectId"])
            or plan.get("fundCode") not in {None, plan["subjectId"]}
            or any(
                plan.get(key) not in {None, "FUND_CN"}
                for key in ("market", "fundMarket")
            )
        ):
            return None
        if kind == "fund":
            try:
                UUID(plan["subjectId"])
            except (ValueError, TypeError):
                return None
    elif (
        funds_only
        or kind != "index"
        or basis not in {"INDEX_POINTS", "INDEX_PRICE"}
        or _unit(plan.get("unit"), basis) != "points"
    ):
        return None
    selected = plan.get("selectedTrack", plan.get("track"))
    tracks = [
        item
        for item in plan.get("tracks", [])
        if isinstance(item, dict) and item.get("track") == selected
    ]
    if selected not in TRACK_LABELS or len(tracks) != 1:
        return None
    track = tracks[0]
    as_of = _day(plan.get("asOf"))
    low, high = (_number(track.get(key)) for key in ("entryZoneMin", "entryZoneMax"))
    if (
        as_of is None
        or as_of > day
        or not plan.get("methodVersion")
        or _number(plan.get("currentPrice")) is None
        or low is None
        or high is None
        or low > high
        or track.get("state") not in {"CANDIDATE", "WATCH"}
    ):
        return None
    stop, first, second = (
        _number(track.get(key))
        for key in ("stopLossPrice", "takeProfitT1", "takeProfitT2")
    )
    if any(
        track.get(key) is not None and _number(track[key]) is None
        for key in ("stopLossPrice", "takeProfitT1", "takeProfitT2")
    ):
        return None
    if (
        stop is not None
        and stop >= low
        or first is not None
        and first <= high
        or second is not None
        and (first is None or second <= first)
    ):
        return None
    return selected, track


def _price_text(value, basis):
    number = _number(value)
    return (
        format(number, ".4f" if basis == "UNIT_NAV" else ".2f")
        if number is not None
        else "—"
    )


def _morning_message(payload, day):
    plans = []
    for plan in (payload.get("actionablePlans") or [])[:24]:
        coordinates = _public_coordinate(plan, day)
        if coordinates is None:
            continue
        selected, track = coordinates
        basis_label = (
            "单位净值（元/份）"
            if plan["priceBasis"] == "UNIT_NAV"
            else "指数点（仅研究坐标）"
        )
        plans.append(
            f"{_text(plan.get('valueDirectionTitle') or THEME_LABELS.get(plan.get('themeCode'), '重点方向'), 100)} · {_text(plan.get('label') or plan['subjectId'], 50)}({_text(plan.get('fundCode') or plan['subjectId'], 40)}) · {TRACK_LABELS[selected]}；{basis_label}；最新 {_price_text(plan['currentPrice'], plan['priceBasis'])}；14:30关注区间 {_price_text(track['entryZoneMin'], plan['priceBasis'])}–{_price_text(track['entryZoneMax'], plan['priceBasis'])}；止损 {_price_text(track.get('stopLossPrice'), plan['priceBasis'])}；目标一 {_price_text(track.get('takeProfitT1'), plan['priceBasis'])}；目标二 {_price_text(track.get('takeProfitT2'), plan['priceBasis'])}；价格日期 {_day(plan['asOf'])} / {_text(plan['methodVersion'], 40)}"
        )
    return _bounded_lines(
        [
            f"### 08:30 每日机会研究 · {day}",
            "宏观周期：" + _text(payload.get("macroCycle") or "待核对", 500),
            _text(payload.get("summary"), 500),
            *plans,
            *[
                _text(event.get("title"), 100)
                for event in payload.get("events", [])[:3]
                if isinstance(event, dict)
            ],
            "模型研究与程序价格依据分别核对；本提醒不执行交易。",
        ]
    )


def _fund_message(rows, day, *, sell_risk_alerts=()):
    labels = {
        "UNKNOWN": "数据待核对",
        "STOP_LOSS": "触及冻结止损参考",
        "TAKE_PROFIT_T1": "达到冻结目标一",
        "IN_ENTRY_ZONE": "进入冻结入场区间",
        "WATCH": "区间外观察",
    }
    lines = [
        f"### 14:30 场外基金核对 · {day}",
        "当日估算净值非最终净值；仅提醒，不执行交易。",
    ]
    if sell_risk_alerts:
        lines.append("### 持仓/跟踪卖出与风控提醒")
        risk_labels = {
            "STOP_LOSS": "触及止损参考",
            "TAKE_PROFIT_T2": "达到目标二参考",
            "TAKE_PROFIT_T1": "达到目标一参考",
            "RSI_OVERBOUGHT": "确认净值RSI14严重超买",
        }
        for alert in sell_risk_alerts:
            line = f"{_text(alert.get('label'), 60)}({_text(alert.get('fundCode'), 12)})：{risk_labels[alert['state']]}；估算/确认参考 {_price_text(alert.get('value'), 'UNIT_NAV')} 元/份；观察 {_text(alert.get('asOf'), 40)}"
            if alert.get("rsi14") is not None:
                line += f"；确认序列RSI14 {_text(alert['rsi14'], 12)}（{_text(alert.get('rsiAsOf'), 12)}）"
            lines.append(line)
    for row in rows:
        checked = row["check"]
        if checked.get("reason") == "ENTRY_PROTECTED":
            lines.append(
                _text(
                    checked.get("subscriptionLabel")
                    or checked.get("entryStatus")
                    or "当前入场保护生效，暂缓入场",
                    160,
                )
            )
        frozen = row["snapshot"]["actionablePlan"]
        track = frozen.get("selectedTrack")
        track_label = TRACK_LABELS.get(track, "轨道待核对")
        origin = (
            "今日日报计划"
            if row.get("planOrigin") == "DAILY_REPORT"
            else "本人冻结计划"
        )
        direction = frozen.get("valueDirectionTitle") or THEME_LABELS.get(
            frozen.get("themeCode")
        )
        line = _text(direction, 100) + " · " if direction else ""
        status_label = (
            "价格在区间内，技术条件待触发"
            if checked.get("reason") == "TECHNICAL_NOT_TRIGGERED"
            else "价格在区间内，冻结技术状态未知"
            if checked.get("reason") == "TECHNICAL_STATE_UNKNOWN"
            else labels[checked["state"]]
        )
        line += f"{_text(row.get('fund_name'), 50)}({_text(row.get('fund_code'), 12)}) · {origin} · {track_label}：{status_label}"
        if checked.get("lowerValue") and checked.get("upperValue"):
            line += f"；估算范围 {_price_text(checked['lowerValue'], 'UNIT_NAV')}–{_price_text(checked['upperValue'], 'UNIT_NAV')} 元/份（可能误差范围）"
        else:
            line += "；估算范围 未提供；可能误差 未知"
        line += (
            f"；覆盖率 {_coverage(checked['coveragePct']):.2f}%"
            if checked.get("coveragePct") is not None
            else "；覆盖率 未提供"
        )
        if checked["state"] == "UNKNOWN":
            line += "；" + {
                "ESTIMATE_STALE": "估值早于14:00或超过30分钟",
                "ESTIMATE_COVERAGE_INSUFFICIENT": "已知覆盖率不足60%或无效",
                "ESTIMATE_INTERVAL_INVALID": "估算区间无效",
            }.get(checked.get("reason"), "缺少同标的、同口径的有效当日估值")
        if checked.get("price"):
            basis_label = (
                "单位净值（元/份）"
                if checked["priceBasis"] == "UNIT_NAV"
                else "收益指数（研究坐标）"
            )
            line += f"；{basis_label} {_price_text(checked['price'], checked['priceBasis'])}；入场区间 {_price_text(checked['entryZoneMin'], checked['priceBasis'])}–{_price_text(checked['entryZoneMax'], checked['priceBasis'])}；止损 {_price_text(checked.get('stopLossPrice'), checked['priceBasis'])}；目标一 {_price_text(checked.get('takeProfitT1'), checked['priceBasis'])}；观察 {checked['asOf']}；冻结 {checked.get('planAsOf')} / {_text(checked.get('methodVersion'), 40)}"
        if checked.get("conversionAsOf"):
            line += f"；估算净值 {_price_text(checked['observedNav'], 'UNIT_NAV')}，按{checked['conversionAsOf']}确认配对换算收益指数"
        lines.append(line)
    return _bounded_lines(lines)


async def load_opportunity_prices(pool, studies, now):
    """Use only the configured actor's governed OTC codes, never all holdings."""
    from backend.integrations import market_data as md

    targets = [
        row
        for row in studies
        if _mapped_otc(row)
        and _identity_matches(row, row["snapshot"]["actionablePlan"])
    ]
    by_instrument, output = {}, {}
    async with system_connection(pool) as conn:
        for row in targets:
            if row.get("instrument_id") is None:
                continue
            observation = await conn.fetchrow(
                """SELECT o.value,o.lower_value,o.upper_value,o.as_of,o.quality_status,o.unit,o.metadata,
                COALESCE(o.metadata->>'sourceUri',s.origin_uri) AS source_uri
                FROM market.market_observations o JOIN market.source_snapshots s ON s.id=o.snapshot_id
                WHERE o.subject_id=$1 AND o.field_code='ESTIMATED_NAV'
                AND (o.as_of AT TIME ZONE 'Asia/Shanghai')::date=$2
                AND o.as_of<=$3 AND o.observed_at<=$3 AND s.retrieved_at<=$3
                AND o.as_of>=$3-interval '30 minutes'
                AND (o.as_of AT TIME ZONE 'Asia/Shanghai')::time>='14:00'::time
                AND o.quality_status='ESTIMATED'
                ORDER BY o.as_of DESC,o.revision DESC,o.observed_at DESC LIMIT 1""",
                row["instrument_id"],
                now.date(),
                now,
            )
            if (
                observation
                and _number(observation["value"]) is not None
                and _fresh_estimate(observation["as_of"], now)
            ):
                by_instrument[str(row["instrument_id"])] = dict(observation)
    missing_codes = sorted(
        {
            row["fund_code"]
            for row in targets
            if str(row.get("instrument_id")) not in by_instrument
        }
    )
    fetched = (
        await md.fetch_fund_intraday_estimates(missing_codes) if missing_codes else []
    )
    by_code = {item.symbol: item for item in fetched}
    for row in targets:
        frozen = row["snapshot"]["actionablePlan"]
        observed = by_instrument.get(str(row.get("instrument_id")))
        external = by_code.get(row["fund_code"])
        if observed is None and external is not None:
            observed = {
                "value": external.value,
                "as_of": external.as_of,
                "quality_status": external.quality_status,
                "unit": external.unit,
                "source_uri": external.origin_uri,
                "lower_value": getattr(external, "lower_value", None),
                "upper_value": getattr(external, "upper_value", None),
                "metadata": getattr(external, "metadata", None),
            }
        if observed is None:
            continue
        price = {
            "subjectKind": frozen.get("subjectKind"),
            "subjectId": frozen.get("subjectId"),
            "priceBasis": "UNIT_NAV",
            "unit": observed.get("unit"),
            "value": str(observed.get("value")),
            "asOf": observed.get("as_of"),
            "quality": observed.get("quality_status"),
            "sourceUri": observed.get("source_uri"),
            "lowerValue": str(observed["lower_value"])
            if observed.get("lower_value") is not None
            else None,
            "upperValue": str(observed["upper_value"])
            if observed.get("upper_value") is not None
            else None,
            "coveragePct": _body(observed.get("metadata")).get("coveragePct"),
        }
        if frozen.get("priceBasis") == "PROVIDER_RETURN_INDEX":
            price["conversion"] = await _conversion_pair(pool, row, now)
        output[str(row["track_id"])] = price
    return output


async def _conversion_pair(pool, row, now):
    frozen = row["snapshot"]["actionablePlan"]
    async with system_connection(pool) as conn:
        data = await conn.fetchrow(
            """SELECT points,result,source_uri,series_kind FROM market.investment_analysis_snapshots
            WHERE subject_kind=$1 AND subject_key=$2 AND available_at<=$3 AND retrieved_at<=$3
            ORDER BY retrieved_at DESC,id DESC LIMIT 1""",
            frozen["subjectKind"],
            frozen["subjectId"],
            now,
        )
    metadata = _body(row.get("instrument_metadata")).get("research", {})
    result = _body(data.get("result")) if data else {}
    points = (
        json.loads(data["points"])
        if data and isinstance(data["points"], str)
        else data.get("points", [])
        if data
        else []
    )
    valid = [
        point
        for point in points
        if isinstance(point, dict)
        and _day(point.get("date")) is not None
        and _day(point["date"]) < now.date()
    ]
    if (
        not valid
        or not data
        or data.get("series_kind") != "PROVIDER_RETURN_INDEX"
        or metadata.get("sourceVerified") is not True
        or metadata.get("returnSeriesVerified") is not True
        or metadata.get("sourceUri") != data.get("source_uri")
        or result.get("returnSeriesVerified") is not True
        or result.get("dataQuality") not in {"READY", "STALE"}
    ):
        return {}
    # fund_history re-anchors each requested window at 100. A later rolling
    # snapshot is comparable only if its real frozen-date point still uses
    # the original coordinate. Never infer a rescaling factor here.
    base = [point for point in valid if _day(point["date"]) == _day(frozen.get("asOf"))]
    if (
        len(base) != 1
        or _number(frozen.get("currentPrice")) is None
        or _number(base[0].get("close")) != _number(frozen["currentPrice"])
        or _number(base[0].get("unitNav")) is None
    ):
        return {}
    point = max(valid, key=lambda item: item["date"])
    if (
        _number(point.get("close")) is None
        or _number(point.get("unitNav")) is None
        or sum(_day(item["date"]) == _day(point["date"]) for item in valid) != 1
    ):
        return {}
    return {
        "subjectId": frozen["subjectId"],
        "date": point["date"],
        "close": point.get("close"),
        "unitNav": point.get("unitNav"),
        "sourceVerified": True,
        "returnSeriesVerified": True,
        "dividendConflict": any(
            item.get("returnQuality") == "CONFLICT" for item in valid
        ),
    }


async def _today_report(pool, now):
    async with system_connection(pool) as conn:
        report = await conn.fetchrow(
            """SELECT r.run_id,r.slot_date,x.recorded_at,x.payload FROM research.daily_research_runs r
            JOIN research.daily_research_results x USING(run_id)
            WHERE r.slot_date=$1 AND r.run_mode='SCHEDULED' AND x.status='READY' AND r.dispatched_at<=$2 AND x.recorded_at<=$2
            ORDER BY x.recorded_at DESC LIMIT 1""",
            now.date(),
            now,
        )
    if (
        not report
        or report.get("slot_date") != now.date()
        or _stamp(report.get("recorded_at")) is None
        or _stamp(report["recorded_at"]) > now
    ):
        return None
    return report


async def _known_public_plans(pool, payload, now, *, funds_only=True):
    """Resolve public identities through the catalog; a direction is not identity proof."""
    plans = [
        plan
        for plan in (payload.get("actionablePlans") or [])[:24]
        if _public_coordinate(plan, now.date(), funds_only=funds_only)
    ]
    if not plans:
        return []
    fund_plans = [plan for plan in plans if plan["subjectKind"] in {"fund", "nav"}]
    identities = sorted(
        {plan["subjectId"] for plan in fund_plans if plan["subjectKind"] == "fund"}
    )
    codes = sorted(
        {plan["subjectId"] for plan in fund_plans if plan["subjectKind"] == "nav"}
    )
    graph, indices = [], []
    async with system_connection(pool) as conn:
        if fund_plans:
            graph = await conn.fetch(
                """SELECT NULL::text AS theme_code,c.fund_code AS asset_code,
            c.fund_name AS asset_name,NULL::text AS asset_kind,c.market,c.instrument_id,
            (SELECT count(*) FROM reference.research_fund_catalog other
              WHERE other.market=c.market AND other.fund_code=c.fund_code) AS mapping_count,
            c.trade_form,i.asset_type,true AS catalog_present,false AS graph_scope_verified
            FROM reference.research_fund_catalog c
            LEFT JOIN reference.instruments i ON i.id=c.instrument_id
            WHERE c.instrument_id::text=ANY($1::text[])
              OR (c.fund_code=ANY($2::text[]) AND c.market='FUND_CN')
            UNION ALL
            SELECT g.theme_code,g.asset_code,g.asset_name,g.asset_kind,g.market,
            NULL::uuid AS instrument_id,0::bigint AS mapping_count,NULL::text AS trade_form,
            NULL::text AS asset_type,false AS catalog_present,true AS graph_scope_verified
            FROM reference.theme_asset_graph g
            WHERE g.market='FUND_CN' AND g.asset_kind IN ('ETF_LINK_FUND','ACTIVE_FUND','LOF')
              AND g.asset_code=ANY($2::text[])
              AND NOT EXISTS(SELECT 1 FROM reference.research_fund_catalog c
                WHERE c.fund_code=g.asset_code AND c.market=g.market)
            ORDER BY market,asset_code,theme_code NULLS FIRST""",
                identities,
                codes,
            )
        index_ids = sorted(
            {plan["subjectId"] for plan in plans if plan["subjectKind"] == "index"}
        )
        if index_ids:
            indices = await conn.fetch(
                "SELECT id,name,market FROM reference.research_indices WHERE id=ANY($1::text[])",
                index_ids,
            )
    output = []
    for plan in plans:
        if plan["subjectKind"] == "index":
            matches = [
                row
                for row in indices
                if row["id"] == plan["subjectId"]
                and plan.get("market") in {None, row["market"]}
            ]
            if len(matches) == 1:
                output.append(({**plan, "label": matches[0]["name"]}, None))
            continue
        matches = [
            dict(row)
            for row in graph
            if (
                not row.get("theme_code")
                or not plan.get("themeCode")
                or row["theme_code"] == plan["themeCode"]
            )
            and (
                plan["subjectKind"] == "nav"
                and row["market"] == "FUND_CN"
                and plan["subjectId"] == row["asset_code"]
                or plan["subjectKind"] == "fund"
                and row.get("mapping_count") == 1
                and row.get("instrument_id") is not None
                and plan["subjectId"] == str(row["instrument_id"])
            )
        ]
        if len(matches) != 1:
            continue
        mapped = matches[0]
        mapped = {
            **mapped,
            "fund_code": mapped["asset_code"],
            "fund_name": mapped["asset_name"],
            "scope": {"fundCode": mapped["asset_code"], "fundMarket": mapped["market"]},
            "graph_scope_verified": mapped.get(
                "graph_scope_verified", mapped.get("mapping_count") in {None, 0}
            ),
            "graph_asset_kind": mapped.get("asset_kind"),
        }
        if (
            plan.get("fundCode") not in {None, mapped["asset_code"]}
            or any(
                plan.get(key) not in {None, mapped["market"]}
                for key in ("market", "fundMarket")
            )
            or not _mapped_otc(mapped)
        ):
            continue
        output.append(
            (
                {
                    **plan,
                    "label": mapped["asset_name"],
                    "fundCode": mapped["asset_code"],
                    "market": mapped["market"],
                },
                mapped,
            )
        )
    return output


async def _daily_plan_rows(pool, now):
    """Public plans are temporary checks, never private studies or trades."""
    report = await _today_report(pool, now)
    if report is None:
        return []
    plans = await _known_public_plans(pool, _body(report.get("payload")), now)
    output, seen = [], set()
    for plan, mapped in plans:
        selected, _ = _public_coordinate(plan, now.date(), funds_only=True)
        key = (mapped["market"], mapped["asset_code"], selected)
        if key in seen:
            continue
        seen.add(key)
        token = f"daily:{report['run_id']}:{mapped['asset_code']}:{selected}"
        output.append(
            {
                "track_id": token,
                "version_id": None,
                "scope": {
                    "fundCode": mapped["asset_code"],
                    "fundMarket": mapped["market"],
                },
                "snapshot": {"actionablePlan": {**plan, "selectedTrack": selected}},
                "instrument_id": mapped.get("instrument_id"),
                "fund_code": mapped["asset_code"],
                "fund_name": mapped["asset_name"],
                "market": mapped["market"],
                "mapping_count": mapped.get("mapping_count"),
                "trade_form": mapped.get("trade_form"),
                "asset_type": mapped.get("asset_type"),
                "graph_scope_verified": mapped["graph_scope_verified"],
                "graph_asset_kind": mapped.get("asset_kind"),
                "planOrigin": "DAILY_REPORT",
                "source": "DAILY_RESEARCH",
                "reportRunId": str(report["run_id"]),
            }
        )
    return output


async def _personal_risk_facts(pool, scope, now, *, quotes=False):
    from backend.investment.sell_risk import load_risk_targets, confirmed_quotes

    report = await _today_report(pool, now)
    daily = (
        {
            "state": "READY",
            "reportRunId": str(report["run_id"]),
            "report": _body(report["payload"]),
        }
        if report
        else None
    )
    async with tenant_transaction(pool, scope[1], scope[0]) as conn:
        targets = await load_risk_targets(conn, scope[0], scope[1], now, daily=daily)
        confirmed = await confirmed_quotes(conn, targets, now) if quotes else {}
    return targets, confirmed


def _risk_plan_row(target):
    if not isinstance(target.get("plan"), dict):
        return None
    instrument = UUID(target["instrumentId"]) if target.get("instrumentId") else None
    return {
        "track_id": target.get("trackId") or "risk:" + target["fundCode"],
        "version_id": target.get("versionId"),
        "scope": {"fundCode": target["fundCode"], "fundMarket": "FUND_CN"},
        "snapshot": {"actionablePlan": target["plan"]},
        "instrument_id": instrument,
        "fund_code": target["fundCode"],
        "fund_name": target["label"],
        "market": "FUND_CN",
        "mapping_count": 1,
        "trade_form": target["tradeForm"],
        "asset_type": "OFF_EXCHANGE_FUND" if instrument else None,
        "planOrigin": target["planOrigin"],
        "source": "PERSONAL_HOLDING",
        "reportRunId": target.get("reportRunId"),
    }


def _risk_quote(target, observation, now):
    if not isinstance(observation, dict):
        return None
    return {
        **observation,
        "fundCode": observation.get("fundCode", target["fundCode"]),
        "retrievedAt": now.isoformat(),
        "usable": True,
    }


async def sync_alpha_opportunities(
    pool, settings, now=None, *, now_provider=now_shanghai, price_loader=None
):
    stamp = _local(now if now is not None else now_provider())
    result = {"morning": {"status": "DISABLED"}, "fund1430": {"status": "DISABLED"}}
    scope = _scope(settings)
    if (
        scope is None
        or not await _active(pool, scope)
        or not getattr(settings, "wecom_opportunity_webhook_url", "")
    ):
        return result
    result["morning"] = {"status": "NOT_READY"}
    if stamp.time() >= time(8, 30):
        report = await _today_report(pool, stamp)
        if report:

            async def morning():
                payload = _body(report["payload"])
                known = await _known_public_plans(
                    pool, payload, stamp, funds_only=False
                )
                active = await _active(pool, scope)
                if _local(now_provider()).date() != stamp.date() or not active:
                    return "EXPIRED", {}
                await send_opportunity_webhook(
                    settings,
                    _morning_message(
                        {**payload, "actionablePlans": [plan for plan, _ in known]},
                        stamp.date(),
                    ),
                )
                return "SENT", {"reportRunId": str(report["run_id"])}

            result["morning"] = await _run_slot(
                pool, scope, stamp.date(), "MORNING", stamp, morning, now_provider
            )
    result["fund1430"] = {"status": "NOT_DUE"}
    if not time(14, 30) <= stamp.time() < time(15):
        return result
    async with system_connection(pool) as conn:
        trading = await conn.fetchval(
            "SELECT is_trading_day FROM reference.trading_calendars WHERE market='SSE' AND calendar_date=$1",
            stamp.date(),
        )
    if trading is not True:
        result["fund1430"] = {
            "status": "MARKET_CLOSED" if trading is False else "UNKNOWN_CALENDAR"
        }
        return result

    async def fund_check():
        studies = await _read_studies(pool, scope, stamp)
        studies = [
            {**row, "planOrigin": "FROZEN_STUDY", "source": "PRIVATE_FROZEN_PLAN"}
            for row in studies
            if _mapped_otc(row)
            and _identity_matches(row, row["snapshot"]["actionablePlan"])
        ]
        frozen_keys = {
            (
                row["market"],
                row["fund_code"],
                row["snapshot"]["actionablePlan"].get("selectedTrack"),
            )
            for row in studies
        }
        studies.extend(
            row
            for row in await _daily_plan_rows(pool, stamp)
            if (
                row["market"],
                row["fund_code"],
                row["snapshot"]["actionablePlan"].get("selectedTrack"),
            )
            not in frozen_keys
        )
        initial_targets, _ = await _personal_risk_facts(pool, scope, stamp)
        existing = {
            (row["fund_code"], row["snapshot"]["actionablePlan"].get("selectedTrack"))
            for row in studies
        }
        for target in initial_targets:
            row = _risk_plan_row(target)
            if (
                row
                and (
                    row["fund_code"],
                    row["snapshot"]["actionablePlan"].get("selectedTrack"),
                )
                not in existing
            ):
                studies.append(row)
                existing.add(
                    (
                        row["fund_code"],
                        row["snapshot"]["actionablePlan"].get("selectedTrack"),
                    )
                )
        if not studies and not initial_targets:
            return "NO_ALERT", {"checked": 0, "sellRiskAlerts": []}
        prices = (
            await (price_loader or load_opportunity_prices)(pool, studies, stamp)
            if studies
            else {}
        )
        active = await _active(pool, scope)
        clock = _local(now_provider())
        if (
            clock.date() != stamp.date()
            or not time(14, 30) <= clock.time() < time(15)
            or not active
        ):
            return "EXPIRED", {"checked": 0}
        from backend.investment.sell_risk import evaluate_sell_risk, _valid_quote

        targets, confirmed = await _personal_risk_facts(pool, scope, clock, quotes=True)
        current = {target["fundCode"]: target for target in targets}
        latest_studies = await _read_studies(pool, scope, clock)
        current_versions = {
            (str(row["track_id"]), str(row["version_id"]))
            for row in latest_studies
            if _mapped_otc(row)
            and _identity_matches(row, row["snapshot"]["actionablePlan"])
        }
        studies = [
            row
            for row in studies
            if (
                (
                    row.get("planOrigin") != "FROZEN_STUDY"
                    or (str(row["track_id"]), str(row.get("version_id")))
                    in current_versions
                )
                and (
                    row.get("source") != "PERSONAL_HOLDING"
                    or row["fund_code"] in current
                    and row["snapshot"]["actionablePlan"]
                    == current[row["fund_code"]].get("plan")
                )
            )
        ]
        from backend.investment.study_price_watch import current_entry_safety

        async with tenant_transaction(pool, scope[1], scope[0]) as conn:
            safety = await current_entry_safety(
                conn,
                [
                    {**row["snapshot"]["actionablePlan"], "fundCode": row["fund_code"]}
                    for row in studies
                ],
                clock,
            )
        active = await _active(pool, scope)
        clock = _local(now_provider())
        if (
            clock.date() != stamp.date()
            or not time(14, 30) <= clock.time() < time(15)
            or not active
        ):
            return "EXPIRED", {"checked": 0}
        observed_by_code = {}
        for row in studies:
            observed = prices.get(str(row["track_id"]))
            if observed is not None:
                observed_by_code.setdefault(row["fund_code"], observed)
        alerts, risk_estimates = [], []
        for target in targets:
            estimate = _risk_quote(
                target, observed_by_code.get(target["fundCode"]), clock
            )
            alert = evaluate_sell_risk(
                target,
                confirmed=confirmed.get(target["fundCode"]),
                estimate=estimate,
                now=clock,
            )
            if alert:
                alerts.append(alert)
            if estimate and _valid_quote(estimate, target, clock, estimate=True):
                risk_estimates.append(
                    {
                        **estimate,
                        **{
                            key: target.get(key)
                            for key in (
                                "trackId",
                                "versionId",
                                "reportRunId",
                                "planOrigin",
                                "plan",
                            )
                        },
                    }
                )
        rows = [
            {
                **row,
                "check": compare_frozen_price(
                    row["snapshot"]["actionablePlan"],
                    prices.get(str(row["track_id"])),
                    clock,
                    entry_safety=guard,
                ),
            }
            for row, guard in zip(studies, safety, strict=True)
        ]
        for row in rows:
            if row["fund_code"] not in current and row["check"]["state"] in {
                "STOP_LOSS",
                "TAKE_PROFIT_T1",
            }:
                row["check"] = {
                    **row["check"],
                    "state": "WATCH",
                    "reason": "UNOWNED_SELL_SUPPRESSED",
                }
        if not rows and not alerts:
            return "NO_ALERT", {
                "checked": 0,
                "sellRiskAlerts": [],
                "riskEstimates": risk_estimates,
            }
        await send_opportunity_webhook(
            settings, _fund_message(rows, stamp.date(), sell_risk_alerts=alerts)
        )
        return "SENT", {
            "checked": len(rows),
            "sellRiskAlerts": alerts,
            "riskEstimates": risk_estimates,
            "checks": [
                {
                    "trackId": str(row["track_id"])
                    if row["planOrigin"] == "FROZEN_STUDY"
                    else None,
                    "versionId": str(row["version_id"])
                    if row.get("version_id") is not None
                    else None,
                    "planOrigin": row["planOrigin"],
                    "source": row["source"],
                    "reportRunId": row.get("reportRunId"),
                    **row["check"],
                }
                for row in rows
            ],
        }

    result["fund1430"] = await _run_slot(
        pool, scope, stamp.date(), "FUND_1430", stamp, fund_check, now_provider
    )
    return result


def compute_confirmed_excursions(frozen, observations, through):
    """MFE/MAE of confirmed research coordinates; never a fill or realised P&L."""
    base_day, base = _day(frozen.get("asOf")), _number(frozen.get("currentPrice"))
    if base_day is None or base is None or base_day > through or not frozen.get("unit"):
        return {"state": "UNKNOWN"}
    selected = {}
    for point in observations:
        day = _day(point.get("asOf"))
        value = _number(point.get("value"))
        if (
            point.get("quality") != "CONFIRMED"
            or day is None
            or not base_day <= day <= through
        ):
            continue
        if (
            value is None
            or point.get("subjectKind") != frozen.get("subjectKind")
            or str(point.get("subjectId")) != str(frozen.get("subjectId"))
            or point.get("priceBasis") != frozen.get("priceBasis")
            or _unit(point.get("unit"), point.get("priceBasis"))
            != _unit(frozen.get("unit"), frozen.get("priceBasis"))
        ):
            return {"state": "UNKNOWN"}
        if day in selected and selected[day] != value:
            return {"state": "UNKNOWN"}
        selected[day] = value
    if selected.get(base_day) != base:
        return {"state": "UNKNOWN"}
    later = {day: value for day, value in selected.items() if day > base_day}
    if not later:
        return {"state": "NOT_READY"}
    changes = [(value / base - 1) * 100 for value in later.values()]
    return {
        "state": "CONFIRMED",
        "baseAsOf": base_day.isoformat(),
        "basePrice": format(base, "f"),
        "priceBasis": frozen["priceBasis"],
        "unit": frozen.get("unit"),
        "lastAsOf": max(later).isoformat(),
        "mfePct": format(max(Decimal("0"), *changes).quantize(Decimal("0.01")), "f"),
        "maePct": format(min(Decimal("0"), *changes).quantize(Decimal("0.01")), "f"),
        "sampleCount": len(later),
    }


async def load_confirmed_history(pool, studies, through, now):
    """Read published NAV facts only; never fetch estimates or move the baseline."""
    output = await _public_confirmed_history(pool, studies, through, now)
    async with system_connection(pool) as conn:
        for row in studies:
            frozen = row["snapshot"]["actionablePlan"]
            base_day = _day(frozen.get("asOf"))
            if base_day is None or frozen.get("priceBasis") != "UNIT_NAV":
                # Return-index histories need separately governed total-return
                # observations. Unit NAV must never be relabelled as that series.
                continue
            observations = await conn.fetch(
                """SELECT DISTINCT ON ((o.as_of AT TIME ZONE 'Asia/Shanghai')::date,o.source_id)
                o.value,o.as_of,o.unit,o.currency,o.quality_status
                FROM market.market_observations o
                JOIN market.source_snapshots s ON s.id=o.snapshot_id
                WHERE o.subject_id=$1 AND o.field_code='NAV'
                AND (o.as_of AT TIME ZONE 'Asia/Shanghai')::date BETWEEN $2 AND $3
                AND o.as_of<=$4 AND o.observed_at<=$4 AND s.retrieved_at<=$4
                AND o.quality_status IN ('FRESH','STALE','REVISED')
                AND s.quality_status IN ('FRESH','STALE','REVISED')
                ORDER BY (o.as_of AT TIME ZONE 'Asia/Shanghai')::date,o.source_id,
                  o.revision DESC,o.observed_at DESC,o.id DESC""",
                row["instrument_id"],
                base_day,
                through,
                now,
            )
            output.setdefault(str(row["track_id"]), []).extend(
                [
                    {
                        "subjectKind": frozen["subjectKind"],
                        "subjectId": frozen["subjectId"],
                        "priceBasis": "UNIT_NAV",
                        "unit": point["unit"],
                        "value": point["value"],
                        "asOf": point["as_of"],
                        "quality": "CONFIRMED",
                    }
                    for point in observations
                ]
            )
    return output


async def _public_confirmed_history(pool, studies, through, now):
    """Confirmed provider NAV in the persisted public batch, for unheld funds."""
    async with system_connection(pool) as conn:
        batch = await conn.fetchrow(
            """SELECT payload,completed_at FROM market.alpha_public_batches
            WHERE status IN ('READY','PARTIAL') AND completed_at<=$1 AND claimed_at<=$1
            ORDER BY run_date DESC,completed_at DESC LIMIT 1""",
            now,
        )
    if (
        not batch
        or _stamp(batch.get("completed_at")) is None
        or _stamp(batch["completed_at"]) > now
    ):
        return {}
    histories = _body(batch.get("payload")).get("fundHistories")
    if not isinstance(histories, list):
        return {}
    output = {}
    for row in studies:
        frozen = row["snapshot"]["actionablePlan"]
        if (
            frozen.get("priceBasis") != "UNIT_NAV"
            or _unit(frozen.get("unit"), "UNIT_NAV") != "CNY/份"
        ):
            continue
        matches = [
            item
            for item in histories
            if isinstance(item, dict) and item.get("fundCode") == row.get("fund_code")
        ]
        if len(matches) != 1:
            continue
        history = matches[0]
        if (
            history.get("priceBasis") != "UNIT_NAV"
            or not str(history.get("sourceUri", "")).startswith("https://")
            or any(
                _stamp(history.get(field)) is None or _stamp(history[field]) > now
                for field in ("retrievedAt", "availableAt")
            )
            or _day(history.get("asOf")) is None
            or _day(history["asOf"]) > now.date()
            or not isinstance(history.get("points"), list)
        ):
            continue
        output[str(row["track_id"])] = [
            {
                "subjectKind": frozen["subjectKind"],
                "subjectId": frozen["subjectId"],
                "priceBasis": "UNIT_NAV",
                "unit": "CNY/份",
                "asOf": point.get("date"),
                "value": point.get("close"),
                "quality": "CONFIRMED",
            }
            for point in history["points"]
            if isinstance(point, dict)
            and _day(point.get("date")) is not None
            and _day(point["date"]) <= min(through, _day(history["asOf"]))
        ]
    return output


async def sync_confirmed_excursions(
    pool, settings, now=None, *, now_provider=now_shanghai, history_loader=None
):
    """Update research excursions once published target-day NAV exists.

    Readiness is a database-only preflight, so an unpublished close does not
    consume the day. After claiming, partial coverage stays a recorded gap;
    the next target day recomputes the complete history from the frozen base.
    """
    stamp = _local(now if now is not None else now_provider())
    scope = _scope(settings)
    if scope is None or not await _active(pool, scope):
        return {"status": "DISABLED"}
    async with system_connection(pool) as conn:
        if stamp.time() >= time(18):
            trading = await conn.fetchval(
                "SELECT is_trading_day FROM reference.trading_calendars WHERE market='SSE' AND calendar_date=$1",
                stamp.date(),
            )
            if trading is not True:
                return {
                    "status": "MARKET_CLOSED"
                    if trading is False
                    else "UNKNOWN_CALENDAR"
                }
            target = stamp.date()
        else:
            target = await conn.fetchval(
                """SELECT max(calendar_date) FROM reference.trading_calendars
                WHERE market='SSE' AND is_trading_day IS TRUE AND calendar_date<$1""",
                stamp.date(),
            )
            if target is None:
                return {"status": "UNKNOWN_CALENDAR"}
    studies = [
        row
        for row in await _read_studies(pool, scope, stamp)
        if _mapped_otc(row)
        and _identity_matches(row, row["snapshot"]["actionablePlan"])
    ]
    instruments = list(
        {
            row["instrument_id"]
            for row in studies
            if row["snapshot"]["actionablePlan"].get("priceBasis") == "UNIT_NAV"
            and _day(row["snapshot"]["actionablePlan"].get("asOf")) is not None
            and _day(row["snapshot"]["actionablePlan"]["asOf"]) < target
        }
    )
    if not studies:
        return {"status": "NOT_READY", "targetDate": target.isoformat()}
    async with system_connection(pool) as conn:
        ready = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM market.market_observations o
            JOIN market.source_snapshots s ON s.id=o.snapshot_id
            WHERE o.subject_id=ANY($1::uuid[]) AND o.field_code='NAV'
            AND (o.as_of AT TIME ZONE 'Asia/Shanghai')::date=$2
            AND o.value>0 AND o.as_of<=$3 AND o.observed_at<=$3 AND s.retrieved_at<=$3
            AND o.quality_status IN ('FRESH','STALE','REVISED')
            AND s.quality_status IN ('FRESH','STALE','REVISED'))""",
            instruments,
            target,
            stamp,
        )
    if ready is not True:
        published = await _public_confirmed_history(pool, studies, target, stamp)
        ready = any(
            any(_day(point.get("asOf")) == target for point in facts)
            for facts in published.values()
        )
        if not ready:
            return {"status": "NOT_READY", "targetDate": target.isoformat()}

    async def confirmed():
        if not await _active(pool, scope):
            return "EXPIRED", {"updated": 0}
        facts = await (history_loader or load_confirmed_history)(
            pool, studies, target, stamp
        )
        updated, gaps = 0, []
        async with system_connection(pool) as conn:
            for row in studies:
                frozen = row["snapshot"]["actionablePlan"]
                metric = compute_confirmed_excursions(
                    frozen, facts.get(str(row["track_id"]), []), target
                )
                if metric["state"] != "CONFIRMED" or _day(metric["lastAsOf"]) != target:
                    gaps.append(
                        {
                            "trackId": str(row["track_id"]),
                            "versionId": str(row["version_id"]),
                            "state": metric["state"]
                            if metric["state"] != "CONFIRMED"
                            else "NOT_READY",
                        }
                    )
                    continue
                command = await conn.execute(
                    """INSERT INTO research.study_excursions
                    (tenant_id,user_id,track_id,version_id,base_as_of,base_price,price_basis,unit,
                     last_as_of,mfe_pct,mae_pct,sample_count,updated_at)
                    SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13
                    WHERE EXISTS(SELECT 1 FROM research.study_tracks t
                      JOIN identity.tenant_members m ON m.tenant_id=t.tenant_id AND m.user_id=t.user_id
                      WHERE t.tenant_id=$1 AND t.user_id=$2 AND t.id=$3 AND t.enabled AND m.status='ACTIVE')
                    ON CONFLICT(tenant_id,user_id,track_id,version_id) DO UPDATE SET
                    last_as_of=EXCLUDED.last_as_of,mfe_pct=EXCLUDED.mfe_pct,mae_pct=EXCLUDED.mae_pct,
                    sample_count=EXCLUDED.sample_count,updated_at=EXCLUDED.updated_at
                    WHERE study_excursions.base_as_of=EXCLUDED.base_as_of
                    AND study_excursions.base_price=EXCLUDED.base_price
                    AND study_excursions.price_basis=EXCLUDED.price_basis AND study_excursions.unit=EXCLUDED.unit
                    AND study_excursions.last_as_of<=EXCLUDED.last_as_of""",
                    *scope,
                    row["track_id"],
                    row["version_id"],
                    _day(metric["baseAsOf"]),
                    Decimal(metric["basePrice"]),
                    metric["priceBasis"],
                    metric["unit"],
                    _day(metric["lastAsOf"]),
                    Decimal(metric["mfePct"]),
                    Decimal(metric["maePct"]),
                    metric["sampleCount"],
                    _local(now_provider()),
                )
                updated += int(command.endswith(" 1"))
        return "COMPLETED", {
            "updated": updated,
            "missing": len(gaps),
            "gaps": gaps,
            "targetDate": target.isoformat(),
        }

    return await _run_slot(
        pool, scope, target, "CONFIRMED_NAV", stamp, confirmed, now_provider
    )


async def _wait(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def run_alpha_opportunity_service(
    pool, settings, *, stop_event=None, now_provider=now_shanghai, wait=_wait
):
    """Both workers may poll; durable unique slots arbitrate external attempts."""
    if _scope(settings) is None:
        return
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        for sync in (sync_alpha_opportunities, sync_confirmed_excursions):
            try:
                await sync(pool, settings, now_provider=now_provider)
            except Exception as exc:  # noqa: BLE001 -- independent services and next polling tick
                logger.warning("alpha_tick_failed error_class=%s", type(exc).__name__)
        await wait(stop_event, 60)
