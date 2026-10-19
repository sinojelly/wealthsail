"""A real full-market master is prepared before event-driven identity lookup."""

from contextlib import asynccontextmanager
from datetime import timedelta
from importlib import import_module, util
import json
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from backend.integrations.fund_master import FundMasterEntry
from test_theme_graph import NOW


def module():
    assert util.find_spec("backend.investment.discovery_master"), (
        "market-wide master bootstrap is missing"
    )
    return import_module("backend.investment.discovery_master")


def rows(count=10000):
    return [
        [f"{900000 + i:06}", "PINYIN", f"测试主题基金{i}A", "股票型-指数", "PINYINFULL"]
        for i in range(count)
    ]


def payload(records):
    return "var r = " + json.dumps(records, ensure_ascii=False) + ";"


def entries(count=10000):
    return [
        FundMasterEntry(code, name, kind, abbr, full)
        for code, abbr, name, kind, full in rows(count)
    ]


def test_parse_master_accepts_the_akshare_public_upstream_without_executing_javascript():
    parsed = module().parse_master_payload(payload(rows()))
    assert len(parsed) == 10000
    assert parsed[0].fund_code == "900000"
    assert parsed[0].fund_name == "测试主题基金0A"
    assert parsed[0].pinyin_abbr == "PINYIN"


def test_blank_publisher_fund_type_is_explicit_unknown_without_losing_real_identity():
    source = rows()
    source[-1][3] = ""
    parsed = module().parse_master_payload(payload(source))
    assert len(parsed) == 10000
    assert parsed[-1].fund_type == "UNKNOWN"
    assert parsed[-1].fund_code == source[-1][0]
    assert parsed[-1].fund_name == source[-1][2]


@pytest.mark.parametrize(
    "mutation", ["SHORT", "DUPLICATE_CONFLICT", "BAD_CODE", "JAVASCRIPT", "OVERSIZE"]
)
def test_incomplete_or_malformed_master_never_counts_as_complete(mutation):
    data = rows()
    raw = payload(data)
    if mutation == "SHORT":
        raw = payload(data[:30])
    elif mutation == "DUPLICATE_CONFLICT":
        raw = payload([*data, [data[0][0], "X", "另一身份", "股票型", "X"]])
    elif mutation == "BAD_CODE":
        data[-1][0] = "123"
        raw = payload(data)
    elif mutation == "JAVASCRIPT":
        raw += "fetch('https://untrusted.invalid/');"
    elif mutation == "OVERSIZE":
        raw = " " * (12_000_000 + 1)
    with pytest.raises(ValueError):
        module().parse_master_payload(raw)


def test_master_freshness_uses_durable_success_and_coverage_not_latest_identity_edit():
    service = module()
    success = {
        "recorded_at": NOW - timedelta(days=6),
        "result": {"status": "SUCCESS", "totalEntries": 10000},
    }
    assert (
        service.master_refresh_due(
            NOW, success=success, previous=success, catalog_count=10000
        )
        == "FRESH"
    )
    assert (
        service.master_refresh_due(
            NOW + timedelta(days=1),
            success=success,
            previous=success,
            catalog_count=10000,
        )
        == "DUE"
    )
    assert (
        service.master_refresh_due(
            NOW, success=success, previous=success, catalog_count=23
        )
        == "DUE"
    )
    assert (
        service.master_refresh_due(
            NOW, success=success, previous=success, catalog_count=9000
        )
        == "DUE"
    )
    failure = {
        "recorded_at": NOW - timedelta(minutes=59),
        "result": {"status": "FAILED"},
    }
    assert (
        service.master_refresh_due(
            NOW, success=None, previous=failure, catalog_count=23
        )
        == "COOLDOWN"
    )
    failure["recorded_at"] = NOW - timedelta(hours=1)
    assert (
        service.master_refresh_due(
            NOW, success=None, previous=failure, catalog_count=23
        )
        == "DUE"
    )


