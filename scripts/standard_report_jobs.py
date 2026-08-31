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


def standard_job_arrays(
    jobs: tuple[StandardReportJob, ...] = STANDARD_REPORT_JOBS,
) -> tuple[list[str], list[str], list[str]]:
    """Return asyncpg-friendly arrays in canonical session order."""

    return (
        [job.name for job in jobs],
        [job.cron for job in jobs],
        [job.session for job in jobs],
    )


def missing_canonical_jobs(
    rows: list[object],
) -> tuple[StandardReportJob, ...] | None:
    """Return missing jobs for an exact canonical subset, or reject conflicts.

    A subset is safe to repair only when every observed session occurs once and
    already matches its canonical name, cron expression and enabled state.
    Returning ``None`` distinguishes a conflicting state from a complete set,
    whose missing tuple is empty.
    """

    expected = {job.session: job for job in STANDARD_REPORT_JOBS}
    observed_sessions: set[str] = set()
    for row in rows:
        session = str(row["report_session"])
        definition = expected.get(session)
        if definition is None or session in observed_sessions:
            return None
        if (
            str(row["job_name"]) != definition.name
            or str(row["cron_expr"]) != definition.cron
            or row["is_enabled"] is not True
        ):
            return None
        observed_sessions.add(session)
    return tuple(
        job for job in STANDARD_REPORT_JOBS if job.session not in observed_sessions
    )


def is_complete_canonical_schedule(rows: list[object]) -> bool:
    """Accept exactly one enabled canonical row for every standard session."""

    return missing_canonical_jobs(rows) == ()


__all__ = [
    "STANDARD_REPORT_JOBS",
    "StandardReportJob",
    "is_complete_canonical_schedule",
    "missing_canonical_jobs",
    "standard_job_arrays",
]
