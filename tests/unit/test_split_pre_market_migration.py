"""Security contract for audience-aware split pre-market reports."""

import re
from pathlib import Path

import scripts.migrate as migration_runner


ROOT = Path(__file__).resolve().parents[2]


def test_045_adds_report_variants_audiences_and_append_only_shared_cache() -> None:
    """Removing any required report/cache boundary must break the migration contract."""
    sql = (ROOT / "migrations/045_split_pre_market_reports.sql").read_text("utf-8")

    for token in (
        "ADD COLUMN report_variant TEXT NOT NULL DEFAULT 'STANDARD'",
        "ADD COLUMN audience_user_id UUID",
        "PRE_MARKET_MACRO",
        "PRE_MARKET_PERSONALIZED",
        "FOREIGN KEY (tenant_id, audience_user_id)",
        "REFERENCES identity.profiles(id)",
        "reports_variant_audience_check",
        "CREATE UNIQUE INDEX uq_reports_variant_audience_version",
        "CREATE TABLE reporting.shared_pre_market_briefs",
        "protect_shared_pre_market_brief",
        "audience_user_id IS NULL",
        "audience_user_id = auth.uid()",
    ):
        assert token in sql

    assert "DROP CONSTRAINT reports_tenant_id_trade_date_report_session_version_key" in sql
    assert "BEFORE UPDATE OR DELETE ON reporting.shared_pre_market_briefs" in sql
    assert (
        "GRANT SELECT, INSERT ON reporting.shared_pre_market_briefs "
        "TO wealthsail_api, wealthsail_worker" in sql
    )
    assert "REVOKE ALL ON reporting.shared_pre_market_briefs FROM PUBLIC, authenticated" in sql
    assert "REVOKE ALL ON reporting.reports, reporting.report_items FROM PUBLIC" in sql
    assert (
        "ON reporting.reports, reporting.report_items TO wealthsail_api" in sql
    )
    assert not re.search(
        r"GRANT\s+(?:[^;]*\b)?(?:UPDATE|DELETE|TRUNCATE)\b[^;]*"
        r"reporting\.shared_pre_market_briefs",
        sql,
        re.IGNORECASE,
    )
    assert not re.search(
        r"GRANT\s+[^;]*\b(?:INSERT|UPDATE|DELETE|TRUNCATE)\b[^;]*"
        r"reporting\.(?:reports|report_items)\s+TO\s+authenticated",
        sql,
        re.IGNORECASE,
    )


def test_report_policy_validator_rejects_tenant_wide_or_cross_audience_reads() -> None:
    """A member predicate alone must never expose another user's report or items."""
    browser_report = """
        current_user = 'authenticated'
        AND identity.is_tenant_member(tenant_id)
        AND (audience_user_id IS NULL OR audience_user_id = auth.uid())
    """
    api_report = """
        identity.is_tenant_member(tenant_id)
        AND (audience_user_id IS NULL OR audience_user_id =
             NULLIF(current_setting('app.user_id', true), '')::uuid)
    """
    browser_items = """
        current_user = 'authenticated'
        AND identity.is_tenant_member(tenant_id)
        AND EXISTS (
            SELECT 1 FROM reporting.reports parent_report
             WHERE parent_report.tenant_id = report_items.tenant_id
               AND parent_report.id = report_items.report_id
               AND (parent_report.audience_user_id IS NULL
                    OR parent_report.audience_user_id = auth.uid())
        )
    """

    assert migration_runner._report_policy_has_required_audience(
        "reports", browser_report, ("authenticated",)
    )
    assert migration_runner._report_policy_has_required_audience(
        "reports", api_report, ("wealthsail_api",)
    )
    assert migration_runner._report_policy_has_required_audience(
        "report_items", browser_items, ("authenticated",)
    )
    assert not migration_runner._report_policy_has_required_audience(
        "reports", "identity.is_tenant_member(tenant_id)", ("authenticated",)
    )
    assert not migration_runner._report_policy_has_required_audience(
        "reports",
        "identity.is_tenant_member(tenant_id) AND "
        "(audience_user_id IS NULL OR audience_user_id = auth.uid())",
        ("authenticated",),
    )
    assert not migration_runner._report_policy_has_required_audience(
        "reports",
        "identity.is_tenant_member(tenant_id) AND audience_user_id IS NOT NULL",
        ("authenticated",),
    )
    assert not migration_runner._report_policy_has_required_audience(
        "reports",
        "current_user = 'authenticated' AND "
        "identity.is_tenant_member(tenant_id) AND audience_user_id IS NULL "
        "OR audience_user_id = auth.uid()",
        ("authenticated",),
    )
    assert not migration_runner._report_policy_has_required_audience(
        "reports", browser_report, ("wealthsail_api",)
    )
    assert not migration_runner._report_policy_has_required_audience(
        "report_items",
        browser_items.replace("auth.uid()", "parent_report.audience_user_id"),
        ("authenticated",),
    )
    assert not migration_runner._report_policy_has_required_audience(
        "report_items",
        browser_items.replace(
            "AND (parent_report.audience_user_id IS NULL\n"
            "                    OR parent_report.audience_user_id = auth.uid())",
            "AND parent_report.audience_user_id IS NULL\n"
            "                    OR parent_report.audience_user_id = auth.uid()",
        ),
        ("authenticated",),
    )
