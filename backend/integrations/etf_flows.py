"""Bounded, separately defined ETF share changes and secondary order flows.

Source schemas: AKShare fund_etf_scale_sse and fund_etf_spot_em (official
repository/docs, checked 2026-10-19). SSE TOT_VOL is in 10,000 shares; f62 is
Eastmoney's order-classified main net flow in CNY. Neither identifies owners.
No database, model, orders, denied-source retries or arbitrary URL input.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from zoneinfo import ZoneInfo

import httpx

from backend.dependencies import now_shanghai

SSE_URL = "https://query.sse.com.cn/commonQuery.do"
MAIN_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
MAX_BYTES = 2 * 1024 * 1024
METHOD = "etf-public-flow-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class _Unavailable(ValueError):
    pass


def _aware(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("ETF flow collection requires an aware clock")
    return value.astimezone(SHANGHAI)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and abs(result) <= Decimal("1e25") else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _source_uri(url, params):
    return str(httpx.URL(url, params=params))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate public source field")
        result[key] = value
    return result


async def _get(client, url, params, *, headers=None):
    async with client.stream(
        "GET", url, params=params, headers=headers, timeout=12, follow_redirects=False
    ) as response:
        response.raise_for_status()
        size = response.headers.get("content-length")
        if size and int(size) > MAX_BYTES:
            raise ValueError("public ETF response exceeds size limit")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("public ETF response exceeds size limit")
        return json.loads(body.decode("utf-8-sig"), object_pairs_hook=_unique_object)


def _failure(error):
    return {
        "status": "UNAVAILABLE"
        if isinstance(error, _Unavailable)
        else "TIMEOUT"
        if isinstance(error, (TimeoutError, httpx.TimeoutException))
        else "FAILED",
        "errorClass": type(error).__name__.lstrip("_"),
        **(
            {"httpStatus": error.response.status_code}
            if isinstance(error, httpx.HTTPStatusError)
            else {}
        ),
    }


def _base(identity, code, name, uri, stamp):
    return {
        "id": "etf-flow:" + hashlib.sha256(identity.encode()).hexdigest()[:24],
        "fundCode": code,
        "fundName": str(name or "")[:120],
        "sourceUri": uri,
        "retrievedAt": stamp.isoformat(),
        "availableAt": stamp.isoformat(),
        "publishedAt": None,
        "timeBasis": "RETRIEVAL_ONLY",
        "institutionAttribution": "UNKNOWN",
        "methodVersion": METHOD,
    }


def _calendar_dates(trading_dates, now):
    if not isinstance(trading_dates, (list, tuple)) or len(trading_dates) != 2:
        return None
    try:
        days = [
            value
            if isinstance(value, date) and not isinstance(value, datetime)
            else date.fromisoformat(str(value))
            for value in trading_dates
        ]
    except (TypeError, ValueError):
        return None
    return days if days[0] < days[1] <= now.date() else None


async def _sse_snapshot(client, day):
    params = {
        "isPagination": "true",
        "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
        "STAT_DATE": day.isoformat(),
    }
    uri = _source_uri(SSE_URL, params)
    payload = await _get(
        client, SSE_URL, params, headers={"Referer": "https://www.sse.com.cn/"}
    )
    rows = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise _Unavailable("exchange ETF share snapshot unavailable")
    parsed, conflicts, invalid = {}, set(), 0
    for row in rows[:10000]:
        if not isinstance(row, dict):
            invalid += 1
            continue
        code, shares = str(row.get("SEC_CODE") or ""), _number(row.get("TOT_VOL"))
        if (
            not re.fullmatch(r"\d{6}", code)
            or shares is None
            or shares < 0
            or row.get("STAT_DATE") != day.isoformat()
        ):
            invalid += 1
            continue
        point = {"shares": shares * 10000, "name": row.get("SEC_NAME")}
        if code in parsed and parsed[code]["shares"] != point["shares"]:
            conflicts.add(code)
        else:
            parsed[code] = point
    for code in conflicts:
        parsed.pop(code, None)
    page = payload.get("pageHelp") or {}
    total = _number(page.get("total")) if isinstance(page, dict) else None
    truncated = len(rows) > 10000 or total is not None and total > len(rows)
    return {
        "rows": parsed,
        "conflicts": conflicts,
        "invalid": invalid,
        "truncated": bool(truncated),
        "uri": uri,
        "received": len(rows),
    }


async def _share_signals(client, cutoff, trading_dates):
    days = _calendar_dates(trading_dates, cutoff)
    if days is None:
        return [], {
            "status": "UNAVAILABLE",
            "errorClass": "TradingCalendarUnavailable",
            "limitations": ["缺两个已确认完成的交易日，不用工作日猜测。"],
        }
    snapshots = await asyncio.gather(
        *(_sse_snapshot(client, day) for day in days), return_exceptions=True
    )
    if any(isinstance(row, BaseException) for row in snapshots):
        error = next(row for row in snapshots if isinstance(row, BaseException))
        return [], _failure(error)
    older, latest = snapshots
    retrieved = _aware(now_shanghai())
    conflicts = older["conflicts"] | latest["conflicts"]
    unpaired = (set(older["rows"]) ^ set(latest["rows"])) - conflicts
    rows = []
    for code in sorted(set(older["rows"]) & set(latest["rows"])):
        old, current = older["rows"][code], latest["rows"][code]
        delta = current["shares"] - old["shares"]
        if delta == 0:
            continue
        rows.append(
            {
                **_base(
                    f"SSE:{code}:{days[0]}:{days[1]}",
                    code,
                    current["name"],
                    latest["uri"],
                    retrieved,
                ),
                "metric": "ETF_SHARE_CHANGE",
                "value": format(delta, "f"),
                "unit": "份",
                "previousShares": format(old["shares"], "f"),
                "currentShares": format(current["shares"], "f"),
                "referenceDate": days[1].isoformat(),
                "comparisonDate": days[0].isoformat(),
                "comparisonSourceUri": older["uri"],
                "sourceKey": "ETF_SHARE_CHANGE_SSE",
                "verification": "EXCHANGE_REPORTED",
                "definition": "上交所两个交易日清算后ETF总份额差；原万份乘10000。可能受份额拆分合并影响，不等同现金净申购，无法识别机构身份。",
            }
        )
    rows.sort(key=lambda row: (-Decimal(row["value"]), row["fundCode"]))
    leaders = [row for row in rows if Decimal(row["value"]) > 0][:12]
    leaders += sorted(
        (row for row in rows if Decimal(row["value"]) < 0),
        key=lambda row: (Decimal(row["value"]), row["fundCode"]),
    )[:4]
    partial = bool(
        conflicts
        or unpaired
        or older["invalid"]
        or latest["invalid"]
        or older["truncated"]
        or latest["truncated"]
    )
    return leaders, {
        "status": "PARTIAL" if partial else "SUCCESS",
        "records": len(leaders),
        "comparedFundCount": len(set(older["rows"]) & set(latest["rows"])),
        "changedFundCount": len(rows),
        "conflictingFundCount": len(conflicts),
        "unpairedFundCount": len(unpaired),
        "referenceDate": days[1].isoformat(),
        "comparisonDate": days[0].isoformat(),
        "sourceUri": latest["uri"],
        "retrievedAt": retrieved.isoformat(),
        "limitations": [
            "仅上交所ETF；按份额变化列增量前12/减量前4，不是机构现金净申购榜。"
        ],
    }


async def _main_signals(client, cutoff, trading_dates):
    params = {
        "pn": "1",
        "pz": "60",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
        "fields": "f12,f14,f62,f124,f297",
    }
    uri = _source_uri(MAIN_URL, params)
    payload = await _get(client, MAIN_URL, params)
    data = payload.get("data") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("rc") != 0
        or not isinstance(data, dict)
        or not isinstance(data.get("diff"), list)
    ):
        raise _Unavailable("ETF secondary flow rows unavailable")
    raw, retrieved = data["diff"][:60], _aware(now_shanghai())
    rows, rejected, conflicts, observed = {}, 0, set(), {}
    days = _calendar_dates(trading_dates, cutoff)
    earliest = days[-1] if days else cutoff.date() - timedelta(days=10)
    for row in raw:
        if not isinstance(row, dict):
            rejected += 1
            continue
        code, value, seconds = (
            str(row.get("f12") or ""),
            _number(row.get("f62")),
            _number(row.get("f124")),
        )
        try:
            quote = (
                datetime.fromtimestamp(float(seconds), SHANGHAI)
                if seconds is not None and seconds > 0
                else None
            )
            text = str(row.get("f297") or "")
            day = (
                date(int(text[:4]), int(text[4:6]), int(text[6:8]))
                if re.fullmatch(r"\d{8}", text)
                else None
            )
        except (ValueError, OverflowError, OSError):
            quote, day = None, None
        if (
            not re.fullmatch(r"\d{6}", code)
            or value is None
            or quote is None
            or day is None
            or day != quote.date()
            or quote > cutoff
            or quote > retrieved
            or not earliest <= day <= cutoff.date()
        ):
            rejected += 1
            continue
        signature = value, day, quote
        if code in observed and observed[code] != signature:
            conflicts.add(code)
            continue
        observed[code] = signature
        rows[code] = {
            **_base(
                f"EASTMONEY:{code}:{quote.isoformat()}",
                code,
                row.get("f14"),
                uri,
                retrieved,
            ),
            "metric": "SECONDARY_MAIN_NET_FLOW_CNY",
            "value": format(value, "f"),
            "unit": "CNY",
            "referenceDate": day.isoformat(),
            "observedAt": quote.isoformat(),
            "sourceKey": "ETF_SECONDARY_MAIN_FLOW",
            "verification": "PROVIDER_REPORTED",
            "definition": "东方财富按订单分类的ETF二级市场主力净流入(f62)，不是一级市场净申购，不证明机构身份。",
        }
    for code in conflicts:
        rows.pop(code, None)
    ordered = sorted(
        rows.values(), key=lambda row: (-Decimal(row["value"]), row["fundCode"])
    )[:16]
    total = _number(data.get("total"))
    truncated = len(data["diff"]) > 60 or total is not None and total > len(raw)
    return ordered, {
        "status": "PARTIAL" if rejected or conflicts or truncated else "SUCCESS",
        "records": len(ordered),
        "providerTotal": int(total) if total is not None and total >= 0 else None,
        "receivedCount": len(raw),
        "rejectedCount": rejected,
        "truncated": bool(truncated),
        "sourceUri": uri,
        "retrievedAt": retrieved.isoformat(),
        "limitations": [
            "供应商主力净流入降序单页最多60条、展示前16；非全市场完整资金榜，无法识别机构身份。"
        ],
    }


async def fetch_etf_flow_signals(now, *, trading_dates, client=None):
    """Fetch independent public definitions once; caller supplies calendar proof."""
    cutoff = _aware(now)
    if client is None:
        async with httpx.AsyncClient(
            headers={"User-Agent": "WealthSail public macro research"},
            follow_redirects=False,
        ) as owned:
            return await fetch_etf_flow_signals(
                cutoff, trading_dates=trading_dates, client=owned
            )

    async def collect(adapter):
        try:
            return await asyncio.wait_for(
                adapter(client, cutoff, trading_dates), timeout=15
            )
        except Exception as error:  # noqa: BLE001 -- independent public source failure
            return [], _failure(error)

    share, main = await asyncio.gather(collect(_share_signals), collect(_main_signals))
    return {
        "methodVersion": METHOD,
        "signals": share[0] + main[0],
        "sourceResults": {
            "ETF_SHARE_CHANGE_SSE": share[1],
            "ETF_SECONDARY_MAIN_FLOW": main[1],
        },
    }
