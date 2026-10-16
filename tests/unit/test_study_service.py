"""Study commands load their own facts and guard optimistic generations."""

from datetime import datetime
from importlib import import_module, util
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.errors import NotFoundError, VersionConflictError

NOW = datetime(2026, 10, 17, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def repository():
    assert util.find_spec("backend.investment.study_repository"), (
        "study repository missing"
    )
    return import_module("backend.investment.study_repository")


@pytest.mark.asyncio
async def test_report_origin_must_be_a_visible_ready_report_and_exact_event():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with pytest.raises(NotFoundError):
        await repository().daily_origin(conn, uuid4(), "unknown", NOW)
    query = conn.fetchrow.await_args.args[0]
    assert "status='READY'" in query and "recorded_at<=$2" in query
    conn.fetchrow.return_value = {
        "run_id": uuid4(),
        "recorded_at": NOW,
        "payload": {
            "events": [
                {"id": "original", "title": "原始事件", "sourceIds": ["source:one"]}
            ],
            "sources": [{"id": "source:one", "url": "https://www.eia.gov/example"}],
        },
    }
    origin = await repository().daily_origin(conn, uuid4(), "original", NOW)
    assert origin["event"]["title"] == "原始事件"
    assert len(origin["sources"]) == 1
    with pytest.raises(NotFoundError):
        await repository().daily_origin(conn, uuid4(), "model-rewritten-id", NOW)


@pytest.mark.asyncio
async def test_generation_guard_rejects_late_writer_and_missing_actor():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with pytest.raises(NotFoundError):
        await repository().lock_track(conn, uuid4(), uuid4(), uuid4(), 1)
    conn.fetchrow.return_value = {"generation": 2, "enabled": True}
    with pytest.raises(VersionConflictError):
        await repository().lock_track(conn, uuid4(), uuid4(), uuid4(), 1)


@pytest.mark.asyncio
async def test_eligible_trades_require_mine_confirmed_and_explicit_instrument_identity():
    conn = AsyncMock()
    conn.fetch.return_value = []
    assert (
        await repository().eligible_trades(
            conn, uuid4(), uuid4(), {"instrumentId": str(uuid4())}, NOW
        )
        == []
    )
    query, *args = conn.fetch.await_args.args
    assert "CONFIRMED" in query
    assert "BENEFICIAL_OWNER" in query and "JOINT_OWNER" in query
    assert "owner.user_id=$2" in query
    assert "f.instrument_id" in query
    assert "created_by=$2" not in query


@pytest.mark.asyncio
async def test_direction_scope_filters_trade_candidates_and_read_does_not_lock_head(
    monkeypatch,
):
    from backend.investment import study_service as service

    conn = AsyncMock()
    conn.fetch.return_value = []
    await repository().eligible_trades(
        conn, uuid4(), uuid4(), {"directionId": "a-share:broad"}, NOW
    )
    assert "direction_index_links" in conn.fetch.await_args.args[0]
    assert conn.fetch.await_args.args[-1] == "a-share:broad"
    monkeypatch.setattr(
        repository(),
        "latest_version",
        AsyncMock(return_value={"snapshot": {"scope": {}}}),
    )
    monkeypatch.setattr(repository(), "eligible_trades", AsyncMock(return_value=[]))
    await service.eligible_trades(conn, uuid4(), uuid4(), uuid4(), NOW)
    assert not any("FOR UPDATE" in str(call) for call in conn.fetchrow.await_args_list)


def test_unread_counts_semantic_changes_not_the_initial_baseline():
    assert "meaningfulChange" in repository()._TRACK_PROJECTION


def test_selection_explanations_preserve_whitelisted_text_and_memory_drives_changes():
    from backend.investment import study_service as service

    assert service.explanation_texts(
        [
            {"label": "渠道", "summary": "真实依据", "private": "omit"},
            "文本",
            {"private": "omit"},
        ]
    ) == ["真实依据", "文本"]
    assert service.meaningful_change(
        {"state": "TRIGGERED"},
        {
            "baseline": False,
            "state": "TRIGGERED",
            "conditionResults": [{"result": "TRUE", "previousResult": "FALSE"}],
        },
    )
