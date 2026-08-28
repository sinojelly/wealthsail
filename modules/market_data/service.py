"""行情数据领域服务（modules/market_data）。

编排层：抓取 → 质量标记 → 落库；交易日历与市场情绪指数。自包含（注入
asyncpg 连接池），不依赖 backend.*。外部 I/O 全部收敛在模块级私有函数，
单元测试可 monkeypatch 注入假数据源。

降级链（fail closed）：
  * A股 / 场内 ETF：AkShare（东财 spot）→ 新浪 hq.sinajs.cn（GBK）→
    落库观测/缓存；彻底失败标记 UNAVAILABLE，绝不虚构价格。
  * 场外基金：天天基金 f10/lsjz（async httpx）；披露日=今日 → FRESH，
    否则 STALE。
  * 市场情绪：AkShare 全市场快照（60s TTL 缓存）→ 新浪市场中心分页统计
    （东财 IP 风控时）→ 北向资金为可选字段（2024-08 后通常不可用 → NULL）。
  * 交易日历：2024-2026 官方日历已回填（reference.trading_calendars）；
    未来窗口以工作日兜底，官方发布后下次回填自动修正。

所有数值一律 Decimal；质量状态枚举与 004/018 迁移的 CHECK 对齐。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from modules.database import AsyncConnection, AsyncPool, system_connection
from modules.errors import DataUnavailableError, ValidationError

logger = logging.getLogger("wealthsail.modules.market_data")

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TIMEOUT = 10.0  # httpx 秒

# field_code 词表（对齐 market.market_observations.field_code）
F_CLOSE = "CLOSE"
F_NAV = "NAV"
F_ESTIMATED_NAV = "ESTIMATED_NAV"

# quality 词表（对齐 source_snapshots / position_snapshots CHECK）
Q_FRESH = "FRESH"
Q_ESTIMATED = "ESTIMATED"
Q_STALE = "STALE"
Q_UNAVAILABLE = "UNAVAILABLE"

AKSOURCE_NAME = "AkShare-OpenSource"
FUNDSOURCE_NAME = "TiantianFund-EastMoney"
SINASOURCE_NAME = "Sina-Finance-Quote"

_SINA_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_SYMBOL_ONLY_RE = re.compile(r"^(\d{6})(?:\.(SH|SZ|BJ))?$", re.IGNORECASE)


@dataclass(frozen=True)
class Observation:
    """来自外部源的标准化行情观测（与 backend/integrations 契约一致）。"""

    symbol: str  # 外部源视角的代码
    subject_type: str  # INSTRUMENT | FUND
    field_code: str  # CLOSE | NAV | ESTIMATED_NAV
    value: Decimal | None
    as_of: datetime  # 观测时刻（源时间）
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
            "lower_value": str(self.lower_value) if self.lower_value is not None else None,
            "upper_value": str(self.upper_value) if self.upper_value is not None else None,
            "quality_status": self.quality_status,
            "as_of": self.as_of.isoformat(),
            "method_version": self.method_version,
            "metadata": self.metadata or {},
        }


def _decimal(value: object) -> Decimal:
    """任何数值型 → Decimal（绝不经过 float 参与金额计算）。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        return Decimal(value.strip())
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    raise TypeError(f"unexpected numeric type: {type(value).__name__}")


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _sina_prefix(symbol: str) -> str:
    """新浪代码前缀：6/5/9 开头 → 沪市，其余 → 深市。"""
    if symbol.startswith(("6", "5", "9")):
        return "sh"
    return "sz"


def _daily_quality(published: date) -> str:
    """日频数据质量：披露日=今日 → FRESH，否则 STALE（不伪装实时）。"""
    return Q_FRESH if published == _now().date() else Q_STALE


def _unavailable(symbol: str, subject_type: str, field_code: str, method: str) -> Observation:
    return Observation(
        symbol=symbol,
        subject_type=subject_type,
        field_code=field_code,
        value=None,
        as_of=datetime.now(timezone.utc),
        quality_status=Q_UNAVAILABLE,
        method_version=method,
    )


