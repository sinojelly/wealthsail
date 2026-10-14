"""Official evidence remains immutable, bounded, and independent of history."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from importlib import import_module, util
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 14, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def modules():
    assert util.find_spec("backend.macro"), "official evidence package missing"
    return (
        import_module("backend.macro.evidence_repository"),
        import_module("backend.jobs.macro_evidence"),
    )


def release(**changes):
    row = dict(
        code="CPI_YOY",
        referencePeriod="2026-09",
        value="0.9",
        unit="%",
        publishedAt=None,
        sourceUri="https://www.stats.gov.cn/release/cpi.html",
        publisher="国家统计局",
        retrievedAt=NOW,
        contentHash="source-content",
    )
    row.update(changes)
    return row


def payload(**changes):
    result = dict(
        releases=[release()],
        schedules=[],
        events=[],
        sourceResults={"NBS_RELEASES": {"status": "SUCCESS"}},
    )
    result.update(changes)
    return result


@pytest.mark.parametrize(
    "kind,row",
    [
        ("releases", release(value="NaN")),
        ("releases", release(value=True)),
        ("releases", release(value="1e100")),
        ("releases", release(referencePeriod="2026-13")),
        ("releases", release(publishedAt=NOW + timedelta(seconds=1))),
        ("releases", release(retrievedAt=NOW + timedelta(seconds=1))),
        ("releases", release(retrievedAt=NOW.replace(tzinfo=None))),
        ("releases", release(sourceUri="https://example.test/fake-official")),
        ("releases", release(unit="index_point")),
    ],
)
def test_invalid_or_future_official_release_is_rejected(kind, row):
    repo, _ = modules()
    with pytest.raises(ValueError):
        repo.normalize_record(kind, row, NOW)


def test_semantic_dedup_ignores_new_retrieval_but_keeps_actual_revision():
    repo, _ = modules()
    old = repo.normalize_record("releases", release(), NOW)
    again = repo.normalize_record(
        "releases",
        release(retrievedAt=NOW + timedelta(hours=1)),
        NOW + timedelta(hours=1),
    )
    revision = repo.normalize_record("releases", release(value="1.0"), NOW)
    assert old["canonical_hash"] == again["canonical_hash"]
    assert old["canonical_hash"] != revision["canonical_hash"]
    assert old["published_at"] is None
    assert repo.normalize_record("releases", release(value="-0.2"), NOW)["value"] < 0


def test_dynamic_source_html_hash_does_not_manufacture_fact_revision():
    repo, _ = modules()
    first = repo.normalize_record("releases", release(contentHash="html-one"), NOW)
    second = repo.normalize_record("releases", release(contentHash="html-two"), NOW)
    assert first["canonical_hash"] == second["canonical_hash"]
    assert first["content_hash"] != second["content_hash"]


def test_future_schedule_is_allowed_and_date_only_actual_time_stays_unknown():
    repo, _ = modules()
    row = release()
    row.pop("value")
    row.pop("unit")
    row.pop("publishedAt")
    row["scheduledAt"] = NOW + timedelta(days=20)
    result = repo.normalize_record("schedules", row, NOW)
    assert result["scheduled_at"] > NOW
    assert repo.normalize_record("releases", release(), NOW)["published_at"] is None


class Store:
    def __init__(self):
        self.active = 0
        self.writes = []
        self.claim = {
            "id": "claim",
            "trigger": "BOOT",
            "release_keys": [],
            "retry_until": None,
        }

    @asynccontextmanager
    async def connection(self, _pool):
        self.active += 1
        try:
            yield self
        finally:
            self.active -= 1

    async def append(self, conn, data, now):
        assert self.active == 1
        self.writes.append(data)
        return {"releases": len(data["releases"]), "schedules": 0, "events": 0}


@pytest.mark.asyncio
async def test_provider_runs_without_connection_and_partial_source_keeps_valid_evidence(
    monkeypatch,
):
    repo, job = modules()
    store = Store()

    async def provider(now):
        assert store.active == 0
        return payload(
            sourceResults={
                "NBS_RELEASES": {"status": "SUCCESS"},
                "PBOC_RELEASES": {"status": "FAILED", "errorClass": "TimeoutError"},
            }
        )

    monkeypatch.setattr(job, "system_connection", store.connection)
    monkeypatch.setattr(repo, "claim_refresh", AsyncMock(return_value=store.claim))
    finish = AsyncMock()
    monkeypatch.setattr(repo, "finish_refresh", finish)
    monkeypatch.setattr(repo, "append_evidence", store.append)
    monkeypatch.setattr(job, "fetch_macro_evidence", provider, raising=False)
    result = await job.sync_macro_evidence(object(), NOW)
    assert result["status"] == "PARTIAL"
    assert result["releases"] == 1
    assert finish.await_args.args[-1]["status"] == "PARTIAL"
    assert store.active == 0


@pytest.mark.asyncio
async def test_provider_failure_finishes_attempt_without_erasing_existing_facts(
    monkeypatch,
):
    repo, job = modules()
    store = Store()
    monkeypatch.setattr(job, "system_connection", store.connection)
    monkeypatch.setattr(repo, "claim_refresh", AsyncMock(return_value=store.claim))
    finish = AsyncMock()
    monkeypatch.setattr(repo, "finish_refresh", finish)
    append = AsyncMock()
    monkeypatch.setattr(repo, "append_evidence", append)
    monkeypatch.setattr(
        job,
        "fetch_macro_evidence",
        AsyncMock(side_effect=RuntimeError("secret-dsn")),
        raising=False,
    )
    result = await job.sync_macro_evidence(object(), NOW)
    assert result["status"] == "UNAVAILABLE"
    assert "secret" not in str(result)
    append.assert_not_awaited()
    assert finish.await_count == 1


@pytest.mark.asyncio
async def test_existing_claim_or_recent_attempt_never_calls_provider(monkeypatch):
    repo, job = modules()
    store = Store()
    monkeypatch.setattr(job, "system_connection", store.connection)
    monkeypatch.setattr(repo, "claim_refresh", AsyncMock(return_value=None))
    provider = AsyncMock()
    monkeypatch.setattr(job, "fetch_macro_evidence", provider, raising=False)
    assert (await job.sync_macro_evidence(object(), NOW))["status"] == "SKIPPED"
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_poll_without_due_actual_announcement_keeps_hourly_retry(
    monkeypatch,
):
    repo, job = modules()
    store = Store()
    store.claim.update(
        trigger="RELEASE",
        release_keys=["plan"],
        release_targets=[{"code": "CPI_YOY", "reference_period": "2026-09"}],
        retry_until=NOW + timedelta(hours=23),
    )
    monkeypatch.setattr(job, "system_connection", store.connection)
    monkeypatch.setattr(repo, "claim_refresh", AsyncMock(return_value=store.claim))
    monkeypatch.setattr(repo, "finish_refresh", AsyncMock())
    monkeypatch.setattr(
        repo,
        "append_evidence",
        AsyncMock(return_value={"releases": 0, "schedules": 0, "events": 0}),
    )
    monkeypatch.setattr(
        repo,
        "read_evidence",
        AsyncMock(return_value={"releases": [], "schedules": [], "events": []}),
    )
    monkeypatch.setattr(
        job, "fetch_macro_evidence", AsyncMock(return_value=payload(releases=[]))
    )
    result = await job.sync_macro_evidence(object(), NOW)
    assert result["status"] == "PARTIAL"
    assert result["reason"] == "AWAITING_SCHEDULED_RELEASE"
    assert result["retryUntil"] == store.claim["retry_until"]


@pytest.mark.asyncio
async def test_exhausted_failure_window_stays_on_6h_checks_until_success(monkeypatch):
    repo, job = modules()
    store = Store()
    store.claim.update(trigger="REGULAR", retry_exhausted=True)
    monkeypatch.setattr(job, "system_connection", store.connection)
    monkeypatch.setattr(repo, "claim_refresh", AsyncMock(return_value=store.claim))
    monkeypatch.setattr(repo, "finish_refresh", AsyncMock())
    monkeypatch.setattr(
        job, "fetch_macro_evidence", AsyncMock(side_effect=TimeoutError())
    )
    result = await job.sync_macro_evidence(object(), NOW)
    assert result["retryExhausted"] is True
    assert not result.get("retryUntil")
    previous = {"started_at": NOW, "recorded_at": NOW, "result": result}
    assert repo.refresh_due(NOW + timedelta(hours=1), previous, [], []) is None
    assert (
        repo.refresh_due(NOW + timedelta(hours=6), previous, [], [])["trigger"]
        == "REGULAR"
    )


@pytest.mark.asyncio
async def test_cancelled_provider_finishes_its_claim_before_task_returns(monkeypatch):
    repo, job = modules()
    store = Store()
    entered = asyncio.Event()

    async def provider(now):
        assert store.active == 0
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(job, "system_connection", store.connection)
    monkeypatch.setattr(repo, "claim_refresh", AsyncMock(return_value=store.claim))
    finish = AsyncMock()
    monkeypatch.setattr(repo, "finish_refresh", finish)
    monkeypatch.setattr(job, "fetch_macro_evidence", provider)
    running = asyncio.create_task(job.sync_macro_evidence(object(), NOW))
    await asyncio.wait_for(entered.wait(), 1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert finish.await_args.kwargs == {"cancelled": True}
    assert finish.await_args.args[-1]["errorClass"] == "CancelledError"
    assert store.active == 0


def test_durable_due_policy_regular_release_retry_and_24h_limit():
    repo, _ = modules()
    success = {
        "started_at": NOW - timedelta(hours=5),
        "recorded_at": NOW - timedelta(hours=5),
        "result": {"status": "COMPLETED"},
    }
    assert repo.refresh_due(NOW, success, [], []) is None
    assert (
        repo.refresh_due(NOW + timedelta(hours=1), success, [], [])["trigger"]
        == "REGULAR"
    )
    schedule = {
        "id": "plan",
        "code": "CPI_YOY",
        "reference_period": "2026-09",
        "scheduled_at": NOW,
        "retrieved_at": NOW - timedelta(days=1),
    }
    assert (
        repo.refresh_due(NOW + timedelta(minutes=14), success, [schedule], []) is None
    )
    due = repo.refresh_due(NOW + timedelta(minutes=15), success, [schedule], [])
    assert due["trigger"] == "RELEASE"
    failed = {
        "started_at": NOW + timedelta(minutes=15),
        "recorded_at": NOW + timedelta(minutes=15),
        "release_keys": ["plan"],
        "result": {
            "status": "PARTIAL",
            "retryUntil": (NOW + timedelta(hours=24)).isoformat(),
        },
    }
    assert repo.refresh_due(NOW + timedelta(minutes=59), failed, [schedule], []) is None
    assert (
        repo.refresh_due(NOW + timedelta(hours=1, minutes=15), failed, [schedule], [])[
            "trigger"
        ]
        == "RETRY"
    )
    assert (
        repo.refresh_due(NOW + timedelta(hours=24, minutes=15), failed, [schedule], [])[
            "trigger"
        ]
        == "REGULAR"
    )


def test_new_due_release_is_not_delayed_by_other_source_failure_retry():
    repo, _ = modules()
    failed = {
        "started_at": NOW - timedelta(minutes=10),
        "recorded_at": NOW - timedelta(minutes=10),
        "release_keys": [],
        "result": {
            "status": "PARTIAL",
            "retryUntil": (NOW + timedelta(hours=23)).isoformat(),
        },
    }
    schedule = {
        "id": "new-plan",
        "code": "CPI_YOY",
        "reference_period": "2026-09",
        "scheduled_at": NOW - timedelta(minutes=15),
    }
    assert repo.refresh_due(NOW, failed, [schedule], [])["trigger"] == "RELEASE"


@pytest.mark.asyncio
async def test_evidence_loop_wakes_each_15m_and_isolates_failed_ticks(monkeypatch):
    _, job = modules()
    calls = []

    async def sync(pool, now, *, force=False):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("unavailable DB")
        return {"status": "SKIPPED"}

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

    stop = Stop()

    async def wait(event, seconds):
        assert seconds == 900
        if len(calls) == 2:
            event.stopped = True

    monkeypatch.setattr(job, "sync_macro_evidence", sync)
    await job.run_macro_evidence_service(
        object(), now_provider=lambda: NOW, wait=wait, stop_event=stop
    )
    assert len(calls) == 2
