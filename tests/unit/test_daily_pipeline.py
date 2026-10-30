"""A scheduled model call requires one owned, completed, same-day source batch."""

from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from backend.macro import daily_repository


NOW = datetime(2026, 10, 30, 7, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_schedule_defaults_to_daily_and_explicit_weekdays_skips_weekends():
    assert daily_repository.scheduled_for(NOW) == NOW
    assert daily_repository.next_run(NOW) == NOW + timedelta(days=1)
    assert daily_repository.next_run(NOW, "WEEKDAYS") == NOW + timedelta(days=3)
    assert daily_repository.next_run(
        NOW + timedelta(days=1), "WEEKDAYS"
    ) == NOW + timedelta(days=3)


def test_partial_source_quality_never_accepts_empty_or_unreliable_inputs():
    from backend.macro.daily_pipeline_repository import source_quality

    result = source_quality({"collectionState": "PARTIAL"}, NOW)
    assert result["allowed"] is False
    assert result["reason"] == "NO_VALID_PUBLIC_FACTS"


def valid_partial_bundle(now=NOW):
    start = now.date() - timedelta(days=200)
    days = [start + timedelta(days=i) for i in range(201)]
    points = [
        {"date": day.isoformat(), "close": "1.00"}
        for day in days
        if day < now.date() and day.weekday() < 5
    ]
    return {
        "collectionState": "PARTIAL",
        "observations": [
            {
                "code": "M1_YOY",
                "value": "5.0",
                "referenceDate": "2026-09-30",
                "frequency": "MONTHLY",
                "unit": "%",
                "sourceKey": "AKSHARE_MONEY_SUPPLY",
                "sourceUri": "https://data.eastmoney.com/cjsj/hbgyzl.html",
                "retrievedAt": now.isoformat(),
                "availableAt": now.isoformat(),
            }
        ],
        "sourceResults": {
            "AKSHARE_MONEY_SUPPLY": {"status": "SUCCESS"},
            "SOME_OPTIONAL_SOURCE": {"status": "FAILED"},
        },
        "fundHistories": [
            {
                "fundCode": "008298",
                "priceBasis": "UNIT_NAV",
                "asOf": points[-1]["date"],
                "sourceUri": "https://fund.eastmoney.com/008298.html",
                "retrievedAt": now.isoformat(),
                "availableAt": now.isoformat(),
                "points": points,
                "calendarProof": {
                    "market": "SSE",
                    "checkedThrough": now.date().isoformat(),
                    "days": [
                        {"date": day.isoformat(), "isTradingDay": day.weekday() < 5}
                        for day in days
                    ],
                },
            }
        ],
    }


def test_partial_with_real_monthly_macro_and_reliable_nav_passes_without_today_observation_date():
    from backend.macro.daily_pipeline_repository import source_quality

    result = source_quality(valid_partial_bundle(), NOW)
    assert result == {
        "allowed": True,
        "status": "PARTIAL",
        "reason": "VALID_PARTIAL_INPUT",
        "macroCount": 1,
        "catalystCount": 0,
        "reliableNavCount": 1,
        "failedSources": ["SOME_OPTIONAL_SOURCE"],
    }


@pytest.mark.parametrize(
    "problem",
    [
        "missing_nav",
        "unproven_nav",
        "future_fact",
        "invalid_source",
        "stale_nav",
        "old_nav_retrieval",
        "wrong_currency",
        "wrong_market",
        "wrong_code",
    ],
)
def test_quality_rejects_unreliable_missing_future_or_stale_evidence(problem):
    from backend.macro.daily_pipeline_repository import source_quality

    bundle = valid_partial_bundle()
    if problem == "missing_nav":
        bundle["fundHistories"] = []
    elif problem == "unproven_nav":
        bundle["fundHistories"][0].pop("calendarProof")
    elif problem == "future_fact":
        bundle["observations"][0]["retrievedAt"] = (
            NOW + timedelta(seconds=1)
        ).isoformat()
    elif problem == "invalid_source":
        bundle["observations"][0]["sourceUri"] = "https://untrusted.test/"
    elif problem == "old_nav_retrieval":
        bundle["fundHistories"][0]["retrievedAt"] = (
            NOW - timedelta(hours=36, seconds=1)
        ).isoformat()
    elif problem == "wrong_currency":
        bundle["fundHistories"][0].update(currency="USD", unit="USD/share")
    elif problem == "wrong_market":
        bundle["fundHistories"][0]["market"] = "US"
    elif problem == "wrong_code":
        bundle["fundHistories"][0]["fundCode"] = "not-a-fund"
    else:
        bundle["fundHistories"][0]["points"] = bundle["fundHistories"][0]["points"][
            :-10
        ]
    assert source_quality(bundle, NOW)["allowed"] is False


def test_future_catalyst_availability_cannot_replace_missing_macro_evidence():
    from backend.macro.daily_pipeline_repository import source_quality

    bundle = valid_partial_bundle()
    bundle["observations"] = []
    bundle["sourceResults"]["CLS_TELEGRAPH"] = {"status": "SUCCESS"}
    bundle["catalysts"] = [
        {
            "sourceKey": "CLS_TELEGRAPH",
            "sourceUri": "https://www.cls.cn/detail/1",
            "title": "产业政策",
            "publishedAt": NOW.isoformat(),
            "retrievedAt": NOW.isoformat(),
            "availableAt": (NOW + timedelta(days=1)).isoformat(),
        }
    ]
    assert source_quality(bundle, NOW)["allowed"] is False


def test_complete_provider_history_cannot_bypass_recent_retrieval_requirement():
    from backend.macro.daily_pipeline_repository import source_quality
    from backend.integrations.nav_history_proof import nav_points_digest

    bundle = valid_partial_bundle()
    history = bundle["fundHistories"][0]
    history["points"] = history["points"][:-1]
    history["asOf"] = history["points"][-1]["date"]
    history["fullHistory"] = True
    history["historyProof"] = {
        "methodVersion": "akshare-unit-nav-full-v1",
        "complete": True,
        "fundCode": history["fundCode"],
        "sourceUri": history["sourceUri"],
        "queryStart": "1900-01-01",
        "queryEnd": NOW.date().isoformat(),
        "pointCount": len(history["points"]),
        "receivedRowCount": len(history["points"]),
        "firstDate": history["points"][0]["date"],
        "lastDate": history["points"][-1]["date"],
        "pointsDigest": nav_points_digest(history["points"]),
    }
    assert source_quality(bundle, NOW)["allowed"] is True
    history["retrievedAt"] = (NOW - timedelta(hours=36, seconds=1)).isoformat()
    assert source_quality(bundle, NOW)["allowed"] is False


def test_full_saved_history_requires_120_samples_inside_the_actual_two_year_window():
    from backend.macro.daily_pipeline_repository import source_quality
    from backend.integrations.nav_history_proof import nav_points_digest

    bundle = valid_partial_bundle()
    history = bundle["fundHistories"][0]
    old = NOW.date().replace(year=2020)
    history["points"] = [
        {"date": (old + timedelta(days=i)).isoformat(), "close": "1"}
        for i in range(119)
    ] + [history["points"][-1]]
    history["fullHistory"] = True
    history["historyProof"] = {
        "methodVersion": "akshare-unit-nav-full-v1",
        "complete": True,
        "fundCode": history["fundCode"],
        "sourceUri": history["sourceUri"],
        "queryStart": "1900-01-01",
        "queryEnd": NOW.date().isoformat(),
        "pointCount": 120,
        "receivedRowCount": 120,
        "firstDate": history["points"][0]["date"],
        "lastDate": history["points"][-1]["date"],
        "pointsDigest": nav_points_digest(history["points"]),
    }
    assert source_quality(bundle, NOW)["allowed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage,age,expected",
    [
        ("COLLECTING", 1, "RUNNING"),
        ("MODEL_RUNNING", 31, "UNCERTAIN"),
        ("WAITING_SOURCES", 121, "MISSED"),
    ],
)
async def test_status_shows_pre_model_progress_expired_lease_and_missed_wait_without_writes(
    stage, age, expected
):
    conn = AsyncMock()
    current = NOW + timedelta(minutes=age)
    conn.fetchval.return_value = {
        "id": "pipeline",
        "state": "RUNNING",
        "stage": stage,
        "scheduled_for": NOW.isoformat(),
        "start_window_ends_at": (NOW + timedelta(hours=2)).isoformat(),
        "runtime_deadline_at": (NOW + timedelta(hours=2)).isoformat(),
        "started_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "finished_at": None,
        "lease_until": (NOW + timedelta(minutes=30)).isoformat(),
        "source_batch_id": "batch",
        "research_run_id": None,
    }
    conn.fetchrow.return_value = None
    result = await daily_repository.read_daily_research(conn, current)
    assert result["state"] == expected
    assert result["pipeline"]["state"] == expected
    assert result["startedAt"] == NOW
    assert result["runId"] is None
    assert result["report"] is None
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_batch_read_never_selects_an_older_ready_batch():
    from backend.macro.alpha_repository import read_alpha_public

    conn = AsyncMock()
    conn.fetchrow.side_effect = [None, None]
    await read_alpha_public(
        conn, NOW, source_batch_id="00000000-0000-0000-0000-000000000001"
    )
    for call in conn.fetchrow.await_args_list:
        assert "id=$3" in call.args[0]
        assert "run_mode='SCHEDULED'" in call.args[0]


@pytest.mark.asyncio
async def test_runtime_never_starts_an_independent_public_loop_with_daily_enabled(
    monkeypatch,
):
    import asyncio
    from backend.jobs import alpha_public, daily_research
    from backend.jobs.alpha_runtime import run_alpha_runtime

    stop = asyncio.Event()
    public = AsyncMock()

    async def daily(*args, **kwargs):
        stop.set()

    monkeypatch.setattr(alpha_public, "run_alpha_public_service", public)
    monkeypatch.setattr(daily_research, "run_daily_research_service", daily)
    await run_alpha_runtime(
        object(),
        SimpleNamespace(daily_research_enabled=True, alpha_public_enabled=True),
        stop_event=stop,
    )
    public.assert_not_awaited()


def isolate_pipeline(monkeypatch, *, batch_state="READY", quality_allowed=True):
    from backend.jobs import daily_research as job, alpha_public
    from backend.macro import daily_pipeline_repository as repo, alpha_repository

    @asynccontextmanager
    async def connection(pool):
        yield object()

    stages = []

    async def stage(pool, claim, **fields):
        stages.append(fields)
        claim["stage"] = fields["stage"]

    monkeypatch.setattr(job, "system_connection", connection)
    monkeypatch.setattr(job, "consumed", AsyncMock(return_value=False))
    monkeypatch.setattr(job, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(job, "_pipeline_stage", stage)
    monkeypatch.setattr(repo, "read_pipeline", AsyncMock(return_value=None))
    monkeypatch.setattr(
        repo,
        "claim_pipeline",
        AsyncMock(
            return_value={
                "id": "pipeline",
                "lease_token": "token",
                "stage": "COLLECTING",
                "scheduled_for": NOW,
                "started_at": NOW,
                "start_window_ends_at": NOW + timedelta(hours=2),
                "runtime_deadline_at": NOW + timedelta(hours=2),
            }
        ),
    )
    monkeypatch.setattr(
        repo,
        "scheduled_source_batch",
        AsyncMock(
            side_effect=[
                None,
                {
                    "id": "batch",
                    "run_date": NOW.date(),
                    "status": batch_state,
                    "claimed_at": NOW - timedelta(minutes=20),
                    "completed_at": None if batch_state == "CLAIMED" else NOW,
                },
            ]
        ),
    )
    monkeypatch.setattr(repo, "source_quality", lambda *_: {"allowed": quality_allowed})
    monkeypatch.setattr(repo, "finish_source_wait_window", AsyncMock())
    monkeypatch.setattr(
        alpha_repository,
        "read_alpha_public",
        AsyncMock(return_value={"collectionState": batch_state}),
    )
    monkeypatch.setattr(
        alpha_public, "sync_alpha_public", AsyncMock(return_value={"status": "SKIPPED"})
    )
    monkeypatch.setattr(job, "collect_daily_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(job, "prepare_research_inputs", AsyncMock(return_value={}))
    monkeypatch.setattr(
        job, "load_public_packet", AsyncMock(return_value={"asOf": NOW.isoformat()})
    )
    monkeypatch.setattr(
        job, "_execute_daily_research", AsyncMock(return_value={"status": "READY"})
    )
    return (
        job,
        stages,
        SimpleNamespace(
            daily_research_enabled=True,
            alpha_public_enabled=False,
            daily_research_schedule_mode="WEEKDAYS",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,allowed,expected",
    [
        ("CLAIMED", True, "WAITING_PUBLIC_DATA"),
        ("FAILED", True, "FAILED"),
        ("PARTIAL", False, "FAILED"),
    ],
)
async def test_pending_failed_or_empty_partial_sources_never_dispatch_even_after_ten_minutes(
    monkeypatch, state, allowed, expected
):
    job, stages, settings = isolate_pipeline(
        monkeypatch, batch_state=state, quality_allowed=allowed
    )
    result = await job.sync_daily_research(object(), settings, NOW)
    assert result["status"] == expected
    job._execute_daily_research.assert_not_awaited()
    job.load_public_packet.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_main_switch_forces_collection_and_binds_frozen_batch(monkeypatch):
    from backend.jobs import alpha_public

    job, stages, settings = isolate_pipeline(monkeypatch)
    result = await job.sync_daily_research(object(), settings, NOW)
    assert result["status"] == "READY"
    alpha_public.sync_alpha_public.assert_awaited_once()
    assert job.prepare_research_inputs.await_args.kwargs["source_batch_id"] == "batch"
    assert job.load_public_packet.await_args.kwargs["source_batch_id"] == "batch"
    assert (
        job._execute_daily_research.await_args.kwargs["packet"]["sourceBatchId"]
        == "batch"
    )
    assert job._execute_daily_research.await_args.kwargs["source_batch_id"] == "batch"
    assert stages[-1]["state"] == "READY"


@pytest.mark.asyncio
async def test_existing_scheduled_model_slot_skips_without_creating_a_new_pipeline(
    monkeypatch,
):
    from backend.jobs import alpha_public
    from backend.macro import daily_pipeline_repository as repo

    job, _, settings = isolate_pipeline(monkeypatch)
    monkeypatch.setattr(job, "consumed", AsyncMock(return_value=True))
    assert (await job.sync_daily_research(object(), settings, NOW))[
        "status"
    ] == "SKIPPED"
    repo.claim_pipeline.assert_not_awaited()
    alpha_public.sync_alpha_public.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stamp,status",
    [
        (NOW - timedelta(minutes=1), "NOT_DUE"),
        (NOW + timedelta(hours=2), "MISSED"),
        (NOW + timedelta(days=1), "NON_WORKDAY"),
    ],
)
async def test_schedule_never_collects_before_due_after_window_or_on_weekend(
    monkeypatch, stamp, status
):
    from backend.jobs import alpha_public

    job, _, settings = isolate_pipeline(monkeypatch)
    assert (await job.sync_daily_research(object(), settings, stamp))[
        "status"
    ] == status
    alpha_public.sync_alpha_public.assert_not_awaited()
