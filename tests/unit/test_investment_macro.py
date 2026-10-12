from decimal import Decimal
import pytest


def test_macro_import_preserves_period_and_does_not_invent_release_time():
    from backend.jobs.investment_macro import normalize_macro
    rows = normalize_macro([{"name":"中国CPI同比","date":"2026-09","value":"-0.2","unit":"%","source":"AkShare/macro"}])
    assert rows[0]["fieldCode"] == "CPI_YOY"
    assert rows[0]["value"] == Decimal("-0.2")
    assert rows[0]["publishedAt"] is None
    assert rows[0]["quality"] == "STALE"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "unknown"])
def test_bad_macro_values_are_not_imported(value):
    from backend.jobs.investment_macro import normalize_macro
    assert normalize_macro([{"name":"中国制造业PMI","date":"2026-09","value":value}]) == []
