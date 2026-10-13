"""Pinned AKShare index valuations and explicitly disclosed annual fund fees."""

import asyncio
import json
import logging
import math
import re
from datetime import date
from decimal import Decimal

logger = logging.getLogger("wealthsail.investment_fundamentals")
_TIMEOUT_SECONDS = 20
_SYMBOLS = {
    "上证50",
    "沪深300",
    "上证380",
    "创业板50",
    "中证500",
    "上证180",
    "深证红利",
    "深证100",
    "中证1000",
    "上证红利",
    "中证100",
    "中证800",
}
_VALUATION = (
    ("pe", "stock_index_pe_lg", "滚动市盈率", "PE_TTM"),
    ("pb", "stock_index_pb_lg", "市净率", "PB"),
)
_FEE_LABELS = {
    "管理费率": "managementRate",
    "托管费率": "custodyRate",
    "销售服务费率": "salesServiceRate",
}


def _valuation_config(subject):
    try:
        metadata = subject.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        config = subject.get("valuation") or metadata.get("research", {}).get(
            "valuation", {}
        )
        return config if isinstance(config, dict) else {}
    except (TypeError, ValueError, AttributeError):
        return {}


def _empty_series(metric, state, source_uri, reasons):
    return {
        "state": state,
        "metric": metric,
        "points": [],
        "sourceUri": source_uri,
        "historyStart": None,
        "historyEnd": None,
        "sampleCount": 0,
        "reasons": list(reasons),
    }


async def fetch_index_valuation(subject, start: date, end: date, *, provider=None):
    """Fetch only registered valuation series; end is a published trading day.

    The caller resolves publication hour and exchange calendar before passing
    end. No calendar dates, valuation observations or missing bars are filled.
    """
    if start > end:
        raise ValueError("valuation start must not be after end")
    config = _valuation_config(subject)
    result = {
        "state": "UNVERIFIED",
        "provider": config.get("provider"),
        "symbol": config.get("symbol"),
        "sourceUri": config.get("sourceUri"),
        "requestedStart": start.isoformat(),
        "requestedEnd": end.isoformat(),
        "reasons": [],
    }
    if (
        config.get("verified") is not True
        or not isinstance(config.get("sourceUri"), str)
        or not config["sourceUri"].strip()
        or type(config.get("publishHour")) is not int
        or not 0 <= config["publishHour"] <= 23
    ):
        result["reasons"] = ["指数估值来源、披露时间或核验登记不完整。"]
    elif (
        config.get("provider") != "LEGULEGU"
        or not isinstance(config.get("symbol"), str)
        or config["symbol"] not in _SYMBOLS
    ):
        result["state"] = "UNSUPPORTED"
        result["reasons"] = ["锁定版本仅支持乐咕乐股白名单指数的滚动市盈率及市净率。"]
    else:
        result["state"] = "READY"
    for key, _, _, metric in _VALUATION:
        result[key] = _empty_series(
            metric, result["state"], result["sourceUri"], result["reasons"]
        )
    if result["state"] != "READY":
        return result
    try:
        if provider is None:
            import akshare as provider
    except Exception as exc:
        result["state"] = "UNAVAILABLE"
        result["reasons"] = [f"估值供应商不可用：{type(exc).__name__}。"]
        for key, _, _, metric in _VALUATION:
            result[key] = _empty_series(
                metric, "UNAVAILABLE", result["sourceUri"], result["reasons"]
            )
        return result
    for key, function, column, metric in _VALUATION:
        try:
            frame = await asyncio.wait_for(
                asyncio.to_thread(getattr(provider, function), symbol=config["symbol"]),
                timeout=_TIMEOUT_SECONDS,
            )
            if (
                frame is None
                or "日期" not in frame.columns
                or column not in frame.columns
            ):
                raise ValueError("valuation source schema unavailable")
            by_day = {}
            for row in frame.to_dict("records"):
                day = date.fromisoformat(str(row["日期"])[:10])
                if not start <= day <= end:
                    continue
                raw_value = row[column]
                value = float(raw_value)
                if (
                    isinstance(raw_value, bool)
                    or not math.isfinite(value)
                    or value <= 0
                ):
                    raise ValueError("valuation must be positive and finite")
                if day in by_day:
                    raise ValueError("duplicate valuation date")
                by_day[day] = value
            points = [
                {"date": day.isoformat(), "value": by_day[day]}
                for day in sorted(by_day)
            ]
            if not points:
                raise ValueError("valuation window has no valid observations")
            state = "STALE" if points[-1]["date"] < end.isoformat() else "READY"
            result[key] = {
                "state": state,
                "metric": metric,
                "points": points,
                "sourceUri": config["sourceUri"],
                "historyStart": points[0]["date"],
                "historyEnd": points[-1]["date"],
                "sampleCount": len(points),
                "reasons": []
                if state == "READY"
                else ["估值历史未达到最新已发布交易日。"],
            }
        except Exception as exc:
            logger.warning(
                "index_valuation_fetch_failed field=%s error_class=%s",
                key,
                type(exc).__name__,
            )
            result[key] = _empty_series(
                metric,
                "UNAVAILABLE",
                result["sourceUri"],
                [f"估值来源不可用：{type(exc).__name__}。"],
            )
    states = [result[key]["state"] for key in ("pe", "pb")]
    result["state"] = (
        "UNAVAILABLE"
        if all(state == "UNAVAILABLE" for state in states)
        else "PARTIAL"
        if "UNAVAILABLE" in states
        else "STALE"
        if "STALE" in states
        else "READY"
    )
    result["reasons"] = [
        reason for key in ("pe", "pb") for reason in result[key]["reasons"]
    ]
    return result


