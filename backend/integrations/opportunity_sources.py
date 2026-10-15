"""Bounded public official interest-rate and credit observations.

Observation dates are economic dates, not invented publication timestamps.
ChinaBond / CFETS clients are intentionally absent: registration alone does not
establish the permission to collect their data in this deployment.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import math
import re
from datetime import date, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from backend.integrations.macro_official import (
    PBOC_RELEASE_INDEX,
    _compact,
    _meta,
    _now,
    _pboc_title_period,
    _published,
    _soup,
    _table_grid,
)

TREASURY_XML_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
SOURCE_KEYS = (
    "UST_NOMINAL",
    "UST_REAL",
    "PBOC_M1",
    "PBOC_TSF",
    "PBOC_LOANS",
    "CHINABOND",
    "CFETS",
)
_SOURCE_TIMEOUT_SECONDS = 45
_MAX_BYTES = 2 * 1024 * 1024
_MAX_PBOC_PERIODS = 2
_MAX_PBOC_DOCUMENTS_PER_PERIOD = 2
_METHOD = "opportunity-official-v1"
_NEW_YORK = ZoneInfo("America/New_York")
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
}
_DEFINITIONS = {
    "US_T10_NOMINAL": (
        "UST_NOMINAL",
        "UST_PAR_YIELD_10Y",
        "UST_MONOTONE_CONVEX_2021_12_06",
        "DAILY",
        "%",
        "U.S. Department of the Treasury",
    ),
    "US_T10_REAL": (
        "UST_REAL",
        "UST_TIPS_PAR_REAL_YIELD_10Y",
        "UST_MONOTONE_CONVEX_2021_12_06",
        "DAILY",
        "%",
        "U.S. Department of the Treasury",
    ),
    "M1_YOY": (
        "PBOC_M1",
        "PBOC_M1_YOY",
        "PBOC_M1_2025",
        "MONTHLY",
        "%",
        "中国人民银行",
    ),
    "TSF_STOCK_YOY": (
        "PBOC_TSF",
        "PBOC_TSF_STOCK_YOY",
        "PBOC_TSF_2023_COMPARABLE",
        "MONTHLY",
        "%",
        "中国人民银行",
    ),
    "CORP_MEDLONG_LOANS_YTD": (
        "PBOC_LOANS",
        "PBOC_CORPORATE_MEDLONG_LOANS_NET_INCREASE_YTD",
        "PBOC_FINANCIAL_STATISTICS_2023",
        "YTD",
        "万亿元",
        "中国人民银行",
    ),
}
logger = logging.getLogger("wealthsail.opportunity_sources")


def _url(value, *, publisher=None):
    parts = urlsplit(value)
    groups = {
        "TREASURY": {"home.treasury.gov"},
        "PBOC": {"www.pbc.gov.cn", "pbc.gov.cn"},
    }
    host = (parts.hostname or "").lower()
    allowed = groups[publisher] if publisher else set().union(*groups.values())
    if (
        parts.scheme not in {"https", "http"}
        or host not in allowed
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 80, 443}
    ):
        raise ValueError("source must be a permitted national official URL")
    if host == "pbc.gov.cn":
        host = "www.pbc.gov.cn"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() != "spm"
        )
    )
    return urlunsplit(("https", host, parts.path or "/", query, ""))


def _decimal(value):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("official numeric value unavailable") from exc
    if not number.is_finite() or not math.isfinite(float(number)):
        raise ValueError("official numeric value must be finite")
    return number


def _row(code, reference, value, source_uri, now, published=None, **metadata):
    source_key, key, version, frequency, unit, source_name = _DEFINITIONS[code]
    retrieved = _now(now).astimezone(timezone.utc)
    published = published.astimezone(timezone.utc) if published else None
    return {
        "code": code,
        "referenceDate": reference.isoformat(),
        "frequency": frequency,
        "value": _decimal(value),
        "unit": unit,
        "sourceUri": source_uri,
        "sourceName": source_name,
        "publishedAt": published,
        "retrievedAt": retrieved,
        "availableAt": published if published else retrieved,
        "timeBasis": "OFFICIAL_PUBLICATION" if published else "RETRIEVAL_ONLY",
        "methodVersion": _METHOD,
        "metadata": {
            "sourceKey": source_key,
            "definitionKey": key,
            "definitionVersion": version,
            **metadata,
        },
    }


def _unique(rows):
    results = {}
    for row in rows:
        key = row["code"], row["referenceDate"]
        if key in results and results[key]["value"] != row["value"]:
            raise ValueError("conflicting official observations")
        results[key] = row
    return sorted(results.values(), key=lambda row: (row["code"], row["referenceDate"]))


def parse_treasury_xml(text, source_uri, code, now):
    """Parse the official EDM 10Y field, retaining the literal NY market date."""
    now = _now(now)
    source_uri = _url(source_uri, publisher="TREASURY")
    expected = {
        "US_T10_NOMINAL": ("daily_treasury_yield_curve", "BC_10YEAR"),
        "US_T10_REAL": ("daily_treasury_real_yield_curve", "TC_10YEAR"),
    }
    if code not in expected:
        raise ValueError("unsupported Treasury series")
    data, field = expected[code]
    parameters = dict(parse_qsl(urlsplit(source_uri).query))
    if (
        urlsplit(source_uri).path != urlsplit(TREASURY_XML_URL).path
        or parameters.get("data") != data
        or re.fullmatch(r"\d{4}", parameters.get("field_tdr_date_value", "")) is None
    ):
        raise ValueError("Treasury feed identity unavailable")
    if len(text.encode()) > _MAX_BYTES or re.search(
        r"<!DOCTYPE|<!ENTITY", text, re.IGNORECASE
    ):
        raise ValueError("unsupported official XML document")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError("official XML document unavailable") from exc
    if root.tag != f"{{{_NS['a']}}}feed" or root.findall("a:link[@rel='next']", _NS):
        raise ValueError("unexpected or incomplete annual Treasury feed")
    entries = root.findall("a:entry", _NS)
    if not entries:
        raise ValueError("Treasury observations unavailable")
    rows = []
    for entry in entries:
        properties = entry.find("a:content/m:properties", _NS)
        if properties is None:
            raise ValueError("Treasury properties unavailable")
        dates, values = (
            properties.findall(f"d:{name}", _NS) for name in ("NEW_DATE", field)
        )
        if len(dates) != 1 or len(values) != 1:
            raise ValueError("Treasury date or 10Y yield unavailable")
        raw_date = dates[0].text or ""
        if (
            re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T00:00:00(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
                raw_date,
            )
            is None
        ):
            raise ValueError("Treasury economic date unavailable")
        reference = date.fromisoformat(raw_date[:10])
        if (
            reference.year != int(parameters["field_tdr_date_value"])
            or reference > now.astimezone(_NEW_YORK).date()
        ):
            raise ValueError("future or wrong-year Treasury economic date")
        if values[0].get(f"{{{_NS['m']}}}null", "false") != "false":
            raise ValueError("Treasury 10Y yield is null")
        value = _decimal(values[0].text)
        if code == "US_T10_NOMINAL" and value < 0:
            raise ValueError("negative nominal Treasury par yield")
        rows.append(
            _row(
                code,
                reference,
                value,
                source_uri,
                now,
                referenceTimezone="America/New_York",
                statistic="tipsParRealYield10Year"
                if code == "US_T10_REAL"
                else "nominalParYield10Year",
                officialField=field,
            )
        )
    return _unique(rows)


def _rate(direction, number):
    if direction == "持平":
        if number:
            raise ValueError("ambiguous unchanged growth rate")
        return Decimal(0)
    value = _decimal(number)
    if direction == "下降":
        if value < 0:
            raise ValueError("ambiguous double negative growth")
        value = -value
    if value < -100:
        raise ValueError("invalid growth rate")
    return value


_RATE = r"同比(增长|下降|持平)([^%。，；]*)(?:%|(?=[。，；]))"


def _report_period(title, code):
    if code == "TSF_STOCK_YOY":
        title = title.replace("社会融资规模存量统计数据报告", "金融统计数据报告")
    return _pboc_title_period(title)


def _accumulation_pattern(year, month):
    chinese = (
        "",
        "一",
        "二",
        "三",
        "四",
        "五",
        "六",
        "七",
        "八",
        "九",
        "十",
        "十一",
        "十二",
    )
    labels = [rf"前(?:{month}|{chinese[month]})个?月", rf"[1１][—\-－至～~]{month}月"]
    special = {
        1: r"(?:1|一)月(?:份)?",
        3: "一季度",
        6: "上半年",
        9: "前三季度",
        12: "全年",
    }
    if month in special:
        labels.append(special[month])
    return rf"(?:{year}年)?(?:{'|'.join(labels)})人民币贷款(?:增加|减少)"


def _backcast(content, body, source_uri, now, published):
    if "按可比口径回溯后，2024年各月末M1可比余额和增速分别为" not in body:
        return []
    rows = []
    for table in content.find_all("table"):
        periods = None
        for cells in _table_grid(table):
            dates = [re.fullmatch(r"(\d{4})年(\d{1,2})月", cell) for cell in cells]
            if any(dates):
                periods = {
                    index: date(int(match[1]), int(match[2]), 1)
                    for index, match in enumerate(dates)
                    if match
                }
                if any(reference.year != 2024 for reference in periods.values()):
                    raise ValueError("M1 comparable backcast year is not 2024")
            elif cells and cells[0] in {"同比增速", "同比增速(%)", "同比增速（%）"}:
                if not periods:
                    raise ValueError("M1 comparable backcast periods unavailable")
                for index, reference in periods.items():
                    if index >= len(cells) or not cells[index].endswith("%"):
                        raise ValueError("M1 comparable backcast rate unavailable")
                    value = _decimal(cells[index][:-1])
                    if value < -100:
                        raise ValueError("invalid M1 backcast growth")
                    rows.append(
                        _row(
                            "M1_YOY",
                            reference,
                            value,
                            source_uri,
                            now,
                            published,
                            statistic="M1YoY",
                            isComparableBackcast=True,
                        )
                    )
    # Missing tables do not create observations. Present malformed tables fail.
    return _unique(rows)


def parse_pboc_series(text, source_uri, code, now):
    """National new-definition M1, TSF stock growth, or corporate YTD loans."""
    if code not in {"M1_YOY", "TSF_STOCK_YOY", "CORP_MEDLONG_LOANS_YTD"}:
        raise ValueError("unsupported PBoC series")
    now = _now(now)
    source_uri = _url(source_uri, publisher="PBOC")
    soup = _soup(text)
    year, month = _report_period(_meta(soup, "ArticleTitle"), code)
    reference = date(year, month, 1)
    period_end = date(year, month, calendar.monthrange(year, month)[1])
    if period_end > now.date() or year < 2023:
        raise ValueError("future or incompatible financial statistics period")
    # Recognizable future days are inadmissible even when time precision is
    # unsupported and would otherwise remain UNKNOWN.
    actual = soup.find(id="shijian")
    for raw in (
        _meta(soup, "PubDate"),
        actual.get_text(" ", strip=True) if actual else "",
    ):
        match = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})", raw)
        if match and date(*map(int, match.groups())) > now.date():
            raise ValueError("future official publication date")
    published = _published(soup, now, pboc=True)
    content = soup.find(id="zoom")
    if content is None:
        raise ValueError("national financial statistics body unavailable")
    body = _compact(content.get_text(" ", strip=True))
    metadata, backcast = {}, []
    if code == "M1_YOY":
        if (
            year < 2025
            or "自统计2025年1月份数据起，启用新修订的狭义货币(M1)统计口径"
            not in body.replace("（", "(").replace("）", ")")
            or "个人活期存款" not in body
            or "非银行支付机构客户备付金" not in body
        ):
            raise ValueError("new 2025 M1 definition not confirmed")
        matches = re.findall(
            r"(?:(\d{4})年)?(\d{1,2})月末[,，]?广义货币[（(]M2[）)]余额[^。]*。狭义货币[（(]M1[）)]余额[^。]*?"
            + _RATE,
            body,
        )
        values = {
            _rate(direction, number)
            for doc_year, doc_month, direction, number in matches
            if (not doc_year or int(doc_year) == year) and int(doc_month) == month
        }
        metadata = {"statistic": "M1YoY", "isComparableBackcast": False}
        backcast = _backcast(content, body, source_uri, now, published)
    elif code == "TSF_STOCK_YOY":
        matches = re.findall(
            r"(?:(\d{4})年)?(\d{1,2})月末社会融资规模存量为[^。]*?" + _RATE,
            body,
        )
        values = {
            _rate(direction, number)
            for doc_year, doc_month, direction, number in matches
            if (not doc_year or int(doc_year) == year) and int(doc_month) == month
        }
        metadata = {"statistic": "totalSocialFinancingStockYoY"}
    else:
        accumulation = _accumulation_pattern(year, month)
        matches = [
            match
            for paragraph in content.find_all("p")
            if re.search(accumulation, _compact(paragraph.get_text(" ", strip=True)))
            for match in re.findall(
                r"企[（(]事[）)]业单位贷款[^；。]*?其中[,，]?[^；。]*?中长期贷款(增加|减少)([^万亿，。；]*)(万亿元|亿元)",
                _compact(paragraph.get_text(" ", strip=True)),
            )
        ]
        values = set()
        for direction, number, unit in matches:
            value = _decimal(number)
            if value < 0:
                raise ValueError("ambiguous corporate loan change")
            value = value / Decimal(10000) if unit == "亿元" else value
            values.add(-value if direction == "减少" else value)
        metadata = {
            "statistic": "corporateMediumLongLoansNetIncreaseYTD",
            "accumulationStart": date(year, 1, 1).isoformat(),
            "accumulationEnd": period_end.isoformat(),
        }
    if len(values) != 1:
        raise ValueError("unavailable or conflicting official statistic")
    return _unique(
        [
            *backcast,
            _row(code, reference, values.pop(), source_uri, now, published, **metadata),
        ]
    )


async def _fetch_text(client, url):
    url = _url(url)
    original_host = urlsplit(url).hostname
    for _ in range(4):
        async with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                url = _url(urljoin(url, response.headers.get("location", "")))
                if urlsplit(url).hostname != original_host:
                    raise ValueError("cross-publisher redirect rejected")
                continue
            response.raise_for_status()
            body = bytearray()
            async for part in response.aiter_bytes():
                body.extend(part)
                if len(body) > _MAX_BYTES:
                    raise ValueError("official document exceeds bounded size")
            return bytes(body).decode("utf-8-sig", errors="strict")
    raise ValueError("official redirect limit exceeded")


def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


async def fetch_opportunity_series(now, permissions=None):
    """Collect independent public sources, with a 45-second whole-source budget.

    The reserved permissions argument does not enable licensed clients. Their
    implementation and verified permission review are outside this public build.
    A failed source emits no replacement values; durable old facts stay intact.
    """
    now = _now(now)
    observations, results, pending = [], {}, {}
    async with httpx.AsyncClient(
        timeout=12,
        follow_redirects=False,
        headers={
            "User-Agent": "WealthSail macro research (+official-source-verification)"
        },
    ) as client:

        async def text(uri):
            if uri not in pending:
                pending[uri] = asyncio.create_task(_fetch_text(client, uri))
            return await asyncio.shield(pending[uri])

        async def collect(key, function):
            try:
                rows = await asyncio.wait_for(
                    function(), timeout=_SOURCE_TIMEOUT_SECONDS
                )
                if not rows:
                    raise ValueError("official observations unavailable")
                observations.extend(rows)
                results[key] = {"status": "SUCCESS", "observations": len(rows)}
            except Exception as exc:  # noqa: BLE001 -- source failures are independent
                results[key] = {"status": "FAILED", "errorClass": type(exc).__name__}
                if isinstance(exc, httpx.HTTPStatusError):
                    code = exc.response.status_code
                    if type(code) is int and 100 <= code <= 599:
                        results[key]["httpStatus"] = code
                logger.warning(
                    "opportunity_source_failed source=%s error_class=%s",
                    key,
                    type(exc).__name__,
                )

        async def treasury(code):
            first_day = _years_before(now.astimezone(_NEW_YORK).date(), 3)
            data = (
                "daily_treasury_real_yield_curve"
                if code == "US_T10_REAL"
                else "daily_treasury_yield_curve"
            )
            uris = [
                f"{TREASURY_XML_URL}?data={data}&field_tdr_date_value={year}"
                for year in range(first_day.year, now.astimezone(_NEW_YORK).year + 1)
            ]
            documents = await asyncio.gather(*(text(uri) for uri in uris))
            rows = [
                row
                for uri, document in zip(uris, documents, strict=True)
                for row in parse_treasury_xml(document, uri, code, now)
                if date.fromisoformat(row["referenceDate"]) >= first_day
            ]
            return _unique(rows)

        async def pboc(code):
            candidates = []
            for anchor in _soup(await text(PBOC_RELEASE_INDEX)).find_all(
                "a", href=True
            ):
                title = _compact(
                    anchor.get("title") or anchor.get_text(" ", strip=True)
                )
                try:
                    year, month = _report_period(title, code)
                except ValueError:
                    continue
                if year < (2025 if code == "M1_YOY" else 2023):
                    continue  # Explicitly different definitions are not candidates.
                candidates.append(
                    (
                        date(year, month, 1),
                        "社会融资规模存量统计数据报告" in title,
                        _url(
                            urljoin(PBOC_RELEASE_INDEX, anchor["href"]),
                            publisher="PBOC",
                        ),
                    )
                )
            # Inspect both same-period documents when present, so conflicting
            # official values cannot be hidden by preferring one report type.
            # Financial reports without this statistic are not stock evidence.
            periods = {}
            for period, dedicated, uri in sorted(set(candidates), reverse=True):
                if period not in periods and len(periods) == _MAX_PBOC_PERIODS:
                    continue
                same_period = periods.setdefault(period, [])
                if len(same_period) < _MAX_PBOC_DOCUMENTS_PER_PERIOD:
                    same_period.append((dedicated, uri))
            # An agreeing dedicated stock document supplies the canonical URI.
            selected = [row for group in periods.values() for row in reversed(group)]
            if not selected:
                raise ValueError(
                    "national financial statistics report links unavailable"
                )
            # Latest bad documents fail this source; older valid rows cannot
            # silently impersonate its current observation.
            rows = []
            for dedicated, uri in selected:
                document = await text(uri)
                if code == "TSF_STOCK_YOY" and not dedicated:
                    content = _soup(document).find(id="zoom")
                    if content is not None and "社会融资规模存量" not in _compact(
                        content.get_text(" ", strip=True)
                    ):
                        continue
                rows.extend(parse_pboc_series(document, uri, code, now))
            return _unique(rows)

        try:
            await asyncio.gather(
                collect("UST_NOMINAL", lambda: treasury("US_T10_NOMINAL")),
                collect("UST_REAL", lambda: treasury("US_T10_REAL")),
                collect("PBOC_M1", lambda: pboc("M1_YOY")),
                collect("PBOC_TSF", lambda: pboc("TSF_STOCK_YOY")),
                collect("PBOC_LOANS", lambda: pboc("CORP_MEDLONG_LOANS_YTD")),
            )
        finally:
            for task in pending.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending.values(), return_exceptions=True)
    results.update(
        {
            key: {
                "status": "PERMISSION_REQUIRED",
                "reason": "Written collection permission and a licensed client are required",
            }
            for key in ("CHINABOND", "CFETS")
        }
    )
    return {
        "observations": sorted(
            observations, key=lambda row: (row["code"], row["referenceDate"])
        ),
        "sourceResults": {key: results[key] for key in SOURCE_KEYS},
    }
