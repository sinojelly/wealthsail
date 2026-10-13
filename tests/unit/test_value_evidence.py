from copy import deepcopy
from datetime import date, timedelta

import pytest


def valuation(values=None, *, gap=2):
    values = list(range(1000, 244, -1)) if values is None else values
    end = date(2026, 10, 12)
    points = [
        {
            "date": (end - timedelta(days=(len(values) - 1 - i) * gap)).isoformat(),
            "value": value,
        }
        for i, value in enumerate(values)
    ]
    return {
        "state": "READY",
        "provider": "LEGULEGU",
        "sourceUri": "https://legulegu.com/stockdata/hs300-ttm-lyr",
        "requestedEnd": end.isoformat(),
        "pe": {"state": "READY", "metric": "PE_TTM", "points": deepcopy(points)},
        "pb": {"state": "READY", "metric": "PB", "points": deepcopy(points)},
    }


def costs(**overrides):
    result = {
        "state": "READY",
        "managementRate": 0.005,
        "custodyRate": 0.001,
        "salesServiceRate": 0.002,
        "totalRate": 0.008,
        "sourceUri": "https://fundf10.eastmoney.com/jjfl_015641.html",
    }
    result.update(overrides)
    return result


def performance(**overrides):
    result = {"state": "READY", "eligible": True, "maxDrawdown": 0.35}
    result.update(overrides)
    return result


def test_complete_low_valuation_low_cost_bounded_risk_is_research_candidate():
    from backend.indicators.value_evidence import evaluate

    result = evaluate(valuation(), costs(), performance(), mapped=True)

    assert result["eligible"] is True
    assert result["state"] == "RESEARCH_CANDIDATE"
    assert result["evidence"]["pe"]["percentile"] == pytest.approx(0.06613756613756613)
    assert result["evidence"]["pb"]["sampleCount"] == 756
    assert result["evidence"]["costs"]["totalRate"] == 0.008
    assert result["algorithmVersion"]
    assert (
        result["parameters"]["percentileMethod"]
        == "(less_than_latest + 0.5 * equal_to_latest) / sample_count * 100"
    )
    assert result["evidence"]["limitations"]


def test_ties_use_half_rank_and_all_equal_is_not_low_valuation():
    from backend.indicators.value_evidence import evaluate

    result = evaluate(valuation([10] * 756), costs(), performance(), mapped=True)

    assert result["evidence"]["pe"]["percentile"] == 50
    assert result["eligible"] is False
    assert result["state"] == "NOT_CANDIDATE"


@pytest.mark.parametrize(
    "values,gap", [(list(range(800, 45, -1)), 2), (list(range(1000, 244, -1)), 1)]
)
def test_sample_count_and_three_calendar_years_are_both_required(values, gap):
    from backend.indicators.value_evidence import evaluate

    result = evaluate(valuation(values, gap=gap), costs(), performance(), mapped=True)

    assert result["eligible"] is False
    assert result["state"] == "INCOMPLETE"
    assert result["evidence"]["pe"]["state"] == "INSUFFICIENT"


@pytest.mark.parametrize(
    "area,state",
    [
        ("valuation", "STALE"),
        ("pe", "UNAVAILABLE"),
        ("pb", "STALE"),
        ("costs", "STALE"),
        ("performance", "STALE"),
    ],
)
def test_any_unready_evidence_blocks_candidate(area, state):
    from backend.indicators.value_evidence import evaluate

    v, c, p = valuation(), costs(), performance()
    target = (
        v
        if area == "valuation"
        else c
        if area == "costs"
        else p
        if area == "performance"
        else v[area]
    )
    target["state"] = state
    result = evaluate(v, c, p, mapped=True)
    assert result["eligible"] is False
    assert result["state"] == "INCOMPLETE"


@pytest.mark.parametrize("key", ["managementRate", "custodyRate", "salesServiceRate"])
def test_unknown_cost_cannot_be_treated_as_zero(key):
    from backend.indicators.value_evidence import evaluate

    result = evaluate(valuation(), costs(**{key: None}), performance(), mapped=True)
    assert result["eligible"] is False
    assert result["state"] == "INCOMPLETE"


def test_expensive_fee_is_recomputed_instead_of_trusting_total():
    from backend.indicators.value_evidence import evaluate

    result = evaluate(
        valuation(),
        costs(managementRate=0.009, totalRate=0),
        performance(),
        mapped=True,
    )
    assert result["eligible"] is False
    assert result["evidence"]["costs"]["totalRate"] == 0.012
    assert result["state"] == "NOT_CANDIDATE"


def test_maximum_drawdown_over_threshold_is_not_candidate():
    from backend.indicators.value_evidence import evaluate

    result = evaluate(
        valuation(), costs(), performance(maxDrawdown=0.3501), mapped=True
    )
    assert result["eligible"] is False
    assert result["state"] == "NOT_CANDIDATE"


