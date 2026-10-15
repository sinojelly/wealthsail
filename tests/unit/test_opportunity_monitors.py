"""Personal opt-in monitoring records baseline and semantic condition changes."""

from importlib import import_module, util
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock
import json

import pytest

NOW = datetime(2026, 10, 14, 18, tzinfo=ZoneInfo("Asia/Shanghai"))


def repository():
    assert util.find_spec("backend.investment.decision_repository"), (
        "private monitoring repository missing"
    )
    return import_module("backend.investment.decision_repository")


def result(state="WAIT", retrieved_at="old"):
    return {
        "decision": {
            "state": state,
            "marketState": state,
            "personalState": "NOT_HELD",
            "gates": [{"id": "validation", "state": "UNKNOWN"}],
            "validation": {"retrievedAt": retrieved_at},
        },
        "portfolio": {
            "state": "READY",
            "asOf": "2026-10-14",
            "noticeBudget": 5,
            "silenceMinutes": 5,
            "heldSelected": state == "POSITION_REVIEW",
            "budgetViolation": state == "POSITION_REVIEW",
            "budgetState": "BLOCKED" if state == "POSITION_REVIEW" else "UNKNOWN",
        },
        "notices": [],
    }


def test_refresh_timestamps_do_not_change_monitor_condition_fingerprint():
    repo = repository()
    assert repo.condition_fingerprint(
        result(retrieved_at="old")
    ) == repo.condition_fingerprint(result(retrieved_at="new"))
    assert repo.condition_fingerprint(result("WAIT")) != repo.condition_fingerprint(
        result("ENTRY_CANDIDATE")
    )


@pytest.mark.asyncio
async def test_same_code_in_two_markets_is_not_inferred_as_a_personal_holding():
    repo = repository()
    conn = AsyncMock()
    conn.fetch.return_value = [{"instrument_id": "one"}, {"instrument_id": "two"}]
    assert await repo.resolve_instrument(conn, None, "510300", None) is None
    conn.fetch.return_value = [{"instrument_id": "one"}]
    assert await repo.resolve_instrument(conn, None, "510300", "SSE") == "one"


@pytest.mark.asyncio
async def test_explicit_identity_cannot_ignore_contradictory_code_or_market():
    repo = repository()
    conn = AsyncMock()
    conn.fetchval.return_value = None
    assert await repo.resolve_instrument(conn, "instrument", "510300", "SZSE") is None
    call = conn.fetchval.await_args
    assert "510300" in call.args and "SZSE" in call.args
    assert "primary_market" in call.args[0] and "research_fund_catalog" in call.args[0]


@pytest.mark.asyncio
async def test_first_monitor_snapshot_is_baseline_and_has_no_notice():
    repo = repository()
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = "snapshot"
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW,
    }
    conn.fetchrow.side_effect = [monitor, None]
    output = await repo.record_monitor_result(conn, monitor, result(), NOW)
    assert output["baseline"] is True and output["notice"] is False
    queries = " ".join(
        call.args[0]
        for call in conn.execute.await_args_list + conn.fetchval.await_args_list
    )
    assert "INSERT INTO risk.opportunity_condition_notices" not in queries
    assert "outbox" not in queries


@pytest.mark.asyncio
async def test_changed_conditions_write_only_actor_notice_with_before_after():
    repo = repository()
    conn = AsyncMock()
    prior = result()
    conn.fetchrow.return_value = {
        "condition_fingerprint": repo.condition_fingerprint(prior),
        "result": prior,
        "as_of": NOW,
    }
    conn.fetchval.side_effect = ["snapshot", "notice"]
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW - timedelta(hours=1),
    }
    conn.fetchrow.side_effect = [
        monitor,
        {
            "condition_fingerprint": repo.condition_fingerprint(prior),
            "result": prior,
            "as_of": NOW,
        },
        {"notice_count": 0, "last_notice": None},
    ]
    output = await repo.record_monitor_result(
        conn, monitor, result("POSITION_REVIEW"), NOW + timedelta(minutes=15)
    )
    assert output["notice"] is True
    notice_call = [
        call
        for call in conn.fetchval.await_args_list
        if "INSERT INTO risk.opportunity_condition_notices" in call.args[0]
    ][0]
    assert "tenant" in notice_call.args and "actor" in notice_call.args
    assert "CRITICAL" in notice_call.args


