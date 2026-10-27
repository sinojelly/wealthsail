"""Environment gates for automatic reports, independent of tenant subscriptions."""

from backend.config import Settings

REPORT_SESSION_SWITCHES = {
    "PRE_MARKET": "daily_research_enabled",
    "INTRADAY": "intraday_tracking_enabled",
    "FUND_1430": "fund_1430_enabled",
    "POST_MARKET": "post_market_review_enabled",
}


def session_enabled(settings: Settings, phase: str) -> bool:
    if phase in {"PRE_MARKET_MACRO", "PRE_MARKET_PERSONALIZED"}:
        phase = "PRE_MARKET"
    switch = REPORT_SESSION_SWITCHES.get(phase)
    # Global reference/cache refreshes have their own scheduling policy.
    return switch is None or bool(getattr(settings, switch, False))
