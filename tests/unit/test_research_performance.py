from datetime import date, timedelta

import pytest


def history(values, *, gap_days=1):
    return [
        {
            "date": (date(2025, 1, 1) + timedelta(days=i * gap_days)).isoformat(),
            "close": value,
        }
        for i, value in enumerate(values)
    ]


def test_adjusted_etf_returns_use_trading_observation_windows():
    from backend.indicators.performance import analyze

    # 253 observations: latest=352, 63 sessions ago=289, 252 ago=100.
    result = analyze(history(range(100, 353)), series_kind="ETF_HFQ", quality="READY")

    assert result["return63"] == pytest.approx(0.2179930795847751)
    assert result["return252"] == pytest.approx(2.52)
    assert result["eligible"] is True
    assert result["state"] == "READY"
    assert result["sampleCount"] == 253
    assert result["historyStart"] == "2025-01-01"
    assert result["historyEnd"] == "2025-09-10"
    assert result["algorithmVersion"]
    assert result["parameters"]["returnWindows"] == [63, 252]
    assert result["parameters"]["annualizationSessions"] == 252


def test_drawdown_uses_running_peak_instead_of_global_minimum_and_maximum():
    from backend.indicators.performance import analyze

    result = analyze(history([100, 50, 200, 190]), series_kind="ETF_HFQ", quality="READY")

    assert result["maxDrawdown"] == pytest.approx(0.5)
    assert result["return63"] is None
    assert result["return252"] is None
    assert result["volatility20"] is None
    assert result["eligible"] is False
    assert result["state"] == "INSUFFICIENT"


def test_volatility_is_sample_stddev_of_latest_twenty_returns_annualized():
    from backend.indicators.performance import analyze

    # Latest 20 returns alternate 0 and 10%; sample variance = 0.05 / 19.
    values = [100 * 1.1 ** (i // 2) for i in range(21)]
    result = analyze(history(values), series_kind="ETF_HFQ", quality="READY")

    assert result["volatility20"] == pytest.approx(0.8143450710459553)
    assert result["maxDrawdown"] == 0


def test_calendar_span_cannot_replace_missing_trading_observations():
    from backend.indicators.performance import analyze

    result = analyze(
        history([100, 110, 120], gap_days=365), series_kind="ETF_HFQ", quality="READY"
    )

    assert result["historyStart"] == "2025-01-01"
    assert result["historyEnd"] == "2027-01-01"
    assert result["sampleCount"] == 3
    assert result["state"] == "INSUFFICIENT"
    assert result["eligible"] is False
    assert result["return63"] is None
    assert result["return252"] is None
    assert result["volatility20"] is None


@pytest.mark.parametrize("values", [[], [100]])
def test_empty_or_single_observation_has_no_risk_measure(values):
    from backend.indicators.performance import analyze

    result = analyze(history(values), series_kind="ETF_HFQ", quality="READY")

    assert result["maxDrawdown"] is None
    assert result["volatility20"] is None
    assert result["eligible"] is False
    assert result["state"] == "INSUFFICIENT"


@pytest.mark.parametrize("series_kind", ["INDEX_PRICE", "UNIT_NAV", "ACCUMULATED_NAV"])
def test_price_or_cash_dividend_nav_cannot_claim_total_return(series_kind):
    from backend.indicators.performance import analyze

    # A cash dividend halves unit NAV; that is not a 50% investor loss.
    result = analyze(
        history([100] * 252 + [50]), series_kind=series_kind, quality="READY"
    )

    assert result["state"] == "UNSUPPORTED"
    assert result["eligible"] is False
    assert all(
        result[key] is None
        for key in ("return63", "return252", "maxDrawdown", "volatility20")
    )
    assert result["sampleCount"] == 253
    assert result["seriesKind"] == series_kind
    assert result["reasons"]
    assert "分红再投" in " ".join(result["reasons"])


@pytest.mark.parametrize("verified", [False, "yes", None])
def test_unregistered_provider_return_index_cannot_be_ranked(verified):
    from backend.indicators.performance import analyze

    result = analyze(
        history(range(100, 353)),
        series_kind="PROVIDER_RETURN_INDEX",
        quality="READY",
        return_index_verified=verified,
    )

    assert result["state"] == "UNSUPPORTED"
    assert result["eligible"] is False
    assert result["return252"] is None
    assert "登记" in " ".join(result["reasons"])


def test_registered_provider_return_index_supports_return_research():
    from backend.indicators.performance import analyze

    result = analyze(
        history(range(100, 353)),
        series_kind="PROVIDER_RETURN_INDEX",
        quality="READY",
        return_index_verified=True,
    )

    assert result["state"] == "READY"
    assert result["eligible"] is True
    assert result["return252"] == pytest.approx(2.52)


@pytest.mark.parametrize("quality", ["STALE", "UNVERIFIED", "CONFLICT"])
def test_unready_history_remains_descriptive_but_cannot_enter_ranking(quality):
    from backend.indicators.performance import analyze

    result = analyze(history(range(100, 353)), series_kind="ETF_HFQ", quality=quality)

    assert result["state"] == quality
    assert result["eligible"] is False
    assert result["return252"] == pytest.approx(2.52)
    assert result["sampleCount"] == 253
    assert quality in " ".join(result["reasons"])


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, None, "bad"])
def test_invalid_close_is_rejected_before_metrics_or_eligibility(value):
    from backend.indicators.performance import analyze

    with pytest.raises(ValueError, match="price"):
        analyze(history([100, value]), series_kind="ETF_HFQ", quality="READY")


