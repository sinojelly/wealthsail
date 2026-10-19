"""Exchange share deltas and order-classified flows are independent public facts."""

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

NOW = datetime(2026, 10, 19, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
DATES = [date(2026, 10, 15), date(2026, 10, 16)]


def sse_row(code, name, shares, day):
    return {
        "STAT_DATE": day,
        "ETF_TYPE": "单市",
        "SEC_CODE": code,
        "NUM": "1",
        "SEC_NAME": name,
        "TOT_VOL": shares,
    }


def main_row(code="515880", value=150000000, *, day=20261016, stamp=None):
    return {
        "f12": code,
        "f14": "通信ETF国泰",
        "f62": value,
        "f124": stamp
        if stamp is not None
        else int(datetime(2026, 10, 16, 15, 0, tzinfo=NOW.tzinfo).timestamp()),
        "f297": day,
    }


def route(request):
    if request.url.host == "query.sse.com.cn":
        day = request.url.params["STAT_DATE"]
        rows = [
            sse_row(
                "510300", "沪深300ETF", "120" if day == "2026-10-16" else "100", day
            ),
            sse_row("512480", "半导体ETF", "80" if day == "2026-10-16" else "90", day),
        ]
        return httpx.Response(200, json={"result": rows, "pageHelp": {"total": 2}})
    assert request.url.host == "push2delay.eastmoney.com"
    return httpx.Response(
        200, json={"rc": 0, "data": {"total": 1, "diff": [main_row()]}}
    )


async def fetch(monkeypatch, handler=route, *, dates=DATES):
    from backend.integrations import etf_flows

    monkeypatch.setattr(etf_flows, "now_shanghai", lambda: NOW)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await etf_flows.fetch_etf_flow_signals(
            NOW, trading_dates=dates, client=client
        )


@pytest.mark.asyncio
async def test_exchange_shares_are_scaled_and_compared_without_claiming_institutional_cash(
    monkeypatch,
):
    actual = await fetch(monkeypatch)
    shares = {
        row["fundCode"]: row
        for row in actual["signals"]
        if row["metric"] == "ETF_SHARE_CHANGE"
    }
    assert shares["510300"]["value"] == "200000"
    assert shares["510300"]["previousShares"] == "1000000"
    assert shares["510300"]["currentShares"] == "1200000"
    assert shares["512480"]["value"] == "-100000"
    assert shares["510300"]["unit"] == "份"
    assert shares["510300"]["referenceDate"] == "2026-10-16"
    assert shares["510300"]["comparisonDate"] == "2026-10-15"
    assert "STAT_DATE=2026-10-16" in shares["510300"]["sourceUri"]
    assert "STAT_DATE=2026-10-15" in shares["510300"]["comparisonSourceUri"]
    assert shares["510300"]["verification"] == "EXCHANGE_REPORTED"
    assert all(row["institutionAttribution"] == "UNKNOWN" for row in actual["signals"])
    assert not any("institutionNetSubscription" in row for row in actual["signals"])
    assert actual["sourceResults"]["ETF_SHARE_CHANGE_SSE"]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_secondary_large_order_flow_retains_its_own_metric_date_and_source(
    monkeypatch,
):
    actual = await fetch(monkeypatch)
    flow = next(
        row
        for row in actual["signals"]
        if row["metric"] == "SECONDARY_MAIN_NET_FLOW_CNY"
    )
    assert flow["value"] == "150000000" and flow["unit"] == "CNY"
    assert flow["referenceDate"] == "2026-10-16"
    assert flow["observedAt"] == "2026-10-16T15:00:00+08:00"
    assert flow["verification"] == "PROVIDER_REPORTED"
    assert "二级市场" in flow["definition"] and "机构" in flow["definition"]
    assert "fid=f62" in flow["sourceUri"]


@pytest.mark.asyncio
async def test_share_delta_never_uses_zero_for_missing_prior_or_conflicting_rows(
    monkeypatch,
):
    def handler(request):
        response = route(request)
        if request.url.host == "query.sse.com.cn":
            payload = response.json()
            day = request.url.params["STAT_DATE"]
            if day == "2026-10-16":
                payload["result"].extend(
                    [
                        sse_row("510300", "沪深300ETF", "125", day),
                        sse_row("588990", "新上市芯片ETF", "800", day),
                        sse_row("512999", "未来数据ETF", "200", "2026-10-20"),
                    ]
                )
            return httpx.Response(200, json=payload)
        return response

    actual = await fetch(monkeypatch, handler)
    shares = [row for row in actual["signals"] if row["metric"] == "ETF_SHARE_CHANGE"]
    assert [row["fundCode"] for row in shares] == ["512480"]
    status = actual["sourceResults"]["ETF_SHARE_CHANGE_SSE"]
    assert status["status"] == "PARTIAL"
    assert status["conflictingFundCount"] == 1 and status["unpairedFundCount"] == 1


@pytest.mark.asyncio
async def test_wrong_quote_day_future_time_and_nonfinite_flow_are_excluded(monkeypatch):
    def handler(request):
        if request.url.host != "push2delay.eastmoney.com":
            return route(request)
        rows = [
            main_row(),
            main_row("159001", day=20261019),
            main_row(
                "159002",
                day=20261019,
                stamp=int((NOW + timedelta(minutes=1)).timestamp()),
            ),
            main_row("159003", value="NaN"),
            main_row("159004", value=True),
            main_row("159005", stamp=0),
            main_row(
                "159006",
                day=20260901,
                stamp=int(datetime(2026, 9, 1, 15, tzinfo=NOW.tzinfo).timestamp()),
            ),
        ]
        return httpx.Response(200, json={"rc": 0, "data": {"total": 7, "diff": rows}})

    actual = await fetch(monkeypatch, handler)
    flows = [
        row
        for row in actual["signals"]
        if row["metric"] == "SECONDARY_MAIN_NET_FLOW_CNY"
    ]
    assert [row["fundCode"] for row in flows] == ["515880"]
    assert actual["sourceResults"]["ETF_SECONDARY_MAIN_FLOW"]["status"] == "PARTIAL"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dates", [[], [DATES[1]], list(reversed(DATES)), [DATES[0], date(2026, 10, 20)]]
)
async def test_missing_or_unordered_calendar_does_not_guess_exchange_dates(
    monkeypatch, dates
):
    def handler(request):
        assert request.url.host != "query.sse.com.cn", (
            "without a known completed pair the exchange must not be queried"
        )
        return route(request)

    actual = await fetch(monkeypatch, handler, dates=dates)
    assert not any(row["metric"] == "ETF_SHARE_CHANGE" for row in actual["signals"])
    assert (
        actual["sourceResults"]["ETF_SHARE_CHANGE_SSE"]["errorClass"]
        == "TradingCalendarUnavailable"
    )
    assert any(
        row["metric"] == "SECONDARY_MAIN_NET_FLOW_CNY" for row in actual["signals"]
    )