# ---------------------------------------------------------------------------
# 抓取：AkShare（东财）优先 → 新浪降级
# ---------------------------------------------------------------------------
_ak_module: Any = None


def _ak_import() -> Any:
    """延迟导入 akshare（重型依赖，仅 A股/ETF/情绪需要）。"""
    global _ak_module
    if _ak_module is None:
        import akshare  # noqa: PLC0415

        _ak_module = akshare
    return _ak_module


async def _fetch_ak_spot_df() -> Any:
    """AkShare 东财全市场快照（60s TTL 缓存，供行情/搜索/情绪复用）。"""
    return await asyncio.to_thread(_ak_import().stock_zh_a_spot_em)


def _parse_ak_spot(df, symbols: list[str], label: str) -> list[Observation]:
    """从东财全市场快照提取指定代码的实时行情（FRESH）。"""
    if df is None or df.empty:
        return []
    columns = list(df.columns)
    if "代码" not in columns or "最新价" not in columns:
        logger.warning("akshare %s 行情列结构变化: %s", label, columns[:8])
        return []
    wanted = set(symbols)
    observations: list[Observation] = []
    now = _now()
    for _, row in df.iterrows():
        code = str(row["代码"]).zfill(6)
        if code not in wanted:
            continue
        price = row["最新价"]
        if price is None or (isinstance(price, float) and price != price):
            observations.append(_unavailable(code, "INSTRUMENT", F_CLOSE, "akshare/spot"))
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
                    and not (isinstance(row["涨跌幅"], float) and row["涨跌幅"] != row["涨跌幅"])
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
    symbols: list[str], subject_type: str = "INSTRUMENT", field_code: str = F_CLOSE
) -> list[Observation]:
    """新浪 hq.sinajs.cn 批量行情（GBK）。字段: 0名称 2昨收 3现价。"""
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
        return [_unavailable(s, subject_type, field_code, "sina/hq") for s in symbols]

    observations: list[Observation] = []
    for line in text.splitlines():
        if '="' not in line:
            continue
        code = line.split("hq_str_")[1].split("=")[0]
        symbol = code[2:]
        fields = line.split('="', 1)[1].rstrip('\";').split(",")
        if len(fields) < 4 or not fields[0]:
            observations.append(_unavailable(symbol, subject_type, field_code, "sina/hq"))
            continue
        current = fields[3]
        try:
            current_decimal = Decimal(current) if current else None
        except (ValueError, TypeError):
            current_decimal = None
        if not current_decimal:
            # 未开盘/停牌 → 昨收兜底并标记 STALE（诚实降级）
            prev_close = fields[2]
            try:
                prev = Decimal(prev_close) if prev_close else None
            except (ValueError, TypeError):
                prev = None
            if prev:
                observations.append(
                    Observation(
                        symbol=symbol,
                        subject_type=subject_type,
                        field_code=field_code,
                        value=prev,
                        return_rate=Decimal("0"),
                        as_of=_now(),
                        quality_status=Q_STALE,
                        method_version="sina/hq-prevclose",
                        origin_uri="https://finance.sina.com.cn",
                    )
                )
            else:
                observations.append(_unavailable(symbol, subject_type, field_code, "sina/hq"))
            continue
        try:
            prev_close = Decimal(fields[2]) if fields[2] else None
        except (ValueError, TypeError):
            prev_close = None
        observations.append(
            Observation(
                symbol=symbol,
                subject_type=subject_type,
                field_code=field_code,
                value=current_decimal,
                return_rate=(
                    (current_decimal - prev_close) / prev_close if prev_close else None
                ),
                as_of=_now(),
                quality_status=Q_FRESH,
                method_version="sina/hq",
                origin_uri="https://finance.sina.com.cn",
            )
        )
    logger.info("新浪行情: 获得 %d/%d 条", len(observations), len(symbols))
    return observations