@pytest.mark.parametrize("field", ["open", "high", "low", "volume"])
def test_nonfinite_optional_values_are_rejected(field):
    from backend.indicators.performance import analyze

    rows = history([100, 110])
    rows[-1][field] = float("nan")
    with pytest.raises(ValueError, match="price|volume"):
        analyze(rows, series_kind="ETF_HFQ", quality="READY")


def test_duplicate_dates_are_rejected_even_with_equal_close():
    from backend.indicators.performance import analyze

    rows = history([100, 100])
    rows[-1]["date"] = rows[0]["date"]
    with pytest.raises(ValueError, match="date"):
        analyze(rows, series_kind="ETF_HFQ", quality="READY")


def test_out_of_order_dates_are_not_silently_reordered_by_calculator():
    from backend.indicators.performance import analyze

    with pytest.raises(ValueError, match="date"):
        analyze(list(reversed(history([100, 110]))), series_kind="ETF_HFQ", quality="READY")


@pytest.mark.parametrize("invalid_date", ["2025-02-30", "not-a-date", "2025-01-01T12:00:00"])
def test_invalid_normalized_date_is_rejected(invalid_date):
    from backend.indicators.performance import analyze

    with pytest.raises(ValueError, match="date"):
        analyze([{"date": invalid_date, "close": 100}], series_kind="ETF_HFQ", quality="READY")


def test_finite_inputs_cannot_produce_infinite_metrics():
    from backend.indicators.performance import analyze

    with pytest.raises(ValueError, match="finite"):
        analyze(history([1e-300, 1e300] * 11), series_kind="ETF_HFQ", quality="READY")


def test_annualized_volatility_overflow_is_rejected():
    from backend.indicators.performance import analyze

    with pytest.raises(ValueError, match="finite"):
        analyze(history([1, 1e308] * 11), series_kind="ETF_HFQ", quality="READY")


@pytest.mark.parametrize("field,value", [("open", -1), ("high", 0), ("low", -1), ("volume", -1)])
def test_invalid_optional_price_or_volume_is_rejected(field, value):
    from backend.indicators.performance import analyze

    rows = history([100, 110])
    rows[-1][field] = value
    with pytest.raises(ValueError, match="price|volume"):
        analyze(rows, series_kind="ETF_HFQ", quality="READY")


def test_returns_use_latest_windows_when_history_exceeds_one_year():
    from backend.indicators.performance import analyze

    result = analyze(history(range(100, 400)), series_kind="ETF_HFQ", quality="READY")

    assert result["return63"] == pytest.approx(0.1875)
    assert result["return252"] == pytest.approx(1.7142857142857144)


def test_constant_series_has_zero_returns_and_risk():
    from backend.indicators.performance import analyze

    result = analyze(history([100] * 253), series_kind="ETF_HFQ", quality="READY")

    assert result["return63"] == result["return252"] == 0
    assert result["maxDrawdown"] == result["volatility20"] == 0
    assert result["eligible"] is True