@pytest.mark.asyncio
async def test_master_http_is_one_bounded_public_request_and_rejects_403():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text=payload(rows()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await module().fetch_discovery_master(client=client)
    assert len(result) == 10000
    assert len(calls) == 1
    assert str(calls[0].url) == "https://fund.eastmoney.com/js/fundcode_search.js"
    assert "Authorization" not in calls[0].headers

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403))
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await module().fetch_discovery_master(client=client)


@pytest.mark.asyncio
async def test_success_uses_governed_upsert_and_records_actual_completion(monkeypatch):
    service = module()
    claimed = {"claimId": uuid4(), "startedAt": NOW, "catalogCount": 23}
    monkeypatch.setattr(service, "_claim_master", AsyncMock(return_value=claimed))
    finished = AsyncMock()
    monkeypatch.setattr(service, "_finish_master", finished)
    conn = object()
    in_connection = 0

    @asynccontextmanager
    async def acquire(pool):
        nonlocal in_connection
        in_connection += 1
        try:
            yield conn
        finally:
            in_connection -= 1

    monkeypatch.setattr(service, "system_connection", acquire)
    persist = AsyncMock(return_value=10000)
    monkeypatch.setattr(service, "upsert_fund_master_entries", persist)

    async def fetcher():
        assert in_connection == 0, "HTTP must not occupy a database connection"
        return entries()

    done = NOW + timedelta(seconds=2)
    result = await service.ensure_discovery_fund_master(
        object(), NOW, fetcher=fetcher, now_provider=lambda: done
    )
    assert result["status"] == "SUCCESS" and result["totalEntries"] == 10000
    assert result["sourceUri"] == "https://fund.eastmoney.com/js/fundcode_search.js"
    assert result["completedAt"] == done.isoformat()
    persist.assert_awaited_once()
    assert persist.await_args.args[0] is conn
    assert finished.await_args.args[2] == done


@pytest.mark.parametrize("failure", ["FETCH", "COVERAGE", "UPSERT"])
@pytest.mark.asyncio
async def test_master_failure_keeps_existing_catalog_and_finishes_durable_attempt(
    monkeypatch, failure
):
    service = module()
    monkeypatch.setattr(
        service,
        "_claim_master",
        AsyncMock(
            return_value={"claimId": uuid4(), "startedAt": NOW, "catalogCount": 20000}
        ),
    )
    finish = AsyncMock()
    monkeypatch.setattr(service, "_finish_master", finish)

    @asynccontextmanager
    async def acquire(pool):
        yield object()

    monkeypatch.setattr(service, "system_connection", acquire)
    persist = AsyncMock(side_effect=RuntimeError if failure == "UPSERT" else None)
    monkeypatch.setattr(service, "upsert_fund_master_entries", persist)
    fetcher = AsyncMock(
        side_effect=RuntimeError if failure == "FETCH" else None,
        return_value=entries(10000 if failure == "COVERAGE" else 20000),
    )
    result = await service.ensure_discovery_fund_master(
        object(), NOW, fetcher=fetcher, now_provider=lambda: NOW
    )
    assert result["status"] == "FAILED"
    finish.assert_awaited_once()
    assert "errorClass" in result
    if failure in {"FETCH", "COVERAGE"}:
        persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_or_active_master_claim_never_performs_network(monkeypatch):
    service = module()
    monkeypatch.setattr(
        service,
        "_claim_master",
        AsyncMock(return_value={"status": "SKIPPED", "reason": "ACTIVE_CLAIM"}),
    )
    fetcher = AsyncMock()
    result = await service.ensure_discovery_fund_master(object(), NOW, fetcher=fetcher)
    assert result == {"status": "SKIPPED", "reason": "ACTIVE_CLAIM"}
    fetcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_nav_preparation_uses_actual_master_completion_before_new_identity_lookup(
    monkeypatch,
):
    from backend.investment import discovery, theme_graph

    done = NOW + timedelta(seconds=2)
    monkeypatch.setattr(
        module(),
        "ensure_discovery_fund_master",
        AsyncMock(
            return_value={
                "status": "SUCCESS",
                "completedAt": done.isoformat(),
                "totalEntries": 10000,
            }
        ),
    )
    conn = AsyncMock()
    conn.fetch.return_value = []

    @asynccontextmanager
    async def acquire(pool):
        yield conn

    monkeypatch.setattr(discovery, "system_connection", acquire)
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    selected = AsyncMock(return_value=[])
    monkeypatch.setattr(discovery, "select_research_assets", selected)
    collect = AsyncMock(return_value={"fundHistories": [], "themeNavSourceResults": {}})
    monkeypatch.setattr(theme_graph, "collect_theme_nav_histories", collect)
    result = await discovery.collect_discovery_nav_histories(
        object(), NOW, catalysts=[]
    )
    assert selected.await_args.args[1] == done
    assert collect.await_args.args[1] == done
    assert result["fundMasterSourceResult"]["totalEntries"] == 10000