# ---------------------------------------------------------------------------
# 场外基金净值：天天基金 f10/lsjz
# ---------------------------------------------------------------------------
async def _fetch_fund_nav(client: httpx.AsyncClient, symbol: str) -> Observation | None:
    """最近披露净值（f10/lsjz 最新一条）。披露日=今日 → FRESH，否则 STALE。"""
    url = (
        "https://api.fund.eastmoney.com/f10/lsjz"
        f"?fundCode={symbol}&pageIndex=1&pageSize=1"
    )
    try:
        response = await client.get(
            url,
            headers={"User-Agent": _SINA_UA, "Referer": "https://fundf10.eastmoney.com/"},
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
    quality = Q_FRESH if published == _now().date() else Q_STALE
    return Observation(
        symbol=symbol,
        subject_type="FUND",
        field_code=F_NAV,
        value=_decimal(nav),
        as_of=datetime.combine(published or date.today(), dtime.min, tzinfo=SHANGHAI_TZ),
        quality_status=quality,
        unit="元/份",
        method_version="eastmoney/f10-lsjz",
        origin_uri="https://fundf10.eastmoney.com",
    )


async def _fetch_fund_observations(symbols: list[str]) -> list[Observation]:
    """场外基金最近披露净值（天天基金，逐只请求）。"""
    if not symbols:
        return []
    observations: list[Observation] = []
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        for symbol in symbols:
            nav = await _fetch_fund_nav(client, symbol)
            observations.append(
                nav or _unavailable(symbol, "FUND", F_NAV, "eastmoney/f10-lsjz")
            )
    logger.info("天天基金: 请求 %d 只, 获得 %d 条观测", len(symbols), len(observations))
    return observations


# ---------------------------------------------------------------------------
# 市场情绪（AkShare → 新浪市场中心 → 北向可选）
# ---------------------------------------------------------------------------
def price_limit_pct(symbol: str, name: str = "") -> Decimal:
    """A 股普通日涨跌幅限制：ST 5% / 北交所 30% / 创业板科创板 20% / 主板 10%。

    新股与复牌豁免需要交易所状态数据，快照中缺失；该类行是
    sentiment/v2 的显式局限，不当作精确值处理。
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
    """按板别阈值近似判断涨停/跌停（0.2 个百分点容差兼容 9.8% 惯例）。"""
    threshold = price_limit_pct(symbol, name) - Decimal("0.2")
    return change_pct >= threshold if up else change_pct <= -threshold


def _sentiment_score(up: int, down: int, limit_up: int, limit_down: int) -> Decimal:
    """综合情绪评分 0-100：涨跌比为基础，涨停加分、跌停减分（封顶 ±20）。"""
    base = Decimal(str(up)) / Decimal(str(up + down)) * 100 if (up + down) else Decimal("50")
    score = base + Decimal("0.5") * min(limit_up, 20) - Decimal("1.0") * min(limit_down, 20)
    return max(Decimal("0"), min(Decimal("100"), score)).quantize(Decimal("0.01"))


async def _fetch_sentiment_from_ak() -> dict:
    """从 AkShare 全市场快照统计情绪指标（涨跌家数/涨跌停/成交额）。"""
    df: Any = await _fetch_ak_spot_df()
    columns = list(df.columns) if df is not None else []
    if "代码" not in columns or "涨跌幅" not in columns:
        logger.warning("akshare 情绪快照列结构变化: %s", columns[:8])
        return {}
    up = down = flat = limit_up = limit_down = 0
    turnover_yuan = Decimal("0")
    source_rows = len(df.index)
    for _, row in df.iterrows():
        change = row["涨跌幅"]
        if change is None or (isinstance(change, float) and change != change):
            continue
        change_decimal = _decimal(change)
        symbol = str(row.get("代码") or "").strip().zfill(6)
        name = str(row.get("名称") or "").strip()
        if change_decimal > 0:
            up += 1
            if _is_limit_move(change_decimal, symbol, name, up=True):
                limit_up += 1
        elif change_decimal < 0:
            down += 1
            if _is_limit_move(change_decimal, symbol, name, up=False):
                limit_down += 1
        else:
            flat += 1
        turnover = row.get("成交额")
        if turnover is not None:
            try:
                turnover_yuan += _decimal(turnover)
            except (ValueError, TypeError, ArithmeticError):
                pass
    total = up + down + flat
    if total == 0:
        return {}
    now = _now()
    return {
        "trade_date": now.date().isoformat(),
        "total_up": up,
        "total_down": down,
        "total_flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "turnover": (turnover_yuan / Decimal("100000000")).quantize(Decimal("0.01")),
        "sentiment_score": _sentiment_score(up, down, limit_up, limit_down),
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
    """北向资金净流入（亿元，可选）。2024-08 后停止实时披露，失败返回 None。"""
    try:
        df = await asyncio.to_thread(_ak_import().stock_hsgt_fund_flow_summary_em)
        if df is None or df.empty:
            return None
        col = "北向资金" if "北向资金" in df.columns else df.columns[-1]
        value = df.iloc[0][col]
        if value is None:
            return None
        return _decimal(value).quantize(Decimal("0.01"))
    except Exception as exc:  # noqa: BLE001 — 可选字段失败留空
        logger.info("北向资金抓取不可用（可选字段留空）: %s", exc.__class__.__name__)
        return None


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------
class MarketDataService:
    """行情抓取、质量标记、落库、交易日历与市场情绪。"""

    def __init__(self, pool: AsyncPool) -> None:
        self.pool = pool

    # -- 抓取（降级链：AkShare 东财 → 新浪 → UNAVAILABLE）-------------------
    async def fetch_equity_quotes(self, symbols: list[str]) -> list[Observation]:
        """A股实时行情：东财 spot 优先，失败降级新浪。"""
        if not symbols:
            return []
        try:
            df = await _fetch_ak_spot_df()
            observations = _parse_ak_spot(df, symbols, "A股")
            if observations:
                logger.info("akshare A股: 获得 %d/%d 条", len(observations), len(symbols))
                return observations
        except Exception as exc:  # noqa: BLE001 — 供应商故障降级
            logger.warning("akshare A股抓取失败，降级新浪: %s", exc)
        return await _fetch_sina_quotes(symbols)

    async def fetch_etf_quotes(self, symbols: list[str]) -> list[Observation]:
        """场内 ETF 实时行情：东财 spot 优先，失败降级新浪。"""
        if not symbols:
            return []
        try:
            df = await _fetch_ak_spot_df()
            observations = _parse_ak_spot(df, symbols, "ETF")
            if observations:
                logger.info("akshare ETF: 获得 %d/%d 条", len(observations), len(symbols))
                return observations
        except Exception as exc:  # noqa: BLE001
            logger.warning("akshare ETF抓取失败，降级新浪: %s", exc)
        return await _fetch_sina_quotes(symbols)

    async def fetch_fund_observations(self, symbols: list[str]) -> list[Observation]:
        """场外基金最近披露净值（天天基金 f10/lsjz）。"""
        return await _fetch_fund_observations(symbols)

    async def fetch_all(
        self,
        stock_symbols: list[str],
        etf_symbols: list[str],
        fund_symbols: list[str],
    ) -> list[Observation]:
        """并行抓取三类标的，聚合为统一 Observation 列表。"""
        stock_task = asyncio.create_task(self.fetch_equity_quotes(stock_symbols))
        etf_task = asyncio.create_task(self.fetch_etf_quotes(etf_symbols))
        fund_task = asyncio.create_task(self.fetch_fund_observations(fund_symbols))
        results = await asyncio.gather(stock_task, etf_task, fund_task)
        return [obs for batch in results for obs in batch]

    # -- 单标的行情（AI 对话入口；AkShare 60s 缓存 → 新浪）-------------------
    async def get_quote(self, symbol: str) -> dict:
        """单标的实时行情；支持 6 位代码（300308 / 300308.SZ）或名称。

        返回统一契约：symbol / name / price / change_pct / volume / turnover /
        high / low / open / prev_close / quality / as_of / source。
        彻底失败返回 quality=UNAVAILABLE（fail closed）。
        """
        query = (symbol or "").strip()
        if not query:
            return _quote_unavailable("", "empty-symbol")
        match = _SYMBOL_ONLY_RE.match(query)
        code = match.group(1) if match else None

        # 1) AkShare 全市场快照（代码精确；名称包含）
        try:
            df = await _fetch_ak_spot_df()
            quote = _match_ak_quote(df, code=code, name=None if code else query)
            if quote is not None:
                return quote
        except Exception as exc:  # noqa: BLE001 — 供应商故障降级
            logger.warning("akshare spot 快照失败（get_quote %s）: %s", query, exc)

        # 2) 新浪降级（仅代码可寻址）
        if code is not None:
            return await _fetch_sina_single_quote(code)
        return _quote_unavailable(query, "akshare-name-unresolved")

    # -- 落库 ----------------------------------------------------------------
    async def persist_observations(
        self, observations: list[Observation], *, session: str
    ) -> dict:
        """标准化落库：数据源注册 + 来源快照 + 观测长表（幂等）。"""
        if not observations:
            return {"snapshots": 0, "observations": 0}

        async with system_connection(self.pool) as conn:
            # 数据源注册（幂等）
            for name, source_type in _source_types(observations):
                await conn.execute(
                    """
                    INSERT INTO reference.data_sources
                        (id, name, source_type, license_model, allowed_usage, priority, status)
                    VALUES (gen_random_uuid(), $1, $2, 'PUBLIC_API', 'PRICE_OBSERVATION', 80, 'ACTIVE')
                    ON CONFLICT (name) DO UPDATE SET status = 'ACTIVE'
                    """,
                    name,
                    source_type,
                )
            source_ids = {
                name: await conn.fetchval(
                    "SELECT id FROM reference.data_sources WHERE name = $1", name
                )
                for name in {
                    (o.metadata or {}).get("source_name", AKSOURCE_NAME)
                    for o in observations
                }
            }

            started = _now()
            retrieval_key = f"{session}:{started.strftime('%Y%m%d%H%M%S')}"
            snapshots = 0
            for source_name, batch in _group_by_source(observations).items():
                source_id = source_ids.get(source_name)
                if not source_id:
                    continue
                quality = (
                    Q_FRESH
                    if any(o.quality_status == Q_FRESH for o in batch)
                    else (Q_STALE if any(o.quality_status == Q_STALE for o in batch) else Q_UNAVAILABLE)
                )
                canonical = json.dumps(
                    [o.to_payload() for o in batch], sort_keys=True, ensure_ascii=False, default=str
                )
                content_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                snapshot_id = await conn.fetchval(
                    """
                    INSERT INTO market.source_snapshots
                        (source_id, retrieval_key, origin_uri, as_of, retrieved_at,
                         content_hash, quality_status)
                    VALUES ($1, $2, 'internal:modules:market', $3, $4, $5, $6)
                    RETURNING id
                    """,
                    source_id,
                    f"{retrieval_key}:{source_name}",
                    started,
                    started,
                    content_hash,
                    quality,
                )
                rows = [
                    (
                        o.subject_type,
                        o.symbol,  # subject_id 为字符串代码时保留；调用方负责映射
                        o.field_code,
                        o.value,
                        o.lower_value,
                        o.upper_value,
                        o.unit,
                        o.currency,
                        o.as_of,
                        started,
                        snapshot_id,
                        source_id,
                        o.quality_status,
                        o.method_version,
                        json.dumps(o.metadata or {}, ensure_ascii=False, default=str),
                    )
                    for o in batch
                ]
                await conn.executemany(
                    """
                    INSERT INTO market.market_observations
                        (subject_type, subject_id, field_code, value, lower_value, upper_value,
                         unit, currency, as_of, observed_at, snapshot_id, source_id,
                         quality_status, method_version, metadata, revision)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                            $13, $14, $15::jsonb, 1)
                    ON CONFLICT (subject_type, subject_id, field_code, as_of, source_id, revision)
                    DO NOTHING
                    """,
                    rows,
                )
                snapshots += 1

        usable = sum(1 for o in observations if o.quality_status != Q_UNAVAILABLE)
        logger.info(
            "行情落库 %s: 快照 %d / 有效观测 %d", session, snapshots, usable
        )
        return {"snapshots": snapshots, "observations": usable}

    # -- 交易日历 -------------------------------------------------------------
    async def is_trading_day(
        self, conn: AsyncConnection, day: date, market: str = "SSE"
    ) -> bool:
        """判断某日是否交易日；日历无记录时以工作日兜底（未来窗口）。"""
        row = await conn.fetchrow(
            """
            SELECT is_trading_day
            FROM reference.trading_calendars
            WHERE market = $1 AND calendar_date = $2
            """,
            market,
            day,
        )
        if row is not None:
            return bool(row["is_trading_day"])
        # 官方日历未覆盖（通常为未来窗口）→ 工作日兜底
        return day.weekday() < 5

    async def previous_trading_day(
        self, conn: AsyncConnection, day: date, market: str = "SSE"
    ) -> date | None:
        """返回给定日期之前的最近交易日（无记录时向前回退找工作日）。"""
        row = await conn.fetchrow(
            """
            SELECT calendar_date
            FROM reference.trading_calendars
            WHERE market = $1 AND calendar_date < $2 AND is_trading_day = TRUE
            ORDER BY calendar_date DESC
            LIMIT 1
            """,
            market,
            day,
        )
        if row is not None:
            return row["calendar_date"]
        cursor = day - timedelta(days=1)
        for _ in range(60):
            if cursor.weekday() < 5:
                return cursor
            cursor -= timedelta(days=1)
        return None

    async def fill_trading_calendar(self) -> int:
        """回填 2024-2026 上交所交易日历（akshare 新浪官方日历，幂等 upsert）。

        2024→今天：官方日期列表为准，缺失工作日=节假日；未来窗口：工作日
        兜底，官方发布后下次运行自动修正。
        """
        def _fetch_dates() -> set[str]:
            df = _ak_import().tool_trade_date_hist_sina()
            return {str(d)[:10] for d in df["trade_date"].tolist()}

        try:
            official = await asyncio.to_thread(_fetch_dates)
        except Exception as exc:  # noqa: BLE001
            logger.error("交易日历拉取失败: %s", exc)
            return 0

        today = date.today()
        rows: list[tuple] = []
        cursor = date(2024, 1, 1)
        end = date(2026, 12, 31)
        while cursor <= end:
            if cursor.weekday() >= 5:
                rows.append((cursor, False))
            elif cursor.isoformat() in official:
                rows.append((cursor, True))
            else:
                rows.append((cursor, cursor > today))
            cursor += timedelta(days=1)

        async with system_connection(self.pool) as conn:
            await conn.executemany(
                """
                INSERT INTO reference.trading_calendars
                    (market, calendar_date, is_trading_day, timezone)
                VALUES ('SSE', $1, $2, 'Asia/Shanghai')
                ON CONFLICT (market, calendar_date)
                DO UPDATE SET is_trading_day = EXCLUDED.is_trading_day
                """,
                rows,
            )
        logger.info("交易日历填充完成: %d 天 (2024-2026, SSE)", len(rows))
        return len(rows)

    # -- 市场情绪 -------------------------------------------------------------
    async def refresh_market_sentiment(self) -> dict:
        """抓取今日市场情绪并落库（降级链 + 治理元数据），Worker/调度器入口。"""
        try:
            stats = await _fetch_sentiment_from_ak()
        except Exception as exc:  # noqa: BLE001 — 供应商故障降级
            logger.warning("akshare 市场情绪抓取失败，降级新浪市场中心: %s", exc)
            stats = {}
        if not stats:
            stats = await _fetch_sina_market_sentiment()
        if not stats:
            return {"status": "ABSTAINED", "reason": "PROVIDER_UNAVAILABLE"}

        stats["north_flow"] = await _fetch_north_flow()
        async with system_connection(self.pool) as conn:
            await conn.execute(
                """
                INSERT INTO market.market_sentiment
                    (trade_date, total_up, total_down, total_flat, limit_up, limit_down,
                     turnover, north_flow, sentiment_score, source_name, source_as_of,
                     retrieved_at, quality_status, coverage_pct, method_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT (trade_date) DO UPDATE
                SET total_up = EXCLUDED.total_up,
                    total_down = EXCLUDED.total_down,
                    total_flat = EXCLUDED.total_flat,
                    limit_up = EXCLUDED.limit_up,
                    limit_down = EXCLUDED.limit_down,
                    turnover = EXCLUDED.turnover,
                    north_flow = EXCLUDED.north_flow,
                    sentiment_score = EXCLUDED.sentiment_score,
                    source_name = EXCLUDED.source_name,
                    source_as_of = EXCLUDED.source_as_of,
                    retrieved_at = EXCLUDED.retrieved_at,
                    quality_status = EXCLUDED.quality_status,
                    coverage_pct = EXCLUDED.coverage_pct,
                    method_version = EXCLUDED.method_version,
                    updated_at = now()
                """,
                stats["trade_date"],
                stats["total_up"],
                stats["total_down"],
                stats["total_flat"],
                stats["limit_up"],
                stats["limit_down"],
                stats.get("turnover"),
                stats.get("north_flow"),
                stats.get("sentiment_score"),
                stats["source_name"],
                stats["source_as_of"],
                stats["retrieved_at"],
                stats["quality_status"],
                stats.get("coverage_pct"),
                stats["method_version"],
            )
        logger.info(
            "市场情绪已落库: %s 涨 %d 跌 %d 涨停 %d 跌停 %d",
            stats["trade_date"], stats["total_up"], stats["total_down"],
            stats["limit_up"], stats["limit_down"],
        )
        return {"status": "COMPLETED", "tradeDate": stats["trade_date"]}

    async def get_market_sentiment(self, conn: AsyncConnection) -> dict:
        """读取最近一次治理快照（无任何外部 I/O）；跨日自动降级为 STALE。"""
        row = await conn.fetchrow(
            """
            SELECT trade_date, total_up, total_down, total_flat, limit_up, limit_down,
                   turnover, north_flow, sentiment_score, source_name, source_as_of,
                   retrieved_at, quality_status, coverage_pct, method_version, updated_at
            FROM market.market_sentiment
            ORDER BY trade_date DESC, created_at DESC
            LIMIT 1
            """
        )
        if row is None:
            return {
                "tradeDate": None, "totalUp": None, "totalDown": None,
                "totalFlat": None, "limitUp": None, "limitDown": None,
                "turnover": None, "northFlow": None, "sentimentScore": None,
                "source": "unavailable", "sourceAsOf": None, "retrievedAt": None,
                "qualityStatus": Q_UNAVAILABLE, "coveragePct": None,
                "methodVersion": None, "updatedAt": _now(),
            }
        persisted_quality = str(row["quality_status"])
        quality = (
            Q_STALE
            if persisted_quality == Q_FRESH and row["trade_date"] != _now().date()
            else persisted_quality
        )
        return {
            "tradeDate": row["trade_date"].isoformat(),
            "totalUp": row["total_up"],
            "totalDown": row["total_down"],
            "totalFlat": row["total_flat"],
            "limitUp": row["limit_up"],
            "limitDown": row["limit_down"],
            "turnover": str(row["turnover"]) if row["turnover"] is not None else None,
            "northFlow": str(row["north_flow"]) if row["north_flow"] is not None else None,
            "sentimentScore": (
                str(row["sentiment_score"]) if row["sentiment_score"] is not None else None
            ),
            "source": row["source_name"],
            "sourceAsOf": row["source_as_of"],
            "retrievedAt": row["retrieved_at"],
            "qualityStatus": quality,
            "coveragePct": str(row["coverage_pct"]) if row["coverage_pct"] is not None else None,
            "methodVersion": row["method_version"],
            "updatedAt": row["updated_at"],
        }


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------
def _source_types(observations: list[Observation]) -> set[tuple[str, str]]:
    """按观测内容推导数据源注册类型。"""
    types: set[tuple[str, str]] = set()
    for obs in observations:
        if obs.field_code == F_NAV:
            types.add((FUNDSOURCE_NAME, "FUND_NAV"))
        elif (obs.metadata or {}).get("source_name") == FUNDSOURCE_NAME:
            types.add((FUNDSOURCE_NAME, "FUND_NAV"))
        else:
            types.add((AKSOURCE_NAME, "MARKET_DATA"))
    return types


def _group_by_source(observations: list[Observation]) -> dict[str, list[Observation]]:
    """按来源分组（新浪/AkShare/天天基金各归一组）。"""
    groups: dict[str, list[Observation]] = {}
    for obs in observations:
        source = obs.metadata.get("source_name") if obs.metadata else None
        if source in (FUNDSOURCE_NAME,):
            name = FUNDSOURCE_NAME
        elif "sina" in (obs.method_version or ""):
            name = SINASOURCE_NAME
        else:
            name = AKSOURCE_NAME
        groups.setdefault(name, []).append(obs)
    return groups


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


def _num(row, col: str) -> float | None:
    """从 DataFrame 行安全取值（容 NaN / None / 缺列）。"""
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
    """从东财全市场快照匹配单标的行情（代码精确 / 名称包含）。"""
    if df is None or df.empty:
        return None
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
        "volume": _num(row, "成交量"),
        "turnover": _num(row, "成交额"),
        "high": _num(row, "最高"),
        "low": _num(row, "最低"),
        "open": _num(row, "今开"),
        "prev_close": prev_close,
        "quality": quality,
        "as_of": _now().isoformat(timespec="seconds"),
        "source": "akshare/spot-em",
    }


async def _fetch_sina_single_quote(code: str) -> dict:
    """新浪 hq.sinajs.cn 单标的行情（GBK）。字段: 0名称 1今开 2昨收 3现价。"""
    url = f"https://hq.sinajs.cn/list={_sina_prefix(code)}{code}"
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

    price = fnum(3)
    prev_close = fnum(2)
    if price is None or price <= 0:
        if prev_close is None or prev_close <= 0:
            return _quote_unavailable(code, "sina/hq-no-price")
        quality, price_out = Q_STALE, prev_close
    else:
        quality, price_out = Q_FRESH, price
    return {
        "symbol": code,
        "name": fields[0],
        "price": price_out,
        "change_pct": _change_pct(price, prev_close),
        "volume": (fnum(8) or 0) / 100 if fnum(8) is not None else None,
        "turnover": fnum(9),
        "high": fnum(4),
        "low": fnum(5),
        "open": fnum(1),
        "prev_close": prev_close,
        "quality": quality,
        "as_of": _now().isoformat(timespec="seconds"),
        "source": "sina/hq",
    }


def _change_pct(price: float | None, prev_close: float | None) -> float | None:
    if price is None or prev_close in (None, 0):
        return None
    return (price - prev_close) / prev_close * 100


async def _fetch_sina_market_sentiment() -> dict:
    """新浪市场中心分页统计（东财 IP 风控时的降级链第 2 级）。"""
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?page={p}&num=100&sort=changepercent&asc=0"
        "&node=hs_a&symbol=&_s_r_a=init"
    )
    up = down = flat = limit_up = limit_down = 0
    turnover = Decimal("0")
    page = 1
    incomplete = False
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        while page <= 100:
            batch = list(range(page, page + 10))
            results = await asyncio.gather(
                *(client.get(url.format(p=p)) for p in batch),
                return_exceptions=True,
            )
            got = 0
            for result in results:
                if not isinstance(result, httpx.Response):
                    incomplete = True
                    continue
                response = result
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
            if got == 0:
                break
            page += 10
            if got < 100 * 10:
                break
    total = up + down + flat
    if total == 0 or incomplete:
        return {}
    now = _now()
    return {
        "trade_date": now.date().isoformat(),
        "total_up": up,
        "total_down": down,
        "total_flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "turnover": (turnover / Decimal("100000000")).quantize(Decimal("0.01")),
        "sentiment_score": _sentiment_score(up, down, limit_up, limit_down),
        "total": total,
        "source_name": SINASOURCE_NAME,
        "source_as_of": now,
        "retrieved_at": now,
        "quality_status": Q_FRESH,
        "coverage_pct": Decimal("100.00"),
        "method_version": "sentiment/v2",
    }