@pytest.mark.asyncio
async def test_disabled_or_other_actors_notice_cannot_be_acknowledged():
    repo = repository()
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    from backend.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await repo.ack_notice(conn, "tenant", "actor", "notice", NOW)
    sql = conn.fetchrow.await_args.args[0]
    assert "tenant_id=$1" in sql and "user_id=$2" in sql


def test_same_transition_after_recovery_is_a_distinct_episode():
    repo = repository()
    prior_a = {"id": "episode-one", "result": result("WAIT")}
    prior_b = {"id": "episode-two", "result": result("WAIT")}
    assert repo.transition_fingerprint(
        prior_a, result("ENTRY_CANDIDATE")
    ) != repo.transition_fingerprint(prior_b, result("ENTRY_CANDIDATE"))
    assert repo.transition_fingerprint(
        prior_a, result("ENTRY_CANDIDATE")
    ) == repo.transition_fingerprint(prior_a, result("ENTRY_CANDIDATE"))


@pytest.mark.asyncio
async def test_silence_uses_previous_notice_time_not_last_poll():
    repo = repository()
    conn = AsyncMock()
    prior = {
        "id": "prior",
        "condition_fingerprint": repo.condition_fingerprint(result()),
        "result": result(),
        "as_of": NOW - timedelta(minutes=1),
    }
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW - timedelta(days=1),
    }
    conn.fetchrow.side_effect = [
        monitor,
        prior,
        {"notice_count": 0, "last_notice": NOW - timedelta(hours=2)},
    ]
    conn.fetchval.side_effect = ["snapshot", "notice"]
    assert (
        await repo.record_monitor_result(conn, monitor, result("POSITION_REVIEW"), NOW)
    )["notice"] is True


@pytest.mark.asyncio
async def test_scoped_notice_lookup_matches_null_identity_and_market_exactly():
    repo = repository()
    conn = AsyncMock()
    conn.fetch.return_value = []
    await repo.list_notices(conn, "tenant", "actor", index_id="hs300", exact_scope=True)
    sql = conn.fetch.await_args.args[0]
    assert "IS NOT DISTINCT FROM" in sql and "IS NULL OR m." not in sql
    assert conn.fetch.await_args.args[-2:] == (None, None)


@pytest.mark.asyncio
async def test_notice_summary_uses_user_visible_chinese_state_labels():
    repo = repository()
    conn = AsyncMock()
    prior = {
        "id": "prior",
        "condition_fingerprint": repo.condition_fingerprint(result()),
        "result": result(),
        "as_of": NOW,
    }
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW,
    }
    conn.fetchrow.side_effect = [
        monitor,
        prior,
        {"notice_count": 0, "last_notice": None},
    ]
    conn.fetchval.side_effect = ["snapshot", "notice"]
    await repo.record_monitor_result(conn, monitor, result("HOLD_REVIEW"), NOW)
    call = [
        item
        for item in conn.fetchval.await_args_list
        if "INSERT INTO risk.opportunity_condition_notices" in item.args[0]
    ][0]
    summary = call.args[8]
    assert "等待" in summary and "持仓观察" in summary and "HOLD_REVIEW" not in summary


def research_result(priority="ATTENTION", delta=20, missing=None):
    return {
        **result(),
        "radar": {
            "asOf": "old",
            "changes": [
                {
                    "id": "rate-change:US_T10_REAL",
                    "priority": priority,
                    "kind": "RISK",
                    "evidenceIds": ["US_T10_REAL"],
                    "delta": delta,
                    "retrievedAt": "old",
                    "summary": f"利率变化{delta}bp",
                },
                {
                    "id": "ordinary",
                    "priority": "INFO",
                    "kind": "OBSERVATION",
                    "evidenceIds": ["PMI"],
                    "delta": 1,
                },
            ],
        },
        "directionModel": {"missingEvidence": missing or ["企业盈利", "美元汇率"]},
    }


