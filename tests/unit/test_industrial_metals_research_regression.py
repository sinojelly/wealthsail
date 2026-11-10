"""Offline reproduction of industrial-metals-repricing, through final ranking.

All prices and quote checks are deterministic fixtures. No model, web request,
queue consumer, database, or stored report is involved.
"""

from copy import deepcopy
from datetime import timedelta

from backend.indicators.actionable_zones import compute_actionable_price_plan
from backend.macro.daily_research import validate_report
from test_daily_research import NOW, report
from test_industry_cycle import checks
from test_value_directions import direction


def server_plan(code, sector):
    closes = [1 + i / 500 for i in range(140)] + [1.12] * 80
    points = [
        {"date": (NOW.date() - timedelta(days=220 - i)).isoformat(), "close": close}
        for i, close in enumerate(closes)
    ]
    plan = compute_actionable_price_plan(
        points,
        subject_kind="nav",
        subject_id=code,
        price_basis="UNIT_NAV",
        unit="CNY/unit",
        source_uri=f"https://fund.eastmoney.com/{code}.html",
        market="FUND_CN",
        pe_percentile=35,
    )
    signals = []
    price = float(plan["currentPrice"])
    for track in plan["tracks"]:
        low, high = float(track["entryZoneMin"]), float(track["entryZoneMax"])
        signals.append(
            {
                "track": track["track"],
                "triggerState": track["state"],
                "candidate": track["candidate"],
                "entryReady": track["entryReady"],
                "inEntryZone": low <= price <= high,
                "distanceToEntryPct": round(
                    (low - price) / price * 100
                    if price < low
                    else (price - high) / price * 100
                    if price > high
                    else 0,
                    4,
                ),
            }
        )
    return {
        **plan,
        "fundCode": code,
        "discoverySector": sector,
        "historyQuality": "READY",
        "historySampleCount": 220,
        "signals": signals,
    }


def case():
    anchors = [server_plan("004432", "NONFERROUS"), server_plan("011035", "RARE_EARTH")]
    public = {
        "asOf": NOW.isoformat(),
        "directions": [],
        "industryCycleResearch": {"required": True},
        "discoveryActionableZones": anchors,
        "discoveryUniverse": deepcopy(anchors),
        "quantitativeScan": {
            "methodVersion": "fund-quant-scan-v2",
            "minimumHistorySamples": 120,
            "asOf": NOW.isoformat(),
            "coverage": {},
            "items": deepcopy(anchors),
        },
        "alphaPublic": {"sectorCatalysts": {"RARE_EARTH_METALS": ["news:metals"]}},
    }
    source = {
        "id": "s1",
        "url": "https://www.cls.cn/detail/offline-rare-earth",
        "title": "稀土行业融资观察",
        "quote": "近期稀土行业融资规模同比下降30%。",
        "publishedDate": (NOW - timedelta(days=1)).date().isoformat(),
        "publishedAt": None,
    }
    checked = {
        "s1": {
            **source,
            "verification": "SOURCE_CHECKED",
            "quoteMatched": True,
            "retrievedAt": NOW.isoformat(),
            "contentHash": "a" * 64,
        }
    }
    reviews = []
    for sector in ("RARE_EARTH", "RARE_EARTH_METALS", "NONFERROUS"):
        risk_checks = checks()
        refs = []
        if sector != "NONFERROUS":
            risk_checks[0].update(
                status="FOUND", sourceIds=["s1"], summary="近期稀土行业融资下降。"
            )
            refs = ["s1"]
        reviews.append(
            {"industry": sector, "sourceIds": refs, "riskChecks": risk_checks}
        )
    raw_direction = direction(
        key="industrial-metals-repricing",
        title="工业金属重定价",
        thesis="稀土与工业金属分别核查，估值重定价仍待订单验证。",
        signal="WATCH",
        macroCodes=[],
        sourceIds=["s1", "s2"],
        reviewIndustryIds=["RARE_EARTH", "RARE_EARTH_METALS"],
        industryCycle={
            "stage": "UNKNOWN",
            "evidenceSummary": "分别保留行业反方证据。",
            "sourceIds": [],
            "evidence": [],
            "riskChecks": checks(),
        },
    )
    template = raw_direction["plans"][0]
    raw_direction["plans"] = [
        {**template, "subjectId": row["subjectId"]} for row in anchors
    ]
    other_source = {
        **source,
        "id": "s2",
        "url": "https://www.cls.cn/detail/offline-industrial-metals",
        "title": "有色金属需求观察",
        "quote": "近期有色金属行业需求传导仍待观察。",
    }
    raw = {
        **report(),
        "events": [],
        "sources": [source, other_source],
        "valueDirections": [raw_direction],
        "industryCycleReviews": reviews,
    }
    return public, raw, checked


