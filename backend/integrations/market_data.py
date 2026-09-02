"""Market data integration adapters (Port / Adapter boundary).

Unified ``Observation`` contract for every external source, with a
degradation chain so a single vendor outage never kills the pipeline:

  * A股 / 场内 ETF — AkShare (东方财富) preferred, httpx → Sina hq.sinajs.cn
    fallback. Both return live quotes → FRESH;
  * 场外基金 — 天天基金/东方财富 f10/lsjz (async httpx, 10s timeout). The
    last published NAV is FRESH only when published today, otherwise STALE.
    (The intraday estimate endpoint fundgz.1234567.com.cn is currently
    offline upstream — the adapter no longer depends on it.)

Quality semantics (fail closed): live quote → FRESH; published NAV (stale
date) → STALE; fetch failure → UNAVAILABLE. All numeric values are Decimal.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("wealthsail.market_data")

DEFAULT_TIMEOUT = 10.0  # seconds (httpx constraint from the spec)

# field_code vocabulary (aligns with market.market_observations.field_code)
F_CLOSE = "CLOSE"
F_NAV = "NAV"
F_ESTIMATED_NAV = "ESTIMATED_NAV"

# quality vocabulary (aligns with market.source_snapshots.quality_status)
Q_FRESH = "FRESH"
Q_ESTIMATED = "ESTIMATED"
Q_STALE = "STALE"
Q_UNAVAILABLE = "UNAVAILABLE"

AKSOURCE_NAME = "AkShare-OpenSource"
FUNDSOURCE_NAME = "TiantianFund-EastMoney"
SINASOURCE_NAME = "Sina-Finance-Quote"
ESTIMATOR_SOURCE_NAME = "WealthSail-Fund-Estimator"
EASTMONEY_INDEX_SOURCE_NAME = "EastMoney-Major-Indices"
EASTMONEY_SECTOR_SOURCE_NAME = "EastMoney-Industry-Boards"
AKSOURCE_TYPE = "MARKET_DATA"
FUNDSOURCE_TYPE = "FUND_NAV"
ESTIMATOR_SOURCE_TYPE = "FUND_NAV"

_SINA_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_EASTMONEY_INDEX_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_EASTMONEY_SECTOR_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_MAJOR_INDEX_SECIDS = "1.000001,0.399001,0.399006"


def _optional_decimal_text(value: object) -> str | None:
    try:
        return str(_decimal(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _parse_major_indices_payload(payload: dict) -> list[dict]:
    """Normalize EastMoney's major-index response without inventing values."""
    diff = (payload.get("data") or {}).get("diff") or []
    rows: list[dict] = []
    for raw in diff:
        symbol = str(raw.get("f12") or "").strip()
        name = str(raw.get("f14") or "").strip()
        price = _optional_decimal_text(raw.get("f2"))
        change_pct = _optional_decimal_text(raw.get("f3"))
        turnover = _optional_decimal_text(raw.get("f6"))
        if not symbol or not name or price is None or change_pct is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "price": price,
                "changePct": change_pct,
                "turnover": turnover,
            }
        )
    return rows


def _major_indices_source_as_of(payload: dict) -> datetime | None:
    timestamps: list[int] = []
    for raw in (payload.get("data") or {}).get("diff") or []:
        try:
            timestamp = int(raw.get("f124"))
        except (TypeError, ValueError):
            continue
        if timestamp > 0:
            timestamps.append(timestamp)
    # The oldest adopted component controls whole-snapshot freshness; one
    # current row must never mask another stale index row.
    return (
        datetime.fromtimestamp(min(timestamps), tz=timezone.utc) if timestamps else None
    )


def _major_indices_quality(source_as_of: datetime | None, now: datetime) -> str:
    """Only same-Shanghai-date quotes can be described as fresh."""
    if source_as_of is None:
        return Q_STALE
    shanghai = ZoneInfo("Asia/Shanghai")
    source_date = source_as_of.astimezone(shanghai).date()
    return Q_FRESH if source_date == now.astimezone(shanghai).date() else Q_STALE


async def get_major_indices_snapshot() -> dict:
    """Fetch the three headline A-share indices for the daily public brief."""
    retrieved_at = datetime.now(timezone.utc)
    source_as_of: datetime | None = None
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                _EASTMONEY_INDEX_URL,
                params={
                    "fltt": "2",
                    "invt": "2",
                    "fields": "f12,f14,f2,f3,f6,f124",
                    "secids": _MAJOR_INDEX_SECIDS,
                },
                headers={"User-Agent": _SINA_UA},
            )
            response.raise_for_status()
            payload = response.json()
            items = _parse_major_indices_payload(payload)
            source_as_of = _major_indices_source_as_of(payload)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("major indices fetch failed: %s", exc)
        items = []
        source_as_of = None
    return {
        "items": items,
        "source": EASTMONEY_INDEX_SOURCE_NAME if items else "unavailable",
        "sourceAsOf": source_as_of.isoformat() if source_as_of else None,
        "retrievedAt": retrieved_at.isoformat(),
        "qualityStatus": (
            _major_indices_quality(source_as_of, retrieved_at)
            if items
            else Q_UNAVAILABLE
        ),
        "methodVersion": "eastmoney-major-indices/v1",
    }


def _parse_sector_focus_payload(payload: dict) -> dict | None:
    """Normalize the top EastMoney industry-board row."""
    diff = (payload.get("data") or {}).get("diff") or []
    for raw in diff:
        symbol = str(raw.get("f12") or "").strip()
        name = str(raw.get("f14") or "").strip()
        change_pct = _optional_decimal_text(raw.get("f3"))
        if symbol and name and change_pct is not None:
            return {"symbol": symbol, "name": name, "changePct": change_pct}
    return None


async def get_sector_focus_snapshot() -> dict:
    """Fetch the leading governed EastMoney industry board once per cache miss."""
    retrieved_at = datetime.now(timezone.utc)
    source_as_of: datetime | None = None
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                _EASTMONEY_SECTOR_URL,
                params={
                    "pn": "1",
                    "pz": "1",
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": "m:90+t:2+f:!50",
                    "fields": "f12,f14,f3,f124",
                },
                headers={"User-Agent": _SINA_UA},
            )
            response.raise_for_status()
            payload = response.json()
            item = _parse_sector_focus_payload(payload)
            source_as_of = _major_indices_source_as_of(payload)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("industry sector snapshot fetch failed: %s", exc)
        item = None
    return {
        "item": item,
        "source": EASTMONEY_SECTOR_SOURCE_NAME if item else "unavailable",
        "sourceAsOf": source_as_of.isoformat() if source_as_of else None,
        "retrievedAt": retrieved_at.isoformat(),
        "qualityStatus": (
            _major_indices_quality(source_as_of, retrieved_at)
            if item
            else Q_UNAVAILABLE
        ),
        "methodVersion": "eastmoney-industry-board/v1",
    }


PRE_MARKET_GLOBAL_METHOD_VERSION = "pre-market-global/v2"
PRE_MARKET_GLOBAL_SOURCE_NAME = "unavailable"

# 进程内缓存：盘前快照 30 分钟内复用，避免每次报告生成都重复外网抓取；
# 抓取超时时降级到上次成功缓存（标注 STALE）或 unavailable（fail-closed）。
_PRE_MARKET_GLOBAL_TTL_SECONDS = 30 * 60
_pre_market_global_cache: dict = {"ts": 0.0, "snapshot": None}


def _reset_pre_market_global_cache() -> None:
    """清空进程内缓存（测试用，避免用例间串扰）。"""
    _pre_market_global_cache["ts"] = 0.0
    _pre_market_global_cache["snapshot"] = None


# 美股三大指数在 AkShare 不同接口下的中文别名，统一映射到 dow/nasdaq/sp500。
_US_INDEX_ALIASES = {
    "道琼斯": "dow",
    "道琼斯工业指数": "dow",
    "道琼斯工业平均指数": "dow",
    "纳斯达克": "nasdaq",
    "纳斯达克综合指数": "nasdaq",
    "标普500": "sp500",
    "标普500指数": "sp500",
    "标准普尔500指数": "sp500",
}

# 美股三大指数在 AkShare 新浪源（index_us_stock_sina）下的 symbol 与中文名。
# 道琼斯 .DJI / 纳斯达克 .IXIC / 标普500 .INX。
_US_SINA_SYMBOLS = (
    (".DJI", "道琼斯", "dow"),
    (".IXIC", "纳斯达克", "nasdaq"),
    (".INX", "标普500", "sp500"),
)


async def fetch_us_overnight_indices() -> list[dict]:
    """美股三大指数最新涨跌幅（AkShare ``index_us_stock_sina`` 新浪源）。

    逐标的调用 ``ak.index_us_stock_sina``，取历史序列最新一条，用最新收盘与前一
    收盘计算涨跌幅 ``(close[-1]-close[-2])/close[-2]*100``。任一标的超时或解析
    失败仅跳过该标的；全部失败返回空列表（下游渲染「暂无」），不抛出、不伪造数据。
    """
    try:
        ak = _ak_import()
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.warning("akshare 不可用，美股指数降级: %s", exc)
        return []
    results: list[dict] = []
    for symbol, name, _key in _US_SINA_SYMBOLS:
        item = await _fetch_single_us_index(ak, symbol, name)
        if item is not None:
            results.append(item)
    return results


