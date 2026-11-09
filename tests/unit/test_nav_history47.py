"""Authenticated chart reads keep real NAV, period depth and dispatch separate."""

from datetime import datetime, timedelta
from importlib import import_module, util
import json
from zoneinfo import ZoneInfo

import pytest

from backend.errors import ValidationError_

NOW = datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def module():
    assert util.find_spec("backend.funds.nav_history"), "NAV chart cache reader missing"
    return import_module("backend.funds.nav_history")


@pytest.fixture(autouse=True)
def offline_sync(monkeypatch):
    async def local_only(conn, codes, now, **kwargs):
        rows = []
        for table in ("fund_nav_chart_histories", "fund_research_nav_histories"):
            rows.extend(
                await conn.fetch(f"SELECT payload FROM market.{table}", codes[0], now)
            )
        return {
            "histories": [module().obj(row["payload"]) for row in rows],
            "asOf": now,
            "refresh": {},
            "latestConfirmed": {},
        }

    monkeypatch.setattr(module(), "ensure_nav_histories", local_only)


def history(*, full=False):
    return {
        "fundCode": "008281",
        "priceBasis": "UNIT_NAV",
        "sourceUri": "https://fund.eastmoney.com/008281.html",
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "fullHistory": full,
        "points": [
            {"date": day, "close": value}
            for day, value in (
                ("2024-01-01", "1"),
                ("2025-10-19", "2"),
                ("2025-10-20", "3"),
                ("2026-04-20", "4"),
                ("2026-10-19", "3"),
            )
        ],
    }


@pytest.mark.parametrize(
    "period,dates",
    [
        ("6m", ["2026-04-20", "2026-10-19"]),
        ("1y", ["2025-10-20", "2026-04-20", "2026-10-19"]),
        ("2y", ["2025-10-19", "2025-10-20", "2026-04-20", "2026-10-19"]),
        ("all", ["2024-01-01", "2025-10-19", "2025-10-20", "2026-04-20", "2026-10-19"]),
    ],
)
def test_periods_slice_actual_dates_and_keep_original_units(period, dates):
    m = module()
    result = m.history_view("008281", "芯片联接A", period, history(), NOW)
    assert [p["date"] for p in result["points"]] == dates
    assert result["priceBasis"] == "UNIT_NAV" and result["unit"] == "CNY/份"
    assert result["summary"]["currentNav"] == "3"


def test_midrank_uses_observations_rather_than_min_max_position():
    result = module().history_view("008281", "芯片联接A", "all", history(), NOW)
    assert result["summary"] == {
        "minimumNav": "1",
        "maximumNav": "4",
        "currentNav": "3",
        "percentilePct": 60.0,
        "percentileMethod": "EMPIRICAL_MIDRANK_V1",
    }


def test_two_year_chart_window_matches_latest_nav_anchored_research_samples():
    from backend.funds.nav_window import two_year_nav_points

    row = history()
    row["points"] = [
        {"date": (NOW.date() - timedelta(days=i + 3)).isoformat(), "close": str(i + 1)}
        for i in reversed(range(900))
    ]
    result = module().history_view("008281", "芯片联接A", "2y", row, NOW)
    expected = two_year_nav_points(row["points"])
    assert len(expected) == len(result["points"]) == 500
    assert result["points"][0]["date"] == expected[0]["date"]
    assert result["points"][-1]["date"] == expected[-1]["date"]
    assert result["coverage"]["requestedStartDate"] == "2024-10-17"


def test_constant_series_is_fifty_and_month_subtraction_handles_leap_day():
    row = history()
    for p in row["points"]:
        p["close"] = "3"
    result = module().history_view("008281", "芯片联接A", "all", row, NOW)
    assert result["summary"]["percentilePct"] == 50
    leap = datetime(2024, 2, 29, 9, tzinfo=NOW.tzinfo)
    assert module().period_start("1y", leap).isoformat() == "2023-02-28"


