from __future__ import annotations

from pathlib import Path

import re


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

    for policy_name, predicate in (
        ("watchlist_self_select", "USING (user_id = auth.uid())"),
        ("watchlist_self_insert", "WITH CHECK (user_id = auth.uid())"),
        ("watchlist_self_delete", "USING (user_id = auth.uid())"),
        (
            "watchlist_domain_api_select",
            "USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)",
        ),
        (
            "watchlist_domain_api_insert",
            "WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)",
        ),
        (
            "watchlist_domain_api_delete",
            "USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)",
        ),
    ):
        policy_sql = sql.split(
            f"CREATE POLICY {policy_name} ON identity.watchlist", 1
        )[1].split(";", 1)[0]
        assert predicate in policy_sql

    assert "FOR UPDATE" not in sql
    assert not re.search(
        r"GRANT .* ON identity\\.watchlist TO [^;]*(?:PUBLIC|authenticated)", sql
    )