async def _fetch_single_us_index(ak, symbol: str, name: str) -> dict | None:
    """单次抓取单个美股指数，失败降级 None。"""
    try:
        df = await asyncio.to_thread(ak.index_us_stock_sina, symbol)
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.warning("akshare 美股指数 %s 抓取失败: %s", symbol, exc)
        return None
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return None
    try:
        closes = df["close"].dropna().astype(float)
        if len(closes) < 2:
            return None
        latest, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
        if not prev or not latest:
            return None
        return {
            "name": name,
            "changePct": (latest - prev) / prev * 100,
            "price": latest,
        }
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning("akshare 美股指数 %s 解析失败: %s", symbol, exc)
        return None


# 东财国际期货中「A50期指当月连续」的品种代码（futures_global_spot_em）。
_EM_A50_SYMBOL = "CN00Y"


async def fetch_a50_future() -> dict | None:
    """富时中国 A50 期货（新加坡交易所）最新涨跌幅。

    抓取链（任一环节失败即降级，全部失败返回 ``None`` → 渲染为「暂无」）::

        1. 新浪 hq.sinajs.cn hf_CHA50CFD   — 单请求、实时，按昨结算计算涨跌幅
        2. 东财 futures_global_spot_em CN00Y — 官方涨跌幅（分页抓取，较重）

    说明：AkShare ``futures_foreign_hist`` 的外盘代码表中**不含 A50**（仅有
    FEF/FCPO 等 30 个品种），新浪 ``nfSA0`` 现已返回空串，故均不可用。
    """
    a50 = await _fetch_sina_a50()
    if a50 is None:
        a50 = await _fetch_a50_via_em()
    return a50


# 返回字典键 → 美股指数中文名（与盘前简报展示顺序一致）。
_US_MARKET_KEYS = (
    ("dow", "道琼斯"),
    ("nasdaq", "纳斯达克"),
    ("sp500", "标普500"),
)


async def fetch_pre_market_global_snapshot() -> dict:
    """盘前美股 + A50 真实涨跌幅快照（flat 格式，供盘前简报直接使用）。

    美股：AkShare ``index_us_stock_sina`` 取 .DJI/.IXIC/.INX 最新一条换算涨跌幅；
    A50：新浪 ``hq.sinajs.cn/list=hf_CHA50CFD`` 优先，失败降级东财
    ``futures_global_spot_em(CN00Y)``。任一数据源不可用对应字段降级
    （``us_markets`` 缺项 / ``a50=None``），绝不编造数字。

    返回::

        {
            "us_markets": {"dow": 0.42, "nasdaq": 0.61, "sp500": 0.15},
            "a50": 0.28,
            "source": "akshare",  # 或 "sina" / "akshare+sina" / "unavailable"
            "as_of": "2026-09-02 08:30:00",
        }
    """
    us_items, a50 = await asyncio.gather(
        fetch_us_overnight_indices(), fetch_a50_future()
    )
    us_markets: dict[str, float] = {}
    for item in us_items:
        key = _US_INDEX_ALIASES.get(str(item.get("name") or ""))
        if key and key not in us_markets:
            us_markets[key] = round(float(item["changePct"]), 2)
    a50_pct: float | None = None
    if isinstance(a50, dict) and a50.get("changePct") is not None:
        a50_pct = round(float(a50["changePct"]), 2)
    # 顶层 source：仅标注数据实际来源（akshare / sina），混合时并列。
    sources: list[str] = []
    if us_items:
        sources.append("akshare")
    if a50 is not None:
        a50_src = "akshare" if "akshare" in str(a50.get("source") or "") else "sina"
        if a50_src not in sources:
            sources.append(a50_src)
    source = "+".join(sources) if sources else "unavailable"
    as_of = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "us_markets": us_markets,
        "a50": a50_pct,
        "source": source,
        "as_of": as_of,
    }


async def _fetch_sina_a50() -> dict | None:
    """新浪实时 SGX A50（``hq.sinajs.cn/list=hf_CHA50CFD``，GBK 编码）。

    字段布局（已与东财 CN00Y 交叉验证：昨结算/今开/最高/最低/持仓量均一致）::

        0 当前价, 1 买价, 2 卖价, 3 均价, 4 最高, 5 最低, 6 时间, 7 昨结算,
        8 今开,  9 持仓量, 10 买量, 11 卖量, 12 日期, 13 名称, 14 成交量

    涨跌幅按期货惯例以**昨结算价**为基准。字段不足或数值缺失时降级 ``None``。
    """
    url = "https://hq.sinajs.cn/list=hf_CHA50CFD"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": _SINA_UA,
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            response.raise_for_status()
            text = response.content.decode("gbk", errors="replace")
    except httpx.HTTPError as exc:
        logger.warning("新浪 A50 抓取失败: %s", exc)
        return None
    if '="' not in text:
        return None
    try:
        fields = text.split('="', 1)[1].rstrip('";\n').split(",")
    except Exception:  # noqa: BLE001
        return None
    if len(fields) < 14:
        logger.warning("新浪 A50 字段数不足(%d): %s", len(fields), fields[:15])
        return None
    price = _safe_float(fields[0])
    prev_settle = _safe_float(fields[7])
    name = fields[13].strip()
    if price is None or price <= 0 or prev_settle in (None, 0):
        return None
    return {
        "name": name or "富时中国A50期货",
        "changePct": (price - prev_settle) / prev_settle * 100,
        "price": price,
        "source": "sina/a50",
    }


async def _fetch_a50_via_em() -> dict | None:
    """东财国际期货实时行情中的 A50 当月连续（代码 ``CN00Y``）。

    ``futures_global_spot_em`` 单次返回全部国际期货（约 640 行、分页抓取），
    故仅在新浪降级失败时使用；直接取用官方 ``涨跌幅``，无需自行换算。
    """
    try:
        df = await asyncio.to_thread(_ak_import().futures_global_spot_em)
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.info("东财国际期货接口不可用: %s", exc.__class__.__name__)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    if "代码" not in df.columns:
        logger.warning("东财国际期货列结构变化: %s", list(df.columns))
        return None
    try:
        rows = df[df["代码"].astype(str).str.strip() == _EM_A50_SYMBOL]
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning("东财 A50 过滤失败: %s", exc)
        return None
    if rows.empty:
        return None
    row = rows.iloc[0]
    change = _num(row, "涨跌幅")
    price = _num(row, "最新价")
    if change is None:
        return None
    return {
        "name": str(row.get("名称") or "富时中国A50期货"),
        "changePct": change,
        "price": price,
        "source": "akshare/a50-em",
    }


# 盘前"经济数据"展示的宏观指标（AkShare 宏观接口，官方口径）。
# 任一指标超时/缺失都跳过该指标；全部失败则返回空列表（渲染为「暂无」）。
_ECONOMIC_AKSHARE_SPECS = (
    # (akshare 函数名, 取值列, 展示名, 单位/后缀)
    ("macro_china_cpi", "全国-同比增长", "中国CPI同比", "%"),
    ("macro_china_pmi", "制造业-指数", "中国制造业PMI", ""),
)


async def fetch_economic_calendar() -> list[dict]:
    """盘前经济数据：取 AkShare 宏观接口最新一期 CPI / PMI。

    - CPI：``ak.macro_china_cpi`` 的「全国-同比增长」（CPI 同比）
    - PMI：``ak.macro_china_pmi`` 的「制造业-指数」（制造业 PMI）

    返回 ``[{summary, title, name, date, value, unit, source}]``。任一指标超时或
    解析失败都跳过该指标；全部失败则返回空列表（下游渲染为「暂无」），绝不编造数字。
    """
    items: list[dict] = []
    ak = _ak_import()
    for func_name, col, label, suffix in _ECONOMIC_AKSHARE_SPECS:
        try:
            func = getattr(ak, func_name)
            df = await asyncio.to_thread(func)
        except Exception as exc:  # noqa: BLE001 — vendor outage degrades
            logger.warning("akshare 经济数据 %s 抓取失败: %s", func_name, exc)
            continue
        if df is None or getattr(df, "empty", True) or col not in df.columns:
            logger.warning(
                "akshare 经济数据 %s 列结构变化: %s",
                func_name,
                list(getattr(df, "columns", [])),
            )
            continue
        try:
            row = df.iloc[0]
            value = _num(row, col)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            logger.warning("akshare 经济数据 %s 解析失败: %s", func_name, exc)
            continue
        if value is None:
            continue
        date_str = str(row.get("月份") or "")
        unit = suffix or ""
        value_text = f"{value}{unit}" if unit else str(value)
        items.append(
            {
                "summary": f"{label}（{date_str}）：{value_text}",
                "title": label,
                "name": label,
                "date": date_str,
                "value": value,
                "unit": unit,
                "source": "AkShare/macro",
            }
        )
    return items


