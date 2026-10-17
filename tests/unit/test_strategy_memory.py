"""Bounded review memory counts judgments without publishing private prose."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib import import_module, util
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest


NOW = datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
TENANT, ACTOR = uuid4(), uuid4()


def memory():
    assert util.find_spec("backend.investment.strategy_memory"), (
        "structured review memory is missing"
    )
    return import_module("backend.investment.strategy_memory")


def review(track, verdict, categories=(), *, minutes=0, lessons=True, **changes):
    return {
        "id": str(uuid4()),
        "track_id": track,
        "version_id": str(uuid4()),
        "tenant_id": TENANT,
        "user_id": ACTOR,
        "theme_code": "UNCLASSIFIED",
        "verdict": verdict,
        "error_categories": list(categories),
        "has_lessons": lessons,
        "created_at": NOW - timedelta(minutes=minutes),
        **changes,
    }


def aggregate(rows):
    return memory().aggregate_reviews(rows, TENANT, ACTOR, "UNCLASSIFIED", NOW)


def rule(rows, category):
    return next(row for row in rows if row["category"] == category)


def fill(track, side, *, price="10", quantity="2", fee="1", days=2, **changes):
    return {
        "fill_id": str(uuid4()),
        "track_id": track,
        "version_id": str(uuid4()),
        "tenant_id": TENANT,
        "user_id": ACTOR,
        "account_id": "mine-account",
        "instrument_id": "same-instrument",
        "currency": "CNY",
        "side": side,
        "quantity": quantity,
        "unit_price": price,
        "fee": fee,
        "trade_date": (NOW - timedelta(days=days)).date(),
        "confirmed_at": NOW - timedelta(hours=1),
        "status": "CONFIRMED",
        "ledger_event_id": str(uuid4()),
        "ledger_verified": True,
        "access_verified": True,
        "shared_track_count": 1,
        **changes,
    }


def closed(track, *, sell="12"):
    return [fill(track, "BUY"), fill(track, "SELL", price=sell, days=1)]


@pytest.mark.parametrize("sell,want", [("12", "2"), ("10", "-2"), ("11", "0")])
def test_realized_roundtrip_uses_actual_closed_quantity_and_both_fees(sell, want):
    module = memory()
    assert hasattr(module, "realized_roundtrip"), (
        "confirmed roundtrip calculation missing"
    )
    assert module.realized_roundtrip(closed("track", sell=sell), NOW) == Decimal(want)


@pytest.mark.parametrize(
    "problem",
    [
        "only_buy",
        "partial",
        "same_day",
        "sell_before_buy",
        "different_account",
        "different_instrument",
        "different_currency",
        "missing_currency",
        "missing_fee",
        "negative_fee",
        "nonfinite",
        "draft",
        "no_ledger",
        "bad_ledger",
        "no_access",
        "shared_track",
        "future_confirmation",
        "future_trade",
        "duplicate_conflict",
    ],
)
def test_incomplete_or_unverified_realized_evidence_is_unknown(problem):
    module = memory()
    assert hasattr(module, "realized_roundtrip"), (
        "confirmed roundtrip calculation missing"
    )
    rows = closed("track")
    changes = {
        "partial": {"quantity": "1"},
        "same_day": {"trade_date": rows[0]["trade_date"]},
        "sell_before_buy": {"trade_date": (NOW - timedelta(days=3)).date()},
        "different_account": {"account_id": "other"},
        "different_instrument": {"instrument_id": "other"},
        "different_currency": {"currency": "USD"},
        "missing_currency": {"currency": None},
        "missing_fee": {"fee": None},
        "negative_fee": {"fee": "-1"},
        "nonfinite": {"unit_price": "NaN"},
        "draft": {"status": "DRAFT"},
        "no_ledger": {"ledger_event_id": None},
        "bad_ledger": {"ledger_verified": False},
        "no_access": {"access_verified": False},
        "shared_track": {"shared_track_count": 2},
        "future_confirmation": {"confirmed_at": NOW + timedelta(seconds=1)},
        "future_trade": {"trade_date": (NOW + timedelta(days=1)).date()},
    }
    if problem == "only_buy":
        rows = rows[:1]
    elif problem == "duplicate_conflict":
        rows.append({**rows[-1], "fee": "0"})
    else:
        rows[-1].update(changes[problem])
    assert module.realized_roundtrip(rows, NOW) is None


def test_roundtrip_deduplicates_a_fill_linked_to_two_versions_and_rounds_actual_cash():
    module = memory()
    assert hasattr(module, "realized_roundtrip"), (
        "confirmed roundtrip calculation missing"
    )
    rows = closed("track", sell="11")
    rows.extend(
        [
            {**rows[0], "version_id": "later-version"},
            {**rows[1], "version_id": "later-version"},
        ]
    )
    assert module.realized_roundtrip(rows, NOW) == Decimal("0")
    exact = [
        fill("track", "BUY", price="1.000000001", quantity="1", fee="0"),
        fill("track", "SELL", price="1.000000002", quantity="1", fee="0", days=1),
    ]
    assert module.realized_roundtrip(exact, NOW) == Decimal("0")


def test_roundtrip_day_boundary_uses_the_same_china_trade_date_as_sql():
    clock = NOW.replace(hour=0, minute=30).astimezone(timezone.utc)
    rows = closed("track")
    rows[0]["trade_date"] = NOW.date() - timedelta(days=1)
    rows[1]["trade_date"] = NOW.date()
    for row in rows:
        row["confirmed_at"] = clock - timedelta(minutes=5)
    assert memory().realized_roundtrip(rows, clock) == Decimal("2")


def test_real_profit_counts_are_independent_of_judgment_and_exclude_shared_trials():
    module = memory()
    reviews = [
        review("win", "REFUTED", ["FACT"]),
        review("loss", "SUPPORTED", ["FACT"]),
        review("flat", "MIXED", ["FACT"]),
    ]
    fills = (
        closed("win", sell="12") + closed("loss", sell="10") + closed("flat", sell="11")
    )
    rows = module.aggregate_reviews(
        reviews, TENANT, ACTOR, "UNCLASSIFIED", NOW, fills=fills
    )
    baseline = rule(rows, "BASELINE")
    assert [
        baseline.get(key)
        for key in (
            "profit_sample_count",
            "profit_win_count",
            "profit_loss_count",
            "profit_flat_count",
        )
    ] == [3, 1, 1, 1]
    public = module.public_memory(rows, NOW)
    assert public[0]["supportRate"] == "0.5"
    assert public[0]["winRate"] == "0.3333333333333333333333333333"
    assert public[0]["profitSampleCount"] == 3
    assert public[0]["profitRateMeaning"] == "CONFIRMED_LINKED_ROUNDTRIP"
    assert "mine-account" not in str(public) and "unit_price" not in str(public)
    base = closed("win")
    shared = [*base, {**base[0], "track_id": "other-track"}]
    rows = module.aggregate_reviews(
        reviews, TENANT, ACTOR, "UNCLASSIFIED", NOW, fills=shared
    )
    assert rule(rows, "BASELINE")["profit_sample_count"] == 0


def test_review_counts_one_latest_judgment_per_track_and_separates_unknowns():
    track = str(uuid4())
    rows = aggregate(
        [
            review(track, "SUPPORTED", ["FACT"], minutes=2),
            review(track, "REFUTED", ["FACT", "FACT"], minutes=1),
            review(str(uuid4()), "SUPPORTED", ["FACT"]),
            review(str(uuid4()), "MIXED", ["FACT"]),
            review(str(uuid4()), "INCONCLUSIVE", ["TIMING"]),
        ]
    )
    baseline = rule(rows, "BASELINE")
    assert baseline["sample_count"] == 4
    assert [
        baseline[key]
        for key in (
            "supported_count",
            "mixed_count",
            "refuted_count",
            "inconclusive_count",
        )
    ] == [1, 1, 1, 1]
    assert baseline["support_rate"] == Decimal("0.5")
    assert rule(rows, "FACT")["sample_count"] == 3
    assert rule(rows, "TIMING")["support_rate"] is None


def test_latest_unqualified_review_does_not_fall_back_to_an_older_trial():
    track = str(uuid4())
    rows = aggregate(
        [
            review(track, "SUPPORTED", ["RISK"], minutes=1),
            review(track, "REFUTED", ["RISK"], lessons=False),
        ]
    )
    assert rule(rows, "BASELINE")["sample_count"] == 0


def test_memory_excludes_other_actors_future_notes_and_unknown_verdicts():
    rows = aggregate(
        [
            review(str(uuid4()), "SUPPORTED", user_id=uuid4()),
            review(str(uuid4()), "SUPPORTED", tenant_id=uuid4()),
            review(str(uuid4()), "SUPPORTED", created_at=NOW + timedelta(seconds=1)),
            review(str(uuid4()), "PROFIT_WIN", ["FACT"]),
            review(str(uuid4()), "SUPPORTED", ["FACT", "PRIVATE_ACCOUNT"]),
        ]
    )
    assert rule(rows, "BASELINE")["sample_count"] == 1
    assert {row["category"] for row in rows if row["sample_count"]} == {
        "BASELINE",
        "FACT",
    }


def test_public_memory_rebuilds_templates_rates_and_never_exports_private_prose():
    rows = aggregate([review(str(uuid4()), "REFUTED", ["DISCIPLINE"])])
    rows[0].update(
        rule_text="PRIVATE: buy my account immediately <script>",
        reason="PRIVATE_REASON",
        lessons="PRIVATE_LESSONS",
        holdings="PRIVATE_HOLDINGS",
        support_rate=Decimal("0.99"),
    )
    before = deepcopy(rows)
    public = memory().public_memory(rows, NOW)
    assert len(public) == 2
    baseline = next(row for row in public if row["category"] == "BASELINE")
    assert baseline["key"] == "UNCLASSIFIED:BASELINE"
    assert baseline["sampleCount"] == 1
    assert baseline["supportRate"] == "0"
    assert all(row["winRate"] is None for row in public)
    assert all(row["rateMeaning"] == "JUDGMENT_SUPPORT_RATE" for row in public)
    assert "PRIVATE" not in str(public)
    assert str(TENANT) not in str(public) and str(ACTOR) not in str(public)
    assert rows == before


def test_empty_and_invalid_memory_never_manufacture_a_statistical_win_rate():
    rows = aggregate([])
    assert len(rows) == 8
    assert memory().public_memory(rows, NOW) == []
    malformed = rule(aggregate([review(str(uuid4()), "SUPPORTED")]), "BASELINE")
    malformed["sample_count"] = 100
    assert memory().public_memory([malformed], NOW) == []
    malformed["sample_count"] = 1
    malformed["updated_at"] = NOW + timedelta(seconds=1)
    assert memory().public_memory([malformed], NOW) == []


def test_theme_comes_only_from_frozen_structured_fields():
    module = memory()
    assert (
        module.theme_for_snapshot({"thesis": "ENERGY private holdings"})
        == "UNCLASSIFIED"
    )
    assert (
        module.theme_for_snapshot({"scope": {"themeCode": "PRIVATE_USERNAME"}})
        == "UNCLASSIFIED"
    )
    assert (
        module.theme_for_snapshot({"scope": {"themeCode": "UNCLASSIFIED"}})
        == "UNCLASSIFIED"
    )
    assert (
        module.theme_for_snapshot({"scope": {"themeCode": "GEO_CRUDE_OIL"}})
        == "GEO_CRUDE_OIL"
    )
    assert (
        module.theme_for_snapshot({"actionablePlan": {"themeCode": "AI_MEMORY_HBM"}})
        == "AI_MEMORY_HBM"
    )


def test_theme_counts_do_not_borrow_another_theme_or_an_older_version_review():
    track = str(uuid4())
    rows = [
        review(track, "SUPPORTED", ["FACT"], minutes=1),
        review(track, "REFUTED", ["TIMING"], theme_code="AI_MEMORY_HBM"),
    ]
    assert rule(aggregate(rows), "BASELINE")["sample_count"] == 0
    other = memory().aggregate_reviews(rows, TENANT, ACTOR, "AI_MEMORY_HBM", NOW)
    assert rule(other, "BASELINE")["sample_count"] == 1
    assert rule(other, "TIMING")["refuted_count"] == 1
    assert rule(other, "FACT")["sample_count"] == 0


class ScopedConnection:
    """DB boundary double records the real owner parameters and persisted rows."""

    def __init__(self, reviews=(), persisted=(), fills=()):
        self.reviews = list(reviews)
        self.persisted = list(persisted)
        self.fills = list(fills)
        self.reads = []
        self.writes = []
        self.locked = False

    async def execute(self, query, *args):
        assert "pg_advisory_xact_lock" in query
        assert str(TENANT) in args[0] and str(ACTOR) in args[0]
        self.locked = True

    async def fetch(self, query, *args):
        self.reads.append((query, args))
        assert args == (TENANT, ACTOR, NOW)
        assert "tenant_id=$1" in query and "user_id=$2" in query
        if "strategy_memory_rules" in query:
            return self.persisted
        if "ledger.trade_fills" in query:
            assert "ledger.ledger_events" in query and "ledger.ledger_legs" in query
            assert "BENEFICIAL_OWNER" in query and "JOINT_OWNER" in query
            assert "identity.tenant_members" in query and "ACTIVE" in query
            assert "l.fill AS" not in query and "l.fill," not in query
            return self.fills
        # Raw note text stays inside SQL; it is not loaded as aggregation data.
        assert "n.payload AS" not in query and "n.payload," not in query
        return self.reviews

    async def executemany(self, query, values):
        assert self.locked and "ON CONFLICT" in query
        self.writes.extend(values)


@pytest.mark.asyncio
async def test_refresh_upserts_actor_scoped_counts_and_resets_absent_categories():
    conn = ScopedConnection([review(str(uuid4()), "SUPPORTED", ["FACT"])])
    rows = await memory().refresh_strategy_memory(
        conn, TENANT, ACTOR, "UNCLASSIFIED", NOW
    )
    assert rule(rows, "FACT")["sample_count"] == 1
    assert len(conn.writes) == 8
    assert all(row[:3] == (TENANT, ACTOR, "UNCLASSIFIED") for row in conn.writes)
    assert "PRIVATE" not in str(conn.writes)
    conn.reviews = []
    conn.writes.clear()
    await memory().refresh_strategy_memory(conn, TENANT, ACTOR, "UNCLASSIFIED", NOW)
    assert all(row[5] == 0 for row in conn.writes)


@pytest.mark.asyncio
async def test_read_revalidates_current_profit_proof_without_writes_or_private_text():
    reviews = [review("closed", "REFUTED")]
    persisted = memory().aggregate_reviews(
        reviews, TENANT, ACTOR, "UNCLASSIFIED", NOW, fills=closed("closed")
    )
    conn = ScopedConnection(reviews, persisted=persisted, fills=closed("closed"))
    rows = await memory().read_strategy_memory(conn, TENANT, ACTOR, NOW)
    assert rule(rows, "BASELINE")["profit_win_count"] == 1
    conn.fills[-1]["access_verified"] = False
    rows = await memory().read_strategy_memory(conn, TENANT, ACTOR, NOW)
    assert rule(rows, "BASELINE")["profit_sample_count"] == 0
    assert memory().public_memory(rows, NOW)[0]["winRate"] is None
    assert persisted[0]["profit_win_count"] == 1
    assert not conn.writes


@pytest.mark.asyncio
async def test_refresh_persists_profit_columns_and_does_not_borrow_other_owners_fills():
    reviews = [review("closed", "SUPPORTED")]
    fills = closed("closed")
    conn = ScopedConnection(reviews, fills=fills)
    rows = await memory().refresh_strategy_memory(
        conn, TENANT, ACTOR, "UNCLASSIFIED", NOW
    )
    assert rule(rows, "BASELINE")["win_rate"] == Decimal("1")
    assert len(conn.writes[0]) == 17
    fills[-1]["user_id"] = uuid4()
    rows = memory().aggregate_reviews(
        reviews, TENANT, ACTOR, "UNCLASSIFIED", NOW, fills=fills
    )
    assert rule(rows, "BASELINE")["profit_sample_count"] == 0


def test_public_memory_recomputes_profit_rate_and_rejects_impossible_counts():
    rows = memory().aggregate_reviews(
        [review("t", "SUPPORTED")],
        TENANT,
        ACTOR,
        "UNCLASSIFIED",
        NOW,
        fills=closed("t"),
    )
    rows[0]["win_rate"] = Decimal("0.001")
    assert memory().public_memory(rows, NOW)[0]["winRate"] == "1"
    rows[0]["profit_sample_count"] = 9
    item = memory().public_memory(rows, NOW)[0]
    assert item["winRate"] is None and item["profitSampleCount"] == 0


@pytest.mark.asyncio
async def test_new_trade_link_refreshes_memory_and_replay_does_not(monkeypatch):
    from unittest.mock import AsyncMock
    from backend.investment import study_service as service
    from backend.investment.study_schemas import TradeLinkRequest

    track_id, version_id, fill_id = uuid4(), uuid4(), uuid4()
    track = {"tenant_id": TENANT, "user_id": ACTOR, "id": track_id, "generation": 1}
    monkeypatch.setattr(service.repository, "lock_track", AsyncMock(return_value=track))
    monkeypatch.setattr(
        service.repository,
        "latest_version",
        AsyncMock(
            return_value={
                "id": version_id,
                "snapshot": {"scope": {"themeCode": "GEO_CRUDE_OIL"}},
            }
        ),
    )
    monkeypatch.setattr(
        service.repository,
        "eligible_trades",
        AsyncMock(return_value=[{"fillId": str(fill_id)}]),
    )
    saved = AsyncMock(return_value={"id": uuid4()})
    monkeypatch.setattr(service.repository, "add_trade_link", saved)
    monkeypatch.setattr(service, "get_detail", AsyncMock(return_value={"saved": True}))
    refresh = AsyncMock(side_effect=RuntimeError("refresh failed"))
    monkeypatch.setattr(memory(), "refresh_memory_for_trade", refresh)
    conn = object()
    request = TradeLinkRequest.model_validate(
        {"expectedGeneration": 1, "fillId": str(fill_id), "reason": "private"}
    )
    with pytest.raises(RuntimeError, match="refresh failed"):
        await service.link_trade(conn, TENANT, ACTOR, track_id, request, NOW)
    refresh.assert_awaited_once_with(
        conn, TENANT, ACTOR, str(fill_id), "GEO_CRUDE_OIL", NOW
    )
    saved.return_value = None
    assert await service.link_trade(conn, TENANT, ACTOR, track_id, request, NOW) == {
        "saved": True
    }
    assert refresh.await_count == 1


@pytest.mark.asyncio
async def test_link_refresh_includes_every_affected_owner_theme(monkeypatch):
    from unittest.mock import AsyncMock

    fill_id = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"theme_code": "AI_MEMORY_HBM"},
        {"theme_code": "private-title"},
    ]
    refresh = AsyncMock()
    monkeypatch.setattr(memory(), "refresh_strategy_memory", refresh)
    await memory().refresh_memory_for_trade(
        conn, TENANT, ACTOR, fill_id, "GEO_CRUDE_OIL", NOW
    )
    assert [call.args[3] for call in refresh.await_args_list] == [
        "AI_MEMORY_HBM",
        "GEO_CRUDE_OIL",
        "UNCLASSIFIED",
    ]
    query, *args = conn.fetch.await_args.args
    assert args == [TENANT, ACTOR, fill_id, NOW]
    assert "tenant_id=$1" in query and "user_id=$2" in query and "fill_id=$3" in query


@pytest.mark.asyncio
async def test_review_save_refreshes_memory_once_and_propagates_refresh_failure(
    monkeypatch,
):
    from backend.investment import study_service as service
    from backend.investment.study_schemas import NoteRequest
    from unittest.mock import AsyncMock

    module = memory()
    track_id, version_id = uuid4(), uuid4()
    track = {"tenant_id": TENANT, "user_id": ACTOR, "id": track_id, "generation": 1}
    monkeypatch.setattr(service.repository, "lock_track", AsyncMock(return_value=track))
    replayed = AsyncMock(return_value=False)
    monkeypatch.setattr(service.repository, "command_replayed", replayed)
    monkeypatch.setattr(
        service.repository,
        "latest_version",
        AsyncMock(
            return_value={
                "id": version_id,
                "snapshot": {"scope": {"themeCode": "UNCLASSIFIED"}},
            }
        ),
    )
    saved = AsyncMock(return_value={"id": uuid4()})
    monkeypatch.setattr(service.repository, "add_note", saved)
    monkeypatch.setattr(service, "get_detail", AsyncMock(return_value={"saved": True}))
    refresh = AsyncMock(side_effect=RuntimeError("aggregation failed"))
    monkeypatch.setattr(module, "refresh_strategy_memory", refresh)
    request = NoteRequest.model_validate(
        {
            "expectedGeneration": 1,
            "kind": "REVIEW",
            "verdict": "REFUTED",
            "reason": "PRIVATE_REASON",
            "lessons": "PRIVATE_LESSONS",
            "errorCategories": ["FACT"],
        }
    )
    conn = object()
    with pytest.raises(RuntimeError, match="aggregation failed"):
        await service.append_note(conn, TENANT, ACTOR, track_id, request, NOW)
    refresh.assert_awaited_once_with(conn, TENANT, ACTOR, "UNCLASSIFIED", NOW)
    replayed.return_value = True
    assert await service.append_note(conn, TENANT, ACTOR, track_id, request, NOW) == {
        "saved": True
    }
    assert refresh.await_count == 1 and saved.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lessons", [None, "", "   "])
async def test_review_without_lessons_refreshes_to_remove_an_older_contribution(
    monkeypatch, lessons
):
    from backend.investment import study_service as service
    from backend.investment.study_schemas import NoteRequest
    from unittest.mock import AsyncMock

    module = memory()
    track_id = uuid4()
    monkeypatch.setattr(
        service.repository,
        "lock_track",
        AsyncMock(
            return_value={
                "tenant_id": TENANT,
                "user_id": ACTOR,
                "id": track_id,
                "generation": 1,
            }
        ),
    )
    monkeypatch.setattr(
        service.repository, "command_replayed", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        service.repository,
        "latest_version",
        AsyncMock(return_value={"id": uuid4(), "snapshot": {"scope": {}}}),
    )
    monkeypatch.setattr(
        service.repository, "add_note", AsyncMock(return_value={"id": uuid4()})
    )
    monkeypatch.setattr(service, "get_detail", AsyncMock(return_value={"saved": True}))
    refresh = AsyncMock()
    monkeypatch.setattr(module, "refresh_strategy_memory", refresh)
    request = NoteRequest.model_validate(
        {
            "expectedGeneration": 1,
            "kind": "REVIEW",
            "verdict": "MIXED",
            "reason": "PRIVATE_REASON",
            "lessons": lessons,
        }
    )
    conn = object()
    assert await service.append_note(conn, TENANT, ACTOR, track_id, request, NOW) == {
        "saved": True
    }
    refresh.assert_awaited_once_with(conn, TENANT, ACTOR, "UNCLASSIFIED", NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,saved", [("DECISION", True), ("REVIEW", False)])
async def test_nonreview_or_conflicted_insert_does_not_refresh_memory(
    monkeypatch, kind, saved
):
    from backend.investment import study_service as service
    from backend.investment.study_schemas import NoteRequest
    from unittest.mock import AsyncMock

    module = memory()
    track_id = uuid4()
    monkeypatch.setattr(
        service.repository,
        "lock_track",
        AsyncMock(
            return_value={
                "tenant_id": TENANT,
                "user_id": ACTOR,
                "id": track_id,
                "generation": 1,
            }
        ),
    )
    monkeypatch.setattr(
        service.repository, "command_replayed", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        service.repository,
        "latest_version",
        AsyncMock(
            return_value={
                "id": uuid4(),
                "snapshot": {"scope": {"themeCode": "GEO_CRUDE_OIL"}},
            }
        ),
    )
    monkeypatch.setattr(
        service.repository,
        "add_note",
        AsyncMock(return_value={"id": uuid4()} if saved else None),
    )
    monkeypatch.setattr(service, "get_detail", AsyncMock(return_value={"saved": True}))
    refresh = AsyncMock()
    monkeypatch.setattr(module, "refresh_strategy_memory", refresh)
    request = NoteRequest.model_validate(
        {
            "expectedGeneration": 1,
            "kind": kind,
            "reason": "PRIVATE_REASON",
            "lessons": "PRIVATE_LESSONS",
            **(
                {"verdict": "SUPPORTED"} if kind == "REVIEW" else {"action": "PLAN_BUY"}
            ),
        }
    )
    assert await service.append_note(
        object(), TENANT, ACTOR, track_id, request, NOW
    ) == {"saved": True}
    refresh.assert_not_awaited()
