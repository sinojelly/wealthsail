"""Public adapters preserve units, provenance, bounded requests and failures."""

import asyncio
from datetime import datetime, timedelta
from importlib import import_module, util
import json
from zoneinfo import ZoneInfo

import httpx
import pytest

NOW = datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("Asia/Shanghai"))


def engine(monkeypatch):
    assert util.find_spec("backend.integrations.alpha_collectors"), (
        "alpha collectors missing"
    )
    module = import_module("backend.integrations.alpha_collectors")
    monkeypatch.setattr(module, "now_shanghai", lambda: NOW)
    return module


def payload(request):
    report = request.url.params.get("reportName") or request.url.params.get("type")
    if report == "RPT_ECONOMY_CURRENCY_SUPPLY":
        return {
            "result": {
                "data": [
                    {
                        "REPORT_DATE": "2026-09-01",
                        "CURRENCY_SAME": "3.2",
                        "BASIC_CURRENCY_SAME": "7.2",
                    },
                    {
                        "REPORT_DATE": "2026-08-01",
                        "CURRENCY_SAME": "4.3",
                        "BASIC_CURRENCY_SAME": None,
                    },
                    {
                        "REPORT_DATE": "2026-12-01",
                        "CURRENCY_SAME": 10,
                        "BASIC_CURRENCY_SAME": 20,
                    },
                ]
            }
        }
    if report == "RPTA_WEB_TREASURYYIELD":
        return {
            "result": {
                "pages": 999,
                "data": [{"SOLAR_DATE": "2026-10-16", "EMM00166466": "1.82"}],
            }
        }
    if report == "RPT_IMP_INTRESTRATEN":
        return {
            "result": {
                "pages": 999,
                "data": [{"REPORT_DATE": "2026-10-16", "IR_RATE": "1.48"}],
            }
        }
    if report == "RPT_ECONOMY_HOUSE_PRICE":
        cities = (
            ["海口", "三亚"]
            if "海口" in request.url.params.get("filter", "")
            else ["上海", "深圳"]
        )
        return {
            "result": {
                "data": [
                    {
                        "REPORT_DATE": "2026-09-01",
                        "CITY": city,
                        "FIRST_COMHOUSE_SEQUENTIAL": "99.8",
                        "SECOND_HOUSE_SEQUENTIAL": "99.4",
                    }
                    for city in cities
                ]
            }
        }
    if request.url.host == "www.cls.cn":
        stamp = int((NOW - timedelta(hours=1)).timestamp())
        return {
            "data": {
                "roll_data": [
                    {
                        "id": 123,
                        "title": "海南商业航天发射计划",
                        "content": "<p>卫星发射仍待确认。</p>",
                        "ctime": stamp,
                        "level": "A",
                    },
                    {
                        "id": 124,
                        "title": "普通娱乐报道",
                        "content": "明星动态",
                        "ctime": stamp,
                        "level": "C",
                    },
                    {
                        "id": 125,
                        "title": "HBM内存",
                        "content": "价格变化",
                        "ctime": stamp,
                        "level": "C",
                    },
                    {
                        "id": 126,
                        "title": "未来原油报道",
                        "content": "尚未发布",
                        "ctime": stamp + 9000,
                        "level": "A",
                    },
                ]
            }
        }
    if request.url.host == "gamma-api.polymarket.com":
        return [
            {
                "id": "77",
                "question": "Will an oil supply event occur?",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.37","0.63"]',
                "closed": False,
                "active": True,
                "updatedAt": (NOW - timedelta(hours=2)).isoformat(),
                "endDate": (NOW + timedelta(days=30)).isoformat(),
            }
        ]
    if request.url.host == "ll.thespacedevs.com":
        return {
            "count": 100,
            "next": "https://127.0.0.1/private",
            "results": [
                {
                    "id": "launch-1",
                    "name": "真实计划名称",
                    "net": (NOW + timedelta(days=1)).isoformat(),
                    "status": {"id": 1, "name": "Go", "abbrev": "Go"},
                },
                {
                    "id": "past",
                    "name": "过去发射",
                    "net": (NOW - timedelta(days=1)).isoformat(),
                },
            ],
        }
    return None