# 公司行动（分红/送转）来自 AkShare stock_dividend，按持仓代码过滤。该接口返回全
# 市场分红，体积较大，故进程内缓存 6 小时，避免每次报告生成都重复抓取。
_CORPORATE_ACTIONS_TTL_SECONDS = 6 * 60 * 60
_corporate_actions_cache: dict = {"ts": 0.0, "items": None}


def _reset_corporate_actions_cache() -> None:
    """清空公司行动缓存（测试用）。"""
    _corporate_actions_cache["ts"] = 0.0
    _corporate_actions_cache["items"] = None


# 今日公司公告（巨潮 cninfo 披露）只取持仓相关、标题含关注关键词的事件。
# 单标的约 1.5s，进程内缓存 15 分钟避免每份报告重复抓取；全部失败降级为空。
_ANNOUNCEMENTS_TTL_SECONDS = 15 * 60
_company_announcements_cache: dict = {"ts": 0.0, "key": ("", ""), "items": []}
_TITLE_FOCUS_KEYWORDS = (
    "报告",  # 定期报告/业绩快报等
    "业绩",  # 业绩预告/业绩说明
    "分红",  # 权益分派/分红
    "回购",  # 回购/增持
    "重组",  # 重组/收购/重大资产
    "停牌",  # 停牌/复牌
    "减持",  # 减持/限售
    "中标",  # 重大合同/中标
    "违规",  # 立案/处罚/违规
)


def _reset_company_announcements_cache() -> None:
    """清空今日公告缓存（测试用）。"""
    _company_announcements_cache["ts"] = 0.0
    _company_announcements_cache["key"] = ("", "")
    _company_announcements_cache["items"] = []


async def fetch_today_company_announcements(
    symbols: list[str], trade_date: object
) -> list[dict]:
    """持仓标今日公司公告（AkShare 巨潮 cninfo 披露，逐标的并行抓取）。

    返回 ``[{symbol, name, title, date}]``，只保留公告日期 == ``trade_date``
    且标题命中关注关键词的条目。任一标的失败仅跳过该标的；全部失败返回空列表
    （渲染「今日暂无重大事件关注」），绝不编造。
    """
    if not symbols:
        return []
    wanted = sorted(
        {str(symbol).zfill(6) for symbol in symbols if str(symbol).zfill(6).isdigit()}
    )
    if not wanted:
        return []
    date_text = str(trade_date)
    now = datetime.now(timezone.utc).timestamp()
    cached_key = _company_announcements_cache["key"]
    cached = _company_announcements_cache["items"]
    if (
        cached is not None
        and cached_key == (tuple(wanted), date_text)
        and (now - _company_announcements_cache["ts"]) < _ANNOUNCEMENTS_TTL_SECONDS
    ):
        return cached
    per_symbol = await asyncio.gather(
        *(_fetch_announcements_for_symbol(code, date_text) for code in wanted),
        return_exceptions=True,
    )
    items: list[dict] = []
    for outcome in per_symbol:
        if isinstance(outcome, list):
            items.extend(outcome)
    items.sort(key=lambda item: (item["symbol"], item["title"]))
    _company_announcements_cache.update(
        ts=now, key=(tuple(wanted), date_text), items=items
    )
    return items


async def _fetch_announcements_for_symbol(symbol: str, date_text: str) -> list[dict]:
    """抓取单标的某日公告并按关注关键词过滤（网络异常降级为空）。"""
    compact = date_text.replace("-", "")
    try:
        df = await asyncio.to_thread(
            _ak_import().stock_zh_a_disclosure_report_cninfo,
            symbol=symbol,
            market="沪深京",
            start_date=compact,
            end_date=compact,
        )
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.info("akshare 公告抓取失败(%s): %s", symbol, exc.__class__.__name__)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    title_col = next(
        (column for column in ("公告标题", "标题") if column in df.columns), None
    )
    date_col = next(
        (column for column in ("公告时间", "日期") if column in df.columns), None
    )
    if title_col is None:
        logger.warning("akshare 公告列结构变化: %s", list(df.columns))
        return []
    items: list[dict] = []
    for _, row in df.iterrows():
        title = str(row[title_col] or "").strip()
        if not title or not any(kw in title for kw in _TITLE_FOCUS_KEYWORDS):
            continue
        announced = str(row[date_col] or "")
        if date_col is not None and date_text not in announced:
            continue
        items.append(
            {
                "symbol": symbol,
                "name": str(row.get("简称") or symbol).strip(),
                "title": title,
                "date": date_text,
            }
        )
    return items


async def fetch_corporate_actions(symbols: list[str]) -> list[dict]:
    """持仓标的最新公司行动（AkShare stock_dividend），按 6 位代码过滤。

    返回 ``[{symbol, name, actionType, exDate, payDate, instrumentId?}]``。
    任何异常、接口不可用或列结构变化都降级为空列表（渲染为「暂无」），绝不编造。
    """
    if not symbols:
        return []
    wanted = {str(s).zfill(6) for s in symbols}
    all_items = await _get_all_corporate_actions()
    return [item for item in all_items if item["symbol"] in wanted]


async def _get_all_corporate_actions() -> list[dict]:
    """抓取并归一化全市场公司行动，进程内缓存 6 小时。"""
    now = datetime.now(timezone.utc).timestamp()
    cached = _corporate_actions_cache["items"]
    if cached is not None and (now - _corporate_actions_cache["ts"]) < (
        _CORPORATE_ACTIONS_TTL_SECONDS
    ):
        return cached
    items = _normalize_corporate_actions()
    _corporate_actions_cache.update(ts=now, items=items)
    return items


