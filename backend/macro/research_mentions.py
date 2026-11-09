"""Closed narrative fund references; identity resolution never grants a plan."""

from collections import OrderedDict
import re
from uuid import UUID

_CODE = re.compile(r"(?<![0-9A-Za-z])[0-9]{6}(?![0-9A-Za-z])")
_URL = re.compile(r"(?:https?://|www\.)[^\s]+", re.IGNORECASE)
_BODY_FIELDS = frozenset(
    {"title", "summary", "thesis", "text", "note", "evidenceSummary"}
)
_BODY_LISTS = frozenset(
    {"catalysts", "risks", "watchFor", "unknowns", "industryClaims", "measures"}
)


def _body_texts(value, depth=0):
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for row in value[:256]:
            yield from _body_texts(row, depth + 1)
    elif isinstance(value, dict):
        for key, row in value.items():
            if key in _BODY_FIELDS | _BODY_LISTS:
                yield from _body_texts(row, depth + 1)


def mentioned_fund_codes(report):
    """Unresolved mentions, for callers to check against frozen/local fund data.

    Deliberately ignores sources, URLs, IDs, arbitrary metadata and queries.
    A six-digit token alone is never a verified fund identity or permission.
    """
    if not isinstance(report, dict):
        return []
    rows = OrderedDict()

    def scan(value, location, direction=None, topics=()):
        for text in _body_texts(value):
            for match in _CODE.finditer(_URL.sub(" ", text[:65536])):
                code = match.group()
                row = rows.setdefault(
                    code,
                    {"fundCode": code, "directionKeys": [], "mentionLocations": []},
                )
                if direction and direction not in row["directionKeys"]:
                    row["directionKeys"].append(direction)
                if location not in row["mentionLocations"]:
                    row["mentionLocations"].append(location)
                for topic in topics:
                    if isinstance(topic, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", topic):
                        linked = row.setdefault("topicKeys", [])
                        if topic not in linked:
                            linked.append(topic)

    for field in ("summary", "macroCycle"):
        scan(report.get(field), field)
    directions = report.get("valueDirections")
    for index, row in enumerate(
        directions[:64] if isinstance(directions, list) else []
    ):
        if isinstance(row, dict):
            key = row.get("key")
            key = (
                key
                if isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key)
                else None
            )
            scan(row, f"valueDirections.{key or index}", key)
    topics = report.get("topicCatalog", [])
    if isinstance(topics, list):
        for index, topic in enumerate(topics[:72]):
            keys = [topic["key"]] if isinstance(topic, dict) and isinstance(topic.get("key"), str) else []
            scan(topic, f"topicCatalog.{index}", topics=keys)
    for field in ("coverage", "events", "upcoming"):
        items = report.get(field)
        for index, item in enumerate(items[:72] if isinstance(items, list) else []):
            keys = item.get("relatedTopicKeys", []) if isinstance(item, dict) else []
            scan(item, f"{field}.{index}", topics=keys if isinstance(keys, list) else [])
    return list(rows.values())


def safe_mentioned_funds(value):
    """Project only resolved identities; observation references never grant a buy."""
    result, seen = [], set()
    for row in value[:256] if isinstance(value, list) else []:
        if not isinstance(row, dict):
            continue
        code, kind, subject = row.get("fundCode"), row.get("subjectKind"), row.get("subjectId")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) or code in seen:
            continue
        if row.get("market") not in (None, "FUND_CN"):
            continue
        if kind == "fund":
            try:
                UUID(str(subject))
            except (ValueError, TypeError, AttributeError):
                continue
        elif kind != "nav" or subject != code:
            continue
        label = row.get("label")
        projected = {
            "fundCode": code, "subjectKind": kind, "subjectId": subject,
            "market": "FUND_CN", "label": label[:200] if isinstance(label, str) else code,
            "qualificationStatus": "OBSERVATION_ONLY", "investmentEligible": False,
        }
        for field in ("directionKeys", "topicKeys", "mentionLocations"):
            projected[field] = list(dict.fromkeys(
                item[:200] for item in row.get(field, [])[:72] if isinstance(item, str)
            )) if isinstance(row.get(field), list) else []
        result.append(projected)
        seen.add(code)
    return result


def extract_mentioned_funds(report, packet):
    """Resolve narrative codes using server-frozen catalog identities only."""
    if not isinstance(packet, dict):
        return []
    candidates = []
    for field in (
        "discoveryUniverse",
        "discoveryActionableZones",
        "actionableZones",
        "themeActionableZones",
    ):
        value = packet.get(field)
        if isinstance(value, list):
            candidates.extend(row for row in value if isinstance(row, dict))
    scan = packet.get("quantitativeScan")
    if isinstance(scan, dict) and isinstance(scan.get("items"), list):
        candidates.extend(row for row in scan["items"] if isinstance(row, dict))
    indexed = {}
    for row in candidates:
        code, kind, subject = (
            row.get("fundCode"),
            row.get("subjectKind"),
            row.get("subjectId"),
        )
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"[0-9]{6}", code)
            or row.get("market") not in (None, "FUND_CN")
        ):
            continue
        if kind == "nav" and subject != code:
            continue
        if kind == "fund":
            try:
                UUID(str(subject))
            except (ValueError, TypeError, AttributeError):
                continue
        elif kind != "nav":
            continue
        indexed.setdefault(code, []).append(row)
    result = []
    for mention in mentioned_fund_codes(report):
        matches = indexed.get(mention["fundCode"], [])
        identities = {
            row["subjectId"] for row in matches if row["subjectKind"] == "fund"
        }
        if not matches or len(identities) > 1:
            continue
        identity = next(
            (row for row in matches if row["subjectKind"] == "fund"), matches[0]
        )
        label = identity.get("label")
        result.append(
            {
                **mention,
                "subjectKind": identity["subjectKind"],
                "subjectId": identity["subjectId"],
                "market": "FUND_CN",
                "label": label[:200]
                if isinstance(label, str) and label
                else mention["fundCode"],
                "investmentEligible": False,
                "qualificationStatus": "OBSERVATION_ONLY",
            }
        )
    return result