def route(request):
    if request.url.host == "jrj.sh.gov.cn":
        if request.url.path.endswith("index.html"):
            return httpx.Response(
                200,
                text='<a href="/SCGK194/20261015/report.html">2026年前三季度金融统计数据报告</a>',
            )
        return httpx.Response(
            200,
            text="<h1>2026年前三季度金融统计数据报告</h1><p>信息来源：中国人民银行 发布时间：2026-10-15</p><p>初步统计，2026年9月末社会融资规模存量为468.17万亿元，同比增长7.1%。</p>",
        )
    data = payload(request)
    return httpx.Response(200, json=data) if data is not None else httpx.Response(404)


async def collect(module, handler=route, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await module.collect_alpha_public(NOW, client=client, **kwargs)


@pytest.mark.asyncio
async def test_same_month_money_gap_and_vendor_rates_keep_units_and_real_dates(
    monkeypatch,
):
    module = engine(monkeypatch)
    result = await collect(module)
    rows = result["observations"]
    gap = [row for row in rows if row["code"] == "M1_M2_GAP"]
    assert [(row["referenceDate"], row["value"], row["unit"]) for row in gap] == [
        ("2026-09-30", "-4.0", "percentage_points")
    ]
    assert gap[0]["metadata"]["verification"] == "DERIVED_FROM_PROVIDER"
    assert not any(row["referenceDate"] > NOW.date().isoformat() for row in rows)
    rates = {row["code"]: row for row in rows if row["code"] in {"CN_T10", "SHIBOR_7D"}}
    assert rates["CN_T10"]["value"] == "1.82" and rates["SHIBOR_7D"]["value"] == "1.48"
    assert all(row["metadata"]["provider"] == "EASTMONEY" for row in rates.values())
    assert all(row["sourceKey"] not in {"CHINABOND", "CFETS"} for row in rates.values())
    assert all(
        row["publishedAt"] is None and row["timeBasis"] == "RETRIEVAL_ONLY"
        for row in rates.values()
    )
    json.dumps(result, allow_nan=False)


@pytest.mark.asyncio
async def test_selected_city_housing_converts_only_mom_and_retains_actual_index(
    monkeypatch,
):
    result = await collect(engine(monkeypatch))
    rows = [row for row in result["observations"] if row["code"].startswith("HOUSE_")]
    assert len(rows) == 8
    assert {row["metadata"]["city"] for row in rows} == {"海口", "三亚", "上海", "深圳"}
    assert {row["value"] for row in rows} == {"-0.2", "-0.6"}
    assert {row["metadata"]["originalIndex"] for row in rows} == {"99.8", "99.4"}
    assert all(
        row["unit"] == "%" and row["metadata"]["coverage"] == "SELECTED_CITIES"
        for row in rows
    )


@pytest.mark.asyncio
async def test_cls_keeps_broad_investment_news_and_filters_future_time_and_html(
    monkeypatch,
):
    result = await collect(engine(monkeypatch))
    assert {row["id"] for row in result["catalysts"]} == {"cls:123", "cls:125"}
    row = result["catalysts"][0]
    assert row["sourceUri"] == "https://www.cls.cn/detail/123"
    assert row["phase"] == "REPORTED" and row["factVerification"] == "UNVERIFIED"
    assert "<p>" not in row["content"] and "卫星" in row["matchedKeywords"]


def cls_row(identity, *, seconds=0, level="B", title=None):
    return {
        "id": identity,
        "title": title or f"重要经营动态{identity}",
        "content": f"<p>公开报道内容{identity}</p>",
        "ctime": int((NOW - timedelta(minutes=1, seconds=seconds)).timestamp()),
        "level": level,
    }


@pytest.mark.asyncio
async def test_cls_keeps_non_theme_important_news_and_broad_investment_topics(
    monkeypatch,
):
    module = engine(monkeypatch)
    pages = [
        [
            cls_row(901, title="公司披露重大经营变化", level="A"),
            cls_row(902, title="医药创新药临床结果公布", level="C"),
            cls_row(903, title="消费龙头三季报利润增长", level="C"),
            cls_row(904, title="普通娱乐明星动态", level="C"),
        ],
        [],
    ]

    async def fetch(uri, *, params):
        return {"data": {"roll_data": pages.pop(0)}}

    result = await module._cls(fetch, NOW)
    assert {row["id"] for row in result["catalysts"]} == {
        "cls:901",
        "cls:902",
        "cls:903",
    }
    assert result["catalysts"][0]["id"] == "cls:901"
    assert result["coverage"]["scannedCount"] == 4
    assert result["coverage"]["retainedCount"] == 3


@pytest.mark.asyncio
async def test_cls_paginates_capped_twenty_row_pages_through_151st_item(monkeypatch):
    module = engine(monkeypatch)
    calls = []

    async def fetch(uri, *, params):
        calls.append(dict(params))
        start = (len(calls) - 1) * 20
        rows = [cls_row(1000 + i, seconds=i) for i in range(start, start + 20)]
        return {"data": {"roll_data": rows}}

    result = await module._cls(fetch, NOW)
    identities = {row["id"] for row in result["catalysts"]}
    assert {"cls:1020", "cls:1150", "cls:1299"} <= identities
    assert len(identities) == 300
    assert len(calls) == 15
    assert all(call["rn"] == "20" for call in calls)
    assert calls[1]["last_time"] < calls[0]["last_time"]
    assert calls[1]["sign"] != calls[0]["sign"]
    assert result["status"] == "PARTIAL"
    assert result["coverage"]["truncated"] is True
    assert result["coverage"]["stopReason"] == "ITEM_LIMIT"


@pytest.mark.asyncio
async def test_cls_deduplicates_repeated_pages_instead_of_looping(monkeypatch):
    module = engine(monkeypatch)
    calls = 0

    async def fetch(uri, *, params):
        nonlocal calls
        calls += 1
        return {"data": {"roll_data": [cls_row(801), cls_row(802, seconds=1)]}}

    result = await module._cls(fetch, NOW)
    assert [row["id"] for row in result["catalysts"]] == ["cls:801", "cls:802"]
    assert calls == 2
    assert result["coverage"]["scannedCount"] == 2
    assert result["coverage"]["stopReason"] == "DUPLICATE_PAGE"
    assert result["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_cls_bounds_sparse_pages_without_claiming_complete_coverage(monkeypatch):
    module = engine(monkeypatch)
    calls = 0

    async def fetch(uri, *, params):
        nonlocal calls
        calls += 1
        return {"data": {"roll_data": [cls_row(700 + calls, seconds=calls)]}}

    result = await module._cls(fetch, NOW)
    assert calls == 15 and len(result["catalysts"]) == 15
    assert result["coverage"]["stopReason"] == "PAGE_LIMIT"
    assert result["coverage"]["truncated"] is True


@pytest.mark.asyncio
async def test_cls_respects_the_24h_window_and_does_not_publish_future_rows(
    monkeypatch,
):
    module = engine(monkeypatch)
    calls = 0

    async def fetch(uri, *, params):
        nonlocal calls
        calls += 1
        return {
            "data": {
                "roll_data": [
                    cls_row(601, seconds=-7200),
                    cls_row(602),
                    cls_row(603, seconds=24 * 3600),
                ]
            }
        }

    result = await module._cls(fetch, NOW)
    assert [row["id"] for row in result["catalysts"]] == ["cls:602"]
    assert calls == 1
    assert result["coverage"]["windowStart"] == "2026-10-17T09:00:00+08:00"
    assert result["coverage"]["windowEnd"] == NOW.isoformat()
    assert result["coverage"]["stopReason"] == "WINDOW_START"
    assert result["coverage"]["truncated"] is False


@pytest.mark.asyncio
async def test_cls_later_page_denial_keeps_facts_and_marks_partial_without_retry(
    monkeypatch,
):
    module = engine(monkeypatch)
    calls = []

    def handler(request):
        if request.url.host != "www.cls.cn":
            return route(request)
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"data": {"roll_data": [cls_row(501)]}})
        return httpx.Response(429, headers={"Retry-After": "90"})

    result = await collect(module, handler)
    assert [row["id"] for row in result["catalysts"]] == ["cls:501"]
    source = result["sourceResults"]["CLS_TELEGRAPH"]
    assert source["status"] == "PARTIAL" and source["records"] == 1
    assert source["httpStatus"] == 429
    assert source["errorClass"] == "HTTPStatusError"
    assert source["coverage"]["stopReason"] == "PAGE_FAILED"
    assert source["coverage"]["pages"] == 2 and len(calls) == 2


