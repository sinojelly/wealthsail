"""Published Beijing motor-fuel ceiling prices and policy effective dates."""

import asyncio
import re
from datetime import date, timedelta, timezone
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

import httpx

from backend.integrations.macro_official import _compact, _now, _soup, _table_grid

BEIJING_FUEL_SOURCE = "BEIJING_FUEL"
BEIJING_FUEL_HISTORY_SOURCE = "BEIJING_FUEL_HISTORY"
BEIJING_FUEL_CURRENT = (
    "https://fgw.beijing.gov.cn/bmcx/djcx/cxldj/202003/t20200331_1752783.htm"
)
BEIJING_FUEL_ARCHIVE = "https://www.beijingprice.cn/zcfg/index.shtml"
BEIJING_FUEL_HOSTS = {"fgw.beijing.gov.cn", "www.beijingprice.cn"}
FUEL_GRADES = {
    "92号汽油": "CN_FUEL_92",
    "95号汽油": "CN_FUEL_95",
    "0号柴油": "CN_FUEL_DIESEL_0",
}
FUEL_CATALOG = {
    code: (f"北京{grade}最高零售价", "DAILY", "元/升", BEIJING_FUEL_SOURCE)
    for grade, code in FUEL_GRADES.items()
}
_ARTICLE_PATH = re.compile(r"/c/(\d{4}-\d{2}-\d{2})/\d+\.shtml")
_TITLE = re.compile(r"^(?:\[北京市\])?本市成品油价格(?:按机制)?(?:调整|上调|下调)$")


def _policy_notice_date(uri):
    parts = urlsplit(uri)
    if (
        parts.scheme != "https"
        or parts.netloc != "www.beijingprice.cn"
        or parts.query
        or parts.fragment
        or not (match := _ARTICLE_PATH.fullmatch(parts.path))
    ):
        raise ValueError("unrecognized Beijing fuel archive notice")
    return date.fromisoformat(match[1])


def _effective_date(text, uri):
    if uri == BEIJING_FUEL_CURRENT:
        match = re.search(r"日期:(\d{4}-\d{2}-\d{2})来源:北京市发展和改革委员会", text)
        if match:
            return date.fromisoformat(match[1]), "TABLE_DATE"
        raise ValueError("dated Beijing fuel table unavailable")
    issued = _policy_notice_date(uri)
    effective = {
        date(int(year), int(month), int(day)) + timedelta(days=int(hour == "24"))
        for year, month, day, hour in re.findall(
            r"最高零售价格自(\d{4})年(\d{1,2})月(\d{1,2})日(0|24)[时点]起", text
        )
    }
    if len(effective) != 1 or "来源:北京市发展和改革委员会" not in text:
        raise ValueError("Beijing fuel policy effective date unavailable")
    reference = effective.pop()
    if not 0 <= (reference - issued).days <= 1:
        raise ValueError("Beijing fuel policy date conflict")
    return reference, "EFFECTIVE_DATE"


def _grade_prices(soup):
    prices = {}
    for table in soup.find_all("table"):
        if "最高零售价格" not in _compact(table.get_text(" ", strip=True)):
            continue
        grid = _table_grid(table)
        for first, second in zip(grid, grid[1:]):
            if first[:5] == [
                "品名",
                "最高批发价格",
                "最高批发价格",
                "最高零售价格",
                "最高零售价格",
            ] and second[3:5] == ["元/吨", "元/升"]:
                break
        else:
            continue
        for row in grid:
            if len(row) != 5 or row[0] not in FUEL_GRADES:
                continue
            if not re.fullmatch(r"[1-9]\d{2,5}", row[3]) or not re.fullmatch(
                r"[1-9]\d?(?:\.\d{1,2})?", row[4]
            ):
                raise ValueError("published Beijing fuel price is malformed")
            values = Decimal(row[4]), int(row[3])
            if row[0] in prices and prices[row[0]] != values:
                raise ValueError("conflicting published Beijing fuel prices")
            prices[row[0]] = values
    if prices and prices.keys() != FUEL_GRADES.keys():
        raise ValueError("incomplete Beijing fuel retail table")
    return prices


def _notice_prices(text):
    prices = {}
    for grade in FUEL_GRADES:
        values = set(
            re.findall(
                rf"(?<![\d-]){grade}由每升\d+(?:\.\d+)?元调整为([1-9]\d?(?:\.\d{{1,2}})?)元",
                text,
            )
        )
        if len(values) != 1:
            raise ValueError("published Beijing fuel grade price unavailable")
        prices[grade] = (Decimal(values.pop()), None)
    return prices


