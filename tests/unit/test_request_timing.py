"""Request timing distinguishes query/pool time without exposing SQL or values."""

from importlib import import_module, util
import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest


def timing():
    assert util.find_spec("backend.common.timing"), "request timing missing"
    return import_module("backend.common.timing")


def test_stage_durations_and_database_totals_are_safe_and_request_scoped(caplog):
    module = timing()
    caplog.set_level(logging.INFO, logger="wealthsail.performance")
    with module.request_trace("request-one") as trace:
        module.record_timing("db.pool_wait", 12)
        with module.trace_stage("context.daily_research"):
            pass
        trace.query_completed(
            SimpleNamespace(
                query="SELECT 'private-nav-token'",
                args=("private-password",),
                elapsed=0.8,
                exception=None,
            )
        )
        assert trace.sql_count == 1
        assert trace.sql_ms == 800
        assert "pool;dur=12.0" in trace.server_timing(920)
        assert "db;dur=800.0" in trace.server_timing(920)
    with module.request_trace("request-two") as other:
        assert other.sql_count == 0
        assert other.stages == {}
    assert "request_id=request-one" in caplog.text
    assert "stage=context.daily_research" in caplog.text
    assert "slow_sql" in caplog.text
    assert "private-nav-token" not in caplog.text
    assert "private-password" not in caplog.text


def test_query_logger_is_removed_even_when_database_operation_raises():
    module = timing()
    callbacks = []
    conn = SimpleNamespace(
        add_query_logger=callbacks.append, remove_query_logger=callbacks.remove
    )
    with module.request_trace("request"):
        with pytest.raises(ValueError):
            with module.trace_queries(conn):
                assert len(callbacks) == 1
                raise ValueError("database failure")
    assert callbacks == []


@pytest.mark.asyncio
async def test_parallel_requests_do_not_share_query_counts_or_stages():
    module = timing()

    async def one(identity, duration):
        with module.request_trace(identity) as trace:
            with module.trace_stage(identity):
                await asyncio.sleep(0)
                trace.query_completed(
                    SimpleNamespace(
                        query="SELECT 1", args=(), elapsed=duration, exception=None
                    )
                )
            return trace

    left, right = await asyncio.gather(one("left", 0.01), one("right", 0.02))
    assert (left.sql_count, left.sql_ms) == (1, 10)
    assert (right.sql_count, right.sql_ms) == (1, 20)
    assert set(left.stages) == {"left"}
    assert set(right.stages) == {"right"}


@pytest.mark.asyncio
async def test_http_exposes_timings_and_logs_start_without_query_or_auth_values(caplog):
    from backend.main import create_app

    module = timing()
    app = create_app()

    @app.get("/timing-fixture")
    async def fixture():
        with module.trace_stage("context.fixture"):
            module.record_timing("db.pool_wait", 7)
        return {"ready": True}

    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/timing-fixture?secret=private-query",
            headers={
                "X-Request-Id": "timing-fixture",
                "Authorization": "Bearer private-auth",
            },
        )
    assert response.status_code == 200
    assert "app;dur=" in response.headers["Server-Timing"]
    assert "pool;dur=7.0" in response.headers["Server-Timing"]
    assert float(response.headers["X-Request-Time-Ms"]) >= 0
    assert "http_request_start" in caplog.text
    assert "db_queries=0" in caplog.text
    application_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("wealthsail.")
    )
    assert "private-query" not in application_logs
    assert "private-auth" not in application_logs