@pytest.mark.asyncio
async def test_cls_ascii_investment_terms_work_next_to_chinese_without_matching_chair(
    monkeypatch,
):
    module = engine(monkeypatch)
    pages = [
        [
            cls_row(401, level="C", title="DRAM价格上调"),
            cls_row(402, level="C", title="HBM需求再增长"),
            cls_row(403, level="C", title="CHAIR普通家具展示"),
        ],
        [],
    ]

    async def fetch(uri, *, params):
        return {"data": {"roll_data": pages.pop(0)}}

    result = await module._cls(fetch, NOW)
    assert {row["id"] for row in result["catalysts"]} == {"cls:401", "cls:402"}


@pytest.mark.asyncio
async def test_cls_deduplicates_same_content_with_different_ids(monkeypatch):
    module = engine(monkeypatch)
    original = cls_row(301)
    duplicate = {**original, "id": 302}
    pages = [[original, duplicate], []]

    async def fetch(uri, *, params):
        return {"data": {"roll_data": pages.pop(0)}}

    result = await module._cls(fetch, NOW)
    assert [row["id"] for row in result["catalysts"]] == ["cls:301"]
    assert result["coverage"]["scannedCount"] == 1


@pytest.mark.asyncio
async def test_cls_stops_nonadvancing_provider_cursor_with_usable_facts(monkeypatch):
    module = engine(monkeypatch)
    pages = [[cls_row(201, seconds=1)], [cls_row(202)]]

    async def fetch(uri, *, params):
        return {"data": {"roll_data": pages.pop(0)}}

    result = await module._cls(fetch, NOW)
    assert len(result["catalysts"]) == 2
    assert result["coverage"]["pages"] == 2
    assert result["coverage"]["stopReason"] == "CURSOR_NOT_ADVANCING"
    assert result["status"] == "PARTIAL"


