"""Forward-migration contract for per-user report push subscriptions."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_043_adds_default_true_report_subscription_columns_without_broad_grants() -> None:
    sql = (ROOT / "migrations/043_user_report_subscriptions.sql").read_text(
        encoding="utf-8"
    )

    for column in (
        "pre_market_report_enabled",
        "intraday_report_enabled",
        "fund_1430_report_enabled",
        "post_market_report_enabled",
    ):
        assert f"{column} BOOLEAN NOT NULL DEFAULT TRUE" in sql

    assert "GRANT" not in sql.upper()
    assert "ALTER TABLE identity.user_preferences ENABLE ROW LEVEL SECURITY" not in sql
