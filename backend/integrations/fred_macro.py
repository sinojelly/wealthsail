"""Reproducible FRED market and price observations with retrieval provenance."""

import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
FRED_HOST = "fred.stlouisfed.org"

# Feed, output code, label, definition. Index levels are never reported as rates.
PRICE_INDEXES = {
    "CPIAUCSL": ("US_CPI", "美国CPI（季调）", "BLS_CPI_ALL_ITEMS_SA"),
    "CPILFESL": ("US_CORE_CPI", "美国核心CPI（季调）", "BLS_CPI_EX_FOOD_ENERGY_SA"),
    "PPIACO": ("US_PPI", "美国商品PPI（未季调）", "BLS_PPI_ALL_COMMODITIES_NSA"),
}
LEVELS = {
    "DFF": ("US_FED_FUNDS", "联邦基金有效利率", "FED_EFFECTIVE_FUNDS_DAILY", "%"),
    "DGS2": (
        "US_T2_NOMINAL",
        "美国2年期国债收益率",
        "FED_TREASURY_CONSTANT_MATURITY_2Y",
        "%",
    ),
    "DGS10": (
        "US_T10_NOMINAL",
        "美国10年期名义国债收益率",
        "FED_TREASURY_CONSTANT_MATURITY_10Y",
        "%",
    ),
    "DFII10": (
        "US_T10_REAL",
        "美国10年期实际国债收益率",
        "FED_TIPS_CONSTANT_MATURITY_10Y",
        "%",
    ),
    "DCOILBRENTEU": (
        "US_BRENT",
        "布伦特原油现货",
        "EIA_DAILY_BRENT_SPOT",
        "USD/barrel",
    ),
}
FRED_CATALOG = {
    code + suffix: (label + name, "MONTHLY", "%", feed)
    for feed, (code, label, _) in PRICE_INDEXES.items()
    for suffix, name in (("_YOY", "同比"), ("_MOM", "环比"))
} | {
    code: (label, "DAILY", unit, feed)
    for feed, (code, label, _, unit) in LEVELS.items()
}


def fred_uri(feed, now):
    if feed not in PRICE_INDEXES and feed not in LEVELS:
        raise ValueError("unsupported FRED series")
    # Include the base year for 36 months of year-over-year derived observations.
    start = now.astimezone(NEW_YORK).date() - timedelta(
        days=366 * (4 if feed in PRICE_INDEXES else 3)
    )
    return f"https://{FRED_HOST}/graph/fredgraph.csv?" + urlencode(
        {"id": feed, "cosd": start.isoformat()}
    )


def parse_fred_macro(text, feed, retrieved, *, observed_by=None):
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise ValueError("FRED retrieval requires an aware time")
    if feed not in PRICE_INDEXES and feed not in LEVELS:
        raise ValueError("unsupported FRED series")
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames not in (["observation_date", feed], ["DATE", feed]):
        raise ValueError("unexpected FRED series identity")
    today = (observed_by or retrieved).astimezone(NEW_YORK).date()
    monthly = feed in PRICE_INDEXES
    values = {}
    for row in reader:
        raw = row.get(feed)
        if raw in (None, "", "."):
            continue
        day = date.fromisoformat(row.get(reader.fieldnames[0], ""))
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError("invalid FRED value") from exc
        if day > today or not value.is_finite() or abs(value) > Decimal("10000000"):
            raise ValueError("invalid FRED observation")
        if monthly and (day.day != 1 or value <= 0):
            raise ValueError("invalid FRED monthly index level")
        if (
            not monthly
            and feed != "DCOILBRENTEU"
            and not Decimal("-20") <= value <= 100
        ):
            raise ValueError("invalid FRED rate")
        if day in values and values[day] != value:
            raise ValueError("conflicting FRED observation")
        values[day] = value

    def observation(code, day, value, definition, **metadata):
        label, frequency, unit, _ = FRED_CATALOG[code]
        return {
            "code": code,
            "referenceDate": day.isoformat(),
            "value": value,
            "label": label,
            "frequency": frequency,
            "unit": unit,
            "sourceUri": f"https://{FRED_HOST}/series/{feed}",
            "sourceName": "FRED · Federal Reserve Bank of St. Louis",
            "publishedAt": None,
            "retrievedAt": retrieved,
            "availableAt": retrieved,
            "timeBasis": "RETRIEVAL_ONLY",
            "methodVersion": "fred-macro-v1",
            "metadata": {
                "sourceKey": feed,
                "definitionKey": definition,
                "definitionVersion": "FRED_V1",
                **metadata,
            },
        }

    start = today - timedelta(days=366 * 3)
    if monthly:
        code, _, definition = PRICE_INDEXES[feed]
        out = []
        for day, value in sorted(values.items()):
            if day < start.replace(day=1):
                continue
            for suffix, months in (("_YOY", 12), ("_MOM", 1)):
                idx = day.year * 12 + day.month - 1 - months
                basis = date(idx // 12, idx % 12 + 1, 1)
                if basis in values:
                    change = ((value / values[basis] - 1) * 100).quantize(
                        Decimal("0.0001")
                    )
                    out.append(
                        observation(
                            code + suffix,
                            day,
                            change,
                            definition + suffix,
                            basePeriod=basis.isoformat(),
                        )
                    )
        return out
    code, _, definition, _ = LEVELS[feed]
    # Keep recent daily data for actual five-observation moves. Earlier months
    # only need their latest valid day for the 36-month dashboard.
    months, daily = {}, []
    cutoff = today - timedelta(days=50)
    for day, value in sorted(values.items()):
        if day >= cutoff:
            daily.append((day, value))
        elif day >= start:
            months[day.year, day.month] = day, value
    return [
        observation(code, day, value, definition)
        for day, value in (*months.values(), *daily)
    ]
