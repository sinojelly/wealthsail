from datetime import date, timedelta

import pytest


def points(count, constant=False, ohlc=True):
    return [
        {
            "date": date(2025, 1, 1) + timedelta(days=i),
            "close": 100 if constant else 100 + i,
            "high": (100 if constant else 100 + i) + 1 if ohlc else None,
            "low": (100 if constant else 100 + i) - 1 if ohlc else None,
        }
        for i in range(count)
    ]


def test_indicators_match_independent_simple_series():
    from backend.indicators.technical import calculate

    result = calculate(points(260))
    assert result["ma20"] == pytest.approx(sum(range(340, 360)) / 20)
    assert result["momentum63"] == pytest.approx(359 / 296 - 1)
    assert result["rsi14"] == 100
    assert result["atr14"] == pytest.approx(2)


def test_constant_flat_series_is_finite():
    from backend.indicators.technical import calculate

    result = calculate(
        [{**p, "high": 100, "low": 100} for p in points(260, constant=True)]
    )
    assert result["rsi14"] == 50
    assert result["atr14"] == 0
    assert result["k"] == 50
    assert result["dif"] == 0


@pytest.mark.parametrize(
    "count,ohlc,state", [(199, True, "INSUFFICIENT"), (260, False, "UNSUPPORTED")]
)
def test_incomplete_input_never_returns_a_normalized_score(count, ohlc, state):
    from backend.indicators.technical import analyze

    result = analyze(points(count, ohlc=ohlc), quality="READY")
    assert result["score"] is None
    assert result["state"] == state
    assert result["indicators"]["k"] is None if not ohlc else True


def test_stale_input_preserves_indicators_but_suppresses_score():
    from backend.indicators.technical import analyze

    result = analyze(points(260), quality="STALE")
    assert result["score"] is None
    assert result["state"] == "STALE"
    assert result["indicators"]["ma200"] is not None


def test_no_future_data_and_score_is_explainable():
    from backend.indicators.technical import analyze

    result = analyze(points(260), quality="READY")
    assert 0 <= result["score"] <= 100
    assert result["score"] == sum(x["points"] for x in result["reasons"])
    assert sum(x["weight"] for x in result["reasons"]) == 100
    assert result == analyze(points(260), quality="READY")


def test_bad_ohlc_cannot_be_scored():
    from backend.indicators.technical import analyze

    data = points(260)
    data[-1]["high"] = 1
    assert analyze(data, quality="READY")["state"] == "CONFLICT"
