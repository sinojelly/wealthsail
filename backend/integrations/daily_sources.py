"""Bounded official documents and EIA/FRED observations, never causal proof.

Quote verification establishes only that text occurs in the cited original.
Publication day, observation day and actual retrieval time stay separate.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_BYTES = 2 * 1024 * 1024
OFFICIAL_HOSTS = frozenset(
    {
        "ofac.treasury.gov",
        "home.treasury.gov",
        "fiscaldata.treasury.gov",
        "www.treasury.gov",
        "treasury.gov",
        "www.federalreserve.gov",
        "federalreserve.gov",
        "www.stats.gov.cn",
        "stats.gov.cn",
        "www.pbc.gov.cn",
        "pbc.gov.cn",
        "www.gov.cn",
        "www.safe.gov.cn",
        "www.ndrc.gov.cn",
        "www.mof.gov.cn",
        "www.eia.gov",
        "eia.gov",
        "fred.stlouisfed.org",
        "www.fec.gov",
        "fec.gov",
        "www.ecb.europa.eu",
        "www.bankofengland.co.uk",
        "www.boj.or.jp",
        "www.imf.org",
        "www.worldbank.org",
        "www.bea.gov",
        "www.bls.gov",
        "www.whitehouse.gov",
        "www.commerce.gov",
        "www.census.gov",
        "www.iea.org",
        "www.opec.org",
        "www.consilium.europa.eu",
    }
)
ENERGY = {
    "DCOILBRENTEU": ("布伦特原油现货", "USD/barrel", "DAILY", 10),
    "GASDESW": ("美国柴油零售价格", "USD/gallon", "WEEKLY", 21),
}
INDEXES = {
    "OFAC": "https://ofac.treasury.gov/recent-actions",
    "FED": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "NBS": "https://www.stats.gov.cn/sj/zxfb/",
}


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timezone-aware collection time required")
    return value.astimezone(SHANGHAI)


def official_url(uri):
    parts = urlsplit(uri)
    if (
        parts.scheme != "https"
        or parts.hostname not in OFFICIAL_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 443}
    ):
        raise ValueError("named public official HTTPS source required")
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_")
        ]
    )
    return urlunsplit(("https", parts.hostname, parts.path or "/", query, ""))


async def fetch_text(client, uri):
    """Check every redirect and streamed byte, including with injected clients."""
    current = official_url(uri)
    for _ in range(3):
        async with client.stream(
            "GET", current, follow_redirects=False, timeout=20
        ) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                current = official_url(
                    urljoin(current, response.headers.get("location", ""))
                )
                continue
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("document exceeds size limit")
            mime = response.headers.get("content-type", "text/plain").split(";")[0]
            if not (
                mime.startswith("text/")
                or mime
                in {
                    "application/xml",
                    "application/xhtml+xml",
                    "application/rss+xml",
                    "application/atom+xml",
                    "application/csv",
                }
            ):
                raise ValueError("unsupported document type")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("document exceeds size limit")
            # All selected publishers expose UTF-8; never parse arbitrary binary.
            return bytes(body).decode("utf-8-sig", errors="replace")
    raise ValueError("redirect limit exceeded")


def _soup(text):
    soup = BeautifulSoup(text, "html.parser")
    for node in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        node.decompose()
    return soup


def _compact(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def _date_in_text(text, value):
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    variants = (
        day.isoformat(),
        day.strftime("%m/%d/%Y"),
        day.strftime("%Y/%m/%d"),
        day.strftime("%B %d, %Y"),
        f"{day.year}年{day.month}月{day.day}日",
        f"{day.strftime('%B')} {day.day}, {day.year}",
        f"{day.day} {day.strftime('%B')} {day.year}",
    )
    compact = _compact(text).casefold()
    return any(_compact(value).casefold() in compact for value in variants)


def _document(text, uri, retrieved):
    soup = _soup(text)
    title = next(
        (
            _compact(node.get_text(" "))
            for node in soup.find_all(["h1", "title"])
            if _compact(node.get_text(" "))
        ),
        "",
    )
    if not title:
        title_meta = soup.find(
            "meta", attrs={"name": re.compile(r"^ArticleTitle$", re.I)}
        )
        title = (
            _compact(title_meta.get("content", "")) if title_meta else "官方公开原文"
        )
    title = title[:200]
    day = None
    # Only publication-bearing metadata or a page date; never infer from URL.
    for node in soup.find_all("meta"):
        name = (node.get("name") or node.get("property") or "").lower()
        if name in {
            "pubdate",
            "publishdate",
            "article:published_time",
            "dcterms.date",
            "dc.date",
            "date",
        }:
            match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", node.get("content", ""))
            if match:
                day = "-".join(match.groups())
                break
    plain = soup.get_text(" ", strip=True)
    if day is None:
        match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", plain)
        if match:
            month, number, year = match.groups()
            day = f"{year}-{month}-{number}"
    if day is None:
        match = re.search(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
            plain,
        )
        if match:
            day = datetime.strptime(match.group(), "%B %d, %Y").date().isoformat()
    body = (
        soup.select_one(".TRS_Editor, .TRS_UEDITOR")
        or soup.find("main")
        or soup.find("article")
        or soup
    )
    paragraphs = [_compact(node.get_text(" ")) for node in body.find_all("p")]
    quote = next((value for value in paragraphs if len(value) >= 30), title)[:240]
    uri = official_url(uri)
    return {
        "id": "official-" + hashlib.sha256(uri.encode()).hexdigest()[:16],
        "url": uri,
        "title": title,
        "quote": quote,
        "publishedDate": day,
        "publishedAt": None,
        "retrievedAt": _aware(retrieved).isoformat(),
        "verification": "SOURCE_CHECKED",
        "contentHash": hashlib.sha256(text.encode()).hexdigest(),
    }


async def verify_research_sources(sources, *, now, client=None, clock=None):
    now = _aware(now)
    clock = clock or (lambda: datetime.now(SHANGHAI))
    if client is None:
        async with httpx.AsyncClient(
            headers={"User-Agent": "WealthSail-public-research/1.0"}
        ) as owned:
            return await verify_research_sources(
                sources, now=now, client=owned, clock=clock
            )
    semaphore = asyncio.Semaphore(3)

    async def check(source):
        base = {
            "verification": "UNVERIFIED",
            "quoteMatched": False,
            "url": source.get("url"),
            "publishedDate": None,
            "publishedAt": None,
        }
        try:
            uri = official_url(source.get("url", ""))
        except (TypeError, ValueError):
            return source.get("id"), {**base, "reason": "SOURCE_NOT_ALLOWLISTED"}
        try:
            async with semaphore, asyncio.timeout(25):
                raw = await fetch_text(client, uri)
            retrieved = _aware(clock())
            doc = _document(raw, uri, retrieved)
            soup = _soup(raw)
            plain = _compact(soup.get_text(" ", strip=True)).casefold()
            quote = _compact(source.get("quote", "")).casefold()
            matched = len(quote) >= 16 and quote in plain
            requested_day = source.get("publishedDate")
            if requested_day:
                day_ok = date.fromisoformat(
                    requested_day
                ) <= retrieved.date() and _date_in_text(raw, requested_day)
            else:
                day_ok = True
            if not matched or not day_ok:
                return source.get("id"), {
                    **base,
                    "title": doc["title"],
                    "retrievedAt": retrieved.isoformat(),
                    "contentHash": doc["contentHash"],
                    "reason": "QUOTE_OR_PUBLICATION_UNCONFIRMED",
                }
            return source.get("id"), {
                **base,
                "verification": "SOURCE_CHECKED",
                "quoteMatched": True,
                "title": doc["title"],
                "publishedDate": requested_day or doc["publishedDate"],
                "retrievedAt": retrieved.isoformat(),
                "contentHash": doc["contentHash"],
            }
        except (httpx.HTTPError, ValueError, TimeoutError, UnicodeError):
            return source.get("id"), {
                **base,
                "verification": "UNAVAILABLE",
                "reason": "SOURCE_FETCH_FAILED",
            }

    return dict(await asyncio.gather(*(check(source) for source in sources[:16])))


def parse_energy_csv(text, code, now):
    now = _aware(now)
    label, unit, frequency, max_age = ENERGY[code]
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames not in (["observation_date", code], ["DATE", code]):
        raise ValueError("unexpected energy series identity")
    days = {}
    for row in reader:
        raw_value = row.get(code)
        if raw_value in {".", "", None}:
            continue
        day = date.fromisoformat(row.get("observation_date", row.get("DATE", "")))
        value = float(raw_value)
        if day > now.date() or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid energy observation")
        if day in days and days[day] != value:
            raise ValueError("conflicting energy observation")
        days[day] = value
    if not days:
        raise ValueError("no observed energy values")
    ordered = sorted(days.items())
    latest_day, latest = ordered[-1]
    prev_day, previous = ordered[-2] if len(ordered) >= 2 else (None, None)
    return {
        "id": code,
        "code": code,
        "label": label,
        "state": "READY" if (now.date() - latest_day).days <= max_age else "STALE",
        "unit": unit,
        "frequency": frequency,
        "value": latest,
        "asOf": latest_day.isoformat(),
        "previousValue": previous,
        "previousDate": prev_day.isoformat() if prev_day else None,
        "change": latest - previous if previous is not None else None,
        "changePct": (latest / previous - 1) * 100 if previous is not None else None,
        "sampleCount": len(ordered),
        "sourceUri": f"https://fred.stlouisfed.org/series/{code}",
        "retrievedAt": now.isoformat(),
        "publishedAt": None,
        "timeBasis": "OBSERVATION_ONLY",
    }


def _safe_error(error):
    if isinstance(error, httpx.HTTPStatusError):
        return "HTTP_" + str(error.response.status_code)
    return type(error).__name__


async def collect_daily_sources(now, *, client=None, clock=None):
    now = _aware(now)
    clock = clock or (lambda: datetime.now(SHANGHAI))
    if client is None:
        async with httpx.AsyncClient(
            headers={"User-Agent": "WealthSail-public-research/1.0"}
        ) as owned:
            return await collect_daily_sources(now, client=owned, clock=clock)
    result = {"documents": [], "energy": [], "sourceResults": {}}
    semaphore = asyncio.Semaphore(3)

    async def collect(key, uri):
        try:
            async with semaphore, asyncio.timeout(40):
                raw = await fetch_text(client, uri)
                if key in ENERGY:
                    record = parse_energy_csv(raw, key, _aware(clock()))
                    return key, [record], None
                links = []
                if key == "FED":
                    root = ElementTree.fromstring(raw)
                    links = [
                        node.text for node in root.findall(".//item/link") if node.text
                    ][:3]
                else:
                    for node in _soup(raw).find_all("a", href=True):
                        href = urljoin(uri, node["href"])
                        pattern = (
                            r"/recent-actions/\d{8}"
                            if key == "OFAC"
                            else r"/sj/zxfb/\d{6}/t\d{8}_"
                        )
                        if re.search(pattern, href) and href not in links:
                            links.append(href)
                    links = links[: 8 if key == "OFAC" else 3]
                records = []
                for link in links:
                    try:
                        text = await fetch_text(client, link)
                        doc = _document(text, link, clock())
                        published = doc["publishedDate"]
                        if published and not (
                            now.date() - timedelta(days=14)
                            <= date.fromisoformat(published)
                            <= _aware(clock()).date()
                        ):
                            continue
                        records.append(doc)
                    except (httpx.HTTPError, ValueError, TimeoutError):
                        continue
                if not records:
                    raise ValueError("no confirmed recent official documents")
                return key, records, None
        except (
            httpx.HTTPError,
            ValueError,
            TimeoutError,
            ElementTree.ParseError,
        ) as error:
            return key, [], _safe_error(error)

    energy_uris = {
        code: "https://fred.stlouisfed.org/graph/fredgraph.csv?"
        + urlencode(
            {
                "id": code,
                "cosd": (now.date() - timedelta(days=90)).isoformat(),
            }
        )
        for code in ENERGY
    }
    outputs = await asyncio.gather(
        *(collect(key, uri) for key, uri in {**INDEXES, **energy_uris}.items())
    )
    for key, records, error in outputs:
        result["sourceResults"][key] = (
            {"status": "FAILED", "errorClass": error}
            if error
            else {"status": "SUCCESS"}
        )
        result["energy" if key in ENERGY else "documents"].extend(records)
    return result
