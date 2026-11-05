"""Frozen server coordinates remain useful without formal buy qualification."""

from copy import deepcopy
from datetime import timedelta, timezone
from importlib import import_module, util
import json
from pathlib import Path
from hashlib import sha256

import pytest

from tests.unit.test_daily_research import NOW
from tests.unit.test_quantitative_scan import scan_plan

RUN = "ecbfb663-b677-440f-b7a8-10443be5799b"


def module():
    assert util.find_spec("backend.investment.fund_observation"), (
        "fund observation projection missing"
    )
    return import_module("backend.investment.fund_observation")


def case(count=3):
    items = []
    for index in range(count):
        row = scan_plan(f"{100000 + index:06}")
        right = row["tracks"][0]
        row["tracks"] = [
            {
                **deepcopy(right),
                "track": "LEFT_VALUE",
                "state": "CANDIDATE" if index < 3 else "WATCH",
            },
            right,
        ]
        row["signals"] = [
            {
                "track": track["track"],
                "triggerState": track["state"],
                "inEntryZone": True,
                "distanceToEntryPct": 0,
            }
            for track in row["tracks"]
        ]
        row["discoverySector"] = "ROBOT"
        items.append(row)
    return {
        "asOf": NOW.isoformat(),
        "discoveryUniverse": [
            {
                "fundCode": row["fundCode"],
                "label": "服务器基金" + row["fundCode"],
                "market": "FUND_CN",
                "subjectKind": "nav",
                "subjectId": row["fundCode"],
                "discoverySector": "ROBOT",
                "identitySourceUri": "https://fund.eastmoney.com/js/fundcode_search.js",
                "identityVerifiedAt": NOW.isoformat(),
            }
            for row in items
        ],
        "discoveryActionableZones": deepcopy(items),
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v1",
            "asOf": NOW.isoformat(),
            "minimumHistorySamples": 220,
            "items": items,
        },
    }


def build(packet, **kwargs):
    return module().build_fund_observation(
        packet, kwargs.pop("now", NOW), research_id=RUN, validated_at=NOW, **kwargs
    )


def test_zero_formal_no_portfolio_still_delivers_ten_observations_and_complete_inventory():
    packet = case(45)
    for row in packet["quantitativeScan"]["items"][39:]:
        for track in row["tracks"]:
            track["priceState"] = "PARTIAL"
    for row in packet["quantitativeScan"]["items"][3:10]:
        row["tracks"][0]["state"] = "INSUFFICIENT"
    packet["discoveryUniverse"].append(
        {
            "fundCode": "199999",
            "label": "尚无净值",
            "market": "FUND_CN",
            "subjectKind": "nav",
            "subjectId": "199999",
        }
    )
    before = deepcopy(packet)

    result = build(packet, formal_report={"valueDirections": []})

    assert result["coverage"] == {
        "catalogFundCount": 46,
        "scannedFundCount": 45,
        "confirmedNavFundCount": 45,
        "pricedFundCount": 39,
        "observableFundCount": 39,
        "unpricedFundCount": 7,
        "highlightCount": 10,
        "selectedScopeCount": 0,
        "deferredScopeCount": 0,
        "executionVerifiedCheckCount": 0,
    }
    assert len(result["items"]) == 46 and len(result["highlights"]) == 10
    assert (
        sum(row["observationStatus"] == "PRICE_INCOMPLETE" for row in result["items"])
        == 6
    )
    assert all(
        row["confirmedNav"] is not None
        and all(t["entryZoneMin"] is None for t in row["referenceTracks"])
        for row in result["items"]
        if row["observationStatus"] == "PRICE_INCOMPLETE"
    )
    assert result["items"][-1]["observationStatus"] == "NOT_SCANNED"
    assert all(
        row["investmentEligible"] is False and row["actionable"] is False
        for row in result["items"]
    )
    assert result["actionable"] is False and result["investmentEligible"] is False
    assert packet == before


