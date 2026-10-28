"""Display-only NAV returns stay bound to the report's frozen NAV coordinate."""

from copy import deepcopy

import pytest

from test_theme_graph import NOW, nav_history


def report():
    return {
        "valueDirections": [
            {
                "plans": [
                    {
                        "subjectKind": "nav",
                        "subjectId": "008281",
                        "fundCode": "008281",
                        "priceBasis": "UNIT_NAV",
                        "asOf": "2026-10-15",
                        "currentPrice": "1.0000",
                    }
                ]
            }
        ],
    }


def test_existing_report_gets_matching_historical_change_without_repricing_or_mutation():
    from backend.investment.nav_change import report_with_nav_changes

    history = nav_history()
    history["points"] = [
        {"date": "2026-10-14", "close": "1.1"},
        {"date": "2026-10-15", "close": "1", "returnPct": "1.25"},
        {"date": "2026-10-16", "close": "1.2", "returnPct": "20"},
    ]
    source = report()
    before = deepcopy(source)
    result = report_with_nav_changes(source, [history], NOW)
    expected = {**before["valueDirections"][0]["plans"][0], "currentChangePct": "1.25"}
    assert result["valueDirections"][0]["plans"][0] == expected
    assert source == before


@pytest.mark.parametrize(
    "change", ["price", "code", "source", "future", "missing_prior"]
)
def test_old_report_never_borrows_a_return_from_a_mismatched_or_unavailable_nav(change):
    from backend.investment.nav_change import report_with_nav_changes

    source = report()
    history = nav_history()
    history["points"] = [
        {"date": "2026-10-14", "close": "1.1"},
        {"date": "2026-10-15", "close": "1"},
        {"date": "2026-10-16", "close": "1.2", "returnPct": "20"},
    ]
    if change == "price":
        source["valueDirections"][0]["plans"][0]["currentPrice"] = "1.01"
    elif change == "code":
        history["fundCode"] = "000001"
    elif change == "source":
        history["sourceUri"] = "https://example.org/unverified"
    elif change == "future":
        history["retrievedAt"] = NOW.replace(year=2027).isoformat()
    elif change == "missing_prior":
        history["points"] = history["points"][1:]
    result = report_with_nav_changes(source, [history], NOW)
    assert result == source