@pytest.mark.parametrize(
    "response",
    [
        {"errno": 1001, "msg": "invalid request", "data": {"roll_data": []}},
        {"errno": "1001", "data": {"roll_data": []}},
        {"errno": False, "data": {"roll_data": []}},
        {"errno": 0, "data": {}},
        {"errno": 0, "data": None},
        {"errno": 0, "data": {"roll_data": None}},
    ],
)
@pytest.mark.asyncio
async def test_cls_provider_error_or_missing_shape_is_not_a_successful_empty_page(
    monkeypatch, response
):
    module = engine(monkeypatch)

    def handler(request):
        return (
            httpx.Response(200, json=response)
            if request.url.host == "www.cls.cn"
            else route(request)
        )

    result = await collect(module, handler)
    assert result["catalysts"] == []
    assert result["sourceResults"]["CLS_TELEGRAPH"]["status"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_cls_documented_successful_empty_page_remains_success(monkeypatch):
    module = engine(monkeypatch)

    def handler(request):
        if request.url.host == "www.cls.cn":
            return httpx.Response(
                200, json={"errno": 0, "msg": "", "data": {"roll_data": []}}
            )
        return route(request)

    result = await collect(module, handler)
    source = result["sourceResults"]["CLS_TELEGRAPH"]
    assert result["catalysts"] == [] and source["status"] == "SUCCESS"
    assert source["coverage"]["stopReason"] == "EMPTY_PAGE"
    assert source["coverage"]["truncated"] is False


@pytest.mark.asyncio
async def test_gamma_pairing_does_not_claim_object_update_is_quote_timestamp(
    monkeypatch,
):
    result = await collect(engine(monkeypatch))
    rows = result["odds"]
    assert [
        (row["outcome"], row["price"], row["impliedProbabilityPct"]) for row in rows
    ] == [("Yes", "0.37", "37.00"), ("No", "0.63", "63.00")]
    assert all(
        row["quoteTimestamp"] is None and row["priceKind"] == "MARKET_IMPLIED_PRICE"
        for row in rows
    )
    assert all(row["observedAt"] == NOW.isoformat() for row in rows)
    assert rows[0]["sourceUpdatedAt"] != rows[0]["observedAt"]


@pytest.mark.asyncio
async def test_launches_are_plans_bounded_to_one_page_and_only_one_attempt_per_day(
    monkeypatch,
):
    module = engine(monkeypatch)
    monkeypatch.setattr(module, "_launch_day", None)
    requests = []

    def handler(request):
        requests.append(request)
        return route(request)

    first = await collect(module, handler)
    second = await collect(module, handler)
    assert len(first["launches"]) == 1 and first["launches"][0]["phase"] == "PLANNED"
    assert first["launches"][0]["scheduledAt"] == (NOW + timedelta(days=1)).isoformat()
    launches = [
        request for request in requests if request.url.host == "ll.thespacedevs.com"
    ]
    assert len(launches) == 1 and launches[0].url.path.startswith("/2.3.0/")
    assert second["sourceResults"]["SPACEDEVS_LAUNCHES"]["status"] == "SKIPPED"
    assert not any(request.url.host == "127.0.0.1" for request in requests)


@pytest.mark.asyncio
async def test_government_reprint_requires_explicit_national_stock_yoy(monkeypatch):
    result = await collect(engine(monkeypatch))
    row = next(row for row in result["observations"] if row["code"] == "TSF_STOCK_YOY")
    assert row["value"] == "7.1" and row["referenceDate"] == "2026-09-30"
    assert (
        row["sourceKey"] == "GOV_SH_PBOC_TSF"
        and row["metadata"]["verification"] == "GOVERNMENT_REPRINT"
    )
    assert (
        row["publishedAt"] is None and row["metadata"]["publishedDate"] == "2026-10-15"
    )


@pytest.mark.asyncio
async def test_increment_text_cannot_be_used_as_stock_growth(monkeypatch):
    def handler(request):
        if request.url.host == "jrj.sh.gov.cn" and not request.url.path.endswith(
            "index.html"
        ):
            return httpx.Response(
                200,
                text="<p>信息来源：中国人民银行 发布时间：2026-10-15</p><p>2026年9月社会融资规模增量同比增长7.1%。</p>",
            )
        return route(request)

    result = await collect(engine(monkeypatch), handler)
    assert not any(row["code"] == "TSF_STOCK_YOY" for row in result["observations"])
    assert result["sourceResults"]["GOV_SH_PBOC_TSF"]["status"] == "UNAVAILABLE"
    assert any(
        "增量" in text
        for text in result["sourceResults"]["GOV_SH_PBOC_TSF"]["limitations"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "信息来源：中国人民银行 上海总部 发布时间：2026-10-15 初步统计，2026年9月末社会融资规模存量为468.17万亿元，同比增长7.1%。",
        "信息来源：中国人民银行 发布时间：2026-10-15 初步统计，本市2026年9月末社会融资规模存量为468.17万亿元，同比增长7.1%。",
    ],
)
async def test_regional_report_cannot_be_promoted_to_national_tsf(monkeypatch, body):
    def handler(request):
        if request.url.host == "jrj.sh.gov.cn" and not request.url.path.endswith(
            "index.html"
        ):
            return httpx.Response(200, text=f"<p>{body}</p>")
        return route(request)

    result = await collect(engine(monkeypatch), handler)
    assert not any(row["code"] == "TSF_STOCK_YOY" for row in result["observations"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_denied_or_rate_limited_source_does_not_retry_or_block_other_sources(
    monkeypatch, status
):
    module = engine(monkeypatch)
    attempts = []

    def handler(request):
        if request.url.params.get("reportName") == "RPT_ECONOMY_CURRENCY_SUPPLY":
            attempts.append(request)
            return httpx.Response(
                status,
                headers={"Retry-After": "120"},
                text="private provider failure must not leak",
            )
        return route(request)

    result = await collect(module, handler)
    failed = result["sourceResults"]["AKSHARE_MONEY_SUPPLY"]
    assert failed["status"] == (
        "PERMISSION_REQUIRED" if status == 403 else "RATE_LIMITED"
    )
    assert len(attempts) == 1 and "private provider" not in json.dumps(result)
    assert any(row["code"] == "CN_T10" for row in result["observations"])


@pytest.mark.asyncio
async def test_timeout_is_per_source_and_does_not_abort_successful_siblings(
    monkeypatch,
):
    async def handler(request):
        if request.url.params.get("reportName") == "RPT_ECONOMY_CURRENCY_SUPPLY":
            await asyncio.sleep(0.05)
        return route(request)

    result = await collect(engine(monkeypatch), handler, timeout_seconds=0.01)
    assert result["sourceResults"]["AKSHARE_MONEY_SUPPLY"]["status"] == "TIMEOUT"
    assert any(row["code"] == "CN_T10" for row in result["observations"])
    assert any(row["code"] == "SHIBOR_7D" for row in result["observations"])


@pytest.mark.asyncio
async def test_all_transport_failures_return_all_source_statuses_without_fabricated_data(
    monkeypatch,
):
    module = engine(monkeypatch)
    monkeypatch.setattr(module, "_launch_day", None)
    result = await collect(module, lambda request: httpx.Response(503, text="SECRET"))
    assert set(result["sourceResults"]) == set(module.SOURCE_KEYS)
    assert all(
        result[key] == [] for key in ("observations", "catalysts", "odds", "launches")
    )
    assert all(row["status"] == "FAILED" for row in result["sourceResults"].values())
    assert "SECRET" not in json.dumps(result)


@pytest.mark.asyncio
async def test_conflicting_duplicate_observations_fail_only_the_affected_source(
    monkeypatch,
):
    def handler(request):
        if request.url.params.get("type") == "RPTA_WEB_TREASURYYIELD":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [
                            {"SOLAR_DATE": "2026-10-16", "EMM00166466": "1.82"},
                            {"SOLAR_DATE": "2026-10-16", "EMM00166466": "2.82"},
                        ]
                    }
                },
            )
        return route(request)

    result = await collect(engine(monkeypatch), handler)
    assert not any(row["code"] == "CN_T10" for row in result["observations"])
    assert result["sourceResults"]["AKSHARE_CN_T10"]["status"] == "FAILED"
    assert any(row["code"] == "SHIBOR_7D" for row in result["observations"])