def test_original_validated_coordinates_and_source_clock_are_copied_without_model_prices():
    packet = case(1)
    result = build(
        packet,
        topics=[
            {
                "key": "claim",
                "title": "机器人",
                "currentPrice": "999",
                "entryZoneMin": "888",
            }
        ],
    )
    row = result["items"][0]
    assert row["name"] == "服务器基金100000"
    assert row["confirmedNav"] == {
        "value": "1.0000",
        "asOf": packet["quantitativeScan"]["items"][0]["asOf"],
        "retrievedAt": NOW.isoformat(),
        "sourceUri": "https://fund.eastmoney.com/100000.html",
    }
    track = row["referenceTracks"][0]
    assert (track["entryZoneMin"], track["entryZoneMax"]) == ("0.9900", "1.0100")
    assert track["inEntryZone"] is True and track["distanceToEntryPct"] == 0
    assert (
        track["technicalState"] == "CANDIDATE"
        and track["observationStatus"] == "TECHNICAL_IN_ZONE"
    )
    assert (
        row["subscription"]["status"] == "UNKNOWN"
        and row["subscription"]["restricted"] is None
    )
    assert "999" not in json.dumps(row) and "888" not in json.dumps(row)


@pytest.mark.parametrize(
    "change", ["duplicate_catalog", "wrong_subject", "wrong_market"]
)
def test_identity_conflicts_retain_gap_without_borrowing_other_fund_prices(change):
    packet = case(1)
    if change == "duplicate_catalog":
        packet["discoveryUniverse"].append(
            {
                **packet["discoveryUniverse"][0],
                "subjectKind": "fund",
                "subjectId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            }
        )
    elif change == "wrong_subject":
        packet["quantitativeScan"]["items"][0]["subjectId"] = "100001"
    else:
        packet["discoveryUniverse"][0]["market"] = "SSE"
    result = build(packet)
    assert result["coverage"]["observableFundCount"] == 0
    assert result["highlights"] == []
    assert result["items"][0]["identityVerified"] is False
    assert result["items"][0]["confirmedNav"] is None


@pytest.mark.parametrize("field", ["retrievedAt", "availableAt"])
def test_future_source_clock_cannot_be_presented_as_confirmed_nav(field):
    packet = case(1)
    packet["quantitativeScan"]["items"][0][field] = (
        NOW + timedelta(seconds=1)
    ).isoformat()
    result = build(packet)
    assert result["items"][0]["confirmedNav"] is None
    assert result["coverage"]["observableFundCount"] == 0


def test_stale_original_coordinates_remain_dated_and_never_rebecome_current():
    packet = case(1)
    result = build(packet, now=NOW + timedelta(days=11))
    assert result["status"] == "STALE" and result["highlights"] == []
    row = result["items"][0]
    assert row["observationStatus"] == "STALE"
    assert row["confirmedNav"]["value"] == "1.0000"
    assert row["referenceTracks"][0]["entryZoneMin"] == "0.9900"
    assert row["referenceTracks"][0]["observationStatus"] == "STALE"
    assert result["researchAsOf"] == NOW.isoformat()


def test_source_recheck_older_than_36_hours_is_stale_even_when_nav_date_is_recent():
    result = build(case(1), now=NOW + timedelta(hours=37))
    assert (
        result["status"] == "STALE" and result["coverage"]["observableFundCount"] == 0
    )


def test_future_scan_calculation_clock_cannot_be_used_for_independent_nav_confirmation():
    packet = case(1)
    packet["quantitativeScan"]["asOf"] = (NOW + timedelta(seconds=1)).isoformat()
    assert build(packet)["items"][0]["confirmedNav"] is None


def test_missing_nav_source_clock_never_exposes_current_reference_tracks_but_keeps_risk():
    packet = case(1)
    packet["quantitativeScan"]["items"][0].pop("retrievedAt")
    review = {
        "industry": "ROBOT",
        "riskLevel": "CONFIRMED",
        "confirmedRiskFlags": ["FINANCING_COOLING"],
    }
    row = build(packet, industry_reviews=[review])["items"][0]
    assert row["identityVerified"] is True
    assert row["risk"]["current"][0]["confirmedRiskFlags"] == ["FINANCING_COOLING"]
    assert row["confirmedNav"] is None and row["investmentEligible"] is False
    assert all(
        track["observationStatus"] == "UNAVAILABLE" and track["entryZoneMin"] is None
        for track in row["referenceTracks"]
    )
    assert "NAV_PROVENANCE_UNVERIFIED" in row["missingEvidence"]


@pytest.mark.parametrize("field", ["tracks", "signals"])
@pytest.mark.parametrize("invalid", [None, {}, "invalid", 1, [None], [1]])
def test_invalid_scan_collections_do_not_break_other_fund_observations(field, invalid):
    packet = case(3)
    packet["quantitativeScan"]["items"][0][field] = invalid
    review = {
        "industry": "ROBOT",
        "riskLevel": "CONFIRMED",
        "confirmedRiskFlags": ["FINANCING_COOLING"],
    }

    result = build(packet, industry_reviews=[review])

    bad, *valid = result["items"]
    assert result["coverage"]["observableFundCount"] == 2
    assert bad["identityVerified"] is True
    assert bad["risk"]["current"][0]["confirmedRiskFlags"] == ["FINANCING_COOLING"]
    assert bad["confirmedNav"] is None
    assert "INVALID_SCAN_STRUCTURE" in bad["missingEvidence"]
    assert all(
        track["observationStatus"] == "UNAVAILABLE" and track["entryZoneMin"] is None
        for track in bad["referenceTracks"]
    )
    assert all(row["observationStatus"] == "OBSERVABLE" for row in valid)
    assert all(row["confirmedNav"]["value"] == "1.0000" for row in valid)
    assert all(row["investmentEligible"] is False for row in result["items"])


def test_unexpected_scanner_runtime_error_is_not_treated_as_invalid_frozen_data(
    monkeypatch,
):
    def broken_scanner(*args, **kwargs):
        raise RuntimeError("unexpected scanner failure")

    monkeypatch.setattr(module(), "public_quantitative_scan", broken_scanner)
    with pytest.raises(RuntimeError, match="unexpected scanner failure"):
        build(case(1))


def test_utc_validation_uses_same_instant_shanghai_day_for_existing_price_validator():
    packet = case(1)
    local = NOW.replace(hour=0, minute=30)
    instant = local.astimezone(timezone.utc)
    packet["asOf"] = packet["quantitativeScan"]["asOf"] = instant.isoformat()
    packet["discoveryUniverse"][0]["identityVerifiedAt"] = instant.isoformat()
    scan = packet["quantitativeScan"]["items"][0]
    scan["asOf"] = local.date().isoformat()
    scan["retrievedAt"] = scan["availableAt"] = instant.isoformat()
    result = module().build_fund_observation(
        packet, instant, research_id=RUN, validated_at=instant
    )
    assert result["researchAsOf"] == instant.isoformat()
    assert result["evaluatedAt"] == instant.isoformat()
    assert result["coverage"]["observableFundCount"] == 1
    assert result["items"][0]["referenceTracks"][0]["entryZoneMin"] == "0.9900"


@pytest.mark.parametrize(
    "boundary",
    ["inventory_budget", "clock", "catalog", "manifest", "version", "research_id"],
)
def test_expected_frozen_input_failures_use_a_narrow_data_error(boundary):
    from backend.macro.research_topics import freeze_discovery_packet

    packet = case(1)
    now, identity = NOW, RUN
    if boundary == "inventory_budget":
        packet["discoveryUniverse"] *= 47
    elif boundary == "clock":
        now = NOW - timedelta(seconds=1)
    elif boundary == "catalog":
        packet["discoveryUniverse"][0]["fundCode"] = "invalid"
    elif boundary == "manifest":
        packet = freeze_discovery_packet(packet, NOW)
        packet["researchDiscovery"]["auditManifest"]["manifestHash"] = "0" * 64
    elif boundary == "version":
        packet["researchDiscovery"] = {"version": "unsupported-v999"}
    else:
        identity = "invalid"
    with pytest.raises(module().FundObservationDataError):
        module().build_fund_observation(
            packet, now, research_id=identity, validated_at=NOW
        )


def test_partial_track_is_retained_as_unavailable_without_inventing_coordinates():
    packet = case(1)
    packet["quantitativeScan"]["items"][0]["tracks"][0]["state"] = "INSUFFICIENT"
    result = build(packet)
    row = result["items"][0]
    assert row["observationStatus"] == "OBSERVABLE"
    assert len(row["referenceTracks"]) == 2
    bad, good = row["referenceTracks"]
    assert bad["observationStatus"] == "UNAVAILABLE" and bad["entryZoneMin"] is None
    assert good["technicalState"] == "WATCH" and good["entryZoneMin"] == "0.9900"
    assert good["observationStatus"] == "WATCH"


def test_subscription_restriction_is_preserved_without_removing_readonly_observation():
    packet = case(1)
    packet["fundSubscriptionStatus"] = {
        "100000": {"purchaseStatus": "SUSPENDED", "retrievedAt": NOW.isoformat()}
    }
    row = build(packet)["items"][0]
    assert (
        row["subscription"]["status"] == "SUSPENDED"
        and row["subscription"]["restricted"] is True
    )
    assert (
        row["investmentEligible"] is False and row["confirmedNav"]["value"] == "1.0000"
    )


def test_complete_technical_signals_cannot_close_an_unallocated_industry_audit():
    row = build(
        case(1),
        coverage={"searchExecutionVerified": True, "executionVerifiedCheckCount": 50},
    )["items"][0]
    assert row["industry"]["industryIds"] == ["ROBOT"]
    assert row["industry"]["auditStatus"] == "NOT_ALLOCATED"
    assert len(row["industry"]["missingChecks"]) == 5


def test_same_scope_five_check_proof_requires_both_trusted_coverage_and_review_structure():
    from backend.macro.industry_cycle import RISK_CHECKS
    from backend.macro.research_topics import freeze_discovery_packet

    packet = freeze_discovery_packet(case(1), NOW)
    review = {
        "industry": "ROBOT",
        "riskChecks": [{"kind": kind, "status": "NOT_FOUND"} for kind in RISK_CHECKS],
    }
    row = build(
        packet, industry_reviews=[review], coverage={"searchExecutionVerified": True}
    )["items"][0]
    assert row["industry"]["auditStatus"] == "STRUCTURE_AND_EXECUTION_VERIFIED"
    assert row["industry"]["missingChecks"] == [] and row["investmentEligible"] is False
    missing = build(
        packet,
        industry_reviews=[{"industry": "ROBOT"}],
        coverage={"searchExecutionVerified": True},
    )["items"][0]
    assert missing["industry"]["auditStatus"] == "PENDING_PROOF"


def test_event_sector_and_topic_declarations_never_invent_standard_industry_proof():
    packet = case(1)
    packet["discoveryUniverse"][0]["discoverySector"] = "EVENT_ROBOT"
    packet["discoveryActionableZones"][0]["discoverySector"] = "EVENT_ROBOT"
    packet["quantitativeScan"]["items"][0]["discoverySector"] = "EVENT_ROBOT"
    row = build(
        packet,
        topics=[
            {
                "key": "robot",
                "title": "机器人产业",
                "match": {"industryIds": ["ROBOT"], "scopeVerified": True},
            }
        ],
    )["items"][0]
    assert row["industry"]["industryIds"] == []
    assert row["industry"]["unresolvedSector"] == "EVENT_ROBOT"
    assert all(
        link["relation"] != "CONFIRMED_SCOPE_LINK" for link in row["topicAssociations"]
    )


def test_topic_scope_link_requires_exact_standard_scope_checked_source_and_scope_verified():
    topic = {
        "topicId": "topic:" + "a" * 32,
        "key": "robot",
        "status": "OBSERVATION_ONLY",
        "title": "机器人原文观察",
        "sourceIds": ["s1"],
        "match": {
            "industryIds": ["ROBOT"],
            "relatedIndustryIds": [],
            "scopeVerified": True,
        },
    }
    source = {
        "id": "s1",
        "url": "https://example.org/robot",
        "title": "机器人行业公开资料",
        "quote": "近期机器人行业公开资料。",
        "verification": "SOURCE_CHECKED",
        "retrievedAt": NOW.isoformat(),
    }
    row = build(case(1), topics=[topic], sources=[source])["items"][0]
    assert row["topicAssociations"][0]["relation"] == "CONFIRMED_SCOPE_LINK"
    unknown = build(case(1), topics=[topic], sources=[])["items"][0]
    assert unknown["topicAssociations"][0]["relation"] == "DECLARED_RELATED_ONLY"


@pytest.mark.parametrize(
    "boundary",
    ["source_future", "source_duplicate", "topic_quarantined", "identity_conflict"],
)
def test_ambiguous_or_future_topic_proof_never_promotes_a_scope_link(boundary):
    packet = case(1)
    topic = {
        "key": "robot",
        "title": "机器人观察",
        "status": "OBSERVATION_ONLY",
        "sourceIds": ["s1"],
        "match": {"industryIds": ["ROBOT"], "scopeVerified": True},
    }
    source = {
        "id": "s1",
        "url": "https://example.org/robot",
        "title": "机器人资料",
        "quote": "机器人行业公开资料。",
        "verification": "SOURCE_CHECKED",
        "retrievedAt": NOW.isoformat(),
    }
    sources = [source]
    if boundary == "source_future":
        source["publishedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif boundary == "source_duplicate":
        sources.append({**source, "url": "https://example.org/other"})
    elif boundary == "topic_quarantined":
        topic["status"] = "QUARANTINED"
    else:
        packet["discoveryUniverse"].append(
            {**packet["discoveryUniverse"][0], "subjectId": "999999"}
        )
    row = build(packet, topics=[topic], sources=sources)["items"][0]
    assert all(
        link["relation"] != "CONFIRMED_SCOPE_LINK" for link in row["topicAssociations"]
    )


def test_historical_risk_uses_original_confirmation_clock_without_restarting_fourteen_days():
    packet = case(1)
    packet["previousIndustryRiskStates"] = [
        {
            "industry": "ROBOT",
            "researchedAt": (NOW - timedelta(days=3)).isoformat(),
            "industryCycle": {
                "riskLevel": "CONFIRMED",
                "confirmedRiskFlags": ["FINANCING_COOLING"],
                "confirmedRiskAt": {
                    "FINANCING_COOLING": (NOW - timedelta(days=15)).isoformat()
                },
            },
        }
    ]
    risk = build(packet)["items"][0]["risk"]["historical"][0]
    assert risk["active"] is False
    assert (
        risk["confirmedRiskAt"]["FINANCING_COOLING"]
        == (NOW - timedelta(days=15)).isoformat()
    )


@pytest.mark.parametrize("price_state", ["READY", "PARTIAL"])
def test_existing_entry_veto_remains_a_visible_warning_on_readonly_reference_tracks(
    price_state,
):
    packet = case(1)
    if price_state == "PARTIAL":
        for track in packet["quantitativeScan"]["items"][0]["tracks"]:
            track["priceState"] = "PARTIAL"
    packet["quantitativeScan"]["items"][0]["tracks"][0].update(
        entryBlocked=True,
        entryBlockReason="DELAYED_NAV_MARKET_DROP",
        entryStatus="净值滞后，暂缓入场",
        inSellZone=True,
        entryStatusTone="rose",
    )
    row = build(packet)["items"][0]
    track = row["referenceTracks"][0]
    assert (
        track["entryBlocked"] is True
        and track["entryBlockReason"] == "DELAYED_NAV_MARKET_DROP"
    )
    assert track["entryStatus"] == "净值滞后，暂缓入场"
    assert track["inSellZone"] is True and track["entryStatusTone"] == "rose"
    assert row["investmentEligible"] is False
    assert track["entryZoneMin"] == ("0.9900" if price_state == "READY" else None)


def test_historical_and_invalid_dynamic_found_risks_remain_visible_without_cross_scope_assignment():
    packet = case(1)
    packet["previousIndustryRiskStates"] = [
        {
            "industry": "ROBOT",
            "researchedAt": (NOW - timedelta(days=3)).isoformat(),
            "industryCycle": {
                "riskLevel": "CONFIRMED",
                "confirmedRiskFlags": ["FINANCING_COOLING"],
                "evidenceSummary": "此前原文风险仍需跟踪。",
            },
        }
    ]
    dynamic = {
        "industry": "TOPIC_SCOPE:" + "a" * 32,
        "riskChecks": [
            {
                "kind": "ORDER_CUTS",
                "status": "FOUND",
                "verification": "UNVERIFIED",
                "summary": "行业归属待核实。",
            }
        ],
    }
    result = build(packet, industry_reviews=[dynamic])
    assert result["unresolvedRiskNotice"]
    assert dynamic["industry"] in result["unresolvedRiskScopes"]
    row = result["items"][0]
    assert row["risk"]["historical"][0]["confirmedRiskFlags"] == ["FINANCING_COOLING"]
    assert row["risk"]["current"] == []
    assert row["investmentEligible"] is False


def test_real_ecb_frozen_scan_delivers_39_observations_without_claiming_audit_or_subscription():
    from backend.macro.research_frozen_bundle import read_frozen_bundle
    from backend.macro.research_batch import evaluate_research_batch

    path = Path("artifacts/research-recovery/ecbfb663-frozen.json")
    if not path.exists():
        pytest.skip("private local export is optional and not a committed fixture")
    before = sha256(path.read_bytes()).hexdigest()
    frozen = read_frozen_bundle(path, RUN, observed_at=NOW + timedelta(days=30))
    batch = evaluate_research_batch(
        frozen.raw,
        frozen.packet,
        frozen.source_checks,
        research_id=RUN,
        validated_at=frozen.validated_at,
    )
    accepted = batch["originalOutcome"].accepted
    result = module().build_fund_observation(
        frozen.packet,
        frozen.validated_at,
        research_id=RUN,
        validated_at=frozen.validated_at,
        topics=batch["registry"]["items"],
        industry_reviews=accepted["industryCycleReviews"],
        sources=accepted["sources"],
        coverage=batch["registry"]["coverage"],
        formal_report=batch["formalReport"],
    )
    assert (
        result["coverage"]["catalogFundCount"],
        result["coverage"]["scannedFundCount"],
        result["coverage"]["observableFundCount"],
    ) == (46, 45, 39)
    assert (
        sum(row["observationStatus"] == "PRICE_INCOMPLETE" for row in result["items"])
        == 6
    )
    assert (
        sum(row["observationStatus"] == "NOT_SCANNED" for row in result["items"]) == 1
    )
    observed = [
        row for row in result["items"] if row["observationStatus"] == "OBSERVABLE"
    ]
    counts = [
        sum(t["observationStatus"] != "UNAVAILABLE" for t in row["referenceTracks"])
        for row in observed
    ]
    assert counts.count(2) == 32 and counts.count(1) == 7
    in_zone = {
        row["fundCode"]
        for row in observed
        if any(
            t["observationStatus"] == "TECHNICAL_IN_ZONE"
            for t in row["referenceTracks"]
        )
    }
    assert in_zone == {"003017", "202015", "023530"}
    assert all(
        row["subscription"]["status"] == "UNKNOWN"
        and row["investmentEligible"] is False
        for row in result["items"]
    )
    assert all(
        row["industry"]["auditStatus"] != "STRUCTURE_AND_EXECUTION_VERIFIED"
        for row in result["items"]
    )
    assert sha256(path.read_bytes()).hexdigest() == before
