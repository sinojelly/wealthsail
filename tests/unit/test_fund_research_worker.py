"""The macro runtime drains only webpage fund requests with durable leases."""

import asyncio
import json
from contextlib import asynccontextmanager
from importlib import import_module, util
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.jobs import data_tasks


def _job():
    name = "backend.jobs.fund_research"
    assert util.find_spec(name), "webpage fund refresh consumer is missing"
    return import_module(name)


def _settings(**changes):
    values = dict(
        data_task_lease_seconds=30,
        data_task_max_attempts=2,
        data_task_base_backoff_seconds=5,
        data_task_poll_interval_seconds=0.01,
    )
    values.update(changes)
    return SimpleNamespace(**values)


class QueueConnection:
    """Observe emitted database commands without provider or database access."""

    def __init__(self, job_type="INVESTMENT_RESEARCH_REFRESH", payload=None):
        self.row = dict(
            id=uuid4(),
            tenant_id=uuid4(),
            job_type=job_type,
            payload=json.dumps(payload or {"kind": "fund", "id": str(uuid4())}),
            attempt_count=0,
            lease_token=None,
        )
        self.status = "PENDING"
        self.claims = []
        self.commands = []
        self.active_connections = 0
        self.next_retry_at = None

    async def fetchrow(self, sql, *args):
        if (
            "FROM reference.research_fund_catalog" in sql
            and "WITH candidate" not in sql
        ):
            return {"fund_code": "110022", "market": "FUND_CN", "trade_form": "FUND"}
        self.claims.append((" ".join(sql.split()), args))
        if self.status != "PENDING":
            return None
        self.row["lease_token"] = uuid4()
        self.row["attempt_count"] += 1
        self.status = "RUNNING"
        return dict(self.row)

    async def execute(self, sql, *args):
        normalized = " ".join(sql.split())
        self.commands.append((normalized, args))
        if (
            str(args[0]) != str(self.row["id"])
            or str(args[1]) != str(self.row["lease_token"])
            or self.status != "RUNNING"
        ):
            return "UPDATE 0"
        if "status = 'COMPLETED'" in normalized:
            self.status = "COMPLETED"
        elif "status = $3" in normalized:
            self.status = args[2]
            self.next_retry_at = None if args[5] else "scheduled"
        elif "status = 'PENDING'" in normalized:
            self.status = "PENDING"
            self.row["attempt_count"] -= 1
        if self.status != "RUNNING":
            self.row["lease_token"] = None
        return "UPDATE 1"


def _connections(monkeypatch, job, conn):
    @asynccontextmanager
    async def connection(pool):
        conn.active_connections += 1
        try:
            yield conn
        finally:
            conn.active_connections -= 1

    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(data_tasks, "system_connection", connection)


@pytest.mark.asyncio
async def test_claim_is_scoped_to_fund_and_nav_and_recovers_expired_leases(monkeypatch):
    job = _job()
    conn = QueueConnection()
    _connections(monkeypatch, job, conn)
    lease = await job.claim_fund_refresh(object(), owner="fund-only", lease_seconds=30)
    assert isinstance(lease, data_tasks.DataTaskLease)
    assert lease.payload["kind"] == "fund"
    assert lease.attempt_count == 1
    sql, args = conn.claims[0]
    assert (
        "(job_type IN ('FUND_NAV_RANGE_FETCH','FUND_UNIT_NAV_HISTORY_FETCH') OR (job_type = 'INVESTMENT_RESEARCH_REFRESH' AND payload->>'kind' = 'fund'"
        in sql
    )
    assert "c.instrument_id::text = payload->>'id'" in sql
    assert "c.trade_form = 'FUND' AND c.market = 'FUND_CN'" in sql
    assert "status = 'RUNNING' AND lease_until < now()" in sql
    assert "status = 'FAILED' AND next_retry_at <= now()" in sql
    assert "status = 'PENDING' AND available_at <= now()" in sql
    assert "next_retry_at IS NULL OR next_retry_at <= now()" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert args == ("fund-only", 30)
    assert (
        await job.claim_fund_refresh(object(), owner="other", lease_seconds=30) is None
    )


@pytest.mark.asyncio
async def test_malformed_nav_payload_is_failed_instead_of_abandoning_a_running_lease(
    monkeypatch,
):
    job = _job()
    conn = QueueConnection(job_type="FUND_NAV_RANGE_FETCH")
    conn.row["payload"] = "[]"
    _connections(monkeypatch, job, conn)
    assert await job.consume_fund_refresh(object(), _settings()) is True
    assert conn.status == "FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["fund", "nav"])
async def test_real_dispatch_reaches_registered_fund_or_unregistered_nav_and_completes(
    monkeypatch,
    kind,
):
    job = _job()
    conn = QueueConnection(
        job_type="FUND_NAV_RANGE_FETCH"
        if kind == "nav"
        else "INVESTMENT_RESEARCH_REFRESH",
        payload={"fundCode": "110022"}
        if kind == "nav"
        else {"kind": "fund", "id": str(uuid4())},
    )
    _connections(monkeypatch, job, conn)
    calls = []

    async def refresh(pool, code):
        assert conn.active_connections == 0, (
            "provider I/O must release claim connection"
        )
        calls.append(("unit-nav", code))

    async def fetch(pool, code):
        assert conn.active_connections == 0
        calls.append(("nav", code))
        return SimpleNamespace(fund_code=code, data_source="AKSHARE")

    monkeypatch.setattr(job, "refresh_fund_nav_history", refresh)
    monkeypatch.setattr(data_tasks, "fetch_and_persist_fund_nav_range", fetch)
    monkeypatch.setattr(data_tasks.random, "uniform", lambda *_: 0)
    assert await job.consume_fund_refresh(object(), _settings()) is True
    assert conn.status == "COMPLETED" and conn.row["lease_token"] is None
    if kind == "fund":
        assert calls == [("unit-nav", "110022")]
    else:
        assert calls == [("nav", "110022"), ("unit-nav", "110022")]
    assert await job.consume_fund_refresh(object(), _settings()) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_attempts,retry", [(0, "scheduled"), (1, None)])
