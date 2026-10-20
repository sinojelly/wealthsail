"""Real observation lags need no invented weekend or publication-time points."""

from copy import deepcopy
from datetime import timedelta

import pytest

from test_alpha_repository import NOW, observation, bundle, engine


def rows(code="CN_T10"):
    dates = [
        NOW.date() - timedelta(days=i)
        for i in range(100)
        if (NOW.date() - timedelta(days=i)).weekday() < 5
    ][:64][::-1]
    key = "AKSHARE_SHIBOR_7D" if code == "SHIBOR_7D" else "AKSHARE_CN_T10"
    return [
        observation(code, str(day), str(1 + i / 100), sourceKey=key)
        for i, day in enumerate(dates)
    ]


@pytest.mark.parametrize("code", ["CN_T10", "SHIBOR_7D"])
def test_64_real_daily_observations_compute_5_and_20_lags_and_window_percentile(code):
    history = rows(code)
    before = deepcopy(history)
    row = next(
        r
        for r in engine().merge_alpha_series([], bundle(history), NOW)
        if r["code"] == code
    )
    stats = row["changes"]
    assert stats["sampleCount"] == 64
    assert stats["changeUnit"] == "bp"
    assert stats["change5Samples"] == pytest.approx(5)
    assert stats["change20Samples"] == pytest.approx(20)
    assert stats["median"] == pytest.approx(1.315)
    assert stats["percentile"] == pytest.approx(99.21875)
    assert history == before and row["publishedAt"] is None


def test_definition_break_and_conflicting_values_cannot_be_skipped_to_make_a_long_run():
    history = rows()
    history[-4]["metadata"] = {"definitionVersion": "DIFFERENT"}
    stats = next(
        r
        for r in engine().merge_alpha_series([], bundle(history), NOW)
        if r["code"] == "CN_T10"
    )["changes"]
    assert stats["change5Samples"] is None and stats["change20Samples"] is None
    assert stats["median"] is None and stats["percentile"] is None


def test_failed_latest_source_and_future_observation_do_not_become_current_statistics():
    history = rows()
    data = bundle(history)
    data["sourceResults"]["AKSHARE_CN_T10"]["status"] = "FAILED"
    stats = next(
        r for r in engine().merge_alpha_series([], data, NOW) if r["code"] == "CN_T10"
    )["changes"]
    assert stats["change5Samples"] is None and stats["median"] is None