def parse_beijing_fuel(document, uri, now):
    now = _now(now)
    soup = _soup(document)
    text = _compact(soup.get_text(" ", strip=True))
    reference, date_role = _effective_date(text, uri)
    if reference > now.date():
        raise ValueError("future Beijing fuel price")
    retrieved = now.astimezone(timezone.utc)
    source_key = (
        BEIJING_FUEL_SOURCE
        if uri == BEIJING_FUEL_CURRENT
        else BEIJING_FUEL_HISTORY_SOURCE
    )
    prices = _grade_prices(soup)
    if not prices and uri != BEIJING_FUEL_CURRENT:
        prices = _notice_prices(text)
    if not prices:
        raise ValueError("published Beijing fuel retail table unavailable")
    return [
        {
            "code": code,
            "referenceDate": reference.isoformat(),
            "frequency": "DAILY",
            "unit": "元/升",
            "value": prices[grade][0],
            "sourceUri": uri,
            "sourceName": "北京市发展和改革委员会",
            "publishedAt": None,
            "availableAt": retrieved,
            "retrievedAt": retrieved,
            "timeBasis": "RETRIEVAL_ONLY",
            "methodVersion": "beijing-fuel-ceiling-v1",
            "metadata": {
                "sourceKey": source_key,
                "definitionKey": code,
                "definitionVersion": "BEIJING_RETAIL_CEILING_V1",
                "retailCnyPerTonne": prices[grade][1],
                "geography": "北京市",
                "dateRole": date_role,
            },
        }
        for grade, code in FUEL_GRADES.items()
    ]


async def _settled(coroutines, timeout):
    tasks = {asyncio.create_task(coroutine) for coroutine in coroutines}
    if not tasks:
        return []
    try:
        done, _ = await asyncio.wait(tasks, timeout=timeout)
        return [
            task.result()
            for task in done
            if not task.cancelled() and task.exception() is None
        ]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _read_index(fetch_text, uri):
    links = {}
    for anchor in _soup(await fetch_text(uri)).find_all("a", href=True):
        if not _TITLE.fullmatch(_compact(anchor.get_text(" ", strip=True))):
            continue
        link = urljoin(uri, anchor["href"])
        try:
            links[link] = _policy_notice_date(link)
        except ValueError:
            continue
    return links


async def collect_beijing_fuel_current(fetch_text, now):
    """Use the current table or the price-monitoring center's latest notice."""
    try:
        return parse_beijing_fuel(
            await fetch_text(BEIJING_FUEL_CURRENT), BEIJING_FUEL_CURRENT, now
        )
    except (ValueError, httpx.HTTPError):
        links = await _read_index(fetch_text, BEIJING_FUEL_ARCHIVE)
        candidates = [
            (issued, uri) for uri, issued in links.items() if issued < _now(now).date()
        ]
        if not candidates:
            raise
        _, uri = max(candidates)
        rows = parse_beijing_fuel(await fetch_text(uri), uri, now)
        for row in rows:
            row["metadata"]["sourceKey"] = BEIJING_FUEL_SOURCE
        return rows


async def collect_beijing_fuel_history(fetch_text, now, *, timeout=36):
    """Discover up to three years of published adjustment notices from the archive."""
    end = _now(now).date()
    first = end.replace(year=end.year - 3, day=1)
    indexes = [BEIJING_FUEL_ARCHIVE] + [
        BEIJING_FUEL_ARCHIVE.replace("index.shtml", f"index_{page}.shtml")
        for page in range(2, 7)
    ]

    links = {
        link: issued
        for found in await _settled(
            (_read_index(fetch_text, uri) for uri in indexes), timeout / 3
        )
        for link, issued in found.items()
        if first - timedelta(days=1) <= issued <= end
    }
    # Sample the last observed adjustment per calendar month. Missing notices
    # and incomplete archive months stay missing.
    selected = {}
    for uri, issued in sorted(links.items(), key=lambda entry: entry[1], reverse=True):
        selected.setdefault((issued.year, issued.month), uri)
    concurrent = asyncio.Semaphore(6)

    async def read_notice(uri):
        async with concurrent:
            try:
                return parse_beijing_fuel(await fetch_text(uri), uri, now)
            except (ValueError, httpx.HTTPError):
                return []

    groups = await _settled(
        (read_notice(uri) for uri in selected.values()), timeout * 2 / 3
    )
    return sorted(
        (
            row
            for group in groups
            for row in group
            if row["referenceDate"] >= first.isoformat()
        ),
        key=lambda row: (row["referenceDate"], row["code"]),
    )