@pytest.mark.parametrize(
    "change", ["conflict", "future", "currency", "code", "zero", "future_retrieval"]
)
def test_invalid_cohort_is_never_drawn_or_claimed_complete(change):
    row = history()
    if change == "conflict":
        row["points"].append({"date": "2026-10-19", "close": "8"})
    if change == "future":
        row["points"].append({"date": "2026-10-21", "close": "8"})
    if change == "currency":
        row["priceBasis"] = "ACCUMULATED_NAV"
    if change == "code":
        row["fundCode"] = "000051"
    if change == "zero":
        row["points"][-1]["close"] = "0"
    if change == "future_retrieval":
        row["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    assert module().history_view("008281", "芯片联接A", "all", row, NOW)["points"] == []


class Cache:
    def __init__(self, *, chart=None, research=None, operator=True, identity_count=1):
        self.chart, self.research, self.operator = chart, research, operator
        self.identity_count = identity_count
        self.jobs = []

    async def fetch(self, sql, *args):
        if "research_fund_catalog" in sql:
            return [{"fund_name": "芯片联接A", "currency": "CNY"}] * self.identity_count
        row = self.chart if "fund_nav_chart_histories" in sql else self.research
        return [{"payload": json.dumps(row)}] if row else []

    async def fetchval(self, sql, *args):
        return self.operator

    async def fetchrow(self, sql, *args):
        if "enqueue_fund_nav_chart_fetch" in sql:
            self.jobs.append(args)
            return {"id": "job47", "status": "PENDING"}
        return None

    async def execute(self, *args):
        return "SELECT 1"

    def transaction(self):
        class Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        return Tx()


@pytest.mark.asyncio
async def test_short_period_reuses_confirmed_cache_without_queueing():
    store = Cache(research=history())
    result = await module().get_nav_history(
        store, "008281", "1y", tenant_id="t", user_id="u", now=NOW
    )
    assert result["state"] == "READY" and result["refresh"]["state"] == "IDLE"
    assert store.jobs == []


@pytest.mark.asyncio
async def test_all_missing_depth_auto_queues_but_read_only_user_does_not_dispatch():
    store = Cache(research=history())
    result = await module().get_nav_history(
        store, "008281", "all", tenant_id="t", user_id="u", now=NOW
    )
    assert result["state"] == "PARTIAL" and result["refresh"]["state"] == "QUEUED"
    assert len(store.jobs) == 1
    read = Cache(research=history(), operator=False)
    result = await module().get_nav_history(
        read, "008281", "all", tenant_id="t", user_id="u", now=NOW
    )
    assert result["refresh"]["state"] == "IDLE" and read.jobs == []


@pytest.mark.asyncio
async def test_ambiguous_identity_is_rejected_before_queueing():
    store = Cache(identity_count=2)
    with pytest.raises(ValidationError_):
        await module().get_nav_history(
            store, "008281", "all", tenant_id="t", user_id="u", now=NOW
        )
    assert store.jobs == []


def test_old_full_flag_without_independent_complete_proof_is_not_trusted():
    row = history(full=True)
    result = module().history_view("008281", "芯片联接A", "all", row, NOW)
    assert result["coverage"]["fullHistory"] is False


@pytest.mark.parametrize("period,count", [("6m", 2), ("1y", 3), ("2y", 4), ("all", 5)])
def test_authenticated_endpoint_returns_the_real_history_contract(
    monkeypatch, period, count
):
    from contextlib import asynccontextmanager
    from fastapi.testclient import TestClient
    from backend.common.tenancy import TenantContext
    from backend.dependencies import get_pool, get_tenant_context
    from backend.funds import api
    from backend.main import create_app

    store = Cache(research=history(), operator=False)

    @asynccontextmanager
    async def transaction(*args):
        yield store

    monkeypatch.setattr(api, "tenant_transaction", transaction)
    monkeypatch.setattr(api, "now_shanghai", lambda: NOW)
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext("u", "t")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/api/v1/funds/008281/nav-history?period={period}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["methodVersion"] == "fund-unit-nav-trend-v1"
    assert len(data["points"]) == count and data["points"][-1]["unitNav"] == "3"
    assert "historyProof" not in data
    assert store.jobs == []
    assert client.get("/api/v1/funds/008281/nav-history?period=3y").status_code == 400
    assert client.get("/api/v1/funds/not-a-code/nav-history").status_code == 400


def test_nav_history_endpoint_requires_authentication():
    from fastapi.testclient import TestClient
    from backend.dependencies import get_pool
    from backend.main import create_app

    app = create_app()
    app.dependency_overrides[get_pool] = lambda: object()
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/funds/008281/nav-history"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_new_confirmed_nav_prevents_an_older_full_chart_from_hiding_the_refresh():
    from test_nav_full_history47 import payload
    from backend.jobs.fund_nav_full_history import validate_full_payload

    chart = validate_full_payload(payload(), "008281", NOW)
    recent = history()
    recent["points"].append({"date": "2026-10-20", "close": "1.7"})
    store = Cache(chart=chart, research=recent)
    result = await module().get_nav_history(
        store, "008281", "all", tenant_id="t", user_id="u", now=NOW
    )
    assert result["state"] == "PARTIAL" and result["coverage"]["fullHistory"] is False
    assert result["refresh"]["state"] == "QUEUED" and len(store.jobs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "period,latest_day,expected_date,expected_nav,expected_percentile,expected_refresh",
    [
        ("2y", "27", "2026-10-27", "2.9733", 37.5, "IDLE"),
        ("2y", "28", "2026-10-28", "3.0027", 70.0, "IDLE"),
        ("all", "27", "2026-10-26", "2.9767", 62.5, "QUEUED"),
        ("all", "28", "2026-10-26", "2.9767", 62.5, "QUEUED"),
    ],
)
async def test_latest_two_year_nav_is_not_hidden_by_an_older_complete_history(
    period,
    latest_day,
    expected_date,
    expected_nav,
    expected_percentile,
    expected_refresh,
):
    from backend.jobs.fund_nav_full_history import validate_full_payload

    now = datetime(2026, 10, 28, 9, tzinfo=NOW.tzinfo)
    old_points = [
        {"date": "2024-10-01", "close": "2.2"},
        {"date": "2025-01-01", "close": "2.0765"},
        {"date": "2026-01-01", "close": "4.2992"},
        {"date": "2026-10-26", "close": "2.9767"},
    ]
    chart = validate_full_payload(
        {
            "fundCode": "008281",
            "sourceUri": "https://fund.eastmoney.com/pingzhongdata/008281.js",
            "queryStart": "1900-01-01",
            "queryEnd": "2026-10-28",
            "complete": True,
            "receivedRowCount": len(old_points),
            "points": old_points,
        },
        "008281",
        now,
    )
    recent = {
        "fundCode": "008281",
        "priceBasis": "UNIT_NAV",
        "sourceUri": "https://fund.eastmoney.com/008281.html",
        "retrievedAt": now.isoformat(),
        "availableAt": now.isoformat(),
        "points": [
            *old_points,
            {"date": "2026-10-27", "close": "2.9733"},
            *(
                [{"date": "2026-10-28", "close": "3.0027"}]
                if latest_day == "28"
                else []
            ),
        ],
    }
    store = Cache(chart=chart, research=recent)

    result = await module().get_nav_history(
        store, "008281", period, tenant_id="t", user_id="u", now=now
    )

    assert result["asOf"] == expected_date
    assert result["points"][-1] == {"date": expected_date, "unitNav": expected_nav}
    assert result["summary"]["currentNav"] == expected_nav
    assert result["summary"]["minimumNav"] == "2.0765"
    assert result["summary"]["maximumNav"] == "4.2992"
    assert result["summary"]["percentilePct"] == expected_percentile
    assert result["state"] == ("READY" if period == "2y" else "PARTIAL")
    assert result["refresh"]["state"] == expected_refresh
    assert result["coverage"]["fullHistory"] is False


@pytest.mark.asyncio
async def test_newer_partial_tail_does_not_replace_a_covered_two_year_history():
    from test_nav_full_history47 import payload
    from backend.jobs.fund_nav_full_history import validate_full_payload

    chart = validate_full_payload(payload(), "008281", NOW)
    recent = history()
    recent["points"] = [{"date": "2026-10-20", "close": "1.7"}]
    store = Cache(chart=chart, research=recent)

    result = await module().get_nav_history(
        store, "008281", "2y", tenant_id="t", user_id="u", now=NOW
    )

    assert result["asOf"] == "2026-10-19"
    assert result["points"][-1] == {"date": "2026-10-19", "unitNav": "1.5"}
    assert result["summary"]["currentNav"] == "1.5"
    assert result["coverage"]["latestKnownNavDate"] == "2026-10-20"
    assert result["state"] == "PARTIAL" and result["refresh"]["state"] == "QUEUED"


@pytest.mark.asyncio
async def test_complete_disclosed_old_fund_without_recent_points_is_not_queued_forever():
    from test_nav_full_history47 import payload
    from backend.jobs.fund_nav_full_history import validate_full_payload

    raw = payload()
    for i, point in enumerate(raw["points"]):
        point["date"] = f"2019-0{i + 1}-01"
    chart = validate_full_payload(raw, "008281", NOW)
    store = Cache(chart=chart)
    result = await module().get_nav_history(
        store, "008281", "1y", tenant_id="t", user_id="u", now=NOW
    )
    assert (
        result["state"] == "UNAVAILABLE" and result["coverage"]["fullHistory"] is True
    )
    assert result["refresh"]["state"] == "IDLE" and store.jobs == []


@pytest.mark.asyncio
async def test_old_full_cache_is_rechecked_even_without_a_new_research_tail():
    from test_nav_full_history47 import payload
    from backend.jobs.fund_nav_full_history import validate_full_payload

    checked = NOW - timedelta(days=10)
    raw = payload()
    raw["queryEnd"] = checked.date().isoformat()
    raw["points"][-1]["date"] = checked.date().isoformat()
    chart = validate_full_payload(raw, "008281", checked)
    store = Cache(chart=chart)
    result = await module().get_nav_history(
        store, "008281", "all", tenant_id="t", user_id="u", now=NOW
    )
    assert result["state"] == "PARTIAL" and result["asOf"] == "2026-10-10"
    assert result["coverage"]["reason"] == "SOURCE_RECHECK_DUE"
    assert result["refresh"]["state"] == "QUEUED" and len(store.jobs) == 1
    assert result["points"][-1]["unitNav"] == "1.5"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "age,expected",
    [(timedelta(hours=36), "READY"), (timedelta(hours=36, seconds=1), "PARTIAL")],
)
async def test_full_cache_recheck_is_based_on_a_bounded_checked_age(age, expected):
    from test_nav_full_history47 import payload
    from backend.jobs.fund_nav_full_history import validate_full_payload

    checked = NOW - age
    raw = payload()
    raw["queryEnd"] = checked.date().isoformat()
    raw["points"][-1]["date"] = "2026-10-16"
    chart = validate_full_payload(raw, "008281", checked)
    store = Cache(chart=chart)
    result = await module().get_nav_history(
        store, "008281", "all", tenant_id="t", user_id="u", now=NOW
    )
    assert result["state"] == expected
    assert len(store.jobs) == (1 if expected == "PARTIAL" else 0)


