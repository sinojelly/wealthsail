"""Full chart retrieval is separate from the frozen 500-point research cohort."""

from datetime import datetime
from importlib import import_module, util
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def module():
    assert util.find_spec("backend.jobs.fund_nav_full_history"), (
        "Full NAV worker missing"
    )
    return import_module("backend.jobs.fund_nav_full_history")


def payload():
    return {
        "fundCode": "008281",
        "sourceUri": "https://fund.eastmoney.com/pingzhongdata/008281.js",
        "queryStart": "1900-01-01",
        "queryEnd": "2026-10-20",
        "complete": True,
        "receivedRowCount": 3,
        "points": [
            {"date": "2026-08-01", "close": "1"},
            {"date": "2026-09-01", "close": "2"},
            {"date": "2026-10-19", "close": "1.5"},
        ],
    }


def test_complete_short_new_fund_is_full_disclosed_history_not_fabricated_two_years():
    row = module().validate_full_payload(payload(), "008281", NOW)
    assert row["fullHistory"] is True and len(row["points"]) == 3
    assert row["historyProof"]["firstDate"] == "2026-08-01"
    assert row["historyProof"]["pointCount"] == 3


@pytest.mark.parametrize(
    "change", ["conflict", "future", "code", "source", "truncated"]
)
def test_full_source_must_match_identity_dates_and_received_count(change):
    row = payload()
    if change == "conflict":
        row["points"][1] = {"date": "2026-08-01", "close": "2"}
    if change == "future":
        row["points"][2]["date"] = "2026-10-21"
    if change == "code":
        row["fundCode"] = "000051"
    if change == "source":
        row["sourceUri"] = "https://example.com/fake"
    if change == "truncated":
        row["receivedRowCount"] = 99
    with pytest.raises(ValueError):
        module().validate_full_payload(row, "008281", NOW)


def test_partial_source_keeps_actual_points_but_not_a_full_claim():
    row = payload()
    row["complete"] = False
    result = module().validate_full_payload(row, "008281", NOW)
    assert result["fullHistory"] is False and len(result["points"]) == 3


def test_full_chart_proof_does_not_depend_on_old_tail_500_digest():
    row = module().validate_full_payload(payload(), "008281", NOW)
    assert "providerHistoryProof" not in row
    assert row["historyProof"]["methodVersion"] == "akshare-unit-nav-full-v1"


class SourceStore:
    def __init__(self, *, old=None, operator=True):
        self.histories = [old] if old else []
        self.research = []
        self.active = False
        self.operator = operator
        self.connections = 0
        self.finishes = []
        self.commands = []
        self.attempts = set()

    def transaction(self):
        from test_nav_history47 import Cache

        return Cache().transaction()

    async def fetch(self, sql, *args):
        if "research_fund_catalog" in sql:
            return [{"currency": "CNY", "fund_name": "芯片联接A"}]
        return [
            {"payload": row}
            for row in reversed(
                self.research
                if "fund_research_nav_histories" in sql
                else self.histories
            )
        ]

    async def fetchval(self, sql, *args):
        if "identity.tenant_members" in sql:
            return self.operator
        return self.active

    async def fetchrow(self, sql, *args):
        if "claim_fund_nav_sync_attempt" in sql:
            code = args[0]
            claimed = code not in self.attempts
            self.attempts.add(code)
            return {"claimed": claimed, "last_sync_attempt_at": NOW}
        return None

    async def execute(self, sql, *args):
        import json

        self.commands.append(sql)
        if "fund_nav_chart_histories" in sql:
            self.histories.append(json.loads(args[2]))
        elif "macro_evidence_attempts" in sql:
            if "'CLAIMED'" in sql:
                self.active = True
            else:
                self.active = False
                self.finishes.append((args[2], args[4], json.loads(args[5])))
        return "INSERT 0 1"


def connect_store(monkeypatch, store):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def conn(pool):
        store.connections += 1
        try:
            yield store
        finally:
            store.connections -= 1

    monkeypatch.setattr(module(), "system_connection", conn)