def _normalize_corporate_actions() -> list[dict]:
    try:
        ak = _ak_import()
        df = None
        for name in ("stock_dividend", "stock_dividend_cninfo"):
            fn = getattr(ak, name, None)
            if fn is not None:
                df = fn()
                break
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.warning("akshare 公司行动抓取失败: %s", exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    cols = set(df.columns)
    code_col = next((c for c in ("股票代码", "代码") if c in cols), None)
    name_col = next((c for c in ("股票简称", "名称", "股票名称") if c in cols), None)
    ex_col = next((c for c in ("除权除息日", "除息日", "除权日") if c in cols), None)
    pay_col = next(
        (c for c in ("派息日", "红利发放日", "股息发放日") if c in cols), None
    )
    if code_col is None or ex_col is None:
        logger.warning("akshare 公司行动列结构变化: %s", list(df.columns))
        return []
    items: list[dict] = []
    for _, row in df.iterrows():
        code = str(row[code_col] or "").zfill(6)
        if not code:
            continue
        ex_date = str(row[ex_col] or "")
        if not ex_date:
            continue
        items.append(
            {
                "symbol": code,
                "name": str(row[name_col]) if name_col else "",
                "actionType": "CASH_DIVIDEND",
                "exDate": ex_date,
                "payDate": (
                    str(row[pay_col])
                    if pay_col is not None and row[pay_col] is not None
                    else None
                ),
                "source": "akshare/stock_dividend",
            }
        )
    return items


def _unavailable_pre_market_global(retrieved_at: datetime) -> dict:
    """The only honest answer while no governed overnight provider exists."""
    return {
        "usIndices": [],
        "a50Future": None,
        "economicEvents": [],
        "globalEvents": [],
        "qualityStatus": Q_UNAVAILABLE,
        "sourceName": PRE_MARKET_GLOBAL_SOURCE_NAME,
        "sourceAsOf": None,
        "retrievedAt": retrieved_at.isoformat(),
        "methodVersion": PRE_MARKET_GLOBAL_METHOD_VERSION,
    }


async def get_pre_market_global_snapshot() -> dict:
    """Return the governed overnight global snapshot for the 08:30 brief.

    数据源：美股三大指数（AkShare 新浪源 index_us_stock_sina）、富时中国 A50
    期货（AkShare 海外期货/新浪）、经济数据（AkShare 宏观接口 CPI/PMI）。
    进程内 30 分钟缓存避免重复外网抓取；部分抓取失败时仍返回已成功的那部分
    （``qualityStatus`` 视数据完整性为 ``FRESH``）。完全失败且无可缓存时
    fail-closed 返回空快照（下游渲染为「暂无」），绝不编造数字。
    """
    now = datetime.now(timezone.utc)
    cached = _pre_market_global_cache["snapshot"]
    if cached is not None and (now.timestamp() - _pre_market_global_cache["ts"]) < (
        _PRE_MARKET_GLOBAL_TTL_SECONDS
    ):
        return cached

    retrieved_at = now
    try:
        us_indices, a50, economic_events = await asyncio.gather(
            fetch_us_overnight_indices(),
            fetch_a50_future(),
            fetch_economic_calendar(),
        )
    except Exception as exc:  # noqa: BLE001 — 极端异常也走降级
        logger.warning("盘前全球快照抓取异常，降级: %s", exc)
        us_indices, a50, economic_events = [], None, []

    has_data = bool(us_indices) or a50 is not None or bool(economic_events)
    if not has_data:
        if cached is not None:
            degraded = dict(cached)
            degraded["qualityStatus"] = Q_STALE
            degraded["sourceName"] = (
                degraded.get("sourceName") or PRE_MARKET_GLOBAL_SOURCE_NAME
            ) + "(cached)"
            return degraded
        return _unavailable_pre_market_global(retrieved_at)

    providers: list[str] = []
    if us_indices:
        providers.append(AKSOURCE_NAME)
    if a50 is not None:
        providers.append(str(a50.get("source") or "sina/a50"))
    if economic_events:
        providers.append("AkShare-Macro")
    snapshot = {
        "usIndices": us_indices,
        "a50Future": a50,
        "economicEvents": economic_events,
        "globalEvents": [],
        "qualityStatus": Q_FRESH,
        "sourceName": "+".join(providers)
        if providers
        else PRE_MARKET_GLOBAL_SOURCE_NAME,
        "sourceAsOf": None,
        "retrievedAt": retrieved_at.isoformat(),
        "methodVersion": PRE_MARKET_GLOBAL_METHOD_VERSION,
    }
    _pre_market_global_cache.update(ts=now.timestamp(), snapshot=snapshot)
    return snapshot


@dataclass(frozen=True)
class Observation:
    """Normalized market observation from an external source."""

    symbol: str  # ticker / fund code as seen by the source
    subject_type: str  # INSTRUMENT | FUND
    field_code: str  # CLOSE | NAV
    value: Decimal | None
    as_of: datetime  # observation timestamp (source time)
    quality_status: str
    lower_value: Decimal | None = None
    upper_value: Decimal | None = None
    return_rate: Decimal | None = None
    unit: str | None = None
    currency: str = "CNY"
    method_version: str = "v1"
    origin_uri: str | None = None
    metadata: dict[str, Any] | None = None

    def to_payload(self) -> dict:
        return {
            "symbol": self.symbol,
            "field_code": self.field_code,
            "value": str(self.value),
            "lower_value": str(self.lower_value)
            if self.lower_value is not None
            else None,
            "upper_value": str(self.upper_value)
            if self.upper_value is not None
            else None,
            "quality_status": self.quality_status,
            "as_of": self.as_of.isoformat(),
            "method_version": self.method_version,
            "metadata": self.metadata or {},
        }


@dataclass(frozen=True)
class FundDisclosure:
    fund_symbol: str
    component_symbol: str
    component_name: str
    weight: Decimal  # fraction: 0.10 means 10%
    disclosed_on: date
    disclosure_label: str


# ---------------------------------------------------------------------------
# AkShare (synchronous → to_thread), with Sina fallback
# ---------------------------------------------------------------------------
async def fetch_equity_quotes(symbols: list[str]) -> list[Observation]:
    """A股实时行情：AkShare 东方财富优先，失败降级新浪。"""
    if not symbols:
        return []
    try:
        df = await asyncio.to_thread(_ak_import().stock_zh_a_spot_em)
        observations = _parse_ak_spot(df, symbols, "A股")
        if observations:
            logger.info("akshare A股: 获得 %d/%d 条", len(observations), len(symbols))
            return observations
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.warning("akshare A股抓取失败，降级新浪: %s", exc)
    return await _fetch_sina_quotes(symbols, "INSTRUMENT", F_CLOSE)


async def fetch_etf_quotes(symbols: list[str]) -> list[Observation]:
    """场内 ETF 实时行情：AkShare 东方财富优先，失败降级新浪。"""
    if not symbols:
        return []
    try:
        df = await asyncio.to_thread(_ak_import().fund_etf_spot_em)
        observations = _parse_ak_spot(df, symbols, "ETF")
        if observations:
            logger.info("akshare ETF: 获得 %d/%d 条", len(observations), len(symbols))
            return observations
    except Exception as exc:  # noqa: BLE001
        logger.warning("akshare ETF抓取失败，降级新浪: %s", exc)
    return await _fetch_sina_quotes(symbols, "INSTRUMENT", F_CLOSE)


def _parse_ak_spot(df, symbols: list[str], label: str) -> list[Observation]:
    columns = list(df.columns)
    if "代码" not in columns or "最新价" not in columns:
        logger.warning("akshare %s 行情列结构变化: %s", label, columns[:8])
        return []
    wanted = set(symbols)
    observations: list[Observation] = []
    now = datetime.now()
    for _, row in df.iterrows():
        code = str(row["代码"]).zfill(6)
        if code not in wanted:
            continue
        price = row["最新价"]
        if price is None or (isinstance(price, float) and price != price):  # NaN
            observations.append(_unavailable(code, "INSTRUMENT", F_CLOSE, "akshare"))
            continue
        observations.append(
            Observation(
                symbol=code,
                subject_type="INSTRUMENT",
                field_code=F_CLOSE,
                value=_decimal(price),
                return_rate=(
                    _decimal(row["涨跌幅"]) / Decimal("100")
                    if "涨跌幅" in columns
                    and row["涨跌幅"] is not None
                    and not (
                        isinstance(row["涨跌幅"], float)
                        and row["涨跌幅"] != row["涨跌幅"]
                    )
                    else None
                ),
                as_of=now,
                quality_status=Q_FRESH,
                method_version="akshare/spot-em",
                origin_uri="https://quote.eastmoney.com",
            )
        )
    return observations


async def _fetch_sina_quotes(
    symbols: list[str], subject_type: str, field_code: str
) -> list[Observation]:
    """新浪 hq.sinajs.cn 实时行情（GBK）。字段: 0名称 2昨收 3现价 30日期 31时间。"""
    if not symbols:
        return []
    market_codes = [f"{_sina_prefix(s)}{s}" for s in symbols]
    url = "https://hq.sinajs.cn/list=" + ",".join(market_codes)
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": _SINA_UA,
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            response.raise_for_status()
            text = response.content.decode("gbk", errors="replace")
    except httpx.HTTPError as exc:
        logger.warning("新浪行情抓取失败: %s", exc)
        return _unavailable_observations(symbols, subject_type, field_code, "sina/hq")

    observations: list[Observation] = []
    for line in text.splitlines():
        if '="' not in line:
            continue
        code = line.split("hq_str_")[1].split("=")[0]
        symbol = code[2:]
        fields = line.split('="', 1)[1].rstrip('";').split(",")
        if len(fields) < 4 or not fields[0]:
            observations.append(
                _unavailable(symbol, subject_type, field_code, "sina/hq")
            )
            continue
        current = fields[3]
        if not current or Decimal(current) == 0:
            # 未开盘/停牌 → 昨收兜底并标记 STALE（诚实降级，不伪装实时）
            prev_close = fields[2]
            if prev_close and Decimal(prev_close) != 0:
                observations.append(
                    Observation(
                        symbol=symbol,
                        subject_type=subject_type,
                        field_code=field_code,
                        value=_decimal(prev_close),
                        return_rate=Decimal("0"),
                        as_of=datetime.now(),
                        quality_status=Q_STALE,
                        method_version="sina/hq-prevclose",
                        origin_uri="https://finance.sina.com.cn",
                    )
                )
            else:
                observations.append(
                    _unavailable(symbol, subject_type, field_code, "sina/hq")
                )
            continue
        observations.append(
            Observation(
                symbol=symbol,
                subject_type=subject_type,
                field_code=field_code,
                value=_decimal(current),
                return_rate=(Decimal(current) - Decimal(fields[2])) / Decimal(fields[2])
                if fields[2] and Decimal(fields[2]) != 0
                else None,
                as_of=datetime.now(),
                quality_status=Q_FRESH,
                method_version="sina/hq",
                origin_uri="https://finance.sina.com.cn",
            )
        )
    logger.info("新浪行情: 获得 %d/%d 条", len(observations), len(symbols))
    return observations


def _sina_prefix(symbol: str) -> str:
    if symbol.startswith(("6", "5", "9")):
        return "sh"
    return "sz"


# ---------------------------------------------------------------------------
# 单标的实时行情（AI 对话查询用）
# ---------------------------------------------------------------------------
_SPOT_CACHE_TTL_SECONDS = 60.0
_spot_cache: dict = {"ts": 0.0, "df": None}

_SYMBOL_ONLY_RE = re.compile(r"^(\d{6})(?:\.(SH|SZ|BJ))?$", re.IGNORECASE)


async def _ak_spot_cached():
    """全市场快照（带 TTL 缓存，避免每次对话全量拉取）。"""
    import time

    now = time.monotonic()
    cached = _spot_cache["df"]
    if cached is not None and now - _spot_cache["ts"] < _SPOT_CACHE_TTL_SECONDS:
        return cached
    df = await asyncio.to_thread(_ak_import().stock_zh_a_spot_em)
    _spot_cache.update(ts=now, df=df)
    return df


async def get_quote(symbol: str) -> dict:
    """单标的实时行情（AI 对话查询入口）。

    ``symbol`` 支持：6 位代码（``300308``）、带后缀（``300308.SZ``）或
    股票名称（``中际旭创``，经全市场快照名称匹配）。

    数据源：AkShare 东财全市场快照（60s TTL 缓存）优先，失败降级新浪
    hq.sinajs.cn（GBK）。返回统一 dict 契约：

    symbol / name / price / change_pct(%) / volume(手) / turnover(元) /
    high / low / open / prev_close + quality(FRESH|STALE|UNAVAILABLE)。
    停牌时以昨收兜底并标记 STALE；彻底失败返回 UNAVAILABLE（fail closed）。
    """
    query = (symbol or "").strip()
    if not query:
        return _quote_unavailable("", "empty-symbol")

    code, suffix = _split_symbol(query)

    # 1) AkShare 全市场快照（代码精确匹配；名称包含匹配）
    try:
        df = await _ak_spot_cached()
        quote = _match_ak_quote(df, code=code, name=None if code else query)
        if quote is not None:
            return quote
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.warning("akshare spot 快照失败（get_quote %s）: %s", query, exc)
        # 继续降级：代码 → 新浪 hq；名称 → 新浪 suggest 解析后 hq

    # 2) 新浪降级（仅代码可寻址）
    if code is not None:
        return await _fetch_sina_quote(code, suffix)

    # 3) 名称 → 新浪 suggest 解析代码 → 行情（AkShare 挂掉时的名称兜底）
    resolved = await _sina_search_code(query)
    if resolved is not None:
        return await _fetch_sina_quote(resolved, None)
    return _quote_unavailable(query, "akshare-name-unresolved")


async def get_sina_quote(symbol: str) -> dict:
    """Resolve one listed security directly through Sina's exact-code quote.

    This intentionally skips the AkShare/EastMoney whole-market snapshot so
    instrument verification keeps an independent provider path during a bulk
    dataset outage. Invalid or unavailable symbols retain the fail-closed
    quote contract.
    """
    code, suffix = _split_symbol((symbol or "").strip())
    if code is None:
        return _quote_unavailable((symbol or "").strip(), "sina/invalid-symbol")
    return await _fetch_sina_quote(code, suffix)


async def get_sina_instrument_identity(symbol: str, expected_type: str) -> dict | None:
    """Resolve an exact listed-security identity with Sina's typed catalog.

    The quote endpoint proves that a code is tradable but does not distinguish
    stocks, ETFs and convertible bonds. Sina's suggest catalog carries a
    security type code: 11 for A shares and 22 for exchange-traded funds.
    Returning only the caller's expected class keeps verification fail closed.
    """
    code, _suffix = _split_symbol((symbol or "").strip())
    type_code = {"STOCK": "11", "ETF": "22"}.get(expected_type)
    if code is None or type_code is None:
        return None
    url = (
        "https://suggest3.sinajs.cn/suggest/"
        f"type={type_code}&key={code}&name=wealthsail_identity"
    )
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": _SINA_UA,
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            response.raise_for_status()
            text = response.content.decode("gbk", errors="replace")
    except httpx.HTTPError as exc:
        logger.warning("新浪标的身份查询失败（%s）: %s", code, exc)
        return None
    match = re.search(r'"([^"]*)"', text)
    if not match:
        return None
    for candidate in match.group(1).split(";"):
        fields = candidate.split(",")
        if (
            len(fields) >= 5
            and fields[1] == type_code
            and fields[2] == code
            and fields[4].strip()
        ):
            return {
                "symbol": code,
                "name": fields[4].strip(),
                "type": expected_type,
                "source": "sina/suggest",
            }
    return None


def _split_symbol(query: str) -> tuple[str | None, str | None]:
    """'300308.SZ' → ('300308', 'SZ')；'中际旭创' → (None, None)。"""
    m = _SYMBOL_ONLY_RE.match(query)
    if m:
        return m.group(1), (m.group(2) or "").upper() or None
    return None, None


def _num(row, col: str) -> float | None:
    """从 AkShare DataFrame 行安全取值（容 NaN / None / 缺列）。"""
    try:
        value = row[col]
    except (KeyError, IndexError):
        return None
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # NaN → None


def _match_ak_quote(df, *, code: str | None, name: str | None) -> dict | None:
    columns = list(df.columns)
    if "代码" not in columns or "名称" not in columns or "最新价" not in columns:
        return None
    try:
        if code is not None:
            hits = df[df["代码"].astype(str).str.zfill(6) == code]
        elif name:
            hits = df[df["名称"].astype(str).str.contains(name, regex=False)]
        else:
            return None
        if not len(hits):
            return None
        row = hits.iloc[0]
    except Exception:  # noqa: BLE001 — 数据形状异常按未命中处理
        return None

    price = _num(row, "最新价")
    prev_close = _num(row, "昨收")
    if price is None or price <= 0:
        if prev_close is None or prev_close <= 0:
            return _quote_unavailable(
                str(row["代码"]).zfill(6), "akshare/spot-no-price"
            )
        quality, price_out = Q_STALE, prev_close
    else:
        quality, price_out = Q_FRESH, price
    return {
        "symbol": str(row["代码"]).zfill(6),
        "name": str(row["名称"]),
        "price": price_out,
        "change_pct": _change_pct(price, prev_close),
        "volume": _num(row, "成交量"),  # 手
        "turnover": _num(row, "成交额"),  # 元
        "high": _num(row, "最高"),
        "low": _num(row, "最低"),
        "open": _num(row, "今开"),
        "prev_close": prev_close,
        "quality": quality,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "source": "akshare/spot-em",
    }


async def _fetch_sina_quote(code: str, suffix: str | None) -> dict:
    """新浪 hq.sinajs.cn 单标的行情（GBK）。

    A 股字段: 0名称 1今开 2昨收 3现价 4最高 5最低 8成交量(股) 9成交额(元)。
    """
    prefix = suffix.lower() if suffix else _sina_prefix(code)
    url = f"https://hq.sinajs.cn/list={prefix}{code}"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": _SINA_UA,
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            response.raise_for_status()
            text = response.content.decode("gbk", errors="replace")
    except httpx.HTTPError as exc:
        logger.warning("新浪 get_quote %s 失败: %s", code, exc)
        return _quote_unavailable(code, "sina/hq-error")
    if '="' not in text:
        return _quote_unavailable(code, "sina/hq-empty")
    fields = text.split('="', 1)[1].rstrip('";').split(",")
    if len(fields) < 32 or not fields[0]:
        return _quote_unavailable(code, "sina/hq-empty")

    def fnum(i: int) -> float | None:
        try:
            value = float(fields[i])
        except (ValueError, IndexError):
            return None
        return value if value == value else None

    name = fields[0]
    open_ = fnum(1)
    prev_close = fnum(2)
    price = fnum(3)
    high = fnum(4)
    low = fnum(5)
    volume_shares = fnum(8)
    turnover = fnum(9)

    if price is None or price <= 0:
        if prev_close is None or prev_close <= 0:
            return _quote_unavailable(code, "sina/hq-no-price")
        quality, price_out = Q_STALE, prev_close
    else:
        quality, price_out = Q_FRESH, price
    return {
        "symbol": code,
        "name": name,
        "price": price_out,
        "change_pct": _change_pct(price, prev_close),
        "volume": volume_shares / 100 if volume_shares is not None else None,  # 股→手
        "turnover": turnover,
        "high": high,
        "low": low,
        "open": open_,
        "prev_close": prev_close,
        "quality": quality,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "source": "sina/hq",
    }


def _change_pct(price: float | None, prev_close: float | None) -> float | None:
    if price is None or prev_close in (None, 0):
        return None
    return (price - prev_close) / prev_close * 100


async def _sina_search_code(name: str) -> str | None:
    """新浪 suggest 联想接口：股票名称 → 6 位代码（GBK JSONP，无需风控）。

    返回示例: var suggestdata_ws="中际旭创,11,300308,sz300308,..."; → '300308'
    """
    url = (
        "https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15"
        f"&key={name}&name=suggestdata_ws"
    )
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": _SINA_UA,
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            response.raise_for_status()
            text = response.content.decode("gbk", errors="replace")
    except httpx.HTTPError as exc:
        logger.warning("新浪 suggest 失败（%s）: %s", name, exc)
        return None
    match = re.search(r'"([^"]*)"', text)
    if not match:
        return None
    for part in match.group(1).split(","):
        if re.fullmatch(r"(sh|sz|bj)\d{6}", part):
            return part[2:]
    return None


def _quote_unavailable(symbol: str, source: str) -> dict:
    """fail-closed 空行情（字段齐全、数值为 None）。"""
    return {
        "symbol": symbol,
        "name": None,
        "price": None,
        "change_pct": None,
        "volume": None,
        "turnover": None,
        "high": None,
        "low": None,
        "open": None,
        "prev_close": None,
        "quality": Q_UNAVAILABLE,
        "as_of": None,
        "source": source,
    }


# ---------------------------------------------------------------------------
# 市场热点（涨跌幅排行）
# ---------------------------------------------------------------------------
# 新浪降级候选池：主流活跃 A 股 + 宽基/行业 ETF（仅 AkShare/东财均失败时兜底）
_SINA_HOT_POOL = [
    "600519",
    "300308",
    "300750",
    "000858",
    "601318",
    "600036",
    "000001",
    "002594",
    "600900",
    "601899",
    "600030",
    "601988",
    "601398",
    "000725",
    "002475",
    "300059",
    "600887",
    "601012",
    "603259",
    "688981",
    "600276",
    "601166",
    "600585",
    "000333",
    "510300",
    "588000",
    "159915",
    "512100",
    "516160",
]

# 东财 push2 clist：全市场按涨跌幅降序取前 N（AkShare 底层同源，轻量直连）
_EM_CLIST_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz={n}&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    "&fields=f12,f14,f2,f3,f5,f6"
)