@pytest.mark.asyncio
async def test_freshly_checked_qdii_disclosures_are_not_stale_because_nav_is_delayed():
    from test_nav_full_history47 import payload
    from backend.jobs.fund_nav_full_history import validate_full_payload

    raw = payload()
    raw["points"][-1]["date"] = "2026-10-16"
    chart = validate_full_payload(raw, "008281", NOW)
    chart["overseasCalendarRequired"] = True
    store = Cache(chart=chart)
    result = await module().get_nav_history(
        store, "008281", "all", tenant_id="t", user_id="u", now=NOW
    )
    assert result["state"] == "READY" and result["asOf"] == "2026-10-16"
    assert result["refresh"]["state"] == "IDLE" and store.jobs == []


@pytest.mark.asyncio
async def test_deeper_period_requeue_uses_controlled_api_write_path():
    """The API can invoke its narrow helper, but cannot reset worker leases."""
    from asyncpg import InsufficientPrivilegeError

    class ApiCache(Cache):
        async def fetchrow(self, sql, *args):
            if "UPDATE workflow.job_runs" in sql:
                raise InsufficientPrivilegeError("permission denied for table job_runs")
            if "enqueue_fund_nav_chart_fetch" in sql:
                self.jobs.append(args)
                return {"id": "job47", "status": "PENDING", "reason": None}
            if "FROM workflow.job_runs" in sql:
                return {
                    "id": "job47",
                    "status": "COMPLETED",
                    "finished_at": NOW - timedelta(hours=2),
                }
            return None

    store = ApiCache(research=history())
    result = await module().get_nav_history(
        store, "008281", "all", tenant_id="t", user_id="u", now=NOW
    )
    assert result["points"][-1]["unitNav"] == "3"
    assert result["refresh"]["state"] == "QUEUED"
