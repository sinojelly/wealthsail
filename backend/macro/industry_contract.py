"""Shared industry obligations for server-owned fund plans.

News buckets describe related research topics, never equivalent fund exposure.
The closed code set is for safe diagnostics, not permission to invent evidence.
"""

import re


STANDARD_INDUSTRY_CODES = frozenset(
    {
        "BROAD",
        "BANK",
        "AI",
        "ACTIVE_GROWTH",
        "RARE_EARTH",
        "NONFERROUS",
        "BROKER",
        "FINTECH",
        "ROBOT",
        "DEFENCE",
        "COMMUNICATION",
        "INNOVATIVE_DRUG",
        "CONSUMPTION",
        "GOLD",
        "GLOBAL_ENERGY",
        "US_TECH",
        "ROBOTICS_MOBILITY",
        "SEMICONDUCTOR_AI",
        "RARE_EARTH_METALS",
        "FINANCIAL",
        "FINANCE_POLICY",
        "DRUG",
        "HEALTHCARE_CLINICAL",
        "GOLD_ENERGY",
        "AEROSPACE",
        "MEMORY",
        "PCB",
        "POWER",
        "SOLID_BATTERY",
        "SATELLITE",
        "LOW_ALTITUDE",
        "HK_TECH",
        "DIVIDEND",
        "TRADITIONAL_MEDICINE",
        "COAL",
        "GAMING",
        "CHEMICAL",
    }
)

# These are search-priority relationships only. Never use them for validation,
# review substitution, risk propagation, earnings proof, or scope verification.
NEWS_RELATED_INDUSTRIES = {"RARE_EARTH_METALS": ("NONFERROUS", "RARE_EARTH")}


def _fund_identity(row):
    if not isinstance(row, dict):
        return None
    kind, subject = row.get("subjectKind"), row.get("subjectId")
    if kind not in {None, "fund", "nav"}:
        return None
    if kind is not None and (not isinstance(subject, str) or not subject.strip()):
        return None
    code = row.get("fundCode")
    if code is not None and (
        not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code)
    ):
        return None
    subject_code = (
        subject
        if isinstance(subject, str) and re.fullmatch(r"[0-9]{6}", subject)
        else None
    )
    if subject_code and code and subject_code != code:
        return None
    code = code or subject_code
    if kind is None and not code:
        return None
    market = row.get("market")
    if market not in {None, "FUND_CN"}:
        return None
    return kind, subject, code, market


def same_fund_identity(left, right):
    """Accept typed identity or consistent CN fund codes, never a bare OR join."""
    first, second = _fund_identity(left), _fund_identity(right)
    if first is None or second is None:
        return False
    kind_a, id_a, code_a, _ = first
    kind_b, id_b, code_b, _ = second
    if code_a and code_b and code_a != code_b:
        return False
    # Two different server instrument identities cannot be reconciled by code.
    if kind_a == kind_b == "fund" and id_a != id_b:
        return False
    if kind_a is not None and kind_a == kind_b and id_a == id_b:
        return True
    return bool(code_a and code_a == code_b)


def plan_industry_ids(packet, plan):
    """Read the exact server anchor's catalog-derived sector, then safe fallbacks.

    Legacy anchors can lack their copied sector. Prefer the canonical catalog
    before scan annotations; conflicting sectors cannot silently become a union
    or an empty result that bypasses required review coverage.
    """
    if not isinstance(plan, dict):
        return set()
    key = plan.get("subjectKind"), plan.get("subjectId")
    if key[0] not in {"fund", "nav"} or not isinstance(key[1], str) or not key[1]:
        return set()
    anchors = [
        row
        for row in packet.get("discoveryActionableZones", [])
        if isinstance(row, dict)
        and (row.get("subjectKind"), row.get("subjectId")) == key
    ]
    if not anchors:
        return set()
    anchor = anchors[0]
    if not same_fund_identity(anchor, plan):
        return set()
    primary = anchor.get("discoverySector")
    if isinstance(primary, str) and primary.strip():
        return {primary.strip()[:100]}

    def unique_sectors(rows):
        sectors = set()
        for row in rows:
            if same_fund_identity(anchor, row):
                sector = row.get("discoverySector") or row.get("sector")
                if isinstance(sector, str) and sector.strip():
                    sectors.add(sector.strip()[:100])
        if len(sectors) > 1:
            raise ValueError("ambiguous server fund industry")
        return sectors

    return unique_sectors(packet.get("discoveryUniverse", [])) or unique_sectors(
        (packet.get("quantitativeScan") or {}).get("items", [])
    )