@pytest.mark.asyncio
async def test_access_denial_is_not_retried_and_does_not_erase_other_source(
    monkeypatch,
):
    attempts = []

    def handler(request):
        if request.url.host == "query.sse.com.cn":
            attempts.append(request.url.params["STAT_DATE"])
            return httpx.Response(403, text="permission denied")
        return route(request)

    actual = await fetch(monkeypatch, handler)
    assert attempts == ["2026-10-15", "2026-10-16"]
    assert actual["sourceResults"]["ETF_SHARE_CHANGE_SSE"]["httpStatus"] == 403
    assert any(
        row["metric"] == "SECONDARY_MAIN_NET_FLOW_CNY" for row in actual["signals"]
    )


@pytest.mark.asyncio
async def test_etf_fund_rank_window_marks_truncation_instead_of_claiming_all_market(
    monkeypatch,
):
    def handler(request):
        if request.url.host != "push2delay.eastmoney.com":
            return route(request)
        return httpx.Response(
            200, json={"rc": 0, "data": {"total": 1656, "diff": [main_row()]}}
        )

    actual = await fetch(monkeypatch, handler)
    status = actual["sourceResults"]["ETF_SECONDARY_MAIN_FLOW"]
    assert status["status"] == "PARTIAL" and status["truncated"] is True
    assert status["providerTotal"] == 1656 and status["receivedCount"] == 1


@pytest.mark.asyncio
async def test_public_response_size_and_redirect_limits_are_enforced(monkeypatch):
    def handler(request):
        if request.url.host == "query.sse.com.cn":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))

    actual = await fetch(monkeypatch, handler)
    assert actual["signals"] == []
    assert all(
        row["status"] in {"FAILED", "UNAVAILABLE"}
        for row in actual["sourceResults"].values()
    )


@pytest.mark.asyncio
async def test_two_exchange_dates_and_secondary_flow_are_collected_concurrently(
    monkeypatch,
):
    pending, all_entered = set(), asyncio.Event()

    async def handler(request):
        pending.add(str(request.url))
        if len(pending) == 3:
            all_entered.set()
        await asyncio.wait_for(all_entered.wait(), 0.5)
        return route(request)

    actual = await fetch(monkeypatch, handler)
    assert len(actual["signals"]) == 3
