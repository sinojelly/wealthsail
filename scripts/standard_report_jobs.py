"""Canonical four-session report schedules shared by operator commands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardReportJob:
    name: str
    cron: str
    session: str


STANDARD_REPORT_JOBS = (
    StandardReportJob("pre_market_brief", "0 30 8 * * 1-5", "PRE_MARKET"),
    StandardReportJob("intraday_tracking", "*/5 9-14 * * 1-5", "INTRADAY"),
    StandardReportJob("fund_1430_estimate", "0 30 14 * * 1-5", "FUND_1430"),
    StandardReportJob("post_market_review", "0 30 15 * * 1-5", "POST_MARKET"),
)


def standard_job_arrays() -> tuple[list[str], list[str], list[str]]:
    """Return asyncpg-friendly arrays in canonical session order."""

    return (
        [job.name for job in STANDARD_REPORT_JOBS],
        [job.cron for job in STANDARD_REPORT_JOBS],
        [job.session for job in STANDARD_REPORT_JOBS],
    )


def is_complete_canonical_schedule(rows: list[object]) -> bool:
    """Accept exactly one enabled canonical row for every standard session."""

    if len(rows) != len(STANDARD_REPORT_JOBS):
        return False
    observed = {
        str(row["report_session"]): (
            str(row["job_name"]),
            str(row["cron_expr"]),
            row["is_enabled"] is True,
        )
        for row in rows
    }
    expected = {
        job.session: (job.name, job.cron, True) for job in STANDARD_REPORT_JOBS
    }
    return observed == expected and len(observed) == len(rows)


__all__ = [
    "STANDARD_REPORT_JOBS",
    "StandardReportJob",
    "is_complete_canonical_schedule",
    "standard_job_arrays",
]
