"""NAV fallback retains unit-price provenance and has a real process deadline."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import importlib
import importlib.util
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from backend.integrations.market_data import Observation


def adapter():
    assert (
        importlib.util.find_spec("backend.integrations.fund_nav_fallback") is not None
    )
    return importlib.import_module("backend.integrations.fund_nav_fallback")


def obs(value="1.01", day=16):
    return Observation(
        symbol="162411",
        subject_type="FUND",
        field_code="NAV",
        value=Decimal(value),
        as_of=datetime(2026, 10, day, tzinfo=timezone.utc),
        quality_status="STALE",
        metadata={"officialUnitNav": True},
    )


def prove(rows):
    from backend.integrations.nav_history_proof import nav_points_digest

    points = [{"date": r.as_of.date().isoformat(), "close": str(r.value)} for r in rows]
    proof = {
        "methodVersion": "eastmoney-nav-history-v1",
        "fundCode": "162411",
        "sourceUri": "https://api.fund.eastmoney.com/f10/lsjz",
        "queryStart": "2026-10-01",
        "queryEnd": "2026-10-19",
        "totalCount": len(rows),
        "receivedRowCount": len(rows),
        "uniquePointCount": len(rows),
        "pointCount": len(points[-500:]),
        "pageCount": 1,
        "requiredPages": 1,
        "complete": True,
        "firstDate": points[-500:][0]["date"],
        "lastDate": points[-1]["date"],
        "pointsDigest": nav_points_digest(points[-500:]),
        "retrievedAt": datetime.now(timezone.utc).isoformat(),
    }
    for r in rows:
        r.metadata["providerHistoryProof"] = proof
    return rows


@pytest.mark.asyncio
async def test_successful_primary_history_does_not_launch_another_provider(monkeypatch):
    m = adapter()

    async def primary(*args, **kwargs):
        return prove([obs()])

    async def forbidden(*args, **kwargs):
        raise AssertionError("sufficient primary NAV must not double provider requests")

    monkeypatch.setattr(m, "fetch_fund_nav_history", primary)
    monkeypatch.setattr(m, "_run_akshare", forbidden)
    rows = await m.fetch_fund_nav_history_with_fallback(
        "162411", date(2026, 10, 1), date(2026, 10, 19), None, minimum_points=1
    )
    assert rows[0].value == Decimal("1.01")


@pytest.mark.asyncio
async def test_failed_primary_uses_verified_unit_nav_fallback_with_source_proof(
    monkeypatch,
):
    m = adapter()

    async def primary(*args, **kwargs):
        raise RuntimeError("primary unavailable")

    async def fallback(*args, **kwargs):
        return {
            "adapter": "fund_open_fund_info_em",
            "sourceUri": "https://fund.eastmoney.com/pingzhongdata/162411.js",
            "rows": [
                {"date": "2026-10-15", "close": "1"},
                {"date": "2026-10-16", "close": "1.01"},
            ],
            "complete": True,
        }

    monkeypatch.setattr(m, "fetch_fund_nav_history", primary)
    monkeypatch.setattr(m, "_run_akshare", fallback)
    rows = await m.fetch_fund_nav_history_with_fallback(
        "162411", date(2026, 10, 1), date(2026, 10, 19), None
    )
    assert [r.value for r in rows] == [Decimal("1"), Decimal("1.01")]
    proof = rows[0].metadata["providerHistoryProof"]
    assert proof["methodVersion"] == "akshare-unit-nav-history-v1"
    assert proof["sourceUri"].endswith("/162411.js")
    assert proof["pointCount"] == 2 and proof["complete"] is True
    assert proof["adapter"] == "fund_open_fund_info_em"
    assert all(r.field_code == "NAV" for r in rows)


def test_fallback_proof_retains_all_550_sync_samples():
    from backend.integrations.nav_history_proof import nav_points_digest

    m = adapter()
    start = date(2024, 1, 1)
    end = start + timedelta(days=599)
    payload = {
        "adapter": "fund_open_fund_info_em",
        "sourceUri": "https://fund.eastmoney.com/pingzhongdata/162411.js",
        "complete": True,
        "rows": [
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "close": str(1 + Decimal(i) / 10000),
            }
            for i in range(600)
        ],
    }
    rows = m._observations(payload, "162411", start, end)
    proof = rows[0].metadata["providerHistoryProof"]
    assert proof["pointCount"] == 550
    assert proof["firstDate"] == (start + timedelta(days=50)).isoformat()
    assert proof["pointsDigest"] == nav_points_digest(payload["rows"][-550:])
    assert m._complete_history(rows, "162411", start, end) is True


@pytest.mark.asyncio
async def test_insufficient_primary_keeps_longer_valid_response_if_fallback_fails(
    monkeypatch,
):
    m = adapter()

    async def primary(*args, **kwargs):
        return [obs()]

    async def fallback(*args, **kwargs):
        raise TimeoutError("fallback deadline")

    monkeypatch.setattr(m, "fetch_fund_nav_history", primary)
    monkeypatch.setattr(m, "_run_akshare", fallback)
    assert (
        len(
            await m.fetch_fund_nav_history_with_fallback(
                "162411", date(2026, 10, 1), date(2026, 10, 19), None
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_cancellation_never_starts_a_fallback_provider(monkeypatch):
    m = adapter()

    async def primary(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(m, "fetch_fund_nav_history", primary)
    with pytest.raises(asyncio.CancelledError):
        await m.fetch_fund_nav_history_with_fallback(
            "162411", date(2026, 10, 1), date(2026, 10, 19), None
        )


def test_akshare_open_uses_disclosed_unit_nav_not_cumulative_or_etf_trade_price(
    monkeypatch,
):
    m = adapter()
    calls = []

    def opened(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            {
                "净值日期": [date(2026, 10, 15), date(2026, 10, 16)],
                "单位净值": [1.01, 1.02],
                "累计净值": [99, 100],
            }
        )

    monkeypatch.setitem(
        sys.modules, "akshare", SimpleNamespace(fund_open_fund_info_em=opened)
    )
    result = m._fetch_akshare_payload(
        "162411", date(2026, 10, 1), date(2026, 10, 19), 2
    )
    assert calls == [{"symbol": "162411", "indicator": "单位净值走势"}]
    assert result["rows"][0]["close"] == "1.01"
    assert result["adapter"] == "fund_open_fund_info_em"


@pytest.mark.asyncio
async def test_fallback_process_timeout_really_terminates_the_child(monkeypatch):
    m = adapter()
    created = []
    spawn = asyncio.create_subprocess_exec

    async def sleeper(*args, **kwargs):
        process = await spawn(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
        )
        created.append(process)
        return process

    monkeypatch.setattr(m.asyncio, "create_subprocess_exec", sleeper)
    with pytest.raises(TimeoutError):
        await m._run_akshare(
            "162411",
            date(2026, 10, 1),
            date(2026, 10, 19),
            minimum_points=220,
            timeout_seconds=0.1,
        )
    assert len(created) == 1 and created[0].returncode is not None


def test_lof_adapter_requires_complete_observed_provider_pagination(monkeypatch):
    m = adapter()
    import requests

    def opened(**kwargs):
        raise ValueError("no open-fund series")

    def listed(**kwargs):
        requests.get(
            "https://api.fund.eastmoney.com/f10/lsjz",
            params={"fundCode": kwargs["fund"], "pageIndex": 1, "pageSize": 20},
        )
        return pd.DataFrame(
            {"净值日期": [date(2026, 10, 16)], "单位净值": [1.01], "累计净值": [99]}
        )

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "PageIndex": 1,
                "PageSize": 20,
                "TotalCount": 1,
                "Data": {"LSJZList": [{"FSRQ": "2026-10-16", "DWJZ": "1.01"}]},
            }

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_open_fund_info_em=opened, fund_etf_fund_info_em=listed),
    )
    result = m._fetch_akshare_payload(
        "162411", date(2026, 10, 1), date(2026, 10, 19), 1
    )
    assert result["adapter"] == "fund_etf_fund_info_em" and result["complete"] is True
    assert result["sourceUri"] == "https://api.fund.eastmoney.com/f10/lsjz"
    assert result["rows"] == [{"date": "2026-10-16", "close": "1.01"}]


def test_lof_adapter_cannot_certify_a_partial_dataframe(monkeypatch):
    m = adapter()

    def opened(**kwargs):
        raise ValueError("no open-fund series")

    def listed(**kwargs):
        return pd.DataFrame({"净值日期": [date(2026, 10, 16)], "单位净值": [1.01]})

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_open_fund_info_em=opened, fund_etf_fund_info_em=listed),
    )
    with pytest.raises(RuntimeError, match="complete"):
        m._fetch_akshare_payload("162411", date(2026, 10, 1), date(2026, 10, 19), 1)


@pytest.mark.asyncio
async def test_foreign_fund_source_from_fallback_cannot_replace_known_history(
    monkeypatch,
):
    m = adapter()

    async def primary(*args, **kwargs):
        return [obs()]

    async def fallback(*args, **kwargs):
        return {
            "adapter": "fund_open_fund_info_em",
            "complete": True,
            "sourceUri": "https://fund.eastmoney.com/pingzhongdata/270042.js",
            "rows": [{"date": "2026-10-16", "close": "99"}],
        }

    monkeypatch.setattr(m, "fetch_fund_nav_history", primary)
    monkeypatch.setattr(m, "_run_akshare", fallback)
    rows = await m.fetch_fund_nav_history_with_fallback(
        "162411", date(2026, 10, 1), date(2026, 10, 19), None
    )
    assert rows[0].value == Decimal("1.01")


@pytest.mark.asyncio
@pytest.mark.parametrize("bounds", [{"page_size": 0}, {"max_pages": 0}])
async def test_invalid_pagination_is_rejected_before_launching_any_provider(
    monkeypatch, bounds
):
    m = adapter()

    async def forbidden(*args, **kwargs):
        raise AssertionError("invalid caller bounds must not reach a provider")

    monkeypatch.setattr(m, "fetch_fund_nav_history", forbidden)
    monkeypatch.setattr(m, "_run_akshare", forbidden)
    with pytest.raises(ValueError, match="bound"):
        await m.fetch_fund_nav_history_with_fallback(
            "162411", date(2026, 10, 1), date(2026, 10, 19), None, **bounds
        )


@pytest.mark.asyncio
async def test_cancelling_fallback_really_terminates_the_provider_child(monkeypatch):
    m = adapter()
    created = []
    spawn = asyncio.create_subprocess_exec

    async def sleeper(*args, **kwargs):
        process = await spawn(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
        )
        created.append(process)
        return process

    monkeypatch.setattr(m.asyncio, "create_subprocess_exec", sleeper)
    task = asyncio.create_task(
        m._run_akshare(
            "162411",
            date(2026, 10, 1),
            date(2026, 10, 19),
            minimum_points=220,
            timeout_seconds=30,
        )
    )
    while not created:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created[0].returncode is not None


@pytest.mark.asyncio
async def test_220_rows_without_complete_proof_still_try_unit_nav_fallback(monkeypatch):
    m = adapter()
    unproved = [obs(str(Decimal("1") + Decimal(i) / 10000)) for i in range(220)]
    for i, row in enumerate(unproved):
        object.__setattr__(
            row,
            "as_of",
            datetime(2026, 10, 16, tzinfo=timezone.utc) - timedelta(days=219 - i),
        )
    called = []

    async def primary(*args, **kwargs):
        return unproved

    async def fallback(*args, **kwargs):
        called.append(True)
        return {
            "adapter": "fund_open_fund_info_em",
            "complete": True,
            "sourceUri": "https://fund.eastmoney.com/pingzhongdata/162411.js",
            "rows": [
                {"date": "2026-10-15", "close": "1"},
                {"date": "2026-10-16", "close": "1.01"},
            ],
        }

    monkeypatch.setattr(m, "fetch_fund_nav_history", primary)
    monkeypatch.setattr(m, "_run_akshare", fallback)
    result = await m.fetch_fund_nav_history_with_fallback(
        "162411", date(2026, 1, 1), date(2026, 10, 19), None
    )
    assert called
    assert (
        result[0].metadata["providerHistoryProof"]["methodVersion"]
        == "akshare-unit-nav-history-v1"
    )


@pytest.mark.asyncio
async def test_complete_short_published_history_is_kept_without_repeated_provider_fallback(
    monkeypatch,
):
    m = adapter()
    short = prove([obs(day=15), obs(day=16)])
    calls = []

    async def primary(*args, **kwargs):
        return short

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError(
            "a complete short history must not be invented or fetched twice"
        )

    monkeypatch.setattr(m, "fetch_fund_nav_history", primary)
    monkeypatch.setattr(m, "_run_akshare", forbidden)
    assert (
        await m.fetch_fund_nav_history_with_fallback(
            "162411", date(2026, 10, 1), date(2026, 10, 19), None
        )
        is short
    )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["code", "digest", "conflict", "future", "interval", "counts"]
)
async def test_primary_history_proof_must_bind_every_returned_observation(
    monkeypatch, mutation
):
    m = adapter()
    primary_rows = prove([obs(day=15), obs(day=16)])
    proof = primary_rows[0].metadata["providerHistoryProof"]
    if mutation == "code":
        proof["fundCode"] = "270042"
    elif mutation == "digest":
        proof["pointsDigest"] = "wrong"
    elif mutation == "conflict":
        primary_rows[-1].metadata["providerHistoryProof"] = {**proof, "totalCount": 99}
    elif mutation == "future":
        proof["retrievedAt"] = (
            datetime.now(timezone.utc) + timedelta(days=1)
        ).isoformat()
    elif mutation == "interval":
        proof["queryEnd"] = "2026-10-16"
    else:
        proof["uniquePointCount"] = 999
    called = []

    async def primary(*args, **kwargs):
        return primary_rows

    async def fallback(*args, **kwargs):
        called.append(True)
        return {
            "adapter": "fund_open_fund_info_em",
            "complete": True,
            "sourceUri": "https://fund.eastmoney.com/pingzhongdata/162411.js",
            "rows": [
                {"date": "2026-10-15", "close": "1"},
                {"date": "2026-10-16", "close": "1.01"},
            ],
        }

    monkeypatch.setattr(m, "fetch_fund_nav_history", primary)
    monkeypatch.setattr(m, "_run_akshare", fallback)
    result = await m.fetch_fund_nav_history_with_fallback(
        "162411", date(2026, 10, 1), date(2026, 10, 19), None, minimum_points=1
    )
    assert called
    assert (
        result[0].metadata["providerHistoryProof"]["methodVersion"]
        == "akshare-unit-nav-history-v1"
    )
