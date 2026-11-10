"""Resolve AI insights against the full fund master before real NAV repricing."""

from copy import deepcopy
import logging
import re

from backend.investment.discovery_limits import OPPORTUNITY_TOPIC_LIMIT
from backend.investment.live_fund_plans import _code
from backend.investment.opportunity_fund_recall import recall_topic_funds
from backend.macro.industry_contract import STANDARD_INDUSTRY_CODES
from backend.macro.research_mentions import mentioned_fund_codes

_LOG = logging.getLogger("wealthsail.research_fund_references")


def _reference_plan(product, direction=None):
    return {
        **deepcopy(product),
        "label": product.get("name", product.get("fundCode")),
        "priceBasis": "UNIT_NAV", "unit": "CNY/份", "currency": "CNY",
        "currentPrice": None, "tracks": [], "referenceOnly": True,
        "qualificationStatus": "OBSERVATION_ONLY", "investmentEligible": False,
        **({"auditStatus": direction["auditStatus"]}
           if direction and direction.get("auditStatus") else {}),
    }


def _plans(direction):
    return [
        row for field in ("plans", "fundPlans") for row in direction.get(field, [])
        if isinstance(row, dict)
    ]


async def resolve_report_fund_references(conn, report, packet, now):
    """Keep all model products; complete unplanned directions and body mentions.

    Audit scope and qualification affect annotations only. Exact narrative fund
    codes survive automatic search quotas, while additional thematic candidates
    use the existing explainable master/index matching and share-family rules.
    """
    result = deepcopy(report)
    topics, references = [], {}

    def prepare(key, insight):
        codes = [row["fundCode"] for row in _plans(insight)
                 if isinstance(row.get("fundCode"), str) and re.fullmatch(r"[0-9]{6}", row["fundCode"])]
        match = insight.get("match") if isinstance(insight.get("match"), dict) else {}
        industry_ids = (match.get("industryIds") or insight.get("reviewIndustryIds")
                        or insight.get("relatedIndustryIds") or insight.get("industryCodes", []))
        claims = insight.get("industryClaims", [])
        claims = claims if isinstance(claims, list) else []
        industry_ids = list(dict.fromkeys([
            *(industry_ids if isinstance(industry_ids, list) else []),
            *(claim for claim in claims if isinstance(claim, str) and claim in STANDARD_INDUSTRY_CODES),
        ]))
        topics.append({
            "topicId": key, "title": insight.get("title", "价值方向"),
            "summary": " ".join(str(insight.get(field) or "") for field in ("summary", "thesis"))
                       + " " + "、".join(codes),
            "industryClaims": insight.get("industryClaims", []),
            "match": {**match, "industryIds": industry_ids},
        })
        references[key] = [insight]

    for index, direction in enumerate(result.get("valueDirections", [])):
        if isinstance(direction, dict) and not any(_code(row) for row in _plans(direction)):
            prepare(f"reference:direction:{index}", direction)
    seen_topics = {}
    for field in ("researchTopics", "topicCatalog"):
        for insight in result.get(field, []):
            if not isinstance(insight, dict) or any(_code(row) for row in _plans(insight)):
                continue
            fingerprint = tuple(str(insight.get(key) or "") for key in ("key", "title", "summary", "thesis"))
            if fingerprint in seen_topics:
                references[seen_topics[fingerprint]].append(insight)
            else:
                key = f"reference:topic:{len(seen_topics)}"
                seen_topics[fingerprint] = key
                prepare(key, insight)
    existing = {row.get("fundCode") for row in result.get("mentionedFunds", []) if isinstance(row, dict)}
    mentions = [row for row in mentioned_fund_codes(result) if row["fundCode"] not in existing]
    if mentions:
        topics.append({
            "topicId": "reference:mentions", "title": "研究正文提及基金",
            "summary": "、".join(row["fundCode"] for row in mentions),
        })
    if not topics:
        return result
    recalls = {}
    try:
        for offset in range(0, len(topics), OPPORTUNITY_TOPIC_LIMIT):
            recalls.update(await recall_topic_funds(
                conn, topics[offset:offset + OPPORTUNITY_TOPIC_LIMIT], packet or {}, now,
            ))
    except Exception as exc:
        # Optional matching must not make already-published insights unreadable.
        # Keep original report products and allow the next read to retry.
        _LOG.warning("research_reference_matching_unavailable reason=%s", type(exc).__name__)
        result.setdefault("referenceWarnings", []).append("LOCAL_FUND_MATCH_RETRY_PENDING")
        return result
    for key, insights in references.items():
        recalled = recalls.get(key, {})
        products = [*recalled.get("funds", []), *recalled.get("indirectFunds", [])]
        for insight in insights:
            if not products:
                insight["fundMatchStatus"] = "PENDING"
                continue
            matched = {row["fundCode"]: _reference_plan(row, insight) for row in products}
            # Replace incomplete model coordinate identities only with the verified
            # same-code master identity; preserve every other model-proposed fund.
            kept = []
            for row in insight.get("plans", []):
                if isinstance(row, dict) and row.get("fundCode") in matched:
                    kept.append({**row, **matched.pop(row["fundCode"])})
                else:
                    kept.append(row)
            insight["plans"] = [*kept, *matched.values()]
            insight["fundMatchStatus"] = "MATCHED"
    products = {
        row["fundCode"]: row
        for row in recalls.get("reference:mentions", {}).get("funds", [])
    }
    if products:
        result.setdefault("mentionedFunds", [])
        for mention in mentions:
            if product := products.get(mention["fundCode"]):
                result["mentionedFunds"].append({**_reference_plan(product), **mention})
    return result