def test_original_missing_nonferrous_audit_preserves_direction_and_all_fund_plans():
    public, raw, checked = case()
    raw["industryCycleReviews"] = raw["industryCycleReviews"][:2]
    actual = validate_report(raw, public, checked, completed_at=NOW)
    direction = actual["valueDirections"][0]
    assert len(direction["plans"]) == 2
    assert "NONFERROUS" in direction["reviewIndustryIds"]
    assert direction["auditStatus"] == "UNAUDITED_SCOPE"


def test_model_unknown_label_is_preserved_as_a_broad_observation():
    public, raw, checked = case()
    raw["valueDirections"][0]["reviewIndustryIds"].append("model-unreviewed-label")
    actual = validate_report(raw, public, checked, completed_at=NOW)
    direction = actual["valueDirections"][0]
    assert "BROAD" in direction["reviewIndustryIds"]
    assert "model-unreviewed-label" in direction["originalIndustryIds"]
    assert len(direction["plans"]) == 2


def test_all_three_genuine_audits_accept_without_cross_sector_or_repeated_discount():
    public, raw, checked = case()
    original = deepcopy(public)
    actual = validate_report(raw, public, checked, completed_at=NOW)
    value = actual["valueDirections"][0]
    assert value["key"] == "industrial-metals-repricing"
    assert set(value["reviewIndustryIds"]) == {
        "RARE_EARTH",
        "RARE_EARTH_METALS",
        "NONFERROUS",
    }
    assert value["industryCycle"]["riskLevel"] == "CONFIRMED"
    assert len(actual["sources"]) <= 24
    for rows in (
        value["plans"],
        actual["actionablePlans"],
        actual["quantitativeScan"]["items"],
    ):
        indexed = {row["fundCode"]: row for row in rows}
        industrial, rare = indexed["004432"], indexed["011035"]
        assert industrial["industryCycle"]["riskFlags"] == []
        assert rare["industryCycle"]["confirmedRiskFlags"] == ["FINANCING_COOLING"]
        for row, anchor in (
            (industrial, public["discoveryActionableZones"][0]),
            (rare, public["discoveryActionableZones"][1]),
        ):
            right = next(
                track for track in row["tracks"] if track["track"] == "RIGHT_TREND"
            )
            original_right = next(
                track for track in anchor["tracks"] if track["track"] == "RIGHT_TREND"
            )
            assert [
                right[key]
                for key in (
                    "entryZoneMin",
                    "entryZoneMax",
                    "stopLossPrice",
                    "takeProfitT1",
                    "takeProfitT2",
                )
            ] == [
                original_right[key]
                for key in (
                    "entryZoneMin",
                    "entryZoneMax",
                    "stopLossPrice",
                    "takeProfitT1",
                    "takeProfitT2",
                )
            ]
        assert (
            industrial["tracks"][0]["entryZoneMin"]
            == public["discoveryActionableZones"][0]["tracks"][0]["entryZoneMin"]
        )
        assert rare["industryRiskAdjustment"]["entryShiftScale"] == 1.0
    assert public == original


