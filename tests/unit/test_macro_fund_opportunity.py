from backend.investment.opportunity_scoring import annotate_macro_opportunities


def test_macro_opportunities_explain_unpriced_coal_and_keep_every_plan():
    def plan(code, name, sector=None):
        return {
            "fundCode": code,
            "subjectId": code,
            "subjectKind": "nav",
            "priceBasis": "UNIT_NAV",
            "label": name,
            "discoverySector": sector,
        }

    coal = plan("161032", "富国中证煤炭", "COAL")
    coal["actionableZones"] = {**coal, "currentPrice": None}
    report = {
        "cycleInsights": {
            "macroSnapshot": {
                "composite": {
                    "ppiYoy": 5.7,
                    "cpiYoy": 0.7,
                    "inflationGap": 5,
                    "us10yReal": 2.91,
                },
            }
        },
        "discoveryActionableZones": [
            coal,
            plan("161724", "招商中证煤炭"),
            plan("012552", "天弘芯片", "SEMICONDUCTOR_AI"),
        ],
    }
    result = annotate_macro_opportunities(report)
    first, second, chip = result["discoveryActionableZones"]
    assert first["macroOpportunity"]["stance"] == "TAILWIND"
    assert "煤价" in first["macroOpportunity"]["rationale"]
    assert "5.70%" in first["macroOpportunity"]["rationale"]
    assert second["macroOpportunity"]["stance"] == "TAILWIND"
    assert chip["macroOpportunity"]["stance"] == "HEADWIND"
    assert first["actionableZones"]["macroOpportunity"] == first["macroOpportunity"]
    assert "macroOpportunity" not in report["discoveryActionableZones"][0]


def test_macro_opportunities_require_prices_and_rates_for_regime_tags():
    coal = {
        "fundCode": "161032",
        "subjectId": "161032",
        "subjectKind": "nav",
        "priceBasis": "UNIT_NAV",
        "label": "富国中证煤炭",
    }
    report = {
        "discoveryActionableZones": [coal],
        "cycleInsights": {
            "macroSnapshot": {"composite": {"ppiYoy": 5.7, "inflationGap": 5}},
        },
    }
    assert (
        "macroOpportunity"
        not in annotate_macro_opportunities(report)["discoveryActionableZones"][0]
    )