@pytest.mark.asyncio
async def test_provider_runs_outside_connections_and_only_appends_chart_history(
    monkeypatch,
):
    m = module()
    store = SourceStore()
    connect_store(monkeypatch, store)

    async def fetch(code, end):
        assert store.connections == 0 and code == "008281" and end == NOW.date()
        return payload()

    result = await m.refresh_full_nav_history(
        object(), "008281", now=NOW, fetcher=fetch, now_provider=lambda: NOW
    )
    assert result["fullHistory"] is True
    assert len(store.histories) == 1
    assert store.finishes[0][2]["status"] == "SUCCESS"
    assert not any(
        "fund_research_nav_histories" in sql or "DELETE" in sql or "UPDATE" in sql
        for sql in store.commands
    )


@pytest.mark.asyncio
async def test_concurrent_tenant_requests_share_one_durable_provider_claim(monkeypatch):
    import asyncio

    m = module()
    store = SourceStore()
    connect_store(monkeypatch, store)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def fetch(code, end):
        calls.append(code)
        entered.set()
        await release.wait()
        return payload()

    first = asyncio.create_task(
        m.refresh_full_nav_history(
            object(), "008281", now=NOW, fetcher=fetch, now_provider=lambda: NOW
        )
    )
    await entered.wait()
    assert (
        await m.refresh_full_nav_history(
            object(), "008281", now=NOW, fetcher=fetch, now_provider=lambda: NOW
        )
        is None
    )
    release.set()
    await first
    assert calls == ["008281"] and len(store.histories) == 1


@pytest.mark.asyncio
async def test_failed_provider_preserves_prior_cache_and_cancellation_releases_claim(
    monkeypatch,
):
    import asyncio
    from backend.errors import DataUnavailableError

    m = module()
    old = {"existing": "actual partial cache"}
    store = SourceStore(old=old)
    connect_store(monkeypatch, store)

    async def fail(code, end):
        raise TimeoutError()

    with pytest.raises(DataUnavailableError):
        await m.refresh_full_nav_history(
            object(), "008281", now=NOW, fetcher=fail, now_provider=lambda: NOW
        )
    assert (
        store.histories == [old]
        and store.finishes[-1][2]["errorClass"] == "TimeoutError"
    )

    async def cancel(code, end):
        raise asyncio.CancelledError()

    # A failed fetch remains on the durable cooldown. Test cancellation on an
    # independent claim rather than silently retrying the failed one.
    with pytest.raises(DataUnavailableError):
        await m.refresh_full_nav_history(
            object(), "008281", now=NOW, fetcher=cancel, now_provider=lambda: NOW
        )
    store.attempts.clear()  # Simulate the next eligible attempt after 12 hours.
    with pytest.raises(asyncio.CancelledError):
        await m.refresh_full_nav_history(
            object(), "008281", now=NOW, fetcher=cancel, now_provider=lambda: NOW
        )
    assert store.histories == [old] and store.finishes[-1][0] == "CANCELLED"


@pytest.mark.asyncio
async def test_clock_regression_cannot_write_an_earlier_success_watermark(monkeypatch):
    from datetime import timedelta

    m = module()
    store = SourceStore()
    connect_store(monkeypatch, store)
    times = iter([NOW + timedelta(seconds=2), NOW + timedelta(seconds=1)])

    async def fetch(code, end):
        return payload()

    await m.refresh_full_nav_history(
        object(), "008281", now=NOW, fetcher=fetch, now_provider=lambda: next(times)
    )
    assert len(store.histories) == 1
    assert store.finishes[-1][2]["status"] == "FAILED"
    assert store.finishes[-1][2]["errorClass"] == "ClockRegression"


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["full_worker", "macro_worker"])
async def test_both_worker_dispatchers_execute_the_real_full_history_path(
    monkeypatch, consumer
):
    from types import SimpleNamespace
    from uuid import uuid4
    from backend.jobs import data_tasks, fund_research

    m = module()
    store = SourceStore()
    connect_store(monkeypatch, store)

    async def source(code, end):
        assert store.connections == 0
        row = payload()
        row["queryEnd"] = end.isoformat()
        return row

    monkeypatch.setattr(m, "fetch_full_payload", source)
    lease = data_tasks.DataTaskLease(
        str(uuid4()),
        str(uuid4()),
        "FUND_UNIT_NAV_HISTORY_FETCH",
        {"fundCode": "008281", "requestedBy": str(uuid4())},
        1,
        str(uuid4()),
    )
    if consumer == "full_worker":
        await data_tasks.process_data_task(object(), SimpleNamespace(), lease)
    else:
        await fund_research._refresh_request(object(), SimpleNamespace(), lease)
    assert len(store.histories) == 1 and store.histories[0]["fullHistory"] is True


