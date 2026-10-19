"""The durable Shanghai-day slot is committed before public network work."""

from contextlib import asynccontextmanager
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from importlib import import_module, util
import json
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 18, 7, tzinfo=ZoneInfo("Asia/Shanghai"))


def engine():
    assert util.find_spec("backend.jobs.alpha_public"), "durable alpha job missing"
    return import_module("backend.jobs.alpha_public")


class Pool:
    def __init__(self):
        self.rows = {}
        self.borrowed = 0
        self.transaction_open = False
        self.events = []

    @asynccontextmanager
    async def acquire(self):
        self.borrowed += 1
        try:
            yield self
        finally:
            self.borrowed -= 1

    @asynccontextmanager
    async def transaction(self):
        self.transaction_open = True
        self.events.append("BEGIN")
        try:
            yield
        finally:
            self.events.append("COMMIT")
            self.transaction_open = False

    async def fetchrow(self, sql, *args):
        if "INSERT INTO market.alpha_public_batches" in sql:
            day, claimed = args
            if day in self.rows:
                return None
            row = {
                "id": f"batch:{day}",
                "run_date": day,
                "claimed_at": claimed,
                "completed_at": None,
                "status": "CLAIMED",
                "payload": {},
            }
            self.rows[day] = row
            return row
        raise AssertionError("unexpected read")

    async def execute(self, sql, *args):
        identity, state, completed, payload = args
        row = next(row for row in self.rows.values() if row["id"] == identity)
        assert row["status"] == "CLAIMED"
        row.update(status=state, completed_at=completed, payload=json.loads(payload))


def bundle():
    return {
        "methodVersion": "alpha-public-v1",
        "asOf": NOW.isoformat(),
        "observations": [
            {
                "code": "CN_T10",
                "value": "1.82",
                "referenceDate": "2026-10-16",
                "retrievedAt": NOW.isoformat(),
            }
        ],
        "catalysts": [],
        "odds": [],
        "launches": [],
        "sourceResults": {"AKSHARE_CN_T10": {"status": "SUCCESS"}},
    }


async def empty_nav(pool, now, *, catalysts):
    assert pool.borrowed == 0
    return {"fundHistories": [], "themeNavSourceResults": {}}


@pytest.mark.asyncio
async def test_dynamic_nav_uses_the_actual_news_completion_clock_without_backdating():
    from backend.investment.dynamic_discovery import event_themes

    pool = Pool()
    published = NOW + timedelta(seconds=1)
    current = NOW + timedelta(seconds=2)

    async def collect(now):
        raw = bundle()
        raw["catalysts"] = [
            {
                "id": "satellite",
                "title": "卫星互联网订单",
                "publishedAt": published.isoformat(),
                "retrievedAt": published.isoformat(),
            }
        ]
        return raw

    async def nav(pool, now, *, catalysts):
        assert now == current
        assert event_themes(catalysts, now)
        return {"fundHistories": [], "themeNavSourceResults": {}}

    result = await engine().sync_alpha_public(
        pool, NOW, collector=collect, nav_collector=nav, now_provider=lambda: current
    )
    assert result["status"] == "READY"


@pytest.mark.asyncio
async def test_a_proven_fresh_master_cache_is_not_a_failed_public_source():
    pool, raw = Pool(), bundle()
    original = deepcopy(raw)

    async def collect(now):
        return raw

    async def nav(pool, now, *, catalysts):
        return {
            "fundHistories": [],
            "themeNavSourceResults": {},
            "fundMasterSourceResult": {
                "status": "SKIPPED",
                "reason": "FRESH",
                "catalogCount": 28107,
            },
        }

    result = await engine().sync_alpha_public(
        pool, NOW, collector=collect, nav_collector=nav, now_provider=lambda: NOW
    )
    assert result["status"] == "READY"
    assert raw == original


@pytest.mark.asyncio
async def test_committed_claim_releases_connection_before_collecting_and_preserves_observation_time():
    pool, payload = Pool(), bundle()
    original = deepcopy(payload)

    async def collect(now):
        assert pool.borrowed == 0 and not pool.transaction_open
        assert pool.events == ["BEGIN", "COMMIT"]
        assert pool.rows[NOW.date()]["status"] == "CLAIMED"
        return payload

    result = await engine().sync_alpha_public(
        pool,
        NOW,
        collector=collect,
        nav_collector=empty_nav,
        now_provider=lambda: NOW + timedelta(seconds=2),
    )
    row = pool.rows[NOW.date()]
    assert result["status"] == row["status"] == "READY"
    assert row["completed_at"] == NOW + timedelta(seconds=2)
    assert row["payload"]["observations"] == original["observations"]
    assert payload == original


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["CLAIMED", "READY", "PARTIAL", "FAILED"])
async def test_any_existing_day_slot_never_recollects_or_overwrites(state):
    pool = Pool()
    pool.rows[NOW.date()] = {
        "id": "old",
        "status": state,
        "payload": {"original": True},
    }

    async def forbidden(now):
        raise AssertionError("network must not run")

    result = await engine().sync_alpha_public(
        pool,
        NOW,
        collector=forbidden,
        nav_collector=empty_nav,
        now_provider=lambda: NOW,
    )
    assert result["status"] == "SKIPPED"
    assert pool.rows[NOW.date()]["status"] == state
    assert pool.rows[NOW.date()]["payload"] == {"original": True}