def test_missing_one_of_five_checks_is_annotated_without_rejecting_other_research():
    public, raw, checked = case()
    raw["industryCycleReviews"][-1]["riskChecks"].pop()
    actual = validate_report(raw, public, checked, completed_at=NOW)
    assert len(actual["valueDirections"][0]["plans"]) == 2
    assert any(row["code"] == "RISK_CHECK_INCOMPLETE" for row in actual["validationWarnings"])


def test_next_report_does_not_reintroduce_direction_union_as_each_industry_history():
    from backend.macro.daily_research import _previous_industry_states

    public, raw, checked = case()
    actual = validate_report(raw, public, checked, completed_at=NOW)
    history = {
        "previousResearch": [
            {
                "status": "READY",
                "runId": "offline-previous",
                "completedAt": NOW.isoformat(),
                "report": actual,
            }
        ]
    }
    states = _previous_industry_states(history, NOW + timedelta(days=1))
    assert ("industry", "RARE_EARTH") in states
    industrial = states.get(("industry", "NONFERROUS"), {}).get("industryCycle", {})
    assert "FINANCING_COOLING" not in industrial.get("confirmedRiskFlags", [])
    assert "FINANCING_COOLING" not in industrial.get("persistedRiskFlags", [])


def test_rare_earth_company_earnings_cannot_release_nonferrous_earnings_watch():
    public, raw, checked = case()
    stamp = (NOW - timedelta(days=2)).isoformat()
    public["previousIndustryRiskStates"] = [
        {
            "industry": "NONFERROUS",
            "researchedAt": stamp,
            "industryCycle": {
                "stage": "UNKNOWN",
                "riskLevel": "SUSPECTED",
                "riskFlags": ["PERSISTED_RISK_WATCH", "FORWARD_PLANS_ONLY"],
                "persistedRiskFlags": ["FORWARD_PLANS_ONLY"],
                "confirmedRiskAt": {"FORWARD_PLANS_ONLY": stamp},
                "earningsVerified": False,
            },
        }
    ]
    evidence = []
    for identity, kind, claim in (
        ("profit", "PROFIT_GROWTH", "本季度净利润同比增长25%"),
        ("cash", "CASH_COLLECTION", "客户订单已实际回款2亿元"),
    ):
        source = {
            "id": identity,
            "url": f"https://www.sse.com.cn/offline/{identity}",
            "title": "稀土甲公司季度财务事实",
            "quote": f"甲公司（稀土行业）2026年第三季度{claim}。",
            "publishedDate": (NOW - timedelta(days=1)).date().isoformat(),
            "publishedAt": None,
        }
        raw["sources"].append(source)
        checked[identity] = {
            **source,
            "verification": "SOURCE_CHECKED",
            "quoteMatched": True,
            "retrievedAt": NOW.isoformat(),
            "contentHash": "b" * 64,
        }
        evidence.append(
            {
                "kind": kind,
                "summary": "同企同报告期财务事实。",
                "sourceIds": [identity],
                "companyName": "甲公司",
                "reportPeriod": "2026Q3",
            }
        )
    raw["valueDirections"][0]["sourceIds"] += ["profit", "cash"]
    raw["valueDirections"][0]["industryCycle"].update(
        stage="EARNINGS_DELIVERY", sourceIds=["profit", "cash"], evidence=evidence
    )
    result = validate_report(raw, public, checked, completed_at=NOW)
    value = result["valueDirections"][0]
    assert value["industryCycle"]["earningsVerified"] is True
    plans = {row["fundCode"]: row for row in value["plans"]}
    assert plans["011035"]["industryCycle"]["earningsVerified"] is True
    assert plans["004432"]["industryCycle"]["earningsVerified"] is False
    review = next(
        row for row in result["industryCycleReviews"] if row["industry"] == "NONFERROUS"
    )
    assert review["persistedRiskFlags"] == ["FORWARD_PLANS_ONLY"]
