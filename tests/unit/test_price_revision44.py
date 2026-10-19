"""Price revisions compare prior calendar-day plans, never debug runs today."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from test_phase2_research import NOW, anchor, proposal
from test_value_directions import direction, packet, validate


def revised(*, requested="MAINTAINED", shift=None, old_changes=None, **updates):
    public, raw = packet(), direction()
    old = deepcopy(public["discoveryActionableZones"][0])
    old.update(selectedTrack="LEFT_VALUE", **(old_changes or {}))
    public["previousValueDirections"] = [{"key": raw["key"], "plans": [old]}]
    if shift:
        public["discoveryActionableZones"][0]["tracks"][0].update(shift)
    raw["plans"][0].update(priceRevisionStatus=requested, **updates)
    return raw, public


def test_unchanged_coordinates_are_maintained_even_when_model_says_downgraded():
    raw, public = revised(requested="DOWNGRADED")
    result = validate([raw], public)[0]["plans"][0]
    assert result["priceRevisionStatus"] == "MAINTAINED"
    assert "维持" in result["priceRevisionNote"]


def test_explicit_maintained_is_valid_and_preserves_one_sentence_reason():
    raw, public = revised(priceRevisionNote="没有新净值，关注区间维持。")
    result = validate([raw], public)[0]["plans"][0]
    assert result["priceRevisionStatus"] == "MAINTAINED"
    assert result["priceRevisionNote"] == "没有新净值，关注区间维持。"


@pytest.mark.parametrize(
    "shift,expected",
    [
        ({"entryZoneMin": "1.1000000000000001"}, "MAINTAINED"),
        (
            {"entryZoneMin": "1.09", "entryZoneMax": "1.19", "takeProfitT1": "1.39"},
            "DOWNGRADED",
        ),
        ({"entryZoneMin": "1.11", "takeProfitT1": "1.39"}, "MAINTAINED"),
        ({"stopLossPrice": "0.90"}, "MAINTAINED"),
    ],
)
def test_server_derives_decimal_price_direction_instead_of_copying_model_status(
    shift, expected
):
    raw, public = revised(requested="DOWNGRADED", shift=shift)
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == expected


def test_changed_nav_and_observation_day_alone_do_not_change_plan_revision():
    raw, public = revised(
        requested="UPGRADED", old_changes={"asOf": "2026-10-15", "currentPrice": "1.23"}
    )
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "MAINTAINED"


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("MAINTAINED", "MAINTAINED"),
        ("INITIAL", "INITIAL"),
        ("DOWNGRADED", "INITIAL"),
        ("UPGRADED", "INITIAL"),
    ],
)
def test_no_comparable_prior_plan_does_not_fabricate_a_revision(requested, expected):
    public, raw = packet(), direction()
    raw["plans"][0]["priceRevisionStatus"] = requested
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == expected


@pytest.mark.parametrize(
    "status,field", [("UPGRADED", "catalysts"), ("DOWNGRADED", "risks")]
)
def test_same_price_fundamental_revision_requires_related_current_note_sources(
    status, field
):
    raw, public = revised(
        requested=status,
        priceRevisionBasis="FUNDAMENTAL",
        priceRevisionSourceIds=["s1"],
    )
    raw[field] = [{"summary": "订单与兑现假设发生变化。", "sourceIds": ["s1"]}]
    result = validate([raw], public)[0]["plans"][0]
    assert result["priceRevisionStatus"] == status
    assert result["priceRevisionBasis"] == "FUNDAMENTAL"
    assert result["priceRevisionSourceIds"] == ["source:one"]


@pytest.mark.parametrize(
    "refs,note_field",
    [([], "risks"), (["s2"], "risks"), (["missing"], "risks"), (["s1"], "catalysts")],
)
def test_unsupported_fundamental_downgrade_is_corrected_without_losing_report(
    refs, note_field
):
    raw, public = revised(
        requested="DOWNGRADED",
        priceRevisionBasis="FUNDAMENTAL",
        priceRevisionSourceIds=refs,
    )
    raw[note_field] = [{"summary": "只作为本方向的当前观察。", "sourceIds": ["s1"]}]
    result = validate([raw], public)[0]["plans"][0]
    assert result["priceRevisionStatus"] == "MAINTAINED"
    assert result["priceRevisionBasis"] == "PRICE"
    assert result["priceRevisionSourceIds"] == []


def test_legacy_theme_plan_uses_same_price_normalization_without_new_fundamental_contract():
    from backend.macro.alpha_research import validate_actionable_plans

    old = anchor()
    old["selectedTrack"] = "LEFT_VALUE"
    public = {"themeActionableZones": [anchor()], "previousActionablePlans": [old]}
    result = validate_actionable_plans(
        [proposal(priceRevisionStatus="DOWNGRADED")], public, NOW
    )
    assert result[0]["priceRevisionStatus"] == "MAINTAINED"


def test_take_profit_preserves_hard_proof_and_drops_unrelated_fundamental_claim():
    raw, public = revised(
        requested="TAKE_PROFIT_ALERT",
        priceRevisionBasis="FUNDAMENTAL",
        priceRevisionSourceIds=["missing"],
    )
    public["discoveryActionableZones"][0]["currentPrice"] = "1.4500"
    result = validate([raw], public)[0]["plans"][0]
    assert result["priceRevisionStatus"] == "TAKE_PROFIT_ALERT"
    assert result["priceRevisionBasis"] == "PRICE"
    assert result["priceRevisionSourceIds"] == []


def test_switched_track_or_changed_price_unit_has_no_comparable_previous_revision():
    raw, public = revised(requested="DOWNGRADED")
    public["previousValueDirections"][0]["plans"][0]["selectedTrack"] = "RIGHT_TREND"
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "INITIAL"
    public["previousValueDirections"][0]["plans"][0].update(
        selectedTrack="LEFT_VALUE", unit="points"
    )
    assert validate([raw], public)[0]["plans"][0]["priceRevisionStatus"] == "INITIAL"


def test_prompt_describes_maintained_cross_day_and_one_sentence_without_repeated_prices():
    from backend.macro.daily_research import build_prompt

    prompt = build_prompt({})
    assert "MAINTAINED" in prompt and "同日DEBUG" in prompt
    assert "priceRevisionSourceIds" in prompt and "严禁重复抄写" in prompt


@pytest.mark.asyncio
async def test_previous_reader_excludes_today_uses_shanghai_day_and_only_visible_ready():
    from backend.macro import daily_repository as repository

    reader = getattr(repository, "read_previous_daily_research", None)
    assert reader is not None, "a dedicated previous-day comparison reader is required"
    stamp = datetime(2026, 10, 18, 17, tzinfo=timezone.utc)  # Shanghai Oct 19
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "run_id": "prior",
        "slot_date": stamp.date(),
        "recorded_at": stamp - timedelta(hours=1),
        "payload": {"valueDirections": []},
    }
    result = await reader(conn, stamp)
    sql, *args = conn.fetchrow.call_args.args
    assert "r.slot_date<$1" in sql and "t.status='READY'" in sql
    assert "r.dispatched_at<=$2" in sql and "t.recorded_at<=$2" in sql
    assert args[0].isoformat() == "2026-10-19"
    assert result["reportRunId"] == "prior"
    assert result["reportSlotDate"].isoformat() == "2026-10-18"
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_both_price_packets_use_previous_day_without_changing_news_continuation(
    monkeypatch,
):
    from backend.jobs import daily_research as job
    from backend.investment import repository, service, discovery, theme_graph
    from backend.macro import market_repository, alpha_repository, alpha_research

    reader = getattr(job, "read_previous_daily_research", None)
    assert reader is not None, (
        "load_public_packet must use the dedicated prior-day reader"
    )
    today = {
        "report": {"summary": "today", "valueDirections": [{"key": "today-debug"}]}
    }
    previous = {
        "report": {"summary": "prior", "valueDirections": [{"key": "previous-day"}]}
    }
    monkeypatch.setattr(service, "market_context", AsyncMock(return_value={}))
    monkeypatch.setattr(repository, "directions", AsyncMock(return_value=[]))
    for name, value in (
        ("read_permissions", {}),
        ("read_collection", {}),
        ("read_series", []),
    ):
        monkeypatch.setattr(market_repository, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        alpha_repository, "read_alpha_public", AsyncMock(return_value={})
    )
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    monkeypatch.setattr(job, "read_daily_research", AsyncMock(return_value=today))
    monkeypatch.setattr(
        job, "read_previous_daily_research", AsyncMock(return_value=previous)
    )
    monkeypatch.setattr(job, "read_recent_public_research", AsyncMock(return_value=[]))
    received = {}

    def make(*args, **kwargs):
        received["news"] = kwargs["previous"]
        return {}

    def extend(*args):
        received["theme"] = args[-2]
        return {}

    async def build(*args, **kwargs):
        received["discovery"] = args[-2]
        return {}

    monkeypatch.setattr(job, "make_research_packet", make)
    monkeypatch.setattr(alpha_research, "extend_public_packet", extend)
    monkeypatch.setattr(discovery, "build_discovery_packet", build)
    conn = AsyncMock()
    conn.fetch.return_value = []
    await job.load_public_packet(conn, NOW, {})
    assert received["news"]["report"]["summary"] == "today"
    assert received["theme"] == previous
    assert received["discovery"] == previous


@pytest.mark.asyncio
async def test_debug_preflight_crossing_packet_day_never_consumes_a_different_slot(
    monkeypatch,
):
    from test_daily_research_job import isolate, settings

    mod, state = isolate(monkeypatch)
    before = NOW.replace(hour=23, minute=59, second=50)
    after = before + timedelta(seconds=20)
    clock = iter([before, after])
    monkeypatch.setattr(mod, "now_shanghai", lambda: next(clock, after))
    mod.load_public_packet.return_value = {"asOf": before.isoformat()}
    result = await mod.sync_daily_research(
        object(),
        settings(app_env="development", daily_research_debug_enabled=True),
        before,
        debug_request_id="request",
        source_batch_id="batch",
    )
    assert result["status"] == "SKIPPED"
    assert not state["consumed"]
    mod.run_codex_research.assert_not_awaited()


@pytest.mark.asyncio
async def test_debug_collection_crossing_midnight_can_dispatch_a_new_day_packet(
    monkeypatch,
):
    from test_daily_research_job import isolate, settings

    mod, state = isolate(monkeypatch)
    before = NOW.replace(hour=23, minute=59, second=50)
    after = before + timedelta(seconds=20)
    monkeypatch.setattr(mod, "now_shanghai", lambda: after)
    mod.load_public_packet.return_value = {"asOf": after.isoformat()}
    result = await mod.sync_daily_research(
        object(),
        settings(app_env="development", daily_research_debug_enabled=True),
        before,
        debug_request_id="request",
        source_batch_id="batch",
    )
    assert result["status"] == "READY"
    assert state["consumed"]
    mod.run_codex_research.assert_awaited_once()
