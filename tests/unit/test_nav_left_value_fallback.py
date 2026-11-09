"""Off-exchange fund value evidence uses its actual two-year NAV history."""

from datetime import date, timedelta

from backend.indicators.actionable_zones import compute_actionable_price_plan


def plan(values, **kwargs):
    end = date(2026, 11, 6)
    rows = [
        {
            "date": (end - timedelta(days=len(values) - index - 1)).isoformat(),
            "close": value,
        }
        for index, value in enumerate(values)
    ]
    return compute_actionable_price_plan(
        rows, subject_kind="nav", subject_id="011035", price_basis="UNIT_NAV", **kwargs
    )


def test_complete_high_nav_without_pe_is_watch_instead_of_permanently_insufficient():
    result = plan([1] * 220)
    left = result["tracks"][0]
    assert left["state"] == "WATCH"
    assert left["missingEvidence"] == []
    assert result["indicators"]["navPercentile2y"] == 50
    assert any("近2年净值分位不高于35%" in reason for reason in left["reasons"])


def test_two_year_nav_low_percentile_substitutes_only_missing_pe():
    result = plan([1.5 + index / 1000 for index in range(199)] + [1.52])
    assert result["indicators"]["navPercentile2y"] <= 35
    assert result["tracks"][0]["state"] == "CANDIDATE"
    assert not any("PE" in reason for reason in result["tracks"][0]["missingEvidence"])
    supplied = plan([1] * 220, pe_percentile=60)
    assert any(
        "PE历史分位不高于40%" in reason for reason in supplied["tracks"][0]["reasons"]
    )


def test_short_nav_history_does_not_invent_two_year_percentile_evidence():
    result = plan([1] * 119)
    assert result["indicators"]["navPercentile2y"] is None
    assert "近2年净值分位不高于35%" in result["tracks"][0]["missingEvidence"]