@pytest.mark.asyncio
async def test_cancelled_master_attempt_is_terminal_and_never_upserts_partial_data(
    monkeypatch,
):
    service = module()
    monkeypatch.setattr(
        service,
        "_claim_master",
        AsyncMock(
            return_value={"claimId": uuid4(), "startedAt": NOW, "catalogCount": 23}
        ),
    )
    finish, persist = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "_finish_master", finish)
    monkeypatch.setattr(service, "upsert_fund_master_entries", persist)

    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await service.ensure_discovery_fund_master(
            object(), NOW, fetcher=cancelled, now_provider=lambda: NOW
        )
    persist.assert_not_awaited()
    assert finish.await_args.kwargs["cancelled"] is True
    assert finish.await_args.args[3]["errorClass"] == "CancelledError"


@pytest.mark.parametrize(
    "stage", ["FETCH", "AFTER_UPSERT", "AFTER_RETRIEVAL", "RECOVERED"]
)
@pytest.mark.asyncio
async def test_clock_regression_never_upserts_from_future_or_fabricates_a_success_watermark(
    monkeypatch, stage
):
    service = module()
    monkeypatch.setattr(
        service,
        "_claim_master",
        AsyncMock(
            return_value={"claimId": uuid4(), "startedAt": NOW, "catalogCount": 23}
        ),
    )
    finish, persist = AsyncMock(), AsyncMock(return_value=10000)
    monkeypatch.setattr(service, "_finish_master", finish)
    monkeypatch.setattr(service, "upsert_fund_master_entries", persist)

    @asynccontextmanager
    async def acquire(pool):
        yield object()

    monkeypatch.setattr(service, "system_connection", acquire)
    samples = iter(
        {
            "FETCH": [NOW - timedelta(seconds=1), NOW - timedelta(seconds=1)],
            "AFTER_UPSERT": [NOW + timedelta(seconds=1), NOW - timedelta(seconds=1)],
            "AFTER_RETRIEVAL": [NOW + timedelta(seconds=2), NOW + timedelta(seconds=1)],
            "RECOVERED": [NOW - timedelta(seconds=1), NOW + timedelta(seconds=1)],
        }[stage]
    )
    result = await service.ensure_discovery_fund_master(
        object(),
        NOW,
        fetcher=AsyncMock(return_value=entries()),
        now_provider=lambda: next(samples),
    )
    assert result["status"] == "FAILED" and result["errorClass"] == "ClockRegression"
    if stage in {"FETCH", "RECOVERED"}:
        persist.assert_not_awaited()
    else:
        persist.assert_awaited_once()
    if stage in {"FETCH", "AFTER_UPSERT"}:
        finish.assert_not_awaited()
        assert "completedAt" not in result
    else:
        finish.assert_awaited_once()
        assert finish.await_args.args[2] == NOW + timedelta(seconds=1)
        assert finish.await_args.args[3]["status"] == "FAILED"
