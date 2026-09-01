from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_044_creates_self_owned_watchlist_without_public_access() -> None:
    sql = (ROOT / "migrations/044_user_watchlist.sql").read_text("utf-8")
    for token in (
        "CREATE TABLE identity.watchlist",
        "PRIMARY KEY (user_id, symbol)",
        "CHECK (asset_type IN ('STOCK', 'ETF', 'FUND'))",
        "ENABLE ROW LEVEL SECURITY",
        "user_id = auth.uid()",
        "REVOKE ALL ON identity.watchlist FROM PUBLIC",
        "GRANT SELECT, INSERT, DELETE ON identity.watchlist TO wealthsail_api",
    ):
        assert token in sql
