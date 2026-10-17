"""Alpha research permits forecasts and trade ideas without lexical vetoes."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.macro.daily_research import validate_report


NOW = datetime(2026, 10, 16, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def validate(summary):
    return validate_report(
        {
            "summary": summary,
            "coverage": [],
            "sources": [],
            "events": [],
            "upcoming": [],
            "energy": [],
        },
        {"asOf": NOW.isoformat(), "directions": []},
        {},
        completed_at=NOW,
    )


@pytest.mark.parametrize(
    "summary",
    [
        "战争发生可能性为70%。",
        "供应中断的可能为65%。",
        "70%的可能性会出现供应冲击。",
        "The probability of conflict is 70%.",
        "There is a 70% chance of a supply shock.",
        "The likelihood of disruption is 60 percent.",
        "建议投资者现在买入能源基金。",
        "可以考虑买入能源基金。",
        "应当立即减仓能源基金。",
        "We recommend buying energy funds now.",
        "We advise investors to sell these funds.",
    ],
)
def test_daily_report_preserves_probability_and_trading_language_as_model_text(summary):
    assert validate(summary)["summary"] == summary


@pytest.mark.parametrize(
    "summary",
    [
        "官方公布CPI同比上涨0.2%，PPI同比下降1.3%；仅描述实际观察。",
        "Observed CPI increased 0.2 percent year over year; the impact remains unverified.",
        "供给改善的可能性仍待实际运输量核对，没有可靠概率。",
        "If supplies increase, energy costs may fall; congestion could offset the effect.",
    ],
)
def test_observed_macro_percentages_and_conditional_research_remain_allowed(summary):
    assert validate(summary)["summary"] == summary