@pytest.mark.asyncio
async def test_locked_bond_upstream_report_selector_is_present(monkeypatch):
    def handler(request):
        if (
            request.url.params.get("type") == "RPTA_WEB_TREASURYYIELD"
            and request.url.params.get("token") != "894050c76af8597a853f5b408b759f5d"
        ):
            return httpx.Response(400)
        return route(request)

    result = await collect(engine(monkeypatch), handler)
    assert any(row["code"] == "CN_T10" for row in result["observations"])


@pytest.mark.asyncio
async def test_oversized_source_and_malformed_odds_do_not_leak_partial_guesses(
    monkeypatch,
):
    module = engine(monkeypatch)

    def handler(request):
        if request.url.host == "www.cls.cn":
            return httpx.Response(
                200,
                headers={"content-length": str(module.MAX_BYTES + 1)},
                content=b"too large",
            )
        if request.url.host == "gamma-api.polymarket.com":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "77",
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["NaN"]',
                        "closed": False,
                        "active": True,
                    }
                ],
            )
        return route(request)

    result = await collect(module, handler)
    assert result["catalysts"] == result["odds"] == []
    assert result["sourceResults"]["CLS_TELEGRAPH"]["status"] == "FAILED"
    assert any(row["code"] == "CN_T10" for row in result["observations"])