async def get_hot_stocks_snapshot(limit: int = 10) -> dict:
    """Fetch one governed market-hot snapshot for Worker persistence.

    降级链：
      1. AkShare 全市场快照（60s 缓存）→ 本地按涨跌幅降序；
      2. 东财 push2 clist（只拉前 N，轻量直连，与 AkShare 同源）；
      3. 新浪候选池批量查询 → 计算涨跌幅后排序（仅代表性标的）。
    返回的 items 字段：symbol / name / price / change_pct(%) / volume(手) /
    turnover(元)。API 层不得直接调用本函数。
    """
    retrieved_at = datetime.now(timezone.utc)
    # 1) AkShare 全市场快照
    try:
        df = await _ak_spot_cached()
        rows = _rank_ak_spot(df, limit)
        if rows:
            return {
                "items": rows,
                "source_name": AKSOURCE_NAME,
                "source_as_of": retrieved_at,
                "retrieved_at": retrieved_at,
                "quality_status": Q_FRESH,
                "method_version": "market-hot/v1",
            }
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.warning("akshare 热点排行失败，降级东财 clist: %s", exc)

    # 2) 东财 clist 前 N
    rows = await _fetch_em_clist(limit)
    if rows:
        return {
            "items": rows,
            "source_name": "EastMoney-push2",
            "source_as_of": retrieved_at,
            "retrieved_at": retrieved_at,
            "quality_status": Q_FRESH,
            "method_version": "market-hot/v1",
        }

    # 3) 新浪候选池
    rows = await _fetch_sina_pool_hot(limit)
    if rows:
        return {
            "items": rows,
            "source_name": SINASOURCE_NAME,
            "source_as_of": retrieved_at,
            "retrieved_at": retrieved_at,
            # The fixed candidate pool is a degraded approximation of the
            # whole-market ranking and must never be labelled fully fresh.
            "quality_status": Q_ESTIMATED,
            "method_version": "market-hot/v1-candidate-pool",
        }
    return {
        "items": [],
        "source_name": "unavailable",
        "source_as_of": None,
        "retrieved_at": retrieved_at,
        "quality_status": Q_UNAVAILABLE,
        "method_version": "market-hot/v1",
    }