@pytest.mark.asyncio
async def test_revoked_operator_never_starts_a_provider_request(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    m = module()
    store = SourceStore(operator=False)
    connect_store(monkeypatch, store)

    async def forbidden(*args):
        raise AssertionError("no authority to fetch")

    monkeypatch.setattr(m, "fetch_full_payload", forbidden)
    with pytest.raises(ValueError, match="no longer authorized"):
        await m.process_full_history_task(
            object(),
            SimpleNamespace(
                tenant_id=str(uuid4()),
                payload={"fundCode": "008281", "requestedBy": str(uuid4())},
            ),
        )
    assert store.histories == [] and store.finishes == []


@pytest.mark.asyncio
async def test_new_confirmed_nav_bypasses_a_same_day_but_older_full_cache(monkeypatch):
    m = module()
    old = m.validate_full_payload(payload(), "008281", NOW)
    store = SourceStore(old=old)
    research = dict(old)
    research["points"] = [*old["points"], {"date": "2026-10-20", "close": "1.7"}]
    store.research = [research]
    connect_store(monkeypatch, store)
    calls = []

    async def fetch(code, end):
        calls.append(code)
        row = payload()
        row["points"].append({"date": "2026-10-20", "close": "1.7"})
        row["receivedRowCount"] = 4
        return row

    result = await m.refresh_full_nav_history(
        object(), "008281", now=NOW, fetcher=fetch, now_provider=lambda: NOW
    )
    assert calls == ["008281"] and result["asOf"] == "2026-10-20"


@pytest.mark.asyncio
async def test_worker_reuses_a_recently_checked_qdii_cache_across_midnight(monkeypatch):
    from datetime import timedelta

    m = module()
    checked = NOW - timedelta(hours=10)
    raw = payload()
    raw["queryEnd"] = checked.date().isoformat()
    raw["points"][-1]["date"] = "2026-10-16"
    cached = m.validate_full_payload(raw, "008281", checked)
    store = SourceStore(old=cached)
    connect_store(monkeypatch, store)

    async def forbidden(*args):
        raise AssertionError("recent full disclosure does not need a new request")

    result = await m.refresh_full_nav_history(
        object(), "008281", now=NOW, fetcher=forbidden, now_provider=lambda: NOW
    )
    assert result["asOf"] == "2026-10-16" and len(store.histories) == 1


@pytest.mark.asyncio
async def test_worker_rechecks_expired_full_cache_without_any_new_research_tail(
    monkeypatch,
):
    from datetime import timedelta

    m = module()
    checked = NOW - timedelta(days=10)
    raw = payload()
    raw["queryEnd"] = checked.date().isoformat()
    raw["points"][-1]["date"] = checked.date().isoformat()
    cached = m.validate_full_payload(raw, "008281", checked)
    store = SourceStore(old=cached)
    connect_store(monkeypatch, store)
    calls = []

    async def source(code, end):
        calls.append(code)
        return payload()

    result = await m.refresh_full_nav_history(
        object(), "008281", now=NOW, fetcher=source, now_provider=lambda: NOW
    )
    assert calls == ["008281"] and result["asOf"] == "2026-10-19"