async def test_provider_failure_uses_existing_retry_and_terminal_policy(
    monkeypatch, previous_attempts, retry
):
    job = _job()
    conn = QueueConnection()
    conn.row["attempt_count"] = previous_attempts
    _connections(monkeypatch, job, conn)

    async def unavailable(*args, **kwargs):
        raise TimeoutError("provider unavailable")

    monkeypatch.setattr(job, "refresh_fund_nav_history", unavailable)
    assert await job.consume_fund_refresh(object(), _settings()) is True
    assert conn.status == "FAILED"
    assert conn.next_retry_at == retry
    assert conn.row["lease_token"] is None


@pytest.mark.asyncio
async def test_cancellation_stops_work_before_requeue_and_preserves_attempt_budget(
    monkeypatch,
):
    job = _job()
    conn = QueueConnection()
    _connections(monkeypatch, job, conn)
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def refresh(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            assert conn.status == "RUNNING"
            cleaned.set()

    monkeypatch.setattr(job, "refresh_fund_nav_history", refresh)
    task = asyncio.create_task(job.consume_fund_refresh(object(), _settings()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    assert conn.status == "PENDING" and conn.row["attempt_count"] == 0
    assert conn.row["lease_token"] is None
    sql, _ = conn.commands[-1]
    assert "lease_token = $2 AND status = 'RUNNING'" in sql


@pytest.mark.asyncio
async def test_lost_heartbeat_cancels_provider_before_any_terminal_update(monkeypatch):
    job = _job()
    conn = QueueConnection()
    _connections(monkeypatch, job, conn)
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def refresh(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    async def heartbeat(pool, lease, seconds):
        await started.wait()
        conn.row["lease_token"] = uuid4()  # another lease generation now owns it
        raise RuntimeError("data-task lease ownership lost")

    monkeypatch.setattr(job, "refresh_fund_nav_history", refresh)
    monkeypatch.setattr(job, "_heartbeat_loop", heartbeat)
    assert await job.consume_fund_refresh(object(), _settings()) is True
    assert cleaned.is_set()
    assert conn.status == "RUNNING"
    assert not any("status = 'COMPLETED'" in sql for sql, _ in conn.commands)


@pytest.mark.asyncio
async def test_heartbeat_failure_is_observed_while_provider_is_still_running(
    monkeypatch,
):
    job = _job()
    conn = QueueConnection()
    _connections(monkeypatch, job, conn)
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def refresh(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    async def heartbeat(pool, lease, seconds):
        await started.wait()
        raise ConnectionError("heartbeat connection failed")

    monkeypatch.setattr(job, "refresh_fund_nav_history", refresh)
    monkeypatch.setattr(job, "_heartbeat_loop", heartbeat)
    assert await asyncio.wait_for(job.consume_fund_refresh(object(), _settings()), 1)
    assert cleaned.is_set()
    assert conn.status == "FAILED" and conn.next_retry_at == "scheduled"


@pytest.mark.asyncio
async def test_service_stop_cancels_and_awaits_active_web_request(monkeypatch):
    job = _job()
    stop, entered, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def consume(*args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    monkeypatch.setattr(job, "consume_fund_refresh", consume)
    task = asyncio.create_task(
        job.run_fund_research_service(object(), _settings(), stop_event=stop)
    )
    await asyncio.wait_for(entered.wait(), 1)
    stop.set()
    await asyncio.wait_for(task, 1)
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_macro_runtime_owns_and_awaits_fund_service_with_other_services(
    monkeypatch,
):
    from backend.jobs import macro_worker as worker
    from backend.jobs import alpha_runtime

    settings = _settings()
    entered, cleaned = asyncio.Event(), asyncio.Event()
    others = set()

    async def fund(pool, actual_settings, *, stop_event):
        assert actual_settings is settings
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    async def macro(pool, *, stop_event):
        await entered.wait()

    async def other(pool, *args, stop_event, **kwargs):
        others.add(asyncio.current_task().get_name())
        await stop_event.wait()

    monkeypatch.setattr(worker, "run_fund_research_service", fund, raising=False)
    monkeypatch.setattr(worker, "run_macro_service", macro)
    for name in (
        "run_macro_evidence_service",
        "run_opportunity_market_service",
        "run_opportunity_monitor_service",
        "run_index_research_service",
        "run_daily_research_service",
    ):
        monkeypatch.setattr(worker, name, other)
    monkeypatch.setattr(alpha_runtime, "run_alpha_runtime", other)
    await asyncio.wait_for(worker._serve(object(), settings), 1)
    assert cleaned.is_set()
    assert others == {
        "wealthsail-macro-official",
        "wealthsail-opportunity-market",
        "wealthsail-opportunity-monitor",
        "wealthsail-index-research",
        "wealthsail-daily-research",
        "wealthsail-alpha-phase2",
    }