async def get_hot_stocks(limit: int = 10) -> list[dict]:
    """Compatibility wrapper for non-API callers; prefer the snapshot form."""
    return (await get_hot_stocks_snapshot(limit))["items"]


def _rank_ak_spot(df, limit: int) -> list[dict]:
    columns = list(df.columns)
    if "代码" not in columns or "名称" not in columns or "涨跌幅" not in columns:
        return []
    rows: list[dict] = []
    for _, row in df.iterrows():
        change = _num(row, "涨跌幅")
        if change is None:
            continue
        price = _num(row, "最新价")
        if price is None:
            continue
        rows.append(
            {
                "symbol": str(row["代码"]).zfill(6),
                "name": str(row["名称"]),
                "price": price,
                "change_pct": change,
                "volume": _num(row, "成交量"),  # 手
                "turnover": _num(row, "成交额"),  # 元
            }
        )
    rows.sort(key=lambda r: (r["change_pct"] or 0), reverse=True)
    return rows[:limit]


async def _fetch_em_clist(limit: int) -> list[dict]:
    """东财 clist：全市场按涨跌幅降序（服务端排序，一次请求）。"""
    url = _EM_CLIST_URL.format(n=limit)
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": _SINA_UA,
                    "Referer": "https://quote.eastmoney.com/",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("东财 clist 热点失败，降级新浪池: %s", exc)
        return []
    try:
        diff = payload["data"]["diff"]
    except (KeyError, TypeError):
        return []
    if not isinstance(diff, list):
        return []
    rows: list[dict] = []
    for item in diff:
        rows.append(
            {
                "symbol": str(item.get("f12", "")).zfill(6),
                "name": item.get("f14") or "",
                "price": item.get("f2"),
                "change_pct": item.get("f3"),
                "volume": item.get("f5"),
                "turnover": item.get("f6"),
            }
        )
    return rows


async def _fetch_sina_pool_hot(limit: int) -> list[dict]:
    """新浪候选池批量查询（一次请求），按涨跌幅降序。"""
    market_codes = [f"{_sina_prefix(s)}{s}" for s in _SINA_HOT_POOL]
    url = "https://hq.sinajs.cn/list=" + ",".join(market_codes)
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": _SINA_UA,
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            response.raise_for_status()
            text = response.content.decode("gbk", errors="replace")
    except httpx.HTTPError as exc:
        logger.warning("新浪池热点失败: %s", exc)
        return []
    rows: list[dict] = []
    for line in text.splitlines():
        if '="' not in line:
            continue
        fields = line.split('="', 1)[1].rstrip('";').split(",")
        if len(fields) < 32 or not fields[0]:
            continue
        name = fields[0]
        prev_close = _safe_float(fields[2])
        price = _safe_float(fields[3])
        if price is None or price <= 0 or prev_close in (None, 0):
            continue
        code = line.split("hq_str_")[1].split("=")[0][2:]
        rows.append(
            {
                "symbol": code,
                "name": name,
                "price": price,
                "change_pct": (price - prev_close) / prev_close * 100,
                "volume": (_safe_float(fields[8]) or 0) / 100,  # 股→手
                "turnover": _safe_float(fields[9]),
            }
        )
    rows.sort(key=lambda r: (r["change_pct"] or 0), reverse=True)
    return rows[:limit]


def _safe_float(value: str) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


# ---------------------------------------------------------------------------
# 天天基金 / 东方财富（httpx 异步）
# ---------------------------------------------------------------------------
async def fetch_fund_observations(symbols: list[str]) -> list[Observation]:
    """场外基金最近披露净值（f10/lsjz 最新一条）。"""
    if not symbols:
        return []
    observations: list[Observation] = []
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        for symbol in symbols:
            nav = await _fetch_fund_nav(client, symbol)
            if nav is not None:
                observations.append(nav)
            else:
                observations.append(
                    _unavailable(symbol, "FUND", F_NAV, "eastmoney/f10-lsjz")
                )
    logger.info("天天基金: 请求 %d 只, 获得 %d 条观测", len(symbols), len(observations))
    return observations