def test_waiting_state_tracks_important_evidence_semantics_and_stable_gaps():
    repo = repository()
    attention = research_result()
    critical = research_result("CRITICAL", 45)
    assert repo.condition_fingerprint(attention) != repo.condition_fingerprint(critical)
    assert repo.condition_fingerprint(attention) != repo.condition_fingerprint(
        research_result(missing=["企业盈利"])
    )
    quiet = research_result(delta=21, missing=["美元汇率", "企业盈利", "企业盈利"])
    quiet["radar"]["asOf"] = "new"
    quiet["radar"]["changes"][0]["retrievedAt"] = "new"
    quiet["radar"]["changes"].reverse()
    assert repo.condition_fingerprint(attention) == repo.condition_fingerprint(quiet)
    for field, value in (
        ("kind", "GAP"),
        ("id", "rate-change:US_T10_NOMINAL"),
        ("evidenceIds", ["US_T10_NOMINAL"]),
    ):
        different = research_result()
        different["radar"]["changes"][0][field] = value
        assert repo.condition_fingerprint(attention) != repo.condition_fingerprint(
            different
        )


@pytest.mark.asyncio
async def test_important_wait_to_wait_notice_persists_only_semantic_summary_and_recovers():
    repo = repository()
    old = research_result()
    changed = research_result("CRITICAL", 45)
    changed["series"] = [{"privateHistory": "must not persist"}]
    conn = AsyncMock()
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW - timedelta(days=1),
    }
    prior = {
        "id": "prior",
        "condition_fingerprint": repo.condition_fingerprint(old),
        "result": old,
        "as_of": NOW - timedelta(minutes=15),
    }
    conn.fetchrow.side_effect = [
        monitor,
        prior,
        {"notice_count": 0, "last_notice": None},
    ]
    conn.fetchval.side_effect = ["snapshot", "notice"]
    assert (await repo.record_monitor_result(conn, monitor, changed, NOW))[
        "notice"
    ] is True
    snapshot_call = [
        call
        for call in conn.fetchval.await_args_list
        if "INSERT INTO risk.opportunity_decision_snapshots" in call.args[0]
    ][0]
    saved = json.loads(snapshot_call.args[-1])
    assert set(saved) == {"decision", "portfolio", "researchConditions"}
    assert repo.condition_fingerprint(saved) == repo.condition_fingerprint(changed)
    assert "privateHistory" not in snapshot_call.args[
        -1
    ] and "retrievedAt" not in json.dumps(saved["researchConditions"])
    assert saved["researchConditions"]["importantChanges"][0] == {
        "id": "rate-change:US_T10_REAL",
        "priority": "CRITICAL",
        "kind": "RISK",
        "evidenceIds": ["US_T10_REAL"],
    }
    notice_call = [
        call
        for call in conn.fetchval.await_args_list
        if "INSERT INTO risk.opportunity_condition_notices" in call.args[0]
    ][0]
    assert (
        "研究证据" in notice_call.args[8]
        and "当前仍等待" in notice_call.args[8]
        and "等待 → 等待" not in notice_call.args[8]
    )
    recovered = {
        "id": "persisted",
        "condition_fingerprint": repo.condition_fingerprint(saved),
        "result": saved,
        "as_of": NOW,
    }
    resumed = AsyncMock()
    resumed.fetchrow.side_effect = [monitor, recovered]
    assert not (
        await repo.record_monitor_result(
            resumed,
            monitor,
            research_result("CRITICAL", 46),
            NOW + timedelta(minutes=15),
        )
    )["notice"]
    resumed.fetchval.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["budget", "silence"])