@pytest.mark.asyncio
async def test_failed_launch_attempt_consumes_day_and_never_follows_redirect(
    monkeypatch,
):
    module = engine(monkeypatch)
    monkeypatch.setattr(module, "_launch_day", None)
    attempts = []

    def handler(request):
        if request.url.host == "ll.thespacedevs.com":
            attempts.append(request)
            return httpx.Response(
                302, headers={"location": "https://127.0.0.1/private"}
            )
        return route(request)

    first, second = await collect(module, handler), await collect(module, handler)
    assert len(attempts) == 1 and first["launches"] == second["launches"] == []
    assert first["sourceResults"]["SPACEDEVS_LAUNCHES"]["status"] == "FAILED"
    assert second["sourceResults"]["SPACEDEVS_LAUNCHES"]["status"] == "SKIPPED"


@pytest.mark.asyncio
async def test_retrieval_is_actual_completion_not_backdated_to_collection_start(
    monkeypatch,
):
    module = engine(monkeypatch)
    finished = NOW + timedelta(minutes=2)
    monkeypatch.setattr(module, "now_shanghai", lambda: finished)
    result = await collect(module)
    assert result["asOf"] == finished.isoformat()
    assert all(
        row["retrievedAt"] == finished.isoformat()
        and row["availableAt"] == finished.isoformat()
        for row in result["observations"]
    )


