from datetime import date
from decimal import Decimal

from backend.funds.service import _range_dto
from backend.integrations.fund_nav_ranges import build_fund_nav_range


def test_range_position_is_not_an_valuation_percentile():
    facts = build_fund_nav_range(
        fund_code="110022", fund_name="测试", fund_type="FUND",
        history=[(date(2024, 1, 1), Decimal("1")),
                 (date(2024, 1, 2), Decimal("3")),
                 (date(2024, 1, 3), Decimal("2"))],
    )
    from dataclasses import asdict
    dto = _range_dto({**asdict(facts), "updated_at": None})
    assert dto.get("navRangePosition") == Decimal("50.0000")
    assert dto.get("historyStartDate") == "2024-01-01"
    assert dto["currentPercentile"] == dto["navRangePosition"]


def test_constant_history_has_unknown_range_position():
    facts = build_fund_nav_range(
        fund_code="110022", fund_name="测试", fund_type="FUND",
        history=[(date(2024, 1, 1), Decimal("1"))],
    )
    from dataclasses import asdict
    dto = _range_dto({**asdict(facts), "updated_at": None})
    assert "navRangePosition" in dto
    assert dto["navRangePosition"] is None
