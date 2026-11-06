"""Archive workers warm only bounded, currently verified public fund histories."""

from copy import deepcopy
from contextlib import asynccontextmanager
from importlib import import_module, util
from datetime import timedelta
import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_live_fund_plans_v6 import LocalHistory, NOW, history


def module():
    assert util.find_spec("backend.investment.opportunity_fund_nav"), (
        "READY recall candidates need an automatic public NAV warmup"
    )
    return import_module("backend.investment.opportunity_fund_nav")


def product(code, *, group=None):
    return {
        "fundCode": code,
        "name": f"测试基金{code}A",
        "subjectKind": "nav",
        "subjectId": code,
        "market": "FUND_CN",
        "fundGroupKey": group or code,
        "associationLevel": "NAME_CANDIDATE",
        "canSubscribe": True,
    }


def test_warmup_round_robins_three_groups_per_topic_and_caps_all_provider_codes():
    recalls = {
        str(topic): {
            "funds": [product(f"{topic * 10 + share:06}") for share in range(5)]
        }
        for topic in range(20)
    }
    rows = module().select_recalled_nav_targets(recalls)
    assert len(rows) == 24
    assert [row["fundCode"] for row in rows[:20]] == [
        f"{topic * 10:06}" for topic in range(20)
    ]
    assert all(int(row["fundCode"]) % 10 < 3 for row in rows)
    assert len({row["fundCode"] for row in rows}) == 24


def test_warmup_deduplicates_topics_and_keeps_currently_available_share():
    preferred = product("987651", group="独立产品")
    recalls = {
        "one": {"funds": [preferred, product("987652"), product("987653")]},
        "two": {"funds": [deepcopy(preferred)]},
    }
    assert [
        row["fundCode"] for row in module().select_recalled_nav_targets(recalls)
    ] == [
        "987651",
        "987652",
        "987653",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary", ["ready", "absent", "short", "stale", "foreign", "unproven"]
)
async def test_only_fresh_trusted_sufficient_local_history_skips_provider(boundary):
    row = history("987651")
    if boundary == "short":
        row["points"] = row["points"][-60:]
        row.pop("providerHistoryProof")
    elif boundary == "stale":
        row["retrievedAt"] = (NOW - timedelta(days=5)).isoformat()
    elif boundary == "foreign":
        row["currency"] = "USD"
    elif boundary == "unproven":
        row.pop("providerHistoryProof")
    conn = LocalHistory([] if boundary == "absent" else [row])
    ready = await module().priced_recalled_nav_codes(conn, [product("987651")], NOW)
    assert ready == ({"987651"} if boundary == "ready" else set())


def test_warmup_rejects_unbounded_or_nonpublic_identities():
    bad = product("987651")
    bad["market"] = "FUND_US"
    assert module().select_recalled_nav_targets({"one": {"funds": [bad]}}) == []


@pytest.mark.asyncio
async def test_independent_service_cancels_and_awaits_active_public_collection(
    monkeypatch,
):
    mod = module()
    assert hasattr(mod, "run_opportunity_fund_nav_service"), (
        "NAV provider I/O must not block archiving"
    )
    started, cancelled, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def collect(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        mod,
        "_latest_ready_runs",
        AsyncMock(return_value=["latest-ready"]),
        raising=False,
    )
    monkeypatch.setattr(mod, "_schedule_recalled_fund_nav_runs", collect, raising=False)
    monkeypatch.setattr(
        mod, "_research_model_running", AsyncMock(return_value=False), raising=False
    )
    service = asyncio.create_task(
        mod.run_opportunity_fund_nav_service(object(), stop_event=stop)
    )
    await asyncio.wait_for(started.wait(), 1)
    stop.set()
    await asyncio.wait_for(service, 1)
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [True, False])
async def test_optional_warmup_defers_each_poll_while_research_is_running(
    monkeypatch, running
):
    mod = module()
    stop, checked = asyncio.Event(), asyncio.Event()

    async def busy(pool):
        checked.set()
        return running

    async def runs(pool):
        stop.set()
        return ["latest"]

    collect = AsyncMock(return_value={"claimed": 0})
    lookup = AsyncMock(side_effect=runs)
    monkeypatch.setattr(mod, "_research_model_running", busy, raising=False)
    monkeypatch.setattr(mod, "_latest_ready_runs", lookup)
    monkeypatch.setattr(mod, "_schedule_recalled_fund_nav_runs", collect)
    service = asyncio.create_task(
        mod.run_opportunity_fund_nav_service(object(), stop_event=stop)
    )
    await asyncio.wait_for(checked.wait(), 1)
    await asyncio.sleep(0)
    if running:
        stop.set()
    await asyncio.wait_for(service, 1)
    assert lookup.await_count == (0 if running else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["reused", "cooldown", "all_new"])
