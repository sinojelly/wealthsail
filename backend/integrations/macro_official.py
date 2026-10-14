"""Bounded official macro release, calendar and monetary-policy RSS evidence.

Ordinary HTTP/HTML protocols were checked against the national publishers.
Reported publication times are independent of page generation and collection.
Missing time precision never becomes an invented release timestamp.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from datetime import date, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

NBS_RELEASE_INDEX = "https://www.stats.gov.cn/sj/zxfb/"
NBS_CALENDAR_INDEX = "https://www.stats.gov.cn/xxgk/sjfb/fbrcb/"
PBOC_RELEASE_INDEX = "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html"
FED_RSS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
SOURCE_KEYS = ("NBS_CPI", "NBS_PPI", "NBS_PMI", "NBS_CALENDAR", "PBOC_M2", "FED_RSS")
_NBS_KEYS = {"CPI_YOY": "NBS_CPI", "PPI_YOY": "NBS_PPI", "PMI": "NBS_PMI"}
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_NUMBER = r"(?:\d+(?:\.\d+)?)"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_DOCUMENTS = 2
_SOURCE_TIMEOUT_SECONDS = 45
_METHOD = "official-macro-v1"
logger = logging.getLogger("wealthsail.macro_official")


def _now(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("official collection requires a timezone-aware now")
    return now.astimezone(_SHANGHAI)


def _url(value, *, publisher=None):
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    groups = {
        "国家统计局": {"www.stats.gov.cn", "stats.gov.cn"},
        "中国人民银行": {"www.pbc.gov.cn", "pbc.gov.cn"},
        "Federal Reserve": {"www.federalreserve.gov", "federalreserve.gov"},
    }
    allowed = groups[publisher] if publisher else set().union(*groups.values())
    if (
        parts.scheme not in {"https", "http"}
        or host not in allowed
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 80, 443}
    ):
        raise ValueError("source must be a national official URL")
    host = host if host.startswith("www.") else "www." + host
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() != "spm"
        )
    )
    return urlunsplit(("https", host, parts.path or "/", query, ""))


def _soup(text):
    if len(text.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("official document exceeds bounded size")
    soup = BeautifulSoup(text, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    return soup


def _compact(text):
    return re.sub(r"\s+", "", text).replace("：", ":").replace("％", "%")


def _meta(soup, name):
    values = {
        node.get("content", "").strip()
        for node in soup.find_all("meta", attrs={"name": name})
    }
    if len(values) > 1:
        raise ValueError("conflicting official document metadata")
    return next(iter(values), "")


def _published(soup, now, *, pboc=False):
    metadata_date = _meta(soup, "PubDate")
    metadata_match = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})(?:\s|$)", metadata_date)
    if metadata_match and date(*map(int, metadata_match.groups())) > now.date():
        raise ValueError("future official publication date")
    actual = soup.find(id="shijian") if pboc else None
    value = actual.get_text(" ", strip=True) if actual else _meta(soup, "PubDate")
    match = re.fullmatch(
        r"(\d{4})[-/](\d{2})[-/](\d{2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", value
    )
    if match is None:
        return None
    year, month, day = map(int, match.group(1, 2, 3))
    if date(year, month, day) > now.date():
        raise ValueError("future official publication")
    if match[4] is None:
        return None
    published = datetime(
        year,
        month,
        day,
        int(match[4]),
        int(match[5]),
        int(match[6] or 0),
        tzinfo=_SHANGHAI,
    )
    if published > now:
        raise ValueError("future official publication")
    if (
        pboc
        and metadata_date
        and metadata_date[:10].replace("/", "-") != published.date().isoformat()
    ):
        raise ValueError("conflicting official publication dates")
    return published


def _hash(row):
    # Collection time, webpage generation time and cosmetic HTML are excluded.
    canonical = {
        key: value
        for key, value in row.items()
        if key not in {"retrievedAt", "contentHash"}
    }
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                canonical,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )


def _finish(row):
    row["contentHash"] = _hash(row)
    return row


def _period(year, month, now):
    period = date(int(year), int(month), 1)
    if period > now.date().replace(day=1):
        raise ValueError("future official reference period")
    return period.strftime("%Y-%m")


def _signed(direction, number):
    if direction == "持平":
        return Decimal(0)
    if number is None:
        raise ValueError("official rate unavailable")
    value = Decimal(number)
    if not value.is_finite() or not math.isfinite(float(value)) or value < 0:
        raise ValueError("official rate unavailable")
    return -value if direction in {"下降", "下跌"} else value


def parse_nbs_release(text, source_uri, code, now):
    now = _now(now)
    source_uri = _url(source_uri, publisher="国家统计局")
    soup = _soup(text)
    title = _meta(soup, "ArticleTitle")
    match = re.match(r"^(\d{4})年(\d{1,2})月份?", title)
    if match is None or "解读" in title:
        raise ValueError("national release title unavailable")
    year, month = map(int, match.groups())
    reference = _period(year, month, now)
    content = soup.select(".TRS_UEDITOR, .trs_editor_view")
    if not content:
        raise ValueError("official release body unavailable")
    body = _compact(" ".join(node.get_text(" ", strip=True) for node in content))
    if code == "PMI":
        if "中国采购经理指数运行情况" not in title:
            raise ValueError("not the manufacturing PMI release")
        pattern = rf"(?:(\d{{4}})年)?(\d{{1,2}})月份?[,，]?制造业采购经理指数[（(]PMI[）)]为({_NUMBER})%"
        matches = re.findall(pattern, body)
        values = {
            Decimal(number)
            for doc_year, doc_month, number in matches
            if (not doc_year or int(doc_year) == year) and int(doc_month) == month
        }
        if any(not value.is_finite() or not 0 <= value <= 100 for value in values):
            raise ValueError("invalid manufacturing PMI")
    else:
        label = {"CPI_YOY": "居民消费价格", "PPI_YOY": "工业生产者出厂价格"}.get(code)
        if label is None or label not in title:
            raise ValueError("wrong national statistic")
        pattern = rf"(?:(\d{{4}})年)?(\d{{1,2}})月份?[,，]?全国{label}同比(上涨|下降|持平)(?:({_NUMBER})%)?"
        matches = [
            match
            for match in re.findall(pattern, body)
            if (not match[0] or int(match[0]) == year) and int(match[1]) == month
        ]
        values = {
            _signed(direction, number or None) for _, _, direction, number in matches
        }
        headline = re.search(
            rf"{label}同比(上涨|下降|持平)(?:({_NUMBER})%)?", _compact(title)
        )
        if headline:
            values.add(_signed(headline[1], headline[2]))
        # A headline alone is insufficient to prove national, contemporaneous scope.
        if not matches:
            raise ValueError("national year-on-year value unavailable")
    if len(values) != 1:
        raise ValueError("unavailable or conflicting official values")
    return _finish(
        {
            "code": code,
            "referencePeriod": reference,
            "value": values.pop(),
            "unit": "" if code == "PMI" else "%",
            "publishedAt": _published(soup, now),
            "sourceUri": source_uri,
            "publisher": "国家统计局",
            "retrievedAt": now,
            "sourceKey": _NBS_KEYS[code],
            "statistic": "manufacturingPMI" if code == "PMI" else "nationalYoY",
            "methodVersion": _METHOD,
        }
    )


def _pboc_title_period(title):
    match = re.fullmatch(
        r"(\d{4})年(?:(\d{1,2})月|(一季度|上半年|前三季度|全年))?金融统计数据报告",
        title,
    )
    if match is None:
        raise ValueError("not a national financial statistics report")
    month = (
        int(match[2])
        if match[2]
        else {"一季度": 3, "上半年": 6, "前三季度": 9, "全年": 12, None: 12}[match[3]]
    )
    return int(match[1]), month


def parse_pboc_release(text, source_uri, now):
    now = _now(now)
    source_uri = _url(source_uri, publisher="中国人民银行")
    soup = _soup(text)
    year, month = _pboc_title_period(_meta(soup, "ArticleTitle"))
    content = soup.find(id="zoom")
    if content is None:
        raise ValueError("official financial statistics body unavailable")
    body = _compact(content.get_text(" ", strip=True))
    pattern = rf"(?:(\d{{4}})年)?(\d{{1,2}})月末[,，]?广义货币[（(]M2[）)]余额[^。]*?同比(增长|下降|持平)(?:({_NUMBER})%)?"
    values = {
        _signed(direction, number or None)
        for doc_year, doc_month, direction, number in re.findall(pattern, body)
        if (not doc_year or int(doc_year) == year) and int(doc_month) == month
    }
    if values:
        values.update(
            _signed("增长", number)
            for number in re.findall(rf"广义货币增长({_NUMBER})%", body)
        )
    if len(values) != 1:
        raise ValueError("unavailable or conflicting M2 year-on-year growth")
    return _finish(
        {
            "code": "M2_YOY",
            "referencePeriod": _period(year, month, now),
            "value": values.pop(),
            "unit": "%",
            "publishedAt": _published(soup, now, pboc=True),
            "sourceUri": source_uri,
            "publisher": "中国人民银行",
            "retrievedAt": now,
            "sourceKey": "PBOC_M2",
            "statistic": "M2YoY",
            "methodVersion": _METHOD,
        }
    )


def _table_grid(table):
    grid, spans = [], {}
    for row_number, row in enumerate(table.find_all("tr")):
        cells, column = {}, 0
        for col, (through, text) in list(spans.items()):
            if through >= row_number:
                cells[col] = text
            else:
                del spans[col]
        for cell in row.find_all(["td", "th"], recursive=False):
            while column in cells:
                column += 1
            colspan, rowspan = int(cell.get("colspan", 1)), int(cell.get("rowspan", 1))
            if not 1 <= colspan <= 50 or not 1 <= rowspan <= 100:
                raise ValueError("unavailable calendar table dimensions")
            text = _compact(cell.get_text(" ", strip=True))
            for col in range(column, column + colspan):
                cells[col] = text
                if rowspan > 1:
                    spans[col] = row_number + rowspan - 1, text
            column += colspan
        grid.append([cells.get(col, "") for col in range(max(cells, default=-1) + 1)])
    return grid


def parse_nbs_calendar(text, source_uri, now):
    now = _now(now)
    source_uri = _url(source_uri, publisher="国家统计局")
    soup = _soup(text)
    year_match = re.fullmatch(
        r"(\d{4})年国家统计局主要统计信息发布日程表", _meta(soup, "ArticleTitle")
    )
    if year_match is None:
        raise ValueError("official annual calendar unavailable")
    year = int(year_match[1])
    _published(soup, now)  # Future calendar publications cannot become evidence.
    body = _compact(soup.get_text(" ", strip=True))
    moves = {
        (int(release_month), int(day)): int(reference_month)
        for reference_month, release_month, day in re.findall(
            r"(\d{1,2})月份采购经理指数月度报告于(\d{1,2})月(\d{1,2})日发布", body
        )
    }
    labels = {
        "采购经理指数月度报告": "PMI",
        "居民消费价格指数月度报告": "CPI_YOY",
        "工业生产者价格指数月度报告": "PPI_YOY",
    }
    results = {}
    for table in soup.find_all("table"):
        grid = _table_grid(table)
        for index, row in enumerate(grid):
            if len(row) != 14 or row[1] not in labels:
                continue
            if not any(re.search(r"\d{1,2}/", value) for value in row[2:]):
                continue  # Repeated label on a time row via rowspan.
            code = labels[row[1]]
            for release_month, cell in enumerate(row[2:], 1):
                if cell and set(cell) <= {"…", ".", "．"}:
                    continue
                days = re.findall(r"(\d{1,2})/[一二三四五六日天]", cell)
                time_text = (
                    cell
                    if re.search(r"\d{1,2}:\d{2}", cell)
                    else grid[index + 1][release_month + 1]
                    if index + 1 < len(grid) and len(grid[index + 1]) == 14
                    else ""
                )
                times = re.findall(r"(\d{1,2}):(\d{2})", time_text)
                if not days or len(set(times)) != 1:
                    raise ValueError("calendar date or time unavailable")
                hour, minute = map(int, times[0])
                if code != "PMI" and len(days) != 1:
                    raise ValueError("ambiguous calendar date")
                if (
                    code == "PMI"
                    and (len(days) > 1 or "注" in cell)
                    and not any((release_month, int(day)) in moves for day in days)
                ):
                    raise ValueError("unexplained calendar exception")
                for day in days:
                    scheduled = datetime(
                        year, release_month, int(day), hour, minute, tzinfo=_SHANGHAI
                    )
                    if code == "PMI":
                        reference = date(
                            year, moves.get((release_month, int(day)), release_month), 1
                        )
                    else:
                        reference = date(
                            year - (release_month == 1), (release_month - 2) % 12 + 1, 1
                        )
                    row = _finish(
                        {
                            "code": code,
                            "referencePeriod": reference.strftime("%Y-%m"),
                            "scheduledAt": scheduled,
                            "sourceUri": source_uri,
                            "publisher": "国家统计局",
                            "retrievedAt": now,
                            "sourceKey": "NBS_CALENDAR",
                            "methodVersion": _METHOD,
                        }
                    )
                    key = code, reference
                    if key in results and results[key]["scheduledAt"] != scheduled:
                        raise ValueError("conflicting official schedule")
                    results[key] = row
    if {row["code"] for row in results.values()} != set(labels.values()):
        raise ValueError("calendar series unavailable")
    return list(results.values())


def _release_event(release, title):
    code = release["code"]
    directions = {
        "PMI": ["a-share:broad", "a-share:cyclical"],
        "CPI_YOY": ["a-share:broad", "a-share:consumer", "bond:government"],
        "PPI_YOY": ["a-share:cyclical", "a-share:consumer", "bond:government"],
        "M2_YOY": ["a-share:broad", "a-share:growth", "bond:government"],
    }
    return _finish(
        {
            "eventKey": "official-url:"
            + hashlib.sha256(release["sourceUri"].encode()).hexdigest(),
            "title": title,
            "category": "DATA_RELEASE",
            "sourceType": "OFFICIAL_FACT",
            "sourceUri": release["sourceUri"],
            "publisher": release["publisher"],
            "publishedAt": release["publishedAt"],
            "retrievedAt": release["retrievedAt"],
            "summary": f"{release['referencePeriod']} {code}：{release['value']}{release['unit']}。官方统计发布；投资影响仍需其它证据。"[
                :300
            ],
            "directionIds": directions[code],
            "evidence": {
                key: release[key]
                for key in ("code", "referencePeriod", "value", "unit", "statistic")
            },
            "sourceKey": release["sourceKey"],
        }
    )


def parse_fed_rss(text, now):
    now = _now(now)
    if (
        re.search(r"<!DOCTYPE|<!ENTITY", text, re.IGNORECASE)
        or len(text.encode()) > _MAX_BYTES
    ):
        raise ValueError("unsupported RSS document")
    root = ElementTree.fromstring(text)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("official RSS channel unavailable")
    events = {}
    for item in root.findall("./channel/item")[:20]:
        title = (item.findtext("title") or "").strip()
        source_uri = _url(item.findtext("link") or "", publisher="Federal Reserve")
        if not title or not urlsplit(source_uri).path.startswith(
            "/newsevents/pressreleases/"
        ):
            raise ValueError("official RSS event unavailable")
        raw_date = item.findtext("pubDate") or ""
        try:
            published = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            published = None
        if published is not None and (
            published.tzinfo is None or published.utcoffset() is None
        ):
            published = None
        if published and published > now:
            continue
        summary = _soup(item.findtext("description") or "").get_text(" ", strip=True)[
            :300
        ]
        monetary = "monetary" in (item.findtext("category") or "").lower()
        row = _finish(
            {
                "eventKey": "official-url:"
                + hashlib.sha256(source_uri.encode()).hexdigest(),
                "title": title,
                "category": "MONETARY_POLICY" if monetary else "CENTRAL_BANK_RELEASE",
                "sourceType": "OFFICIAL_FACT",
                "sourceUri": source_uri,
                "publisher": "Federal Reserve",
                "publishedAt": published,
                "retrievedAt": now,
                "summary": summary,
                "directionIds": [
                    "us:broad",
                    "hk:broad",
                    "a-share:growth",
                    "a-share:technology",
                    "bond:government",
                    "gold:physical",
                ]
                if monetary
                else [],
                "evidence": {
                    "feedUri": FED_RSS_URL,
                    "category": item.findtext("category"),
                },
                "sourceKey": "FED_RSS",
            }
        )
        if (
            source_uri in events
            and events[source_uri]["contentHash"] != row["contentHash"]
        ):
            raise ValueError("conflicting RSS event")
        events[source_uri] = row
    return list(events.values())


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


def _links(text, base, predicate, publisher):
    results = {}
    for anchor in _soup(text).find_all("a", href=True):
        title = anchor.get("title") or anchor.get_text(" ", strip=True)
        if predicate(title):
            uri = _url(urljoin(base, anchor["href"]), publisher=publisher)
            results.setdefault(uri, title)
    return list(results.items())


async def fetch_macro_evidence(now):
    """Collect six independent sources; retain partial rows, report source failures."""
    now = _now(now)
    releases, schedules, events, source_results = [], [], [], {}
    async with httpx.AsyncClient(
        timeout=12,
        follow_redirects=False,
        headers={
            "User-Agent": "WealthSail macro research (+official-source-verification)"
        },
    ) as client:
        nbs_index = asyncio.create_task(_fetch_text(client, NBS_RELEASE_INDEX))

        async def collect(key, function):
            try:
                await asyncio.wait_for(function(), timeout=_SOURCE_TIMEOUT_SECONDS)
                source_results[key] = {"status": "SUCCESS"}
            except Exception as exc:  # noqa: BLE001 -- publishers must fail independently
                source_results[key] = {
                    "status": "FAILED",
                    "errorClass": type(exc).__name__,
                }
                logger.warning(
                    "official_macro_source_failed source=%s error_class=%s",
                    key,
                    type(exc).__name__,
                )

        async def nbs(code):
            labels = {
                "CPI_YOY": "居民消费价格",
                "PPI_YOY": "工业生产者出厂价格",
                "PMI": "中国采购经理指数运行情况",
            }
            candidates = _links(
                await nbs_index,
                NBS_RELEASE_INDEX,
                lambda title: bool(re.match(r"^\d{4}年\d{1,2}月份?", title))
                and labels[code] in title
                and "解读" not in title,
                "国家统计局",
            )[:_MAX_DOCUMENTS]
            if not candidates:
                raise ValueError("national release links unavailable")
            failures = []
            for uri, title in candidates:
                try:
                    release = parse_nbs_release(
                        await _fetch_text(client, uri), uri, code, now
                    )
                    releases.append(release)
                    events.append(_release_event(release, title))
                except Exception as exc:  # noqa: BLE001 -- retain independently valid documents
                    failures.append(exc)
            if failures:
                raise failures[0]

        async def calendar():
            index = await _fetch_text(client, NBS_CALENDAR_INDEX)
            candidates = _links(
                index,
                NBS_CALENDAR_INDEX,
                lambda title: bool(
                    re.fullmatch(
                        rf"({now.year}|{now.year + 1})年国家统计局主要统计信息发布日程表",
                        title,
                    )
                ),
                "国家统计局",
            )[:2]
            if not candidates:
                raise ValueError("annual release calendar unavailable")
            for uri, _ in candidates:
                schedules.extend(
                    parse_nbs_calendar(await _fetch_text(client, uri), uri, now)
                )

        async def pboc():
            index = await _fetch_text(client, PBOC_RELEASE_INDEX)
            candidates = _links(
                index,
                PBOC_RELEASE_INDEX,
                lambda title: bool(
                    re.fullmatch(
                        r"\d{4}年(?:(?:\d{1,2})月|一季度|上半年|前三季度|全年)?金融统计数据报告",
                        title,
                    )
                ),
                "中国人民银行",
            )[:_MAX_DOCUMENTS]
            if not candidates:
                raise ValueError("financial statistics report links unavailable")
            failures = []
            for uri, title in candidates:
                try:
                    release = parse_pboc_release(
                        await _fetch_text(client, uri), uri, now
                    )
                    releases.append(release)
                    events.append(_release_event(release, title))
                except Exception as exc:  # noqa: BLE001 -- retain independently valid documents
                    failures.append(exc)
            if failures:
                raise failures[0]

        async def fed():
            events.extend(parse_fed_rss(await _fetch_text(client, FED_RSS_URL), now))

        await asyncio.gather(
            *(
                collect(key, lambda code=code: nbs(code))
                for code, key in _NBS_KEYS.items()
            ),
            collect("NBS_CALENDAR", calendar),
            collect("PBOC_M2", pboc),
            collect("FED_RSS", fed),
        )
    return {
        "releases": releases,
        "schedules": schedules,
        "events": events,
        "sourceResults": {key: source_results[key] for key in SOURCE_KEYS},
    }