@pytest.mark.asyncio
async def test_collector_failure_persists_only_safe_error_class_and_consumes_slot():
    pool = Pool()

    async def fail(now):
        raise RuntimeError("DB_PASSWORD=secret source body must not persist")

    result = await engine().sync_alpha_public(
        pool, NOW, collector=fail, nav_collector=empty_nav, now_provider=lambda: NOW
    )
    assert result["status"] == "FAILED"
    assert pool.rows[NOW.date()]["payload"] == {"errorClass": "RuntimeError"}
    again = await engine().sync_alpha_public(
        pool, NOW, collector=fail, nav_collector=empty_nav, now_provider=lambda: NOW
    )
    assert again["status"] == "SKIPPED"


@pytest.mark.asyncio
async def test_nav_failures_make_partial_batch_without_discarding_macro_observations():
    pool = Pool()

    async def collect(now):
        return bundle()

    async def nav(pool, now, *, catalysts):
        assert pool.borrowed == 0
        return {
            "fundHistories": [],
            "themeNavSourceResults": {
                "008975": {"status": "FAILED", "errorClass": "TimeoutError"}
            },
        }

    result = await engine().sync_alpha_public(
        pool, NOW, collector=collect, nav_collector=nav, now_provider=lambda: NOW
    )
    assert result["status"] == "PARTIAL"
    assert pool.rows[NOW.date()]["payload"]["observations"][0]["value"] == "1.82"
    assert (
        pool.rows[NOW.date()]["payload"]["themeNavSourceResults"]["008975"]["status"]
        == "FAILED"
    )


@pytest.mark.asyncio
async def test_concurrent_workers_share_one_committed_day_batch():
    pool = Pool()

    async def collect(now):
        pool.events.append("HTTP")
        assert pool.borrowed == 0
        await asyncio.sleep(0)
        return bundle()

    results = await asyncio.gather(
        *(
            engine().sync_alpha_public(
                pool,
                NOW,
                collector=collect,
                nav_collector=empty_nav,
                now_provider=lambda: NOW,
            )
            for _ in range(2)
        )
    )
    assert {result["status"] for result in results} == {"READY", "SKIPPED"}
    assert len(pool.rows) == 1 and pool.events.count("HTTP") == 1


@pytest.mark.asyncio
async def test_service_waits_until_0830_then_wakes_hourly_without_calling_model(
    monkeypatch,
):
    module = engine()
    current = NOW.replace(hour=8, minute=0)
    event = asyncio.Event()
    attempts, delays = [], []

    async def sync(pool, now, **kwargs):
        attempts.append(now)
        if len(attempts) == 2:
            event.set()
        return {"status": "SKIPPED"}

    async def wait(stop, seconds):
        nonlocal current
        delays.append(seconds)
        current += timedelta(seconds=seconds)

    monkeypatch.setattr(module, "sync_alpha_public", sync)
    await module.run_alpha_public_service(
        object(), stop_event=event, wait=wait, now_provider=lambda: current
    )
    assert [stamp.time().isoformat() for stamp in attempts] == ["08:30:00", "09:30:00"]
    assert delays == [1800, 3600]


@pytest.mark.asyncio
async def test_nav_success_without_any_actual_history_is_still_partial():
    pool = Pool()

    async def collect(now):
        return bundle()

    async def nav(pool, now, *, catalysts):
        return {
            "fundHistories": [],
            "themeNavSourceResults": {"008975": {"status": "SUCCESS", "records": 0}},
        }

    result = await engine().sync_alpha_public(
        pool, NOW, collector=collect, nav_collector=nav, now_provider=lambda: NOW
    )
    assert result["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_utc_evening_consumes_next_shanghai_calendar_day():
    from datetime import timezone

    pool = Pool()
    at = datetime(2026, 10, 18, 23, tzinfo=timezone.utc)

    async def collect(now):
        assert now.date().isoformat() == "2026-10-19"
        return bundle()

    result = await engine().sync_alpha_public(
        pool, at, collector=collect, nav_collector=empty_nav, now_provider=lambda: at
    )
    assert result["runDate"] == "2026-10-19"
    assert next(iter(pool.rows)).isoformat() == "2026-10-19"
