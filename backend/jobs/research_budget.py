"""Inherited whole-request budget; it never widens a frozen pipeline deadline."""

from contextlib import contextmanager
from contextvars import ContextVar
from math import ceil
from time import monotonic

from backend.ai_usage.tracker import SNAPSHOT_TOTAL_BUDGET_SECONDS
from backend.integrations.daily_sources import (
    MAX_RESEARCH_SOURCE_CHECKS,
    SOURCE_CHECK_CONCURRENCY,
    SOURCE_CHECK_TIMEOUT_SECONDS,
)

_request_deadline = ContextVar("research_request_deadline", default=float("inf"))
# Leave bounded room for source verification and immutable result persistence.
POST_MODEL_RESERVE_SECONDS = (
    ceil(MAX_RESEARCH_SOURCE_CHECKS / SOURCE_CHECK_CONCURRENCY)
    * SOURCE_CHECK_TIMEOUT_SECONDS
    + 30
)


class ResearchExecutionBudgetInsufficient(TimeoutError):
    pass


@contextmanager
def research_request_budget(seconds):
    deadline = min(_request_deadline.get(), monotonic() + seconds)
    token = _request_deadline.set(deadline)
    try:
        yield
    finally:
        _request_deadline.reset(token)


def model_budget_fits(worker_seconds, *, pipeline_deadline=float("inf")):
    remaining = min(_request_deadline.get(), pipeline_deadline) - monotonic()
    return remaining >= (
        worker_seconds + SNAPSHOT_TOTAL_BUDGET_SECONDS + POST_MODEL_RESERVE_SECONDS
    )
