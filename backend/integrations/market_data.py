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
from datetime import date, datetime
from decimal import Decimal

import httpx

logger = logging.getLogger("wealthsail.market_data")

DEFAULT_TIMEOUT = 10.0  # seconds (httpx constraint from the spec)

# field_code vocabulary (aligns with market.market_observations.field_code)
F_CLOSE = "CLOSE"
F_NAV = "NAV"

# quality vocabulary (aligns with market.source_snapshots.quality_status)
Q_FRESH = "FRESH"
Q_STALE = "STALE"
Q_UNAVAILABLE = "UNAVAILABLE"

AKSOURCE_NAME = "AkShare-OpenSource"
FUNDSOURCE_NAME = "TiantianFund-EastMoney"
SINASOURCE_NAME = "Sina-Finance-Quote"

_SINA_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


@dataclass(frozen=True)
class Observation:
    """Normalized market observation from an external source."""

    symbol: str  # ticker / fund code as seen by the source
    subject_type: str  # INSTRUMENT | FUND
    field_code: str  # CLOSE | NAV
    value: Decimal
    as_of: datetime  # observation timestamp (source time)
    quality_status: str
    unit: str | None = None
    currency: str = "CNY"
    method_version: str = "v1"
    origin_uri: str | None = None

    def to_payload(self) -> dict:
        return {
            "symbol": self.symbol,
            "field_code": self.field_code,
            "value": str(self.value),
            "quality_status": self.quality_status,
            "as_of": self.as_of.isoformat(),
        }


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
                headers={"User-Agent": _SINA_UA, "Referer": "https://finance.sina.com.cn/"},
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
            observations.append(_unavailable(symbol, subject_type, field_code, "sina/hq"))
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
                        as_of=datetime.now(),
                        quality_status=Q_STALE,
                        method_version="sina/hq-prevclose",
                        origin_uri="https://finance.sina.com.cn",
                    )
                )
            else:
                observations.append(_unavailable(symbol, subject_type, field_code, "sina/hq"))
            continue
        observations.append(
            Observation(
                symbol=symbol,
                subject_type=subject_type,
                field_code=field_code,
                value=_decimal(current),
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
            return _quote_unavailable(str(row["代码"]).zfill(6), "akshare/spot-no-price")
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
                headers={"User-Agent": _SINA_UA, "Referer": "https://finance.sina.com.cn/"},
            )
            response.raise_for_status()
            text = response.content.decode("gbk", errors="replace")
    except httpx.HTTPError as exc:
        logger.warning("新浪 get_quote %s 失败: %s", code, exc)
        return _quote_unavailable(code, "sina/hq-error")
    if '="' not in text:
        return _quote_unavailable(code, "sina/hq-empty")
    fields = text.split('="', 1)[1].rstrip('\";').split(",")
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
                headers={"User-Agent": _SINA_UA, "Referer": "https://finance.sina.com.cn/"},
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
    "600519", "300308", "300750", "000858", "601318", "600036",
    "000001", "002594", "600900", "601899", "600030", "601988",
    "601398", "000725", "002475", "300059", "600887", "601012",
    "603259", "688981", "600276", "601166", "600585", "000333",
    "510300", "588000", "159915", "512100", "516160",
]

# 东财 push2 clist：全市场按涨跌幅降序取前 N（AkShare 底层同源，轻量直连）
_EM_CLIST_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz={n}&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    "&fields=f12,f14,f2,f3,f5,f6"
)


async def get_hot_stocks(limit: int = 10) -> list[dict]:
    """市场热点：全市场实时涨跌幅排行前 N。

    降级链：
      1. AkShare 全市场快照（60s 缓存）→ 本地按涨跌幅降序；
      2. 东财 push2 clist（只拉前 N，轻量直连，与 AkShare 同源）；
      3. 新浪候选池批量查询 → 计算涨跌幅后排序（仅代表性标的）。
    返回字段：symbol / name / price / change_pct(%) / volume(手) / turnover(元)。
    """
    # 1) AkShare 全市场快照
    try:
        df = await _ak_spot_cached()
        rows = _rank_ak_spot(df, limit)
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 — vendor outage degrades
        logger.warning("akshare 热点排行失败，降级东财 clist: %s", exc)

    # 2) 东财 clist 前 N
    rows = await _fetch_em_clist(limit)
    if rows:
        return rows

    # 3) 新浪候选池
    rows = await _fetch_sina_pool_hot(limit)
    if rows:
        return rows
    return []


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
                headers={"User-Agent": _SINA_UA, "Referer": "https://quote.eastmoney.com/"},
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
                headers={"User-Agent": _SINA_UA, "Referer": "https://finance.sina.com.cn/"},
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
        fields = line.split('="', 1)[1].rstrip('\";').split(",")
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
        as_of=datetime.now(),
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


def _unavailable(symbol: str, subject_type: str, field_code: str, method: str) -> Observation:
    return Observation(
        symbol=symbol,
        subject_type=subject_type,
        field_code=field_code,
        value=Decimal("0"),
        as_of=datetime.now(),
        quality_status=Q_UNAVAILABLE,
        method_version=method,
    )


def _unavailable_observations(
    symbols: list[str], subject_type: str, field_code: str, method: str
) -> list[Observation]:
    return [_unavailable(symbol, subject_type, field_code, method) for symbol in symbols]


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


def source_names() -> tuple[str, str]:
    return AKSOURCE_NAME, FUNDSOURCE_NAME


_ak_module = None


def _ak_import():
    global _ak_module
    if _ak_module is None:
        import akshare  # deferred import: heavy, only needed for equity/etf

        _ak_module = akshare
    return _ak_module
