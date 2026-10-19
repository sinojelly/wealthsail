"""Bounded public upstream adapters mapped to pinned AKShare 1.18.94.

The adapters use httpx rather than AKShare's unbounded requests/pagination.
Eastmoney/CLS are providers, not licensed ChinaBond/CFETS clients. Gamma prices
are market-implied prices, and Launch Library times are planned schedules.

Provider schemas were checked against AKShare's official macro, bond, interest
rate and stock documentation, Polymarket's markets API and the LL2 official FAQ.
No adapter sends orders, invokes a model, accesses a database or retries denials.
"""

from __future__ import annotations

import asyncio
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import inspect
import json
import math
import re
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from backend.dependencies import now_shanghai
from backend.integrations.institutional_signals import (
    is_media_noise,
    split_numbered_digest,
)

VERSION = "alpha-public-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_BYTES = 2 * 1024 * 1024
SOURCE_KEYS = (
    "AKSHARE_MONEY_SUPPLY",
    "AKSHARE_CN_T10",
    "AKSHARE_SHIBOR_7D",
    "AKSHARE_HOUSE_HAINAN",
    "AKSHARE_HOUSE_MAINLAND",
    "CLS_TELEGRAPH",
    "POLYMARKET_GAMMA",
    "SPACEDEVS_LAUNCHES",
    "GOV_SH_PBOC_TSF",
)
EASTMONEY = "https://datacenter-web.eastmoney.com/api/data/v1/get"
BOND_URL = "https://datacenter.eastmoney.com/api/data/get"
CLS_URL = "https://www.cls.cn/v1/roll/get_roll_list"
GAMMA_URL = "https://gamma-api.polymarket.com/markets"
LAUNCH_URL = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/"
TSF_INDEX = "https://jrj.sh.gov.cn/SCGK194/index.html"
HOSTS = frozenset(
    {
        "datacenter-web.eastmoney.com",
        "datacenter.eastmoney.com",
        "www.cls.cn",
        "gamma-api.polymarket.com",
        "ll.thespacedevs.com",
        "jrj.sh.gov.cn",
    }
)
CITY_CODES = {"海口": "HAIKOU", "三亚": "SANYA", "上海": "SHANGHAI", "深圳": "SHENZHEN"}
KEYWORDS = (
    "海南",
    "海口",
    "三亚",
    "自贸港",
    "浦东",
    "伊朗",
    "中东",
    "俄乌",
    "俄罗斯",
    "乌克兰",
    "原油",
    "石油",
    "人工智能",
    "AI",
    "DRAM",
    "HBM",
    "内存",
    "存储",
    "商业航天",
    "卫星",
    "火箭",
    "发射",
    "社融",
    "货币",
    "美联储",
)
INVESTMENT_KEYWORDS = KEYWORDS + (
    "宏观",
    "经济",
    "政策",
    "财政",
    "关税",
    "自贸",
    "封关",
    "利率",
    "通胀",
    "降息",
    "降准",
    "PMI",
    "CPI",
    "PPI",
    "GDP",
    "就业",
    "银行",
    "保险",
    "证券",
    "金融",
    "红利",
    "股息",
    "财报",
    "季报",
    "年报",
    "业绩",
    "营收",
    "利润",
    "订单",
    "产能",
    "投资",
    "回购",
    "并购",
    "重组",
    "医药",
    "医疗",
    "创新药",
    "生物",
    "消费",
    "零售",
    "旅游",
    "食品",
    "家电",
    "汽车",
    "新能源",
    "光伏",
    "锂电",
    "电力",
    "煤炭",
    "有色",
    "黄金",
    "铜",
    "铝",
    "钢铁",
    "化工",
    "房地产",
    "地产",
    "住房",
    "基建",
    "军工",
    "国防",
    "科技",
    "半导体",
    "芯片",
    "算力",
    "数据中心",
    "机器人",
    "通信",
    "软件",
    "航运",
    "出口",
    "进口",
    "贸易",
)
SECTOR_KEYWORDS = {
    "AI_COMPUTE_SEMICONDUCTOR": (
        "人工智能",
        "AI",
        "英伟达",
        "NVIDIA",
        "GTC",
        "PCB",
        "半导体",
        "芯片",
        "算力",
        "数据中心",
        "HBM",
        "DRAM",
        "内存",
        "存储",
        "海光",
        "澜起",
        "中科曙光",
        "生益科技",
        "台积电",
    ),
    "RARE_EARTH_METALS": (
        "稀土",
        "盛和资源",
        "中国稀土",
        "有色",
        "战略资源",
        "铜",
        "铝",
        "矿产",
    ),
    "ROBOTICS_MOBILITY": (
        "机器人",
        "具身智能",
        "智驾",
        "自动驾驶",
        "智能驾驶",
        "汽车",
        "人形",
    ),
    "FINANCE_POLICY": (
        "金融",
        "金融街",
        "央行",
        "货币",
        "社融",
        "财政",
        "证监",
        "券商",
        "银行",
        "证券",
        "保险",
        "降息",
        "降准",
        "政策利率",
    ),
    "HEALTHCARE": ("医药", "医疗", "创新药", "临床", "ESMO", "ASCO", "FDA", "生物制药"),
    "GOLD_GEO_ENERGY": (
        "黄金",
        "原油",
        "油气",
        "石油",
        "天然气",
        "以色列",
        "伊朗",
        "中东",
        "俄乌",
        "俄罗斯",
        "乌克兰",
        "预警打击",
        "地缘",
    ),
    "SPACE_DEFENSE": (
        "商业航天",
        "卫星",
        "火箭",
        "发射",
        "军工",
        "国防",
        "航天",
        "航空",
    ),
}
ANNOUNCEMENT_KEYWORDS = (
    "回购",
    "增持",
    "并购",
    "重组",
    "股权转让",
    "业绩",
    "财报",
    "季报",
    "年报",
    "营收",
    "利润",
)
_launch_day = None