async def fetch_fund_costs(code, *, provider=None):
    """Read annual operating rates only; unknown sales charges are never zero."""
    result = {
        "state": "UNAVAILABLE",
        **{key: None for key in _FEE_LABELS.values()},
        "totalRate": None,
        "sourceUri": f"https://fundf10.eastmoney.com/jjfl_{code}.html",
        "reasons": [],
    }
    try:
        if provider is None:
            import akshare as provider
        frame = await asyncio.wait_for(
            asyncio.to_thread(provider.fund_fee_em, symbol=code, indicator="运作费用"),
            timeout=_TIMEOUT_SECONDS,
        )
        if frame is None or frame.empty:
            raise ValueError("operating fee table unavailable")
        disclosed = {label: [] for label in _FEE_LABELS}
        for row in frame.to_dict("records"):
            explicit = [(label, row[label]) for label in _FEE_LABELS if label in row]
            cells = list(row.values())
            explicit.extend(
                (cell, cells[i + 1])
                for i, cell in enumerate(cells[:-1])
                if isinstance(cell, str) and cell in _FEE_LABELS
            )
            for label, value in explicit:
                match = re.fullmatch(
                    r"([0-9]+(?:\.[0-9]+)?)%(?:（每年）)?", str(value).strip()
                )
                rate = float(Decimal(match[1]) / 100) if match else None
                if rate is not None and (not math.isfinite(rate) or rate > 1):
                    rate = None
                disclosed[label].append(rate)
        for label, rates in disclosed.items():
            result[_FEE_LABELS[label]] = (
                rates[0]
                if rates and None not in rates and len(set(rates)) == 1
                else None
            )
        if all(result[key] is not None for key in _FEE_LABELS.values()):
            result["totalRate"] = float(
                sum(Decimal(str(result[key])) for key in _FEE_LABELS.values())
            )
            result["state"] = "READY"
        else:
            result["reasons"] = [
                "管理、托管或销售服务费用未明确完整披露，未知费率不填零。"
            ]
    except Exception as exc:
        logger.warning("fund_cost_fetch_failed error_class=%s", type(exc).__name__)
        result["reasons"] = [f"基金费用来源不可用：{type(exc).__name__}。"]
    return result
