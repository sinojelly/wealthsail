"""Small request-local timing spans; never log SQL text, values or credentials."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
import logging
import re
from time import perf_counter

logger = logging.getLogger("wealthsail.performance")
_active = ContextVar("request_timing", default=None)
_stage = ContextVar("request_timing_stage", default="request")


def _token(value):
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value))[:100]


@dataclass
class RequestTrace:
    request_id: str
    stages: dict[str, float] = field(default_factory=dict)
    sql_count: int = 0
    sql_ms: float = 0

    def query_completed(self, record):
        elapsed = max(0, record.elapsed * 1000)
        self.sql_count += 1
        self.sql_ms += elapsed
        if elapsed >= 250 or record.exception is not None:
            fingerprint = sha256(record.query.encode("utf-8")).hexdigest()[:12]
            logger.info(
                "slow_sql request_id=%s stage=%s query_id=%s duration_ms=%.1f status=%s",
                self.request_id,
                _token(_stage.get()),
                fingerprint,
                elapsed,
                "error" if record.exception is not None else "ok",
            )

    def server_timing(self, elapsed):
        return (
            f"app;dur={elapsed:.1f}, db;dur={self.sql_ms:.1f}, "
            f"pool;dur={self.stages.get('db.pool_wait', 0):.1f}"
        )


@contextmanager
def request_trace(request_id):
    trace = RequestTrace(_token(request_id))
    token = _active.set(trace)
    try:
        yield trace
    finally:
        _active.reset(token)


def record_timing(stage, duration_ms, *, status="ok"):
    trace = _active.get()
    if trace is None:
        return
    name = _token(stage)
    trace.stages[name] = trace.stages.get(name, 0) + max(0, duration_ms)
    logger.info(
        "perf_stage request_id=%s stage=%s duration_ms=%.1f status=%s",
        trace.request_id,
        name,
        duration_ms,
        status,
    )


@contextmanager
def trace_stage(stage):
    start = perf_counter()
    token = _stage.set(_token(stage))
    status = "ok"
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        record_timing(stage, (perf_counter() - start) * 1000, status=status)
        _stage.reset(token)


@contextmanager
def trace_queries(conn):
    trace = _active.get()
    if trace is None:
        yield
        return
    callback = trace.query_completed
    conn.add_query_logger(callback)
    try:
        yield
    finally:
        # asyncpg 0.30's convenience query_logger does not remove on errors.
        conn.remove_query_logger(callback)
