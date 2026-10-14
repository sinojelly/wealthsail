"""Macro completeness follows real reporting months and source collections."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from importlib import import_module, util
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 14, 8, tzinfo=SHANGHAI)
SOURCES = {
    "CPI_YOY": "cpi",
    "PMI": "pmi",
    "PPI_YOY": "ppi",
    "M2_YOY": "hbgyl",
}


def _job():
    name = "backend.jobs.investment_macro_refresh"
    assert util.find_spec(name), "macro bootstrap implementation is missing"
    return import_module(name)


def _months(start, end):
    while start <= end:
        yield start
        start = date(start.year + (start.month == 12), start.month % 12 + 1, 1)


class History:
    def __init__(self, *, complete=True, collected_at=NOW):
        self.rows = []
        self.snapshots = []
        self.sql = []
        if complete:
            self.fill(collected_at=collected_at)

    def fill(self, *, collected_at=NOW, codes=None, ends=None):
        for code in codes or SOURCES:
            self.rows = [row for row in self.rows if row["field_code"] != code]
            end = (ends or {}).get(code, date(2026, 9, 1))
            self.rows.extend(
                {"field_code": code, "period": period, "value": Decimal("-0.2")}
                for period in _months(date(2021, 10, 1), end)
            )
            self.snapshots = [
                row for row in self.snapshots if row["source_uri"] != self.uri(code)
            ]
            self.snapshots.append(
                {
                    "source_uri": self.uri(code),
                    "source_as_of": datetime.combine(
                        end, datetime.min.time(), SHANGHAI
                    ),
                    "retrieved_at": collected_at,
                }
            )

    @staticmethod
    def uri(code):
        return f"https://data.eastmoney.com/cjsj/{SOURCES[code]}.html"

    async def fetch(self, sql, *args):
        self.sql.append((sql, args))
        return self.rows if "o.field_code" in sql else self.snapshots

    @asynccontextmanager
    async def connection(self, pool):
        yield self


def _setup(monkeypatch, history, *, refresh=None):
    job = _job()
    monkeypatch.setattr(job, "system_connection", history.connection)
    sync = AsyncMock(side_effect=refresh or (lambda *_: {"observations": 0}))
    monkeypatch.setattr(job, "sync_macro", sync)
    return job, sync


@pytest.mark.asyncio
async def test_empty_history_bootstraps_all_four_sources(monkeypatch):
    history = History(complete=False)

    async def refresh(pool, now):
        history.fill(collected_at=now)
        return {"observations": 240}

    job, sync = _setup(monkeypatch, history, refresh=refresh)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "COMPLETED"
    assert result["observations"] == 240
    assert set(result["coverage"]) == set(SOURCES)
    assert all(row["complete"] for row in result["coverage"].values())
    assert all(
        row["publicationVerified"] is False for row in result["coverage"].values()
    )
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_recent_continuous_history_skips_even_if_current_month_unpublished(
    monkeypatch,
):
    history = History(collected_at=NOW - timedelta(hours=10))
    job, sync = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "SKIPPED"
    assert result["observations"] == 0
    assert result["coverage"]["CPI_YOY"]["lastPeriod"] == "2026-09"
    sync.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", [date(2021, 10, 1), date(2024, 7, 1), date(2026, 9, 1)])
async def test_missing_start_middle_or_source_reported_tail_forces_backfill(
    monkeypatch, gap
):
    history = History()
    history.rows = [
        row
        for row in history.rows
        if not (row["field_code"] == "PMI" and row["period"] == gap)
    ]
    job, sync = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "UNAVAILABLE"
    assert result["coverage"]["PMI"]["missingPeriods"] == [gap.strftime("%Y-%m")]
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_series_is_not_complete_with_other_sources_present(monkeypatch):
    history = History()
    history.rows = [row for row in history.rows if row["field_code"] != "M2_YOY"]
    job, sync = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "UNAVAILABLE"
    assert result["coverage"]["M2_YOY"]["complete"] is False
    sync.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [timedelta(hours=24), timedelta(hours=25)])
async def test_24_hour_collection_expiry_refetches(monkeypatch, age):
    history = History(collected_at=NOW - age)
    job, sync = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "UNAVAILABLE"
    assert not result["coverage"]["PMI"]["fresh"]
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_unchanged_facts_use_latest_collection_snapshot_not_old_observation_time(
    monkeypatch,
):
    history = History(collected_at=NOW - timedelta(hours=25))

    async def refresh(pool, now):
        # Persistence deduplicates unchanged values; observations remain unchanged.
        for snapshot in history.snapshots:
            snapshot["retrieved_at"] = now
        return {"observations": 240}

    job, _ = _setup(monkeypatch, history, refresh=refresh)
    first = await job.ensure_macro_history(object(), NOW)
    second = await job.ensure_macro_history(object(), NOW + timedelta(hours=1))
    assert first["status"] == "COMPLETED"
    assert second["status"] == "SKIPPED"


@pytest.mark.asyncio
async def test_each_source_ends_at_its_actual_available_period_not_exactly_60_months(
    monkeypatch,
):
    history = History()
    history.fill(ends={"PMI": date(2026, 10, 1), "M2_YOY": date(2026, 8, 1)})
    job, sync = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "SKIPPED"
    assert result["coverage"]["PMI"]["observations"] == 61
    assert result["coverage"]["M2_YOY"]["observations"] == 59
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_month_roll_moves_window_and_does_not_infer_new_release(monkeypatch):
    later = datetime(2026, 11, 1, 7, tzinfo=SHANGHAI)
    history = History(collected_at=later - timedelta(hours=2))
    job, sync = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), later)
    assert result["status"] == "SKIPPED"
    assert result["coverage"]["PMI"]["firstPeriod"] == "2021-11"
    assert result["coverage"]["PMI"]["observations"] == 59
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_forced_refresh_detects_failed_source_even_while_old_source_is_fresh(
    monkeypatch,
):
    history = History(collected_at=NOW - timedelta(hours=10))

    async def refresh(pool, now):
        history.fill(collected_at=now, codes=["PMI", "PPI_YOY", "M2_YOY"])
        return {"observations": 180}

    job, _ = _setup(monkeypatch, history, refresh=refresh)
    result = await job.refresh_macro_history(object(), NOW)
    assert result["status"] == "PARTIAL"
    assert result["observations"] == 180
    assert result["coverage"]["CPI_YOY"]["fresh"]
    assert not result["coverage"]["CPI_YOY"]["refreshed"]
    assert result["coverage"]["PMI"]["refreshed"]


@pytest.mark.asyncio
async def test_successful_source_with_gap_is_partial_and_keeps_gap_visible(monkeypatch):
    history = History()

    async def refresh(pool, now):
        history.fill(collected_at=now)
        history.rows = [
            row for row in history.rows if row["period"] != date(2024, 3, 1)
        ]
        return {"observations": 236}

    job, _ = _setup(monkeypatch, history, refresh=refresh)
    result = await job.refresh_macro_history(object(), NOW)
    assert result["status"] == "PARTIAL"
    assert result["coverage"]["PMI"]["missingPeriods"] == ["2024-03"]


@pytest.mark.asyncio
async def test_provider_exception_preserves_existing_facts_and_does_not_claim_completion(
    monkeypatch,
):
    history = History(collected_at=NOW - timedelta(hours=25))
    before = list(history.rows)

    async def refresh(pool, now):
        raise RuntimeError("https://worker:secret@example.test provider failure")

    job, _ = _setup(monkeypatch, history, refresh=refresh)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "REFRESH_FAILED"
    assert history.rows == before
    assert result["coverage"]["PMI"]["observations"] == 60
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_future_collection_cannot_make_history_fresh(monkeypatch):
    history = History(collected_at=NOW + timedelta(hours=1))
    job, sync = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "UNAVAILABLE"
    assert not result["coverage"]["CPI_YOY"]["fresh"]
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_latest_revision_with_nan_creates_missing_month_instead_of_false_complete(
    monkeypatch,
):
    history = History()
    next(row for row in history.rows if row["field_code"] == "PMI")["value"] = Decimal(
        "NaN"
    )
    job, _ = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), NOW)
    assert result["status"] == "UNAVAILABLE"
    assert result["coverage"]["PMI"]["missingPeriods"] == ["2021-10"]


@pytest.mark.asyncio
async def test_queries_scope_sources_revision_and_method_without_changing_facts(
    monkeypatch,
):
    history = History()
    job, _ = _setup(monkeypatch, history)
    await job.ensure_macro_history(object(), NOW)
    queries = " ".join(sql for sql, _ in history.sql)
    assert "AKSHARE_RESEARCH_MACRO" in queries
    assert "research-macro-history-v2" in queries
    assert "revision DESC" in queries
    assert "publicationVerified" in queries
    assert "JOIN reference.data_sources" in queries
    assert "o.observed_at <= $2" in queries
    assert "s.retrieved_at <= $2" in queries
    assert queries.count("s.published_at IS NULL OR s.published_at <= $2") == 2
    assert "s.as_of <= $2" in queries
    assert "INSERT" not in queries and "UPDATE" not in queries


@pytest.mark.asyncio
async def test_provider_tail_regression_cannot_hide_later_known_month(monkeypatch):
    history = History(collected_at=NOW - timedelta(hours=25))
    known_september = next(
        row
        for row in history.rows
        if row["field_code"] == "CPI_YOY" and row["period"] == date(2026, 9, 1)
    )

    async def refresh(pool, now):
        history.fill(collected_at=now, ends={"CPI_YOY": date(2026, 8, 1)})
        # A truncated source response never deletes the previously known fact.
        history.rows.append(known_september)
        return {"observations": 239}

    job, _ = _setup(monkeypatch, history, refresh=refresh)
    result = await job.refresh_macro_history(object(), NOW)
    assert result["status"] == "PARTIAL"
    cpi = result["coverage"]["CPI_YOY"]
    assert not cpi["complete"]
    assert cpi["sourceLatestPeriod"] == "2026-08"
    assert cpi["lastPeriod"] == "2026-09"


@pytest.mark.asyncio
async def test_failed_database_coverage_check_is_unavailable_without_fetch(monkeypatch):
    history = History()
    history.fetch = AsyncMock(side_effect=RuntimeError("database offline"))
    job, sync = _setup(monkeypatch, history)
    result = await job.ensure_macro_history(object(), NOW)
    assert result == {
        "status": "UNAVAILABLE",
        "reason": "COVERAGE_CHECK_FAILED",
        "observations": 0,
        "coverage": {},
    }
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_cancellation_propagates_out_of_collection(monkeypatch):
    history = History(complete=False)

    async def refresh(pool, now):
        import asyncio

        raise asyncio.CancelledError

    job, _ = _setup(monkeypatch, history, refresh=refresh)
    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await job.ensure_macro_history(object(), NOW)