async def test_recent_ready_runs_skip_cached_or_cooling_funds_without_starving_older_run(
    monkeypatch, blocked
):
    mod = module()
    assert hasattr(mod, "_schedule_recalled_fund_nav_runs"), (
        "Each poll must share one bounded budget across recent READY runs"
    )
    first = [product(f"{1000 + i:06}") for i in range(24)]
    older = product("987651")

    class Connection:
        fetchval = AsyncMock(return_value="wealthsail_worker")

        @asynccontextmanager
        async def transaction(self):
            yield

    @asynccontextmanager
    async def connect(pool):
        yield Connection()

    async def targets(conn, run_id, now):
        if blocked == "all_new":
            return [product(f"{int(run_id) * 100 + i:06}") for i in range(30)]
        return first if run_id == "new" else [first[0], older]

    async def claim(conn, code):
        return (
            {"code": code}
            if blocked == "all_new" or code == older["fundCode"]
            else None
        )

    fetch = AsyncMock(return_value={"fundHistories": []})
    monkeypatch.setattr(mod, "system_connection", connect)
    monkeypatch.setattr(mod, "_load_targets", targets)
    monkeypatch.setattr(
        mod,
        "priced_recalled_nav_codes",
        AsyncMock(
            return_value={row["fundCode"] for row in first}
            if blocked == "reused"
            else set()
        ),
    )
    monkeypatch.setattr(mod, "_claim", claim)
    monkeypatch.setattr(
        mod, "_finish", AsyncMock(return_value={"persisted": False, "usable": False})
    )
    monkeypatch.setattr(mod, "collect_theme_nav_histories", fetch)
    runs = ["1", "2", "3", "4"] if blocked == "all_new" else ["new", "older"]
    result = await mod._schedule_recalled_fund_nav_runs(object(), runs, now=NOW)
    if blocked == "all_new":
        assert result["selected"] == 120 and result["claimed"] == 24
        requested = fetch.await_args.kwargs["asset_rows"]
        assert len(requested) == 24
        assert len({row["asset_code"] for row in requested}) == 24
    else:
        assert result["claimed"] == 1
        assert fetch.await_args.kwargs["asset_rows"] == [
            {"asset_code": "987651", "overseas_calendar_required": False}
        ]


@pytest.mark.asyncio
async def test_cancellation_during_later_claim_cleans_up_already_committed_claim(
    monkeypatch,
):
    mod = module()
    started = asyncio.Event()

    class Connection:
        fetchval = AsyncMock(return_value="wealthsail_worker")

        @asynccontextmanager
        async def transaction(self):
            yield

    @asynccontextmanager
    async def connect(pool):
        yield Connection()

    async def claim(conn, code):
        if code == "987651":
            return {"code": code}
        started.set()
        await asyncio.Event().wait()

    finish = AsyncMock(return_value={"persisted": False, "usable": False})
    monkeypatch.setattr(mod, "system_connection", connect)
    monkeypatch.setattr(
        mod,
        "_load_targets",
        AsyncMock(return_value=[product("987651"), product("987652")]),
    )
    monkeypatch.setattr(mod, "priced_recalled_nav_codes", AsyncMock(return_value=set()))
    monkeypatch.setattr(mod, "_claim", claim)
    monkeypatch.setattr(mod, "_finish", finish)
    task = asyncio.create_task(mod.schedule_recalled_fund_nav(object(), "new", now=NOW))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finish.await_count == 1
    assert finish.await_args.kwargs["cancelled"] is True
