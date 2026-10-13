import pytest

from backend.market.research_history import normalize


def bar(**overrides):
    row = {
        "date": "2026-10-12",
        "open": 1,
        "close": 1,
        "high": 2,
        "low": 0.5,
        "volume": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("opening", [0.4, 2.1])
def test_opening_price_must_be_inside_reported_high_low(opening):
    with pytest.raises(ValueError, match="OHLC"):
        normalize([bar(open=opening)])


@pytest.mark.parametrize("field", ["open", "high", "low"])
@pytest.mark.parametrize("value", [0, -1])
def test_every_present_price_must_be_positive_even_without_other_ohlc(field, value):
    with pytest.raises(ValueError, match="price|OHLC"):
        normalize([{"date": "2026-10-12", "close": 1, field: value}])


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_market_values_cannot_enter_normalized_history(field, value):
    with pytest.raises(ValueError, match="price|volume"):
        normalize([bar(**{field: value})])


def test_negative_volume_is_rejected_but_disclosed_zero_volume_is_valid():
    with pytest.raises(ValueError, match="volume"):
        normalize([bar(volume=-1)])
    assert normalize([bar(volume=0)])[0]["volume"] == 0


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_boolean_is_not_a_price_or_volume(field):
    with pytest.raises(ValueError, match="price|volume"):
        normalize([bar(**{field: True})])


@pytest.mark.parametrize("ohlc", [{"high": 0.9}, {"low": 1.1}])
def test_single_reported_high_or_low_still_bounds_close(ohlc):
    with pytest.raises(ValueError, match="OHLC"):
        normalize([{"date": "2026-10-12", "close": 1, **ohlc}])


def test_missing_ohlc_remains_missing_for_real_nav_history():
    result = normalize([{"date": "2026-10-12", "close": 1.3}])
    assert result[0]["open"] is result[0]["high"] is result[0]["low"] is None
    assert result[0]["volume"] is None