def test_mapping_and_supported_fund_history_are_required():
    from backend.indicators.value_evidence import evaluate

    assert (
        evaluate(valuation(), costs(), performance(), mapped=False)["eligible"] is False
    )
    assert (
        evaluate(valuation(), costs(), performance(eligible=False), mapped=True)[
            "eligible"
        ]
        is False
    )


@pytest.mark.parametrize("value", [-1, 0, float("nan"), float("inf"), None, True])
def test_nonpositive_or_unknown_pe_blocks_percentile(value):
    from backend.indicators.value_evidence import evaluate

    v = valuation()
    v["pe"]["points"][-1]["value"] = value
    result = evaluate(v, costs(), performance(), mapped=True)
    assert result["eligible"] is False
    assert result["evidence"]["pe"]["percentile"] is None
    assert result["state"] == "INCOMPLETE"


def test_only_actual_latest_five_years_enter_percentile_sample():
    from backend.indicators.value_evidence import evaluate

    v = valuation()
    old = [
        {"date": (date(2015, 1, 1) + timedelta(days=i)).isoformat(), "value": 1}
        for i in range(800)
    ]
    for key in ("pe", "pb"):
        v[key]["points"] = old + v[key]["points"]
    result = evaluate(v, costs(), performance(), mapped=True)
    assert result["eligible"] is True
    assert result["evidence"]["pe"]["sampleCount"] == 756
    assert result["evidence"]["pe"]["percentile"] == pytest.approx(0.06613756613756613)


@pytest.mark.parametrize("metric", ["PE1", "PE_STATIC", None])
def test_unknown_pe_method_is_not_accepted_as_registered_rolling_pe(metric):
    from backend.indicators.value_evidence import evaluate

    v = valuation()
    v["pe"]["metric"] = metric
    result = evaluate(v, costs(), performance(), mapped=True)
    assert result["eligible"] is False
    assert result["state"] == "INCOMPLETE"
    assert result["evidence"]["pe"]["state"] == "UNSUPPORTED"


@pytest.mark.parametrize("area", ["valuation", "costs"])
def test_unattributed_source_does_not_support_research_candidate(area):
    from backend.indicators.value_evidence import evaluate

    v, c = valuation(), costs()
    (v if area == "valuation" else c)["sourceUri"] = None
    result = evaluate(v, c, performance(), mapped=True)
    assert result["eligible"] is False
    assert result["state"] == "INCOMPLETE"


def test_low_pe_alone_does_not_offset_high_pb_percentile():
    from backend.indicators.value_evidence import evaluate

    v = valuation()
    for point in v["pb"]["points"]:
        point["value"] = 10
    result = evaluate(v, costs(), performance(), mapped=True)
    assert result["eligible"] is False
    assert result["evidence"]["pe"]["percentile"] < 30
    assert result["evidence"]["pb"]["percentile"] == 50


def test_exact_thirtieth_percentile_and_disclosed_zero_sales_fee_are_allowed():
    from backend.indicators.value_evidence import evaluate

    v = valuation([1] * 200 + [10] * 55 + [20] * 504 + [10])
    result = evaluate(
        v, costs(salesServiceRate=0), performance(maxDrawdown=0), mapped=True
    )
    assert result["evidence"]["pe"]["percentile"] == 30
    assert result["eligible"] is True


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, None, True, 1e308])
def test_invalid_fee_fraction_is_unknown_evidence(value):
    from backend.indicators.value_evidence import evaluate

    result = evaluate(
        valuation(), costs(managementRate=value), performance(), mapped=True
    )
    assert result["eligible"] is False
    assert result["state"] == "INCOMPLETE"


def test_empty_evidence_does_not_raise_or_become_candidate():
    from backend.indicators.value_evidence import evaluate

    result = evaluate(None, None, None, mapped=False)
    assert result["eligible"] is False
    assert result["state"] == "INCOMPLETE"
    assert result["evidence"]["pe"]["percentile"] is None


@pytest.mark.parametrize(
    "last_price,expected",
    [(0.845, True), (0.84487, False), (0.8449999999999, False)],
)
def test_calculated_drawdown_respects_exact_thirty_five_percent_boundary(
    last_price, expected
):
    from backend.indicators.performance import analyze
    from backend.indicators.value_evidence import evaluate

    end = date(2026, 10, 12)
    points = [
        {
            "date": (end - timedelta(days=252 - i)).isoformat(),
            "close": 1.3 if i < 252 else last_price,
        }
        for i in range(253)
    ]
    measured = analyze(points, series_kind="ETF_HFQ", quality="READY")
    result = evaluate(valuation(), costs(), measured, mapped=True)

    if expected:
        assert measured["maxDrawdown"] == 0.35
    else:
        assert measured["maxDrawdown"] > 0.35
    assert result["eligible"] is expected
    assert result["state"] == ("RESEARCH_CANDIDATE" if expected else "NOT_CANDIDATE")