@pytest.mark.asyncio
async def test_boolean_measurement_is_missing_but_actual_zero_is_retained(monkeypatch):
    def handler(request):
        if request.url.params.get("reportName") == "RPT_ECONOMY_CURRENCY_SUPPLY":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [
                            {
                                "REPORT_DATE": "2026-09-01",
                                "CURRENCY_SAME": True,
                                "BASIC_CURRENCY_SAME": 7,
                            },
                            {
                                "REPORT_DATE": "2026-08-01",
                                "CURRENCY_SAME": 0,
                                "BASIC_CURRENCY_SAME": 7,
                            },
                        ]
                    }
                },
            )
        return route(request)

    result = await collect(engine(monkeypatch), handler)
    m1 = [row for row in result["observations"] if row["code"] == "M1_YOY"]
    assert [(row["referenceDate"], row["value"]) for row in m1] == [("2026-08-31", "0")]


@pytest.mark.asyncio
async def test_known_future_reprint_publication_time_is_not_reduced_to_a_day(
    monkeypatch,
):
    def handler(request):
        if request.url.host == "jrj.sh.gov.cn" and not request.url.path.endswith(
            "index.html"
        ):
            future = (NOW + timedelta(hours=1)).isoformat()
            return httpx.Response(
                200,
                text=f'<meta name="pubdate" content="{future}"><p>信息来源：中国人民银行 发布时间：2026-10-18</p><p>初步统计，2026年9月末社会融资规模存量为468.17万亿元，同比增长7.1%。</p>',
            )
        return route(request)

    result = await collect(engine(monkeypatch), handler)
    assert not any(row["code"] == "TSF_STOCK_YOY" for row in result["observations"])


@pytest.mark.asyncio
async def test_malformed_retry_after_cannot_escape_source_failure_isolation(
    monkeypatch,
):
    def handler(request):
        if request.url.params.get("type") == "RPTA_WEB_TREASURYYIELD":
            return httpx.Response(429, headers={"Retry-After": "9" * 5000})
        return route(request)

    result = await collect(engine(monkeypatch), handler)
    assert result["sourceResults"]["AKSHARE_CN_T10"]["status"] == "RATE_LIMITED"
    assert any(row["code"] == "SHIBOR_7D" for row in result["observations"])
