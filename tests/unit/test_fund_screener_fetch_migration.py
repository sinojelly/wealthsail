"""Database contract for durable on-demand fund-screener fetches."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_065_allows_only_the_worker_to_consume_on_demand_range_fetches() -> None:
    """The new job type must be claimable without making the cache API-writable."""

    sql = (ROOT / "migrations/065_fund_nav_range_fetch_jobs.sql").read_text("utf-8")

    assert "FUND_NAV_RANGE_FETCH" in sql
    assert "DROP CONSTRAINT IF EXISTS job_runs_job_type_check" in sql
    assert "idx_job_runs_data_claimable" in sql
    assert "GRANT INSERT ON reference.fund_nav_ranges TO wealthsail_api" not in sql