def _keyword_in_text(word, text):
    return (
        bool(
            re.search(
                r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", text, re.I
            )
        )
        if word.isascii()
        else word in text
    )


def catalyst_sectors(text):
    return [
        sector
        for sector, words in SECTOR_KEYWORDS.items()
        if any(_keyword_in_text(word, text) for word in words)
    ]


def core_catalyst(text, sectors=None):
    return bool(sectors or catalyst_sectors(text)) or any(
        word in text for word in ANNOUNCEMENT_KEYWORDS
    )


def _industry_order(rows):
    """Keep every retained item; bring one leading clue per industry forward."""
    groups = {}
    for row in rows:
        # A multi-industry article appears once while retaining all its tags.
        sector = (row.get("sectorBuckets") or ["OTHER"])[0]
        groups.setdefault(sector, []).append(row)
    return [
        group[i]
        for i in range(max((len(group) for group in groups.values()), default=0))
        for group in groups.values()
        if i < len(group)
    ]


class _Unavailable(ValueError):
    pass


class _Skipped(Exception):
    pass


def _aware(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("timezone-aware collection time required")
    return value.astimezone(SHANGHAI)


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (
            stamp
            if stamp.tzinfo is not None and stamp.utcoffset() is not None
            else None
        )
    except (ValueError, TypeError):
        return None


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")
    try:
        if re.fullmatch(r"\d{6}", text):
            return date(int(text[:4]), int(text[4:]), 1)
        return date.fromisoformat(text[:10])
    except ValueError:
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月份?", text)
        try:
            return date(int(match[1]), int(match[2]), 1) if match else None
        except ValueError:
            return None


def _month(value):
    day = _day(value)
    return (
        date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
        if day
        else None
    )


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and math.isfinite(float(number)) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _text(value, limit=1000):
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()[:limit]


def _url(uri):
    parts = urlsplit(uri)
    if (
        parts.scheme != "https"
        or parts.hostname not in HOSTS
        or parts.username
        or parts.password
        or parts.port not in {None, 443}
    ):
        raise ValueError("unapproved public source URL")
    return uri


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate upstream field")
        result[key] = value
    return result


async def _fetch(client, uri, *, params=None, timeout=12, text=False):
    async with client.stream(
        "GET", _url(uri), params=params, timeout=timeout, follow_redirects=False
    ) as response:
        response.raise_for_status()
        length = response.headers.get("content-length")
        if length and int(length) > MAX_BYTES:
            raise ValueError("upstream response exceeds bounded size")
        body = bytearray()
        async for part in response.aiter_bytes():
            body.extend(part)
            if len(body) > MAX_BYTES:
                raise ValueError("upstream response exceeds bounded size")
        decoded = bytes(body).decode("utf-8-sig")
        return (
            decoded if text else json.loads(decoded, object_pairs_hook=_unique_object)
        )


def _data(payload, limit):
    rows = payload.get("result", {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise _Unavailable("documented upstream rows unavailable")
    return rows[:limit]


def _row(
    code,
    reference,
    value,
    key,
    uri,
    retrieved,
    *,
    frequency="DAILY",
    unit="%",
    name="东方财富公开数据（AKShare上游适配）",
    metadata=None,
):
    return {
        "code": code,
        "referenceDate": reference.isoformat(),
        "frequency": frequency,
        "value": format(value, "f"),
        "unit": unit,
        "sourceKey": key,
        "sourceUri": uri,
        "sourceName": name,
        "publishedAt": None,
        "retrievedAt": retrieved.isoformat(),
        "availableAt": retrieved.isoformat(),
        "timeBasis": "RETRIEVAL_ONLY",
        "methodVersion": VERSION,
        "metadata": {
            "provider": "EASTMONEY",
            "verification": "PROVIDER_REPORTED",
            **(
                {"akshareVersion": "1.18.94"}
                if (metadata or {}).get("provider", "EASTMONEY") == "EASTMONEY"
                else {}
            ),
            **(metadata or {}),
        },
    }


def _result(**items):
    return {"observations": [], "catalysts": [], "odds": [], "launches": [], **items}


async def _money(fetch, cutoff):
    params = {
        "reportName": "RPT_ECONOMY_CURRENCY_SUPPLY",
        "columns": "REPORT_DATE,TIME,CURRENCY_SAME,BASIC_CURRENCY_SAME",
        "pageNumber": 1,
        "pageSize": 120,
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    payload = await fetch(EASTMONEY, params=params)
    retrieved, rows = _aware(now_shanghai()), []
    for item in _data(payload, 120):
        reference = _month(item.get("REPORT_DATE") or item.get("TIME"))
        if reference is None or reference > cutoff.date():
            continue
        m1, m2 = (
            _number(item.get("CURRENCY_SAME")),
            _number(item.get("BASIC_CURRENCY_SAME")),
        )
        metadata = {
            "mappedAkshareFunction": "macro_china_money_supply",
            "definitionVersion": "EASTMONEY_REPORTED",
            "definitionNote": "M1自2025年统计口径修订；供应商历史未认证可比回溯，不跨定义断点推导趋势。",
            "referencePeriod": reference.strftime("%Y-%m"),
        }
        for code, value, unit in (
            ("M1_YOY", m1, "%"),
            ("M2_YOY", m2, "%"),
            (
                "M1_M2_GAP",
                m1 - m2 if m1 is not None and m2 is not None else None,
                "percentage_points",
            ),
        ):
            if value is not None:
                derived = (
                    {
                        "verification": "DERIVED_FROM_PROVIDER",
                        "calculation": "M1_YOY-M2_YOY",
                        "components": {
                            "M1_YOY": format(m1, "f"),
                            "M2_YOY": format(m2, "f"),
                        },
                    }
                    if code == "M1_M2_GAP"
                    else {}
                )
                rows.append(
                    _row(
                        code,
                        reference,
                        value,
                        "AKSHARE_MONEY_SUPPLY",
                        "https://data.eastmoney.com/cjsj/hbgyl.html",
                        retrieved,
                        frequency="MONTHLY",
                        unit=unit,
                        metadata={**metadata, **derived},
                    )
                )
    return _result(
        observations=rows,
        limitations=[
            "同一月份有M1与M2时才计算剪刀差；单页最多120个月，供应商口径不替代央行认证。"
        ],
    )


async def _rate(fetch, cutoff, *, shibor=False):
    if shibor:
        key, code, field, day_key, uri = (
            "AKSHARE_SHIBOR_7D",
            "SHIBOR_7D",
            "IR_RATE",
            "REPORT_DATE",
            EASTMONEY,
        )
        params = {
            "reportName": "RPT_IMP_INTRESTRATEN",
            "columns": "REPORT_DATE,IR_RATE",
            "filter": '(MARKET_CODE="001")(CURRENCY_CODE="CNY")(INDICATOR_ID="101")',
            "pageNumber": 1,
            "pageSize": 64,
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        source_uri = "https://data.eastmoney.com/shibor/shibor.aspx"
        mapped = "rate_interbank(market=上海银行同业拆借市场,symbol=Shibor人民币,indicator=1周)"
    else:
        key, code, field, day_key, uri = (
            "AKSHARE_CN_T10",
            "CN_T10",
            "EMM00166466",
            "SOLAR_DATE",
            BOND_URL,
        )
        # Public report selector shipped in the pinned AKShare source, not a
        # user credential or a grant of licensed data access.
        params = {
            "type": "RPTA_WEB_TREASURYYIELD",
            "sty": "ALL",
            "st": "SOLAR_DATE",
            "sr": "-1",
            "token": "894050c76af8597a853f5b408b759f5d",
            "p": 1,
            "ps": 64,
            "pageNo": 1,
            "pageNum": 1,
        }
        source_uri, mapped = (
            "https://data.eastmoney.com/cjsj/zmgzsyl.html",
            "bond_zh_us_rate",
        )
    payload = await fetch(uri, params=params)
    retrieved, rows = _aware(now_shanghai()), []
    for item in _data(payload, 64):
        reference, value = _day(item.get(day_key)), _number(item.get(field))
        if reference is not None and reference <= cutoff.date() and value is not None:
            rows.append(
                _row(
                    code,
                    reference,
                    value,
                    key,
                    source_uri,
                    retrieved,
                    metadata={
                        "mappedAkshareFunction": mapped,
                        "coverage": "ONE_PAGE_MAX_64",
                        "authorizationNote": "东方财富公开供应商数据，不代表中债/CFETS许可；SHIBOR不等于DR007。",
                    },
                )
            )
    return _result(
        observations=rows,
        limitations=["单页最多64个实际观察；不跟随历史分页，不认证中债/CFETS授权。"],
    )


async def _housing(fetch, cutoff, *, hainan):
    cities = ("海口", "三亚") if hainan else ("上海", "深圳")
    key = "AKSHARE_HOUSE_HAINAN" if hainan else "AKSHARE_HOUSE_MAINLAND"
    params = {
        "reportName": "RPT_ECONOMY_HOUSE_PRICE",
        "columns": "REPORT_DATE,CITY,FIRST_COMHOUSE_SEQUENTIAL,SECOND_HOUSE_SEQUENTIAL",
        "filter": f'(CITY in ("{cities[0]}","{cities[1]}"))',
        "pageNumber": 1,
        "pageSize": 48,
        "sortColumns": "REPORT_DATE,CITY",
        "sortTypes": "-1,-1",
        "source": "WEB",
        "client": "WEB",
    }
    payload = await fetch(EASTMONEY, params=params)
    retrieved, rows = _aware(now_shanghai()), []
    for item in _data(payload, 48):
        reference, city = _month(item.get("REPORT_DATE")), item.get("CITY")
        if reference is None or reference > cutoff.date() or city not in cities:
            continue
        for kind, field in (
            ("NEW", "FIRST_COMHOUSE_SEQUENTIAL"),
            ("SECOND", "SECOND_HOUSE_SEQUENTIAL"),
        ):
            value = _number(item.get(field))
            if value is None or value <= 0:
                continue
            rows.append(
                _row(
                    f"HOUSE_{CITY_CODES[city]}_{kind}_MOM",
                    reference,
                    value - 100,
                    key,
                    "https://data.eastmoney.com/cjsj/newhouse.html",
                    retrieved,
                    frequency="MONTHLY",
                    metadata={
                        "verification": "DERIVED_FROM_PROVIDER",
                        "mappedAkshareFunction": "macro_china_new_house_price",
                        "city": city,
                        "housingKind": kind,
                        "originalIndex": format(value, "f"),
                        "indexBasis": "PREVIOUS_MONTH_100",
                        "transformation": "originalIndex-100",
                        "coverage": "SELECTED_CITIES",
                        "selectedCities": list(cities),
                        "notATradePrice": True,
                    },
                )
            )
    return _result(
        observations=rows,
        limitations=[
            "只覆盖海口/三亚/上海/深圳指定城市；不是70城全覆盖，也不是住宅成交单价。"
        ],
    )


async def _cls(fetch, cutoff):
    window_start = cutoff - timedelta(hours=24)
    cursor = int(cutoff.timestamp())
    rows, seen_ids, seen_texts = [], set(), set()
    pages, scanned, oldest, error_fields = 0, 0, None, {}
    filtered_noise = 0
    deadline = asyncio.get_running_loop().time() + 18
    stop_reason = "PAGE_LIMIT"
    for _ in range(15):
        params = {
            "app": "CailianpressWeb",
            "category": "",
            "last_time": cursor,
            "os": "web",
            "refresh_type": "1",
            "rn": "20",
            "sv": "8.4.6",
        }
        params["sign"] = hashlib.md5(
            hashlib.sha1(urlencode(params).encode()).hexdigest().encode()
        ).hexdigest()
        try:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("CLS daily window budget exhausted")
            pages += 1
            payload = await asyncio.wait_for(fetch(CLS_URL, params=params), remaining)
            if (
                isinstance(payload, dict)
                and "errno" in payload
                and (
                    type(payload["errno"]) not in {int, str}
                    or str(payload["errno"]) != "0"
                )
            ):
                raise _Unavailable("CLS upstream rejected request")
            data = payload.get("data") if isinstance(payload, dict) else None
            raw = data.get("roll_data") if isinstance(data, dict) else None
            if not isinstance(raw, list):
                raise _Unavailable("CLS roll data unavailable")
        except Exception as error:  # noqa: BLE001 -- keep completed public pages
            if not scanned:
                raise
            stop_reason = "PAGE_FAILED"
            error_fields["errorClass"] = type(error).__name__
            if isinstance(error, httpx.HTTPStatusError):
                error_fields["httpStatus"] = error.response.status_code
            break
        if not raw:
            stop_reason = "EMPTY_PAGE"
            break
        retrieved = _aware(now_shanghai())
        page_new, page_times, reached_window = 0, [], False
        for item in raw[:20]:
            if not isinstance(item, dict):
                continue
            seconds = _number(item.get("ctime"))
            if seconds is None:
                continue
            try:
                published = datetime.fromtimestamp(float(seconds), SHANGHAI)
            except (ValueError, OSError, OverflowError):
                continue
            if published > cutoff or published > retrieved:
                continue
            page_times.append(published)
            if published < window_start:
                reached_window = True
                continue
            title, content = (
                _text(item.get("title"), 200),
                _text(item.get("content"), 32000),
            )
            text = title + " " + content
            if not text.strip():
                continue
            raw_identity = str(item.get("id") or "")
            identity = (
                raw_identity if re.fullmatch(r"[1-9]\d{0,18}", raw_identity) else None
            )
            digest = hashlib.sha256(text.encode()).hexdigest()
            key = identity or digest
            if key in seen_ids or digest in seen_texts:
                continue
            seen_ids.add(key)
            seen_texts.add(digest)
            scanned += 1
            page_new += 1
            oldest = published if oldest is None else min(oldest, published)
            if "新闻联播" in title and is_media_noise(title, ""):
                filtered_noise += 1
                continue
            level = item.get("level")
            parent_id = "cls:" + (identity or digest[:20])
            for ordinal, child_title, child_content in split_numbered_digest(
                title, content
            ):
                child_text = child_title + " " + child_content
                matched = [
                    word
                    for word in INVESTMENT_KEYWORDS
                    if _keyword_in_text(word, child_text)
                ]
                sectors = catalyst_sectors(child_text)
                if is_media_noise(child_title, child_content):
                    filtered_noise += 1
                    continue
                core = core_catalyst(child_text, sectors)
                if level not in {"A", "B"} and not matched and not core:
                    continue
                child_id = (
                    parent_id
                    if ordinal is None
                    else (
                        f"{parent_id}:item:{ordinal}:"
                        + hashlib.sha256(child_content.encode()).hexdigest()[:12]
                    )
                )
                rows.append(
                    {
                        "id": child_id,
                        "title": child_title,
                        "content": child_content,
                        **(
                            {
                                "parentSourceId": parent_id,
                                "parentTitle": title,
                                "itemOrdinal": ordinal,
                            }
                            if ordinal is not None
                            else {}
                        ),
                        "phase": "REPORTED",
                        "importance": "HIGH"
                        if level == "A"
                        else "MEDIUM"
                        if level == "B"
                        else "LOW",
                        "matchedKeywords": matched,
                        "sectorBuckets": sectors or ["OTHER"],
                        "researchPriority": "CORE_CATALYST" if core else "MARKET_CLUE",
                        "factVerification": "UNVERIFIED",
                        "asOf": published.date().isoformat(),
                        "publishedAt": published.isoformat(),
                        "retrievedAt": retrieved.isoformat(),
                        "availableAt": retrieved.isoformat(),
                        "timeBasis": "PROVIDER_PUBLICATION",
                        "sourceKey": "CLS_TELEGRAPH",
                        "sourceUri": f"https://www.cls.cn/detail/{identity}"
                        if identity
                        else "https://www.cls.cn/telegraph",
                        "sourceName": "财联社电报",
                        "methodVersion": VERSION,
                    }
                )
            if scanned >= 300:
                break
        if scanned >= 300:
            stop_reason = "ITEM_LIMIT"
            break
        if reached_window:
            stop_reason = "WINDOW_START"
            break
        if page_new == 0:
            stop_reason = "DUPLICATE_PAGE" if page_times else "NO_VALID_CURSOR"
            break
        next_cursor = int(min(page_times).timestamp()) - 1
        if next_cursor >= cursor:
            stop_reason = "CURSOR_NOT_ADVANCING"
            break
        cursor = next_cursor
    complete = stop_reason in {"EMPTY_PAGE", "WINDOW_START"}
    rows.sort(
        key=lambda row: (
            {"HIGH": 2, "MEDIUM": 1, "LOW": 0}[row["importance"]],
            row["publishedAt"],
            row["id"],
        ),
        reverse=True,
    )
    if len(rows) > 300:
        complete = False
        stop_reason = "EXPANDED_ITEM_LIMIT"
    retained = _industry_order(rows)[:300]
    return _result(
        catalysts=retained,
        status="SUCCESS" if complete else "PARTIAL",
        allowEmpty=True,
        coverage={
            "scannedCount": scanned,
            "retainedCount": len(retained),
            "filteredNoiseCount": filtered_noise,
            "pages": pages,
            "windowStart": window_start.isoformat(),
            "windowEnd": cutoff.isoformat(),
            "oldestPublishedAt": oldest.isoformat() if oldest else None,
            "truncated": not complete,
            "stopReason": stop_reason,
        },
        **error_fields,
        limitations=[
            "最近24小时最多300条父电报、15页；编号合集每篇最多40子项，行业均衡后最多保留300项，覆盖不足记录在coverage。媒体报道尚待原始来源印证。"
        ],
    )


def _array(value):
    result = json.loads(value) if isinstance(value, str) else value
    return result if isinstance(result, list) else None


async def _gamma(fetch, cutoff):
    payload = await fetch(
        GAMMA_URL,
        params={"closed": "false", "active": "true", "limit": 20, "offset": 0},
    )
    if not isinstance(payload, list):
        raise _Unavailable("Gamma market list unavailable")
    retrieved, rows = _aware(now_shanghai()), []
    for item in payload[:20]:
        if (
            item.get("closed") is not False
            or item.get("active") is not True
            or item.get("archived") is True
        ):
            continue
        identity = str(item.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity):
            continue
        try:
            outcomes, prices = (
                _array(item.get("outcomes")),
                _array(item.get("outcomePrices")),
            )
        except (ValueError, TypeError):
            continue
        if (
            not outcomes
            or not prices
            or len(outcomes) != len(prices)
            or len(outcomes) > 20
        ):
            continue
        numbers = [_number(value) for value in prices]
        if any(value is None or not 0 <= value <= 1 for value in numbers):
            continue
        updated, end = _stamp(item.get("updatedAt")), _stamp(item.get("endDate"))
        if (
            updated
            and (updated > cutoff or updated > retrieved)
            or end
            and end <= cutoff
        ):
            continue
        for outcome, price in zip(outcomes, numbers, strict=True):
            if not isinstance(outcome, str) or not outcome.strip():
                continue
            rows.append(
                {
                    "marketId": identity,
                    "question": _text(item.get("question"), 300),
                    "outcome": _text(outcome, 120),
                    "price": format(price, "f"),
                    "impliedProbabilityPct": format(price * 100, "f"),
                    "unit": "%",
                    "priceKind": "MARKET_IMPLIED_PRICE",
                    "quoteTimestamp": None,
                    "sourceUpdatedAt": updated.isoformat() if updated else None,
                    "marketEndAt": end.isoformat() if end else None,
                    "observedAt": retrieved.isoformat(),
                    "asOf": retrieved.date().isoformat(),
                    "retrievedAt": retrieved.isoformat(),
                    "availableAt": retrieved.isoformat(),
                    "publishedAt": None,
                    "timeBasis": "RETRIEVAL_ONLY",
                    "sourceKey": "POLYMARKET_GAMMA",
                    "sourceUri": f"{GAMMA_URL}/{identity}",
                    "sourceName": "Polymarket Gamma",
                    "methodVersion": VERSION,
                    "verification": "PROVIDER_REPORTED",
                }
            )
    return _result(
        odds=rows,
        allowEmpty=True,
        limitations=[
            "单页最多20个活跃市场；价格不是统计真实概率，updatedAt不是赔率生成时刻。"
        ],
    )


async def _launches(fetch, cutoff):
    global _launch_day
    if _launch_day == cutoff.date():
        raise _Skipped("one LL2 attempt per Shanghai day")
    _launch_day = cutoff.date()  # consume before HTTP, including failed attempts
    payload = await fetch(
        LAUNCH_URL,
        params={"limit": 20, "hide_recent_previous": "true", "ordering": "net"},
    )
    raw = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise _Unavailable("Launch Library results unavailable")
    retrieved, rows = _aware(now_shanghai()), []
    for item in raw[:20]:
        net = _stamp(item.get("net"))
        if net is None or net <= cutoff or net <= retrieved:
            continue
        identity = str(item.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity):
            continue
        status = item.get("status") or {}
        if not isinstance(status, dict):
            status = {}
        updated = _stamp(item.get("last_updated"))
        if updated and (updated > cutoff or updated > retrieved):
            continue
        rows.append(
            {
                "id": identity,
                "name": _text(item.get("name"), 200),
                "phase": "PLANNED",
                "scheduledAt": net.isoformat(),
                "windowStart": _stamp(item.get("window_start")).isoformat()
                if _stamp(item.get("window_start"))
                else None,
                "windowEnd": _stamp(item.get("window_end")).isoformat()
                if _stamp(item.get("window_end"))
                else None,
                "status": {
                    "id": status.get("id") if type(status.get("id")) is int else None,
                    "name": _text(status.get("name"), 100) or None,
                    "abbrev": _text(status.get("abbrev"), 30) or None,
                },
                "sourceUpdatedAt": updated.isoformat() if updated else None,
                "asOf": retrieved.date().isoformat(),
                "publishedAt": None,
                "retrievedAt": retrieved.isoformat(),
                "availableAt": retrieved.isoformat(),
                "timeBasis": "RETRIEVAL_ONLY",
                "sourceKey": "SPACEDEVS_LAUNCHES",
                "sourceUri": f"https://ll.thespacedevs.com/2.3.0/launches/{identity}/",
                "sourceName": "The Space Devs Launch Library 2",
                "methodVersion": VERSION,
            }
        )
    return _result(
        launches=rows,
        allowEmpty=True,
        limitations=[
            "生产匿名接口最多15次/小时；每个采集任务只尝试一次，调试重跑是显式新任务，仍受来源限频；单页不跟随next。"
        ],
    )


async def _tsf(fetch, cutoff):
    index = BeautifulSoup(await fetch(TSF_INDEX, text=True), "html.parser")
    candidates = set()
    for anchor in index.find_all("a", href=True):
        if "金融统计数据报告" not in _text(
            anchor.get("title") or anchor.get_text(), 200
        ):
            continue
        uri = urljoin(TSF_INDEX, anchor["href"])
        parts = urlsplit(uri)
        if (
            parts.scheme == "https"
            and parts.hostname == "jrj.sh.gov.cn"
            and parts.path.startswith("/SCGK194/")
            and parts.path.endswith(".html")
            and not parts.path.endswith("index.html")
        ):
            candidates.add(uri)
    retrieved, rows = _aware(now_shanghai()), []
    for uri in sorted(candidates, reverse=True)[:2]:
        soup = BeautifulSoup(await fetch(uri, text=True), "html.parser")
        retrieved = _aware(now_shanghai())
        publication_stamps = {
            stamp
            for meta in soup.find_all("meta")
            if str(meta.get("name") or meta.get("property") or "").casefold()
            in {
                "pubdate",
                "publishdate",
                "article:published_time",
                "dc.date",
                "dcterms.date",
            }
            and (stamp := _stamp(meta.get("content"))) is not None
        }
        if any(stamp > cutoff or stamp > retrieved for stamp in publication_stamps):
            continue
        body = soup.get_text(" ", strip=True)
        publisher = re.search(r"信息来源\s*[:：]\s*(.*?)\s*发布时间", body)
        if publisher is None or publisher[1].strip() != "中国人民银行":
            continue
        publication = re.search(r"发布时间\s*[:：]\s*(\d{4}-\d{2}-\d{2})", body)
        day = _day(publication[1]) if publication else None
        if day is None or day > cutoff.date():
            continue
        matches = re.findall(
            r"初步统计[，,]?\s*(\d{4})年(\d{1,2})月末社会融资规模存量为[\d.]+万亿元[，,]同比(增长|下降)(\d+(?:\.\d+)?)\s*[%％]",
            body,
        )
        values = {}
        for year, month, direction, amount in matches:
            reference = _month(f"{year}-{int(month):02d}-01")
            value = _number(amount)
            if reference and reference <= cutoff.date() and value is not None:
                value = -value if direction == "下降" else value
                values.setdefault(reference, set()).add(value)
        if any(len(group) > 1 for group in values.values()):
            raise ValueError("conflicting stock growth in government reprint")
        retrieved = _aware(now_shanghai())
        for reference, group in values.items():
            rows.append(
                _row(
                    "TSF_STOCK_YOY",
                    reference,
                    next(iter(group)),
                    "GOV_SH_PBOC_TSF",
                    uri,
                    retrieved,
                    frequency="MONTHLY",
                    name="上海市委金融办转载（原发布：中国人民银行）",
                    metadata={
                        "provider": "SHANGHAI_GOV_PBOC_REPRINT",
                        "verification": "GOVERNMENT_REPRINT",
                        "publishedDate": day.isoformat(),
                        "originalPublisher": "中国人民银行",
                        "statistic": "totalSocialFinancingStockYoY",
                        "definitionVersion": "PBOC_REPORTED_STOCK",
                        "notFromIncrement": True,
                    },
                )
            )
    return _result(
        observations=rows,
        limitations=[
            "仅官方gov站明确转载的全国社融存量句；索引或正文缺失时UNAVAILABLE，不用社融增量代替。"
        ],
    )


async def collect_alpha_public(now, *, client=None, provider=None, timeout_seconds=20):
    """Collect independent bounded sources; optional provider maps async adapters.

    A provider override is a mapping from a SOURCE_KEY to an async callable with
    (fetch, cutoff) arguments and the same normalized result as our adapters.
    The default uses no AKShare requests threads. Caller-owned clients stay open.
    LL2's in-memory daily guard must be backed by the caller's durable daily slot.
    """
    cutoff = _aware(now)
    if client is None:
        async with httpx.AsyncClient(
            headers={"User-Agent": "WealthSail public macro research"},
            follow_redirects=False,
        ) as owned:
            return await collect_alpha_public(
                cutoff, client=owned, provider=provider, timeout_seconds=timeout_seconds
            )
    bundle = {
        "methodVersion": VERSION,
        "asOf": cutoff.isoformat(),
        **_result(),
        "sourceResults": {},
    }
    adapters = {
        "AKSHARE_MONEY_SUPPLY": _money,
        "AKSHARE_CN_T10": lambda fetch, at: _rate(fetch, at),
        "AKSHARE_SHIBOR_7D": lambda fetch, at: _rate(fetch, at, shibor=True),
        "AKSHARE_HOUSE_HAINAN": lambda fetch, at: _housing(fetch, at, hainan=True),
        "AKSHARE_HOUSE_MAINLAND": lambda fetch, at: _housing(fetch, at, hainan=False),
        "CLS_TELEGRAPH": _cls,
        "POLYMARKET_GAMMA": _gamma,
        "SPACEDEVS_LAUNCHES": _launches,
        "GOV_SH_PBOC_TSF": _tsf,
    }
    host_locks = {host: asyncio.Semaphore(2) for host in HOSTS}

    async def fetch(uri, **kwargs):
        async with host_locks[urlsplit(_url(uri)).hostname]:
            return await _fetch(
                client, uri, timeout=min(float(timeout_seconds), 12), **kwargs
            )

    async def collect(key):
        try:
            adapter = (
                provider.get(key, adapters[key])
                if provider is not None
                else adapters[key]
            )
            pending = adapter(fetch, cutoff)
            if not inspect.isawaitable(pending):
                raise TypeError("source adapter must be asynchronous")
            result = await asyncio.wait_for(pending, timeout=float(timeout_seconds))
            observations = {}
            for row in result.get("observations", []):
                identity = row["code"], row["referenceDate"]
                if identity in observations and (
                    observations[identity]["value"] != row["value"]
                    or observations[identity]["unit"] != row["unit"]
                ):
                    raise ValueError("conflicting observations from one source")
                observations.setdefault(identity, row)
            result["observations"] = list(observations.values())
            count = sum(
                len(result.get(name, []))
                for name in ("observations", "catalysts", "odds", "launches")
            )
            if count == 0 and not result.get("allowEmpty"):
                raise _Unavailable("documented observations unavailable")
            for name in ("observations", "catalysts", "odds", "launches"):
                bundle[name].extend(result.get(name, []))
            status = {
                "status": "PARTIAL" if result.get("status") == "PARTIAL" else "SUCCESS",
                "records": count,
                "retrievedAt": _aware(now_shanghai()).isoformat(),
                "limitations": result.get("limitations", []),
            }
            if key == "CLS_TELEGRAPH":
                for field in ("coverage", "errorClass", "httpStatus"):
                    if field in result:
                        status[field] = result[field]
        except _Skipped:
            status = {
                "status": "SKIPPED",
                "errorClass": "DailyBudgetConsumed",
                "limitations": ["本进程今日LL2尝试已消耗；不重试。"],
            }
        except Exception as error:  # noqa: BLE001 -- failures cannot abort sibling sources
            status = {
                "status": "UNAVAILABLE"
                if isinstance(error, _Unavailable)
                else "TIMEOUT"
                if isinstance(error, (TimeoutError, httpx.TimeoutException))
                else "FAILED",
                "errorClass": type(error).__name__,
            }
            if isinstance(error, httpx.HTTPStatusError):
                status["httpStatus"] = error.response.status_code
                if error.response.status_code in {401, 403}:
                    status["status"] = "PERMISSION_REQUIRED"
                elif error.response.status_code == 429:
                    status["status"] = "RATE_LIMITED"
                    retry = error.response.headers.get("retry-after", "")
                    if retry.isascii() and retry.isdecimal() and len(retry) <= 8:
                        status["retryAfterSeconds"] = min(int(retry), 86400)
            status["retrievedAt"] = _aware(now_shanghai()).isoformat()
        status.setdefault("retrievedAt", _aware(now_shanghai()).isoformat())
        status.setdefault("records", 0)
        status.setdefault(
            "limitations",
            [
                "未核验到官方转载的全国社融存量同比；不使用增量替代，可保留既有官源事实。"
                if key == "GOV_SH_PBOC_TSF"
                else "本源未取得可用公开记录；不重试拒绝/限流，不补默认值。"
            ],
        )
        bundle["sourceResults"][key] = status

    await asyncio.gather(*(collect(key) for key in SOURCE_KEYS))
    bundle["asOf"] = _aware(now_shanghai()).isoformat()
    bundle["sourceResults"] = {key: bundle["sourceResults"][key] for key in SOURCE_KEYS}
    bundle["observations"].sort(key=lambda row: (row["code"], row["referenceDate"]))
    return bundle
