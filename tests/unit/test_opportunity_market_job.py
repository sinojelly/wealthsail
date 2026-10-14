"""Opportunity collection owns bounded claims and reuses only durable facts."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from importlib import import_module, util
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import asyncpg
import pytest

NOW = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
JOB = "OPPORTUNITY_MARKET_EVIDENCE"


def job():
    assert util.find_spec("backend.jobs.opportunity_market"), (
        "market collection job missing"
    )
    return import_module("backend.jobs.opportunity_market")


class Store:
    def __init__(self, previous=None, active=False, brief=None):
        self.previous, self.active, self.brief = previous, active, brief
        self.connections = 0
        self.transactions = 0
        self.lock = asyncio.Lock()
        self.writes = []
        self.claims = []
        self.finishes = []

    @asynccontextmanager
    async def connection(self, _pool):
        self.connections += 1
        try:
            yield self
        finally:
            self.connections -= 1

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            self.transactions += 1
            try:
                yield
            finally:
                self.transactions -= 1

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        if "INSERT INTO market.macro_evidence_attempts" in sql:
            if "'CLAIMED'" in sql:
                self.active = True
                self.claims.append(args)
            else:
                self.active = False
                self.finishes.append(args)
                self.previous = {"recorded_at": args[4], "result": json.loads(args[7])}

    async def fetchval(self, sql, *args):
        assert "job_key=$1" in sql and args[0] == JOB
        assert "lease_until" in sql and "CANCELLED" in sql
        return self.active

    async def fetchrow(self, sql, *args):
        if "reporting.shared_pre_market_briefs" in sql:
            assert "generated_at<=" in sql and "created_at<=" in sql
            return self.brief
        assert "job_key=$1" in sql and args[0] == JOB
        return self.previous


def payload(status="SUCCESS"):
    return {
        "observations": [
            {
                "code": "US_T10_REAL",
                "value": "1.2",
                "referenceDate": "2026-10-13",
                "frequency": "DAILY",
                "unit": "%",
                "sourceUri": "https://home.treasury.gov/rates",
                "sourceName": "US Treasury",
                "publishedAt": None,
                "retrievedAt": NOW,
                "availableAt": NOW,
                "timeBasis": "RETRIEVAL_ONLY",
                "methodVersion": "test-v1",
                "metadata": {
                    "sourceKey": "UST_REAL",
                    "definitionKey": "UST_REAL10",
                    "definitionVersion": "v1",
                },
            }
        ],
        "sourceResults": {
            "UST_REAL": {"status": status},
            "CHINABOND": {"status": "PERMISSION_REQUIRED"},
            "CFETS": {"status": "PERMISSION_REQUIRED"},
        },
    }


def patch_job(monkeypatch, store, data=None):
    module = job()
    monkeypatch.setattr(module, "system_connection", store.connection)
    permissions = AsyncMock(return_value={"CHINABOND": {"status": "UNKNOWN"}})
    append = AsyncMock(return_value={"observations": 1, "rejected": 0})
    provider = AsyncMock(return_value=payload() if data is None else data)
    monkeypatch.setattr(module.repository, "read_permissions", permissions)
    monkeypatch.setattr(module.repository, "append_series", append)
    monkeypatch.setattr(module, "fetch_opportunity_series", provider)
    return module, permissions, append, provider


@pytest.mark.asyncio
async def test_permissions_and_claim_release_the_connection_before_source_http(
    monkeypatch,
):
    store = Store()
    module, permissions, append, provider = patch_job(monkeypatch, store)

    async def fetch(now, permissions=None):
        assert store.connections == 0 and store.transactions == 0
        assert now == NOW and permissions == {"CHINABOND": {"status": "UNKNOWN"}}
        return payload()

    provider.side_effect = fetch
    finished = NOW + timedelta(minutes=2)
    result = await module.sync_opportunity_market(
        object(), NOW, now_provider=lambda: finished
    )
    assert result["status"] == "COMPLETED"
    assert result["observations"] == 1 and result["rejected"] == 0
    assert result["sourceResults"]["CHINABOND"]["status"] == "PERMISSION_REQUIRED"
    assert not result.get("retryUntil")
    assert permissions.await_count == append.await_count == 1
    assert append.await_args.args[2] == finished
    assert store.claims[0][1] == JOB and store.claims[0][3] == NOW + timedelta(
        minutes=10
    )
    assert store.finishes[0][1:3] == (JOB, "FINISHED")
    assert store.finishes[0][4] == finished


@pytest.mark.asyncio
async def test_request_clock_is_replaced_by_actual_completion_availability(monkeypatch):
    data = payload()
    published = NOW - timedelta(days=1)
    official = {
        **data["observations"][0],
        "code": "US_T10_NOMINAL",
        "publishedAt": published,
        "availableAt": published,
        "timeBasis": "OFFICIAL_PUBLICATION",
        "metadata": {
            "sourceKey": "UST_NOMINAL",
            "definitionKey": "UST_NOMINAL10",
            "definitionVersion": "v1",
        },
    }
    data["observations"].append(official)
    data["sourceResults"]["UST_NOMINAL"] = {"status": "SUCCESS"}
    module, _, append, _ = patch_job(monkeypatch, Store(), data)
    finished = NOW + timedelta(minutes=2)
    await module.sync_opportunity_market(object(), NOW, now_provider=lambda: finished)
    records = [record for call in append.await_args_list for record in call.args[1]]
    assert all(
        record["retrievedAt"] == finished and record["availableAt"] == finished
        for record in records
    )
    assert (
        next(record for record in records if record["code"] == "US_T10_NOMINAL")[
            "publishedAt"
        ]
        == published
    )


@pytest.mark.asyncio
async def test_rejected_future_fact_marks_its_source_failed_without_stale_success(
    monkeypatch,
):
    data = payload()
    data["observations"][0]["referenceDate"] = "2026-12-01"
    module, _, append, _ = patch_job(monkeypatch, Store(), data)
    result = await module.sync_opportunity_market(
        object(), NOW, now_provider=lambda: NOW
    )
    assert result["status"] == "UNAVAILABLE" and result["rejected"] == 1
    assert result["sourceResults"]["UST_REAL"]["status"] == "FAILED"
    append.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_active_claim_prevents_even_forced_parallel_sources(monkeypatch, force):
    module, permissions, append, provider = patch_job(monkeypatch, Store(active=True))
    result = await module.sync_opportunity_market(
        object(), NOW, force=force, now_provider=lambda: NOW
    )
    assert result["status"] == "SKIPPED"
    permissions.assert_not_awaited()
    provider.assert_not_awaited()
    append.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_forced_collectors_have_one_active_provider(monkeypatch):
    store = Store()
    module, _, _, provider = patch_job(monkeypatch, store)
    entered, released = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await released.wait()
        return payload()

    provider.side_effect = fetch
    first = asyncio.create_task(
        module.sync_opportunity_market(
            object(), NOW, force=True, now_provider=lambda: NOW
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    try:
        second = await module.sync_opportunity_market(
            object(), NOW, force=True, now_provider=lambda: NOW
        )
        assert second["status"] == "SKIPPED"
        assert provider.await_count == 1
    finally:
        released.set()
        await first


@pytest.mark.parametrize("hours,due", [(23.99, False), (24, True), (25, True)])
def test_successful_collection_has_a_persistent_24_hour_interval(hours, due):
    previous = {"recorded_at": NOW, "result": {"status": "COMPLETED"}}
    result = job()._refresh_due(NOW + timedelta(hours=hours), previous)
    assert (result is not None) is due
    if due:
        assert result["trigger"] == "REGULAR"


def test_failed_hourly_retries_keep_the_original_24_hour_deadline():
    deadline = NOW + timedelta(hours=24)
    previous = {
        "recorded_at": NOW,
        "result": json.dumps({"status": "PARTIAL", "retryUntil": deadline.isoformat()}),
    }
    assert job()._refresh_due(NOW + timedelta(minutes=59), previous) is None
    assert (
        job()._refresh_due(NOW + timedelta(hours=1), previous)["retry_until"]
        == deadline
    )
    previous["recorded_at"] = NOW + timedelta(hours=23)
    result = job()._refresh_due(deadline, previous)
    assert result["trigger"] == "REGULAR" and result["retry_exhausted"] is True
    after = {
        "recorded_at": deadline,
        "result": {"status": "UNAVAILABLE", "retryExhausted": True},
    }
    assert job()._refresh_due(deadline + timedelta(hours=1), after) is None
    assert (
        job()._refresh_due(deadline + timedelta(hours=24), after)["trigger"]
        == "REGULAR"
    )


@pytest.mark.asyncio
async def test_force_bypasses_interval_but_leaves_prior_facts_intact(monkeypatch):
    previous = {"recorded_at": NOW, "result": {"status": "COMPLETED"}}
    store = Store(previous=previous)
    module, _, _, provider = patch_job(monkeypatch, store)
    assert (
        await module.sync_opportunity_market(object(), NOW, now_provider=lambda: NOW)
    )["status"] == "SKIPPED"
    provider.assert_not_awaited()
    assert (
        await module.sync_opportunity_market(
            object(), NOW, force=True, now_provider=lambda: NOW
        )
    )["status"] == "COMPLETED"
    assert store.claims[0][4] == "FORCE"
    assert not any("DELETE" in sql or "UPDATE" in sql for sql, _ in store.writes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes,status",
    [
        (
            {"UST_REAL": {"status": "SUCCESS"}, "PBOC_M1": {"status": "FAILED"}},
            "PARTIAL",
        ),
        ({"UST_REAL": {"status": "FAILED"}}, "UNAVAILABLE"),
    ],
)
async def test_source_failures_are_independent_and_preserve_valid_rows(
    monkeypatch, outcomes, status
):
    store = Store()
    data = {**payload(), "sourceResults": outcomes}
    module, _, append, _ = patch_job(monkeypatch, store, data)
    result = await module.sync_opportunity_market(
        object(), NOW, now_provider=lambda: NOW
    )
    assert result["status"] == status
    assert (
        result["sourceResults"]["UST_REAL"]["status"] == outcomes["UST_REAL"]["status"]
    )
    assert result["retryUntil"] == NOW + timedelta(hours=24)
    append.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_rows_report_partial_without_erasing_success(monkeypatch):
    module, _, append, _ = patch_job(monkeypatch, Store())
    append.return_value = {"observations": 2, "rejected": 1}
    result = await module.sync_opportunity_market(
        object(), NOW, now_provider=lambda: NOW
    )
    assert (
        result["status"] == "PARTIAL"
        and result["observations"] == 2
        and result["rejected"] == 1
    )


@pytest.mark.asyncio
async def test_provider_failure_finishes_claim_with_original_retry_deadline(
    monkeypatch,
):
    deadline = NOW + timedelta(hours=2)
    store = Store(
        previous={
            "recorded_at": NOW - timedelta(hours=1),
            "result": {"status": "UNAVAILABLE", "retryUntil": deadline.isoformat()},
        }
    )
    module, _, _, provider = patch_job(monkeypatch, store)
    provider.side_effect = TimeoutError()
    result = await module.sync_opportunity_market(
        object(), NOW, now_provider=lambda: NOW
    )
    assert result["errorClass"] == "TimeoutError" and result["retryUntil"] == deadline
    assert store.finishes[0][2] == "FINISHED"


@pytest.mark.asyncio
async def test_cancellation_is_persisted_before_it_propagates(monkeypatch):
    store = Store()
    module, _, _, provider = patch_job(monkeypatch, store)
    entered = asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await asyncio.Future()

    provider.side_effect = fetch
    running = asyncio.create_task(
        module.sync_opportunity_market(object(), NOW, now_provider=lambda: NOW)
    )
    await asyncio.wait_for(entered.wait(), 1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert store.connections == 0 and store.finishes[0][2] == "CANCELLED"
    assert json.loads(store.finishes[0][7])["errorClass"] == "CancelledError"


def brief(**changes):
    context = {
        "sourceName": "Sina stored fact",
        "methodVersion": "pre-market-v1",
        "qualityStatus": "FRESH",
        "retrievedAt": (NOW - timedelta(minutes=30)).isoformat(),
        "usIndices": [
            {
                "name": "标普500",
                "changePct": "-1.2",
                "sourceAsOf": "2026-10-13",
                "sourceUri": "https://finance.sina.com.cn/stock/usstock/SPX",
                "qualityStatus": "FRESH",
            },
            {
                "name": "纳斯达克",
                "changePct": "-0.8",
                "sourceAsOf": "2026-10-13",
                "sourceUri": "https://finance.sina.com.cn/stock/usstock/IXIC",
                "qualityStatus": "FRESH",
            },
        ],
        "a50Future": {
            "name": "富时中国A50期货",
            "changePct": "0.3",
            "sourceAsOf": (NOW - timedelta(hours=1)).isoformat(),
            "sourceUri": "https://hq.sinajs.cn/list=hf_CHA50CFD",
            "qualityStatus": "FRESH",
        },
    }
    context.update(changes)
    return {
        "id": "brief-id",
        "trade_date": NOW.date(),
        "source_context": json.dumps(context),
        "quality_status": "FRESH",
        "generated_at": NOW - timedelta(minutes=20),
        "created_at": NOW - timedelta(minutes=20),
    }


def test_only_explicit_typed_pre_market_facts_are_reused_with_their_original_times():
    rows, outcome = job()._reuse_brief(brief(), NOW)
    assert {row["code"] for row in rows} == {
        "A50_CHANGE_PCT",
        "US_SP500_CHANGE_PCT",
        "US_NASDAQ_CHANGE_PCT",
    }
    assert outcome["status"] == "SUCCESS" and outcome["observations"] == 3
    assert all(
        row["publishedAt"] is None and row["timeBasis"] == "RETRIEVAL_ONLY"
        for row in rows
    )
    assert all(
        row["retrievedAt"] == NOW - timedelta(minutes=30)
        and row["availableAt"] == row["retrievedAt"]
        for row in rows
    )
    assert all(row["metadata"]["originBriefId"] == "brief-id" for row in rows)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_uri",
        "future_quote",
        "future_retrieval",
        "stale",
        "bad_number",
        "naive_time",
    ],
)
def test_incomplete_or_unusable_brief_facts_do_not_receive_fabricated_provenance(
    mutation,
):
    record = brief()
    context = json.loads(record["source_context"])
    context["usIndices"] = []
    quote = context["a50Future"]
    if mutation == "missing_uri":
        quote.pop("sourceUri")
    elif mutation == "future_quote":
        quote["sourceAsOf"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "future_retrieval":
        context["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "stale":
        quote["qualityStatus"] = "STALE"
    elif mutation == "bad_number":
        quote["changePct"] = "NaN"
    elif mutation == "naive_time":
        quote["sourceAsOf"] = "2026-10-14T10:00:00"
    record["source_context"] = context
    rows, outcome = job()._reuse_brief(record, NOW)
    assert rows == [] and outcome["status"] == "NOT_COLLECTED"


@pytest.mark.asyncio
async def test_pre_market_reuse_is_read_only_and_missing_provenance_is_not_public_failure(
    monkeypatch,
):
    store = Store(brief=brief(sourceName=None))
    module, _, append, _ = patch_job(monkeypatch, store)
    result = await module.sync_opportunity_market(
        object(), NOW, now_provider=lambda: NOW
    )
    assert result["status"] == "COMPLETED"
    assert result["sourceResults"]["PRE_MARKET"]["reason"] == "INSUFFICIENT_PROVENANCE"
    assert not result.get("retryUntil")
    assert append.await_args.args[1] == payload()["observations"]


@pytest.mark.asyncio
async def test_service_checks_immediately_then_every_15_minutes_and_survives_failure(
    monkeypatch,
):
    module = job()
    sync = AsyncMock(
        side_effect=[RuntimeError("source unavailable"), {"status": "SKIPPED"}]
    )
    monkeypatch.setattr(module, "sync_opportunity_market", sync)
    stop = asyncio.Event()
    delays = []

    async def wait(event, seconds):
        assert sync.await_count == len(delays) + 1
        delays.append(seconds)
        if len(delays) == 2:
            event.set()

    await module.run_opportunity_market_service(
        object(), now_provider=lambda: NOW, wait=wait, stop_event=stop
    )
    assert delays == [900, 900]
    assert sync.await_args_list[0].kwargs["force"] is True
    assert sync.await_args_list[1].kwargs["force"] is False


@pytest.mark.asyncio
async def test_a_pre_stopped_service_does_not_attempt_collection(monkeypatch):
    module = job()
    sync = AsyncMock()
    monkeypatch.setattr(module, "sync_opportunity_market", sync)
    stop = asyncio.Event()
    stop.set()
    await module.run_opportunity_market_service(object(), stop_event=stop)
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_worker_role_claims_and_append_are_idempotent_inside_rollback():
    dsn = os.getenv("OPPORTUNITY_JOB_TEST_DSN")
    if not dsn:
        pytest.skip("isolated opportunity validation database not configured")
    module = job()
    conn = await asyncpg.connect(dsn)
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        stamp = NOW + timedelta(days=30)
        first = await module._claim_refresh(conn, stamp, force=True)
        assert first is not None
        assert await module._claim_refresh(conn, stamp, force=True) is None
        await module._finish_refresh(conn, first, stamp, {"status": "COMPLETED"})
        assert await module._claim_refresh(conn, stamp) is None
        rows = await conn.fetch(
            "SELECT phase,job_key FROM market.macro_evidence_attempts WHERE run_id=$1",
            first["id"],
        )
        assert {row["phase"] for row in rows} == {"CLAIMED", "FINISHED"}
        assert all(row["job_key"] == JOB for row in rows)
        assert not await conn.fetchval(
            "SELECT has_table_privilege(current_user,'reference.opportunity_source_permissions','INSERT')"
        )
        record = {
            "code": "US_T10_REAL",
            "referenceDate": "2026-10-13",
            "frequency": "DAILY",
            "unit": "%",
            "value": "1.123456",
            "sourceUri": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?job_test=rollback",
            "sourceName": "US Treasury",
            "publishedAt": None,
            "retrievedAt": stamp,
            "availableAt": stamp,
            "timeBasis": "RETRIEVAL_ONLY",
            "methodVersion": "job-test-v1",
            "metadata": {
                "definitionKey": "UST_REAL_JOB_TEST",
                "definitionVersion": "v1",
            },
        }
        assert (await module.repository.append_series(conn, [record], stamp))[
            "observations"
        ] == 1
        assert (await module.repository.append_series(conn, [record], stamp))[
            "observations"
        ] == 0
    finally:
        await transaction.rollback()
        await conn.close()