async def test_important_research_change_still_requires_confirmed_budget_and_silence(
    blocked,
):
    repo = repository()
    changed = research_result("CRITICAL", 45)
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW - timedelta(days=1),
    }
    prior = {
        "id": "prior",
        "condition_fingerprint": repo.condition_fingerprint(research_result()),
        "result": research_result(),
        "as_of": NOW - timedelta(minutes=15),
    }
    conn = AsyncMock()
    conn.fetchval.return_value = "snapshot"
    conn.fetchrow.side_effect = [
        monitor,
        prior,
        {"notice_count": 0, "last_notice": NOW - timedelta(minutes=1)},
    ]
    if blocked == "budget":
        changed["portfolio"].pop("noticeBudget")
    output = await repo.record_monitor_result(conn, monitor, changed, NOW)
    assert not output["notice"]
    assert output["reason"] == (
        "CONFIRMED_NOTICE_BUDGET_REQUIRED"
        if blocked == "budget"
        else "BUDGET_OR_SILENCE"
    )
    assert not any(
        "INSERT INTO risk.opportunity_condition_notices" in call.args[0]
        for call in conn.fetchval.await_args_list
    )


def board_result(severity="MEDIUM", *, state="WAIT"):
    return {
        **research_result(),
        "decision": result(state)["decision"],
        "portfolio": result(state)["portfolio"],
        "researchBoard": {
            "methodVersion": "research-board-v1",
            "asOf": "old",
            "items": [
                {
                    "id": "rate:real",
                    "key": "rate:US_T10_REAL",
                    "category": "RISK",
                    "severity": severity,
                    "confidence": "PARTIAL",
                    "tone": "ORANGE",
                    "evidenceIds": ["US_T10_REAL"],
                    "summary": "利率变化20bp",
                    "readAt": None,
                },
                {
                    "id": "missing-m1",
                    "key": "missing:M1",
                    "category": "DATA_GAP",
                    "severity": "HIGH",
                    "confidence": "UNKNOWN",
                    "evidenceIds": ["M1_YOY"],
                },
                {
                    "id": "source-failure",
                    "key": "source:PBOC",
                    "category": "SYSTEM",
                    "severity": "HIGH",
                    "confidence": "UNKNOWN",
                    "evidenceIds": ["PBOC_M1"],
                },
                {
                    "id": "ordinary",
                    "key": "ordinary",
                    "category": "CHANGE",
                    "severity": "LOW",
                    "confidence": "VERIFIED",
                    "evidenceIds": ["PMI"],
                },
            ],
            "dataStatus": {"itemIds": ["missing-m1", "source-failure"]},
        },
        "directionModel": {
            "missingRequiredEvidence": ["FX_USD"],
            "missingEvidence": ["美元汇率"],
        },
    }


