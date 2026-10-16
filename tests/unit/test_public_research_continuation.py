"""Public continuation is bounded, source attributed and never actor research."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from test_daily_research import NOW, engine


def saved(age=1, **updates):
    stamp = NOW - timedelta(days=age)
    return {
        "runId": f"ready-{age}",
        "completedAt": stamp.isoformat(),
        "slotDate": stamp.date().isoformat(),
        "status": "READY",
        "report": {
            "methodVersion": "daily-research-v1",
            "asOf": stamp.isoformat(),
            "sources": [
                {
                    "id": "s",
                    "url": "https://www.eia.gov/todayinenergy/item.php?id=1",
                    "title": "公共原文",
                    "quote": "公共短引文",
                    "retrievedAt": stamp.isoformat(),
                }
            ],
            "events": [
                {
                    "id": f"event:{age}",
                    "title": "公开主题",
                    "summary": "原始条件论点",
                    "phase": "ANNOUNCED",
                    "importance": "HIGH",
                    "sourceIds": ["s"],
                    "affectedDirectionIds": ["a-share:broad"],
                    "channels": ["传导待核实"],
                    "scenarios": [
                        {
                            "condition": "正式实施",
                            "impact": "需观察",
                            "counter": "未实施",
                        }
                    ],
                    "watchFor": ["正式实施公告"],
                    "unknowns": ["实际价格传导"],
                    "privateNotes": "SECRET",
                    "holdings": {"amount": 123},
                }
            ],
            "privateNotes": "SECRET",
            "studyTracks": [{"user": "SECRET"}],
        },
        **updates,
    }


def packet(history):
    return engine().make_research_packet(
        {}, [], [], [], now=NOW, previous={"history": history}
    )


def test_public_continuation_keeps_original_identity_conditions_and_no_private_fields():
    result = packet([saved()])
    item = result["previousResearch"][0]
    assert item["reportRunId"] == "ready-1" and item["eventId"] == "event:1"
    assert item["researchedAt"] == (NOW - timedelta(days=1)).isoformat()
    assert item["summary"] == "原始条件论点"
    assert item["scenarios"][0]["counter"] == "未实施"
    assert item["sources"][0]["url"].startswith("https://www.eia.gov/")
    assert "SECRET" not in str(result) and "123" not in str(result)


def test_only_saved_ready_visible_recent_public_reports_and_bounded_events_are_used():
    future = saved(-1)
    failed = saved(1, status="FAILED")
    old = saved(15)
    result = packet([future, failed, old] + [saved(age) for age in range(1, 15)])
    rows = result["previousResearch"]
    assert 8 <= len(rows) <= 12
    assert all(row["reportRunId"] not in {"ready--1", "ready-15"} for row in rows)
    assert rows[0]["reportRunId"] == "ready-1"


def test_older_event_requires_public_unresolved_conditions_and_plans_are_next_seven_days():
    record = saved(10)
    record["report"]["events"][0]["watchFor"] = []
    record["report"]["events"][0]["unknowns"] = []
    record["report"]["upcoming"] = [
        {
            "id": f"p:{days}",
            "title": "公开日程",
            "phase": "PLANNED",
            "sourceIds": ["s"],
            "scheduledDate": (NOW + timedelta(days=days)).date().isoformat(),
            "watchFor": ["真实公布"],
        }
        for days in (-1, 3, 8)
    ]
    result = packet([record])
    assert result["previousResearch"] == []
    assert [row["eventId"] for row in result["previousUpcoming"]] == ["p:3"]


def test_exact_event_repeated_across_reports_does_not_spend_continuation_quota_twice():
    recent, older = saved(1), saved(2)
    older["report"]["events"][0]["id"] = "event:1"
    result = packet([older, recent])
    assert len(result["previousResearch"]) == 1
    assert result["previousResearch"][0]["reportRunId"] == "ready-1"


def test_runtime_history_empty_does_not_forward_questions_from_expired_fallback():
    previous = {"history": [], "report": saved(30)["report"]}
    result = engine().make_research_packet({}, [], [], [], now=NOW, previous=previous)
    assert result["previousResearch"] == []
    assert result["previousQuestions"] == []


def test_candidate_audit_index_is_bounded_and_not_copied_into_model_prompt():
    external = {
        "candidateIndex": [
            {
                "id": f"candidate-{i}",
                "url": f"https://www.eia.gov/item?id={i}",
                "title": "PUBLIC_CANDIDATE",
                "quote": "PRIVATE_FULL_BODY",
                "sourceKey": "EIA_NEWS",
                "retrievedAt": NOW.isoformat(),
                "personalNotes": "SECRET",
            }
            for i in range(45)
        ],
        "documents": [],
    }
    result = engine().make_research_packet({}, [], [], [], now=NOW, external=external)
    assert len(result["candidateIndex"]) == 40
    assert "PRIVATE_FULL_BODY" not in str(result) and "SECRET" not in str(result)
    assert "PUBLIC_CANDIDATE" not in engine().build_prompt(result)


@pytest.mark.asyncio
async def test_job_loads_only_public_ready_history_and_keeps_latest_attempt_separate(
    monkeypatch,
):
    from backend.jobs import daily_research as job
    from backend.investment import repository, service
    from backend.macro import market_repository

    monkeypatch.setattr(
        service, "market_context", AsyncMock(return_value={"items": []})
    )
    monkeypatch.setattr(repository, "directions", AsyncMock(return_value=[]))
    for name, value in (
        ("read_permissions", {}),
        ("read_collection", {}),
        ("read_series", []),
    ):
        monkeypatch.setattr(market_repository, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        job,
        "read_daily_research",
        AsyncMock(return_value={"state": "FAILED", "runId": "failed", "report": None}),
    )
    monkeypatch.setattr(
        job, "read_recent_public_research", AsyncMock(return_value=[saved()])
    )
    conn = AsyncMock()
    conn.fetch.return_value = []
    result = await job.load_public_packet(conn, NOW, {})
    assert result["previousResearch"][0]["reportRunId"] == "ready-1"
    job.read_recent_public_research.assert_awaited_once_with(conn, NOW)
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_recent_reader_queries_only_ready_public_history_with_time_fences():
    from backend.macro import daily_repository as repository

    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "run_id": "ready-1",
            "slot_date": (NOW - timedelta(days=1)).date(),
            "recorded_at": NOW - timedelta(days=1),
            "payload": saved()["report"],
        }
    ]
    result = await repository.read_recent_public_research(conn, NOW)
    sql, *args = conn.fetch.call_args.args
    assert "t.status='READY'" in sql and "t.recorded_at<=$2" in sql
    assert "r.dispatched_at<=$2" in sql and "t.recorded_at>=$3" in sql
    assert "study_" not in sql and "tenant" not in sql
    assert args[2] == NOW - timedelta(days=14)
    assert result[0]["status"] == "READY"
    assert result[0]["runId"] == "ready-1"
    conn.execute.assert_not_awaited()