async def fetch_fund_nav_history(
    symbol: str,
    start: date,
    end: date,
    client: httpx.AsyncClient,
    *,
    page_size: int = 100,
    max_pages: int = 200,
) -> list[Observation]:
    """Fetch a bounded inclusive interval of official disclosed unit NAV.

    EastMoney returns newest rows first and can repeat a boundary row between
    pages. The first occurrence is authoritative for that response snapshot.
    Network failures get one bounded retry; malformed/truncated pagination is
    surfaced so the durable job can retry instead of materializing false data.
    """
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("fund symbol must contain exactly six digits")
    if start > end:
        raise ValueError("fund NAV start date must not be after end date")
    if page_size <= 0 or max_pages <= 0:
        raise ValueError("pagination bounds must be positive")

    url = "https://api.fund.eastmoney.com/f10/lsjz"
    by_day: dict[date, Observation] = {}
    total_count: int | None = None
    provider_page_size: int | None = None
    required_pages: int | None = None
    received = 0
    completed = False
    for page_index in range(1, max_pages + 1):
        response = None
        for attempt in range(2):
            try:
                response = await client.get(
                    url,
                    params={
                        "fundCode": symbol,
                        "pageIndex": page_index,
                        "pageSize": page_size,
                        "startDate": start.isoformat(),
                        "endDate": end.isoformat(),
                    },
                    headers={
                        "User-Agent": _SINA_UA,
                        "Referer": "https://fundf10.eastmoney.com/",
                    },
                    timeout=DEFAULT_TIMEOUT,
                )
                response.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt == 1:
                    raise
        if response is None:  # pragma: no cover - defensive type narrowing
            raise RuntimeError("fund NAV provider produced no response")
        try:
            payload = response.json()
            data = payload["Data"]
            rows = data["LSJZList"]
            response_page_index = int(payload["PageIndex"])
            response_page_size = int(payload["PageSize"])
            response_total_count = int(payload["TotalCount"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimeError("fund NAV provider returned an invalid payload") from exc
        if not isinstance(rows, list):
            raise RuntimeError("fund NAV provider rows are not a list")
        if (
            response_page_index != page_index
            or response_page_size <= 0
            or response_total_count < 0
        ):
            raise RuntimeError("fund NAV provider returned invalid pagination metadata")

        if total_count is None:
            total_count = response_total_count
            provider_page_size = response_page_size
            required_pages = (
                0
                if total_count == 0
                else (total_count + provider_page_size - 1) // provider_page_size
            )
            if required_pages > max_pages:
                raise RuntimeError("fund NAV provider page bound exceeded")
        elif (
            response_total_count != total_count
            or response_page_size != provider_page_size
        ):
            raise RuntimeError("fund NAV provider pagination metadata changed")

        if total_count == 0:
            if rows:
                raise RuntimeError(
                    "fund NAV provider returned invalid pagination metadata"
                )
            completed = True
            break
        if not rows:
            raise RuntimeError("fund NAV provider pagination was truncated")

        received += len(rows)
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            try:
                observed_on = date.fromisoformat(str(raw.get("FSRQ", "")))
                nav = _decimal(raw.get("DWJZ"))
            except (ValueError, TypeError, ArithmeticError):
                continue
            if (
                observed_on < start
                or observed_on > end
                or not nav.is_finite()
                or nav <= 0
            ):
                continue
            by_day.setdefault(
                observed_on,
                Observation(
                    symbol=symbol,
                    subject_type="FUND",
                    field_code=F_NAV,
                    value=nav,
                    as_of=datetime.combine(observed_on, time.min, tzinfo=timezone.utc),
                    quality_status=Q_FRESH if observed_on == date.today() else Q_STALE,
                    unit="CNY/share",
                    method_version="eastmoney/f10-lsjz-history/v1",
                    origin_uri="https://fundf10.eastmoney.com",
                    metadata={"officialUnitNav": True},
                ),
            )
        if required_pages is not None and page_index >= required_pages:
            if received < total_count:
                raise RuntimeError("fund NAV provider pagination was truncated")
            completed = True
            break

    if total_count is not None and not completed:
        raise RuntimeError("fund NAV provider page bound exceeded")
    return [by_day[day] for day in sorted(by_day)]


async def fetch_fund_disclosures(symbols: list[str]) -> list[FundDisclosure]:
    """Fetch the latest disclosed fund holdings with bounded concurrency.

    AkShare exposes EastMoney's public fund archive.  The result is disclosure
    data, not a live portfolio: downstream code must show the disclosure date
    and must not treat undisclosed residual assets as known.
    """
    semaphore = asyncio.Semaphore(4)

    async def one(symbol: str) -> list[FundDisclosure]:
        async with semaphore:
            return await asyncio.to_thread(_fetch_one_fund_disclosure, symbol)

    batches = await asyncio.gather(*(one(symbol) for symbol in symbols))
    return [row for batch in batches for row in batch]


def _fetch_one_fund_disclosure(symbol: str) -> list[FundDisclosure]:
    current_year = date.today().year
    df = None
    for year in (current_year, current_year - 1):
        try:
            candidate = _ak_import().fund_portfolio_hold_em(
                symbol=symbol, date=str(year)
            )
        except Exception as exc:  # noqa: BLE001 — provider failure degrades
            logger.debug("基金 %s 的 %s 持仓披露抓取失败: %s", symbol, year, exc)
            continue
        if candidate is not None and not candidate.empty:
            df = candidate
            break
    if df is None or df.empty:
        return []
    required = {"股票代码", "股票名称", "占净值比例", "季度"}
    if not required.issubset(set(df.columns)):
        logger.warning("基金持仓披露列结构变化: %s", list(df.columns))
        return []

    latest_label = str(df.iloc[0]["季度"])
    latest = df[df["季度"].astype(str) == latest_label]
    disclosed_on = _quarter_end(latest_label)
    rows: list[FundDisclosure] = []
    for _, item in latest.iterrows():
        code = str(item["股票代码"]).strip().zfill(6)
        try:
            weight = _decimal(item["占净值比例"]) / Decimal("100")
        except (TypeError, ValueError, ArithmeticError):
            continue
        if not code or weight <= 0 or weight > 1:
            continue
        rows.append(
            FundDisclosure(
                fund_symbol=symbol,
                component_symbol=code,
                component_name=str(item["股票名称"]).strip(),
                weight=weight,
                disclosed_on=disclosed_on,
                disclosure_label=latest_label,
            )
        )
    return rows


def _quarter_end(label: str) -> date:
    match = re.search(r"(20\d{2}).*?([1-4])\s*季度", label)
    if not match:
        return date.today()
    year, quarter = int(match.group(1)), int(match.group(2))
    month_day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return date(year, *month_day)


async def _fetch_fund_nav(client: httpx.AsyncClient, symbol: str) -> Observation | None:
    """最近披露净值。披露日=今日 → FRESH，否则 STALE。"""
    url = (
        "https://api.fund.eastmoney.com/f10/lsjz"
        f"?fundCode={symbol}&pageIndex=1&pageSize=1"
    )
    try:
        response = await client.get(
            url,
            headers={
                "User-Agent": _SINA_UA,
                "Referer": "https://fundf10.eastmoney.com/",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("基金 %s 净值抓取失败: %s", symbol, exc)
        return None
    try:
        rows = response.json()["Data"]["LSJZList"]
    except (ValueError, KeyError, TypeError):
        return None
    if not rows:
        return None
    row = rows[0]
    nav_date = row.get("FSRQ", "")
    nav = row.get("DWJZ")
    if not nav:
        return None
    try:
        published = date.fromisoformat(nav_date)
    except ValueError:
        published = None
    quality = Q_FRESH if published == date.today() else Q_STALE
    return Observation(
        symbol=symbol,
        subject_type="FUND",
        field_code=F_NAV,
        value=_decimal(nav),
        as_of=datetime.combine(
            published or date.today(), time.min, tzinfo=timezone.utc
        ),
        quality_status=quality,
        unit="元/份",
        method_version="eastmoney/f10-lsjz",
        origin_uri="https://fundf10.eastmoney.com",
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        return Decimal(value.strip())
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    raise TypeError(f"unexpected numeric type: {type(value).__name__}")


def _unavailable(
    symbol: str, subject_type: str, field_code: str, method: str
) -> Observation:
    return Observation(
        symbol=symbol,
        subject_type=subject_type,
        field_code=field_code,
        value=None,
        as_of=datetime.now(timezone.utc),
        quality_status=Q_UNAVAILABLE,
        method_version=method,
    )


def _unavailable_observations(
    symbols: list[str], subject_type: str, field_code: str, method: str
) -> list[Observation]:
    return [
        _unavailable(symbol, subject_type, field_code, method) for symbol in symbols
    ]


async def fetch_all(
    stock_symbols: list[str],
    etf_symbols: list[str],
    fund_symbols: list[str],
) -> list[Observation]:
    """并行抓取三类标的，聚合为统一 Observation 列表。"""
    stock_task = asyncio.create_task(fetch_equity_quotes(stock_symbols))
    etf_task = asyncio.create_task(fetch_etf_quotes(etf_symbols))
    fund_task = asyncio.create_task(fetch_fund_observations(fund_symbols))
    results = await asyncio.gather(stock_task, etf_task, fund_task)
    return [obs for batch in results for obs in batch]


def source_definitions() -> tuple[tuple[str, str], ...]:
    """Return the canonical provider names and database source types."""
    return (
        (AKSOURCE_NAME, AKSOURCE_TYPE),
        (FUNDSOURCE_NAME, FUNDSOURCE_TYPE),
        (ESTIMATOR_SOURCE_NAME, ESTIMATOR_SOURCE_TYPE),
    )


def source_names() -> tuple[str, ...]:
    return tuple(name for name, _source_type in source_definitions())


_ak_module = None


# ---------------------------------------------------------------------------
# 市场情绪指数（涨跌家数 / 涨停跌停 / 成交额 / 北向资金）
# ---------------------------------------------------------------------------
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def price_limit_pct(symbol: str, name: str = "") -> Decimal:
    """Return the ordinary daily price-limit band for a listed A-share.

    New-listing and resumption exemptions require exchange-status data that is
    not present in the snapshot.  Those exceptional rows remain an explicit
    limitation of ``sentiment/v2`` rather than being silently treated as exact.
    """
    normalized_name = name.upper().replace("*", "")
    if "ST" in normalized_name:
        return Decimal("5")
    if symbol.startswith(("4", "8", "92")):
        return Decimal("30")
    if symbol.startswith(("300", "301", "688", "689")):
        return Decimal("20")
    return Decimal("10")


def _is_limit_move(change_pct: Decimal, symbol: str, name: str, *, up: bool) -> bool:
    # Quotes are rounded by the Provider; 0.2 percentage-point tolerance
    # matches the old 9.8% main-board convention without applying it to all boards.
    threshold = price_limit_pct(symbol, name) - Decimal("0.2")
    return change_pct >= threshold if up else change_pct <= -threshold


def _sentiment_from_ak(df) -> dict:
    """从 AkShare 全市场快照统计情绪指标。

    涨停/跌停按 ST、主板、创业板/科创板、北交所的通常价格限制分别近似；
    新股/复牌等无涨跌停例外因快照缺少交易状态，作为方法局限写入版本说明。
    """
    columns = list(df.columns)
    if "代码" not in columns or "涨跌幅" not in columns:
        logger.warning("akshare 情绪快照列结构变化: %s", columns[:8])
        return {}
    up = down = flat = 0
    limit_up = limit_down = 0
    turnover_yuan = Decimal("0")
    source_rows = len(df.index)
    for _, row in df.iterrows():
        change = _num(row, "涨跌幅")
        if change is None:
            continue
        change_decimal = Decimal(str(change))
        symbol = str(row.get("代码") or "").strip().zfill(6)
        name = str(row.get("名称") or "").strip()
        if change > 0:
            up += 1
            if _is_limit_move(change_decimal, symbol, name, up=True):
                limit_up += 1
        elif change < 0:
            down += 1
            if _is_limit_move(change_decimal, symbol, name, up=False):
                limit_down += 1
        else:
            flat += 1
        t = _num(row, "成交额")
        if t is not None:
            turnover_yuan += Decimal(str(t))
    total = up + down + flat
    if total == 0:
        return {}
    turnover_yi = turnover_yuan / Decimal("100000000")
    # 综合情绪评分 0-100：涨跌家数比为基础，涨停加分、跌停减分（封顶 ±20）
    base = (
        Decimal(str(up)) / Decimal(str(up + down)) * 100
        if (up + down)
        else Decimal("50")
    )
    score = (
        base + Decimal("0.5") * min(limit_up, 20) - Decimal("1.0") * min(limit_down, 20)
    )
    score = max(Decimal("0"), min(Decimal("100"), score))
    now = datetime.now(_SHANGHAI_TZ)
    return {
        "trade_date": now.date().isoformat(),
        "total_up": up,
        "total_down": down,
        "total_flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "turnover": turnover_yi.quantize(Decimal("0.01")),
        "sentiment_score": score.quantize(Decimal("0.01")),
        "total": total,
        "source_name": AKSOURCE_NAME,
        "source_as_of": now,
        "retrieved_at": now,
        "quality_status": Q_FRESH,
        "coverage_pct": (
            (Decimal(total) / Decimal(source_rows) * 100).quantize(Decimal("0.01"))
            if source_rows
            else None
        ),
        "method_version": "sentiment/v2",
    }


async def _fetch_north_flow() -> Decimal | None:
    """北向资金净流入（亿元，可选）。

    沪深港通北向实时净买入自 2024-08 起停止披露，该接口通常不可用；
    失败返回 None（字段留空，不虚构数值）。
    """
    try:
        df = await asyncio.to_thread(_ak_import().stock_hsgt_fund_flow_summary_em)
        if df is None or df.empty:
            return None
        col = "北向资金" if "北向资金" in df.columns else df.columns[-1]
        value = df.iloc[0][col]
        if value is None:
            return None
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except (ValueError, TypeError):
            return None
    except Exception as exc:  # noqa: BLE001 — 北向为可选字段，失败留空
        logger.info("北向资金抓取不可用（可选字段留空）: %s", exc.__class__.__name__)
        return None


_SINA_MC_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page={p}&num=100&sort=changepercent&asc=0"
    "&node=hs_a&symbol=&_s_r_a=init"
)


def _count_from_sina_items(
    items: list[dict],
) -> tuple[int, int, int, int, int, Decimal]:
    """新浪市场中心行统计 → (up, down, flat, limit_up, limit_down, turnover_元)。"""
    up = down = flat = limit_up = limit_down = 0
    turnover = Decimal("0")
    for item in items:
        cp = item.get("changepercent")
        if cp is None:
            continue
        try:
            cp_value = Decimal(str(cp))
        except (TypeError, ValueError):
            continue
        symbol = str(item.get("symbol") or item.get("code") or "").strip()
        symbol = symbol[-6:] if len(symbol) >= 6 else symbol.zfill(6)
        name = str(item.get("name") or "")
        if cp_value > 0:
            up += 1
            if _is_limit_move(cp_value, symbol, name, up=True):
                limit_up += 1
        elif cp_value < 0:
            down += 1
            if _is_limit_move(cp_value, symbol, name, up=False):
                limit_down += 1
        else:
            flat += 1
        amt = item.get("amount")
        if amt is not None:
            try:
                turnover += Decimal(str(amt))
            except (ValueError, TypeError):
                pass
    return up, down, flat, limit_up, limit_down, turnover


async def _fetch_sina_market_sentiment() -> dict:
    """新浪市场中心全市场分页统计（降级链第 2 级，不受东财 IP 风控影响）。

    并发 10 页/轮拉取全市场快照（每页 100 条，按涨跌幅降序），直到数据取完
    或连续失败。约 54 页 / 6 轮，耗时 10-20s（仅在东财不可用时触发）。
    """
    up = down = flat = limit_up = limit_down = 0
    turnover = Decimal("0")
    page = 1
    incomplete = False
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        while page <= 100:
            batch = list(range(page, page + 10))
            results = await asyncio.gather(
                *(client.get(_SINA_MC_URL.format(p=p)) for p in batch),
                return_exceptions=True,
            )
            got = 0
            for r in results:
                if isinstance(r, Exception):
                    incomplete = True
                    continue
                response: httpx.Response = r  # type: ignore[assignment] — gather 联合类型收窄
                if response.status_code != 200:
                    incomplete = True
                    continue
                try:
                    items = response.json()
                except ValueError:
                    incomplete = True
                    continue
                if not isinstance(items, list) or not items:
                    continue
                got += len(items)
                u, d, f, lu, ld, t = _count_from_sina_items(items)
                up += u
                down += d
                flat += f
                limit_up += lu
                limit_down += ld
                turnover += t
            if got == 0:
                break  # 接口失败或已到末尾
            page += 10
            if got < 100 * 10:
                break  # 最后一轮不足一满页 = 已取完
    total = up + down + flat
    if total == 0 or incomplete:
        return {}
    turnover_yi = turnover / Decimal("100000000")
    base = (
        Decimal(str(up)) / Decimal(str(up + down)) * 100
        if (up + down)
        else Decimal("50")
    )
    score = (
        base + Decimal("0.5") * min(limit_up, 20) - Decimal("1.0") * min(limit_down, 20)
    )
    score = max(Decimal("0"), min(Decimal("100"), score))
    now = datetime.now(_SHANGHAI_TZ)
    return {
        "trade_date": now.date().isoformat(),
        "total_up": up,
        "total_down": down,
        "total_flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "turnover": turnover_yi.quantize(Decimal("0.01")),
        "sentiment_score": score.quantize(Decimal("0.01")),
        "total": total,
        "source_name": SINASOURCE_NAME,
        "source_as_of": now,
        "retrieved_at": now,
        "quality_status": Q_FRESH,
        "coverage_pct": Decimal("100.00"),
        "method_version": "sentiment/v2",
    }


async def fetch_market_sentiment() -> dict:
    """今日市场情绪指数。

    降级链：AkShare 全市场快照（60s 缓存，涨跌家数/涨停跌停/成交额）→
    新浪市场中心分页统计（东财 IP 风控时）→ 北向资金单独可选
    （2024-08 后通常不可用 → NULL）→ 彻底失败返回空 dict
    （由调用方回退到数据库最近一条记录）。
    """
    try:
        df = await _ak_spot_cached()
        stats = _sentiment_from_ak(df)
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.warning("akshare 市场情绪抓取失败，降级新浪市场中心: %s", exc)
        stats = {}
    if not stats:
        stats = await _fetch_sina_market_sentiment()
    if stats:
        north = await _fetch_north_flow()
        stats["north_flow"] = north
        logger.info(
            "市场情绪: 涨 %d 跌 %d 平 %d 涨停 %d 跌停 %d 成交 %.0f 亿",
            stats["total_up"],
            stats["total_down"],
            stats["total_flat"],
            stats["limit_up"],
            stats["limit_down"],
            stats["turnover"] or 0,
        )
        return stats
    return {}


def _ak_import():
    global _ak_module
    if _ak_module is None:
        import akshare  # deferred import: heavy, only needed for equity/etf

        _ak_module = akshare
    return _ak_module