def test_new_board_semantics_ignore_legacy_labels_data_status_copy_sort_and_ack():
    repo = repository()
    current = board_result()
    semantic = repo.research_conditions(current)
    assert semantic["methodVersion"] == "research-board-v1"
    assert [row["id"] for row in semantic["importantItems"]] == ["rate:real"]
    assert semantic["missingRequiredEvidence"] == ["FX_USD"]
    quiet = board_result()
    quiet["researchBoard"]["items"][0].update(
        summary="利率变化21bp", readAt="now", status="READ", firstSeenAt="now"
    )
    quiet["researchBoard"]["items"][1]["summary"] = "来源仍未接入"
    quiet["researchBoard"]["items"].reverse()
    quiet["researchBoard"]["asOf"] = "new"
    quiet["directionModel"]["missingEvidence"] = ["新中文文案"]
    quiet["directionModel"]["missingRequiredEvidence"] = ["FX_USD", "FX_USD"]
    quiet["radar"]["changes"][0]["priority"] = "CRITICAL"
    assert repo.condition_fingerprint(quiet) == repo.condition_fingerprint(current)
    for field, value in (
        ("severity", "HIGH"),
        ("confidence", "VERIFIED"),
        ("category", "OPPORTUNITY"),
        ("evidenceIds", ["US_T10_NOMINAL"]),
    ):
        changed = board_result()
        changed["researchBoard"]["items"][0][field] = value
        assert repo.condition_fingerprint(changed) != repo.condition_fingerprint(
            current
        )
    changed = board_result()
    changed["directionModel"]["missingRequiredEvidence"] = ["INDEX_EARNINGS"]
    assert repo.condition_fingerprint(changed) != repo.condition_fingerprint(current)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_kind",
    ["legacy-radar", "unversioned-snapshot", "unversioned-board", "empty-old"],
)
async def test_semantic_upgrade_persists_baseline_without_notice_then_same_version_changes_notify(
    old_kind,
):
    repo = repository()
    old = research_result() if old_kind == "legacy-radar" else result()
    if old_kind == "unversioned-snapshot":
        old["researchConditions"] = {
            "importantChanges": [
                {"id": "old", "priority": "CRITICAL", "kind": "RISK", "evidenceIds": []}
            ],
            "missingEvidence": [],
        }
    if old_kind == "unversioned-board":
        old["researchConditions"] = {
            "importantItems": [],
            "missingRequiredEvidence": [],
        }
    prior = {
        "id": "old-snapshot",
        "condition_fingerprint": repo.condition_fingerprint(old),
        "result": old,
        "as_of": NOW - timedelta(minutes=15),
    }
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW - timedelta(days=1),
    }
    conn = AsyncMock()
    conn.fetchrow.side_effect = [monitor, prior]
    conn.fetchval.return_value = "new-baseline"
    current = board_result()
    output = await repo.record_monitor_result(conn, monitor, current, NOW)
    assert output["baseline"] and not output["notice"]
    assert conn.fetchrow.await_count == 2  # no budget consumption for a model upgrade
    saved = json.loads(conn.fetchval.await_args.args[-1])
    assert (
        saved["researchConditions"]["methodVersion"] == "research-board-v1"
        and "researchBoard" not in saved
    )
    assert repo.condition_fingerprint(saved) == repo.condition_fingerprint(current)
    resumed = AsyncMock()
    resumed.fetchrow.side_effect = [
        monitor,
        {
            "id": "new-baseline",
            "condition_fingerprint": repo.condition_fingerprint(saved),
            "result": saved,
            "as_of": NOW,
        },
        {"notice_count": 0, "last_notice": None},
    ]
    resumed.fetchval.side_effect = ["new-snapshot", "notice"]
    assert (
        await repo.record_monitor_result(
            resumed, monitor, board_result("HIGH"), NOW + timedelta(minutes=15)
        )
    )["notice"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_board_notice_is_critical_only_for_confirmed_high_holding_budget_breach(
    confirmed,
):
    repo = repository()
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW - timedelta(days=1),
    }
    old = board_result()
    current = board_result("HIGH", state="POSITION_REVIEW")
    if not confirmed:
        current["portfolio"].update(
            heldSelected=None,
            budgetViolation=False,
            budgetState="UNKNOWN",
            state="UNKNOWN",
        )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        monitor,
        {
            "id": "old",
            "condition_fingerprint": repo.condition_fingerprint(old),
            "result": old,
            "as_of": NOW - timedelta(minutes=15),
        },
        {"notice_count": 0, "last_notice": None},
    ]
    conn.fetchval.side_effect = ["snapshot", "notice"]
    assert (await repo.record_monitor_result(conn, monitor, current, NOW))["notice"]
    call = [
        call
        for call in conn.fetchval.await_args_list
        if "INSERT INTO risk.opportunity_condition_notices" in call.args[0]
    ][0]
    assert call.args[9] == ("CRITICAL" if confirmed else "WATCH")


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["budget", "silence"])
async def test_board_semantic_change_still_respects_confirmed_budget_and_silence(
    blocked,
):
    repo = repository()
    monitor = {
        "id": "monitor",
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled_at": NOW - timedelta(days=1),
    }
    prior = board_result()
    current = board_result("HIGH")
    if blocked == "budget":
        current["portfolio"].pop("noticeBudget")
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        monitor,
        {
            "id": "old",
            "condition_fingerprint": repo.condition_fingerprint(prior),
            "result": prior,
            "as_of": NOW - timedelta(minutes=15),
        },
        {"notice_count": 0, "last_notice": NOW - timedelta(minutes=1)},
    ]
    conn.fetchval.return_value = "snapshot"
    output = await repo.record_monitor_result(conn, monitor, current, NOW)
    assert not output["notice"] and not output["baseline"]
    assert output["reason"] == (
        "CONFIRMED_NOTICE_BUDGET_REQUIRED"
        if blocked == "budget"
        else "BUDGET_OR_SILENCE"
    )
