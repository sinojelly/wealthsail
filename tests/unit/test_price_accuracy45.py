"""Real price plans distinguish technical entry readiness and fund-code revisions."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.indicators.actionable_zones import compute_actionable_price_plan
from test_value_directions import direction, packet, validate


def calculate(closes, *, basis="UNIT_NAV"):
    return compute_actionable_price_plan(
        [
            {
                "date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                "close": value,
                **(
                    {"high": value + 1, "low": value - 1} if basis != "UNIT_NAV" else {}
                ),
            }
            for i, value in enumerate(closes)
        ],
        subject_kind="nav" if basis == "UNIT_NAV" else "index",
        subject_id="005112" if basis == "UNIT_NAV" else "hs300",
        price_basis=basis,
        unit="CNY" if basis == "UNIT_NAV" else "points",
    )


def test_flat_nav_near_ma20_is_not_a_triggered_right_candidate():
    plan = calculate([1] * 220)
    right = plan["tracks"][1]
    assert Decimal(right["entryZoneMin"]) <= 1 <= Decimal(right["entryZoneMax"])
    assert right["state"] == "WATCH"
    assert right.get("candidate") is False


def test_real_positive_momentum_and_acceptable_rsi_mark_right_candidate():
    # Earlier higher prices keep the trend fixture below the new exit percentile.
    plan = calculate(
        [2] * 40 + [1 + 0.0005 * i + (0.03 if i % 2 else 0) for i in range(180)]
    )
    assert plan["indicators"]["navPercentile1y"] < 85
    assert plan["tracks"][1]["state"] == "CANDIDATE"
    assert plan["tracks"][1].get("candidate") is True


def test_high_position_exit_veto_wins_over_positive_momentum_and_acceptable_rsi():
    plan = calculate([1 + 0.0005 * i + (0.03 if i % 2 else 0) for i in range(220)])
    assert plan["indicators"]["momentum20"] > 0
    assert plan["indicators"]["rsi14"] <= 60
    right = plan["tracks"][1]
    assert right["inSellZone"] is True
    assert right["state"] == "WATCH"
    assert right["candidate"] is right["entryReady"] is False


@pytest.mark.parametrize(
    "index,expected",
    [(0, ("1.0384", "1.0644")), (1, ("1.0315", "1.0515"))],
)
def test_short_nav_reference_keeps_legacy_risk_floor_without_claiming_history(
    index, expected
):
    item = calculate([1] * 20)["tracks"][index]
    assert item["priceState"] == "PARTIAL"
    assert (item["takeProfitT1"], item["takeProfitT2"]) == expected
    mid = (Decimal(item["entryZoneMin"]) + Decimal(item["entryZoneMax"])) / 2
    risk = mid - Decimal(item["stopLossPrice"])
    assert Decimal(item["takeProfitT1"]) - mid >= Decimal("1.5") * risk
    assert Decimal(item["takeProfitT2"]) - mid >= Decimal("2.4") * risk


@pytest.mark.parametrize("index", [0, 1])
def test_historical_nav_sell_band_supersedes_legacy_risk_target_floor(index):
    result = calculate([1] * 220)
    item = result["tracks"][index]
    # A flat historical peak cannot support a 12% exit above the entry.
    assert (item["takeProfitT1"], item["takeProfitT2"]) == (None, None)
    assert item["sellZoneMin"] == item["takeProfitT1"]
    assert item["sellZoneMax"] == item["takeProfitT2"]
    assert (
        result["parameters"]["navRewardRiskBasis"]
        == "TWO_YEAR_SPAN_SUPPORT_RESISTANCE_PEAK_CAPPED"
    )
    assert item["priceState"] == "PARTIAL"
    assert item["estimatedProfitMinPct"] is item["estimatedProfitMaxPct"] is None
    assert item["entryReady"] is False


def test_index_targets_apply_the_same_floor_at_their_display_precision():
    right = calculate([100] * 220, basis="INDEX_POINTS")["tracks"][1]
    assert right["takeProfitT1"] == "106.90"
    assert right["takeProfitT2"] == "111.22"
    assert right["stopLossPrice"] == "94.90"


def revision_fixture(*, current=("1.1000", "1.2000"), old_updates=None):
    public, raw = packet(), direction()
    current_anchor = public["discoveryActionableZones"][0]
    current_anchor.update(fundCode="005112", market="FUND_CN")
    old = deepcopy(current_anchor)
    old.update({"selectedTrack": "LEFT_VALUE", **(old_updates or {})})
    public["previousValueDirections"] = [
        {"key": "yesterday-different-topic", "plans": [old]}
    ]
    current_anchor["tracks"][0].update(entryZoneMin=current[0], entryZoneMax=current[1])
    raw["plans"][0]["priceRevisionStatus"] = "INITIAL"
    return raw, public


@pytest.mark.parametrize(
    "current,expected",
    [
        (("1.1000", "1.2000"), "MAINTAINED"),
        (("1.105499999", "1.205999999"), "MAINTAINED"),
        (
            (
                "1.105499999999999999999999999999999",
                "1.205999999999999999999999999999999",
            ),
            "MAINTAINED",
        ),
        (("1.1055", "1.2060"), "UPGRADED"),
        (("1.0945", "1.1940"), "DOWNGRADED"),
        (("1.1060", "1.2010"), "MAINTAINED"),
        (("1.1060", "1.1940"), "MAINTAINED"),
    ],
)
def test_fund_code_compares_across_topic_keys_with_half_percent_threshold(
    current, expected
):
    raw, public = revision_fixture(current=current)
    result = validate([raw], public)[0]["plans"][0]
    assert result["priceRevisionStatus"] == expected
    assert "同方向" not in result["priceRevisionNote"]


def test_same_fund_code_survives_nav_to_catalog_uuid_mapping_change():
    raw, public = revision_fixture()
    identifier = "51111111-1111-1111-1111-111111111111"
    public["discoveryActionableZones"][0].update(
        subjectKind="fund", subjectId=identifier
    )
    raw["plans"][0].update(subjectKind="fund", subjectId=identifier)
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "MAINTAINED"


def test_legacy_uuid_without_fund_code_uses_unambiguous_current_catalog_identity():
    raw, public = revision_fixture()
    identifier = "51111111-1111-1111-1111-111111111111"
    public["discoveryActionableZones"][0].update(
        subjectKind="fund", subjectId=identifier
    )
    old = public["previousValueDirections"][0]["plans"][0]
    old.update(subjectKind="fund", subjectId=identifier)
    old.pop("fundCode")
    raw["plans"][0].update(subjectKind="fund", subjectId=identifier)
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "MAINTAINED"


def test_target_only_changes_do_not_reclassify_an_unchanged_buy_zone():
    raw, public = revision_fixture()
    public["discoveryActionableZones"][0]["tracks"][0]["takeProfitT1"] = "1.5000"
    raw["plans"][0]["priceRevisionStatus"] = "UPGRADED"
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "MAINTAINED"


def test_buy_zone_revision_does_not_depend_on_new_target_availability():
    raw, public = revision_fixture(current=("1.1055", "1.2060"))
    public["discoveryActionableZones"][0]["tracks"][0]["takeProfitT2"] = "1.6000"
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "UPGRADED"


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_previous_fund_plans_require_consistent_frozen_coordinates(conflict):
    raw, public = revision_fixture()
    duplicate = deepcopy(public["previousValueDirections"][0])
    duplicate["key"] = "another-old-topic"
    if conflict:
        duplicate["plans"][0]["tracks"][0]["entryZoneMin"] = "1.0800"
    public["previousValueDirections"].append(duplicate)
    expected = "INITIAL" if conflict else "MAINTAINED"
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == expected


@pytest.mark.parametrize(
    "updates",
    [
        {"market": "FUND_HK"},
        {"priceBasis": "ACCUMULATED_NAV"},
        {"unit": "USD"},
        {"selectedTrack": "RIGHT_TREND"},
        {"fundCode": "999999"},
    ],
)
def test_fund_code_never_overrides_market_basis_unit_track_or_conflicting_code(updates):
    raw, public = revision_fixture(old_updates=updates)
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "INITIAL"


def test_uuid_catalog_conflict_does_not_infer_a_previous_fund_code():
    raw, public = revision_fixture()
    identifier = "51111111-1111-1111-1111-111111111111"
    current = public["discoveryActionableZones"][0]
    current.update(subjectKind="fund", subjectId=identifier)
    conflicting = deepcopy(current)
    conflicting["fundCode"] = "999999"
    public["discoveryActionableZones"].append(conflicting)
    raw["plans"][0].update(subjectKind="fund", subjectId=identifier)
    with pytest.raises(ValueError, match="ambiguous discovery anchor"):
        validate([raw], public)


@pytest.mark.parametrize(
    "technical,price_state,expected",
    [
        ("WATCH", "READY", False),
        ("CANDIDATE", "READY", True),
        ("CANDIDATE", "PARTIAL", False),
    ],
)
def test_direction_quantitative_entry_readiness_comes_from_server_technical_track(
    technical, price_state, expected
):
    from test_value_directions import quantitative_packet

    public = quantitative_packet()
    anchor = public["discoveryActionableZones"][0]
    anchor["currentPrice"] = "1.1500"
    anchor["tracks"][0].update(
        state=technical,
        priceState=price_state,
        stopLossPrice="0.9900",
        takeProfitT2="1.6000",
    )
    scan = public["quantitativeScan"]["items"][0]
    scan.update(currentPrice="1.1500", tracks=deepcopy(anchor["tracks"]))
    scan["signals"][0].update(
        triggerState=technical, inEntryZone=True, entryReady=True, candidate=True
    )
    raw = direction(signal="WATCH", quantitativeFundCodes=["005112"])
    signal = validate([raw], public)[0]["quantitativeEvidence"][0]["signals"][0]
    assert signal.get("candidate") is (technical == "CANDIDATE")
    assert signal.get("entryReady") is expected


def legacy_previous_packet(
    *,
    old_market="FUND_CN",
    theme_market="FUND_CN",
    theme_code="008281",
    old_updates=None,
    conflicting_market=None,
):
    from backend.macro.alpha_research import extend_public_packet
    from test_phase2_research import NOW, anchor

    public, raw = packet(), direction()
    current = public["discoveryActionableZones"][0]
    current.update(subjectId="008281", fundCode="008281", market="FUND_CN")
    raw["plans"][0]["subjectId"] = "008281"
    previous = anchor()
    previous.update(fundCode="008281", selectedTrack="LEFT_VALUE")
    if old_market is not None:
        previous["market"] = old_market
    previous.update(old_updates or {})
    theme = {
        "themeCode": "AI_MEMORY_HBM",
        "assetCode": theme_code,
        "assetKind": "ETF_LINK_FUND",
        "market": theme_market,
    }
    themes = [theme]
    if conflicting_market:
        themes.append({**theme, "market": conflicting_market})
    public.update(
        extend_public_packet(
            {}, themes, [], [], {"report": {"actionablePlans": [previous]}}, NOW
        )
    )
    return raw, public


@pytest.mark.parametrize("old_market", ["FUND_CN", None])
def test_legacy_previous_compatibility_plan_compares_by_code_to_new_dynamic_direction(
    old_market,
):
    raw, public = legacy_previous_packet(old_market=old_market)
    previous = public["previousActionablePlans"][0]
    assert previous.get("market") == "FUND_CN"
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "MAINTAINED"


@pytest.mark.parametrize(
    "changes",
    [
        {"old_market": "FUND_HK"},
        {"old_market": None, "theme_market": None},
        {"old_market": None, "theme_code": "999999"},
        {"old_updates": {"priceBasis": "ACCUMULATED_NAV"}},
        {"old_updates": {"unit": "USD"}},
        {"old_market": None, "conflicting_market": "FUND_HK"},
    ],
)
def test_unproven_legacy_market_or_code_does_not_become_a_comparable_prior_plan(
    changes,
):
    raw, public = legacy_previous_packet(**changes)
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "INITIAL"


@pytest.mark.parametrize(
    "conflict,expected", [(False, "MAINTAINED"), (True, "INITIAL")]
)
def test_prior_dynamic_and_compatibility_plans_share_conflict_aware_code_index(
    conflict, expected
):
    raw, public = legacy_previous_packet()
    dynamic = deepcopy(public["previousActionablePlans"][0])
    if conflict:
        dynamic["tracks"][0]["entryZoneMin"] = "1.0900"
    public["previousValueDirections"] = [
        {"key": "different-yesterday-topic", "plans": [dynamic]}
    ]
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == expected
