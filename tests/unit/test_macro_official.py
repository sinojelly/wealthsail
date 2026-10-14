"""Small official-protocol fixtures; no assumed economic facts or calendar."""

from datetime import datetime, timedelta
from decimal import Decimal
from importlib import import_module, util
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

NOW = datetime(2025, 10, 16, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
FIXTURES = Path(__file__).parents[1] / "fixtures" / "macro_official"
NBS = "https://www.stats.gov.cn"
PBC = "https://www.pbc.gov.cn"


def _adapter():
    name = "backend.integrations.macro_official"
    assert util.find_spec(name), "official macro adapter is missing"
    return import_module(name)


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _nbs_html(title, text, published="2025/10/15 09:30"):
    return f'<meta name="ArticleTitle" content="{title}"><meta name="PubDate" content="{published}"><div class="TRS_UEDITOR"><p>{text}</p></div>'


def test_nbs_value_is_national_yoy_not_city_monthly_or_duplicate_mobile_body():
    adapter = _adapter()
    row = adapter.parse_nbs_release(
        _fixture("nbs_release.html"),
        NBS + "/sj/zxfb/202510/t20251015_100.html",
        "CPI_YOY",
        NOW,
    )
    assert row["referencePeriod"] == "2025-09"
    assert row["value"] == Decimal("-0.2")
    assert row["unit"] == "%"
    assert row["publishedAt"] == NOW.replace(day=15, hour=9, minute=30)
    assert row["publisher"] == "国家统计局"
    assert row["retrievedAt"] == NOW


@pytest.mark.parametrize(
    "code,title,text,expected,unit",
    [
        (
            "PPI_YOY",
            "2025年9月份工业生产者出厂价格同比下降2.1%",
            "2025年9月份，全国工业生产者出厂价格同比下降2.1%，环比上涨0.3%。工业生产者购进价格同比下降1.1%。",
            "-2.1",
            "%",
        ),
        (
            "PMI",
            "2025年9月中国采购经理指数运行情况",
            "9 月份，制造业采购经理指数（ PMI ）为 49.8% ，非制造业商务活动指数为50.0%。",
            "49.8",
            "",
        ),
    ],
)
def test_correct_manufacturing_and_factory_output_statistics(
    code, title, text, expected, unit
):
    row = _adapter().parse_nbs_release(
        _nbs_html(title, text), NBS + "/sj/zxfb/202510/t20251015_101.html", code, NOW
    )
    assert (row["value"], row["unit"]) == (Decimal(expected), unit)


def test_date_only_publication_is_not_replaced_by_planned_time_or_collection():
    html = _fixture("nbs_release.html").replace("2025/10/15 09:30", "2025/10/15")
    row = _adapter().parse_nbs_release(
        html, NBS + "/sj/zxfb/202510/t20251015_100.html", "CPI_YOY", NOW
    )
    assert row["publishedAt"] is None


@pytest.mark.parametrize(
    "change",
    [
        lambda html: html.replace("2025/10/15 09:30", "2025/10/17 09:30"),
        lambda html: html.replace("2025年9月份", "2025年11月份"),
        lambda html: html.replace(
            "全国居民消费价格同比下降0.2%", "全国居民消费价格同比下降NaN%"
        ),
        lambda html: html.replace("全国居民消费价格", "北京市居民消费价格"),
        lambda html: html.replace("下降0.2%", "上涨-0.2%"),
    ],
)
def test_future_wrong_scope_or_bad_values_never_become_release_evidence(change):
    with pytest.raises(ValueError):
        _adapter().parse_nbs_release(
            change(_fixture("nbs_release.html")),
            NBS + "/sj/zxfb/202510/t20251015_100.html",
            "CPI_YOY",
            NOW,
        )


def test_conflicting_same_document_values_or_headline_are_not_cherry_picked():
    html = _fixture("nbs_release.html").replace(
        "全国居民消费价格同比下降0.2%", "全国居民消费价格同比下降0.3%", 1
    )
    with pytest.raises(ValueError):
        _adapter().parse_nbs_release(
            html, NBS + "/sj/zxfb/202510/t20251015_100.html", "CPI_YOY", NOW
        )


def test_headline_cannot_verify_value_when_body_belongs_to_another_month():
    html = _fixture("nbs_release.html").replace(
        "2025年9月份，全国", "2025年8月份，全国"
    )
    with pytest.raises(ValueError):
        _adapter().parse_nbs_release(
            html, NBS + "/sj/zxfb/202510/t20251015_100.html", "CPI_YOY", NOW
        )


def test_pboc_quarter_title_body_month_and_actual_second_timestamp():
    row = _adapter().parse_pboc_release(
        _fixture("pboc_release.html"),
        PBC + "/diaochatongjisi/116219/116225/report/index.html",
        NOW,
    )
    assert row["code"] == "M2_YOY" and row["referencePeriod"] == "2025-09"
    assert row["value"] == Decimal("8.1") and row["unit"] == "%"
    assert row["publishedAt"] == NOW.replace(day=15, hour=17, minute=0, second=4)


def test_pboc_missing_actual_time_keeps_publication_unknown_despite_create_date():
    html = _fixture("pboc_release.html").replace(
        '<span id="shijian">2025-10-15 17:00:04</span>', ""
    )
    row = _adapter().parse_pboc_release(
        html, PBC + "/diaochatongjisi/116219/116225/report/index.html", NOW
    )
    assert row["publishedAt"] is None


def test_pboc_future_date_only_metadata_is_rejected_even_with_empty_time_span():
    html = (
        _fixture("pboc_release.html")
        .replace('content="2025-10-15"', 'content="2025-10-17"')
        .replace("2025-10-15 17:00:04", "")
    )
    with pytest.raises(ValueError):
        _adapter().parse_pboc_release(
            html, PBC + "/diaochatongjisi/116219/116225/report/index.html", NOW
        )


def test_pboc_conflicting_m2_heading_is_not_ignored():
    html = _fixture("pboc_release.html").replace("广义货币增长8.1%", "广义货币增长8.2%")
    with pytest.raises(ValueError):
        _adapter().parse_pboc_release(
            html, PBC + "/diaochatongjisi/116219/116225/report/index.html", NOW
        )


@pytest.mark.parametrize(
    "title,month", [("一季度", 3), ("上半年", 6), ("前三季度", 9), ("", 12)]
)
def test_pboc_quarter_half_year_and_year_reports_retain_end_month(title, month):
    html = (
        _fixture("pboc_release.html")
        .replace("前三季度", title)
        .replace("9月末", f"{month}月末")
    )
    row = _adapter().parse_pboc_release(
        html,
        PBC + "/diaochatongjisi/116219/116225/report/index.html",
        NOW.replace(year=2026),
    )
    assert row["referencePeriod"] == f"2025-{month:02d}"


@pytest.mark.parametrize(
    "url",
    [
        "https://stats.gov.cn.evil.test/report",
        "https://zjzd.stats.gov.cn/report",
        "http://127.0.0.1/report",
        "file:///tmp/data",
        "https://secret@www.stats.gov.cn/report",
        "https://www.stats.gov.cn:6543/report",
    ],
)
def test_source_url_must_be_exact_national_official_host_without_credentials(url):
    with pytest.raises(ValueError):
        _adapter().parse_nbs_release(_fixture("nbs_release.html"), url, "CPI_YOY", NOW)


def test_canonical_hash_and_event_identity_do_not_change_with_retrieval_time_or_tracking():
    adapter = _adapter()
    url = NBS + "/sj/zxfb/202510/t20251015_100.html"
    first = adapter.parse_nbs_release(
        _fixture("nbs_release.html"), url + "?utm_source=one#content", "CPI_YOY", NOW
    )
    second = adapter.parse_nbs_release(
        _fixture("nbs_release.html"), url, "CPI_YOY", NOW + timedelta(hours=1)
    )
    assert first["sourceUri"] == second["sourceUri"] == url
    assert first["contentHash"] == second["contentHash"]
    assert first["retrievedAt"] != second["retrievedAt"]


def test_semantic_query_parameters_are_not_removed_from_document_identity():
    adapter = _adapter()
    url = NBS + "/sj/zxfb/202510/t20251015_100.html?id=one&utm_campaign=test"
    row = adapter.parse_nbs_release(_fixture("nbs_release.html"), url, "CPI_YOY", NOW)
    assert row["sourceUri"].endswith("?id=one")


def _calendar_html(*, exception=True, malformed=False):
    # Real calendar structure: day row + time row, rowspans, duplicate mobile table.
    header = (
        "<tr><td>序号</td><td>内容</td>"
        + "".join(f"<td>{m}月</td>" for m in range(1, 13))
        + "</tr>"
    )

    def pair(label, days):
        return (
            '<tr><td rowspan="2">4</td><td rowspan="2">'
            + label
            + "</td>"
            + "".join(f"<td>{day}</td>" for day in days)
            + "</tr><tr>"
            + "".join("<td>9:30</td>" for _ in days)
            + "</tr>"
        )

    pmi = [
        "31/五",
        "……",
        "4/二 注5<br>31/一",
        "30/三",
        "31/六",
        "30/一",
        "31/四",
        "31/日",
        "30/二",
        "31/五",
        "30/日",
        "31/三",
    ]
    days = ["9/四"] * 12
    if malformed:
        days[10] = "待定"
    body = (
        header
        + pair("采购经理指数月度报告", pmi)
        + pair("居民消费价格指数月度报告", days)
        + pair("工业生产者价格指数月度报告", days)
    )
    note = (
        "5.因春节假期影响，2月份采购经理指数月度报告于3月4日发布。" if exception else ""
    )
    return f'<meta name="ArticleTitle" content="2025年国家统计局主要统计信息发布日程表"><meta name="PubDate" content="2024/12/25 09:30"><div class="TRS_UEDITOR"><table>{body}</table>{note}</div>'


def test_calendar_parses_published_table_and_exception_without_fixed_month_end_guess():
    rows = _adapter().parse_nbs_calendar(
        _calendar_html(), NBS + "/xw/tjxw/tzgg/202412/t20241225_100.html", NOW
    )
    feb = next(
        row
        for row in rows
        if row["code"] == "PMI" and row["referencePeriod"] == "2025-02"
    )
    march = next(
        row
        for row in rows
        if row["code"] == "PMI" and row["referencePeriod"] == "2025-03"
    )
    assert feb["scheduledAt"] == NOW.replace(month=3, day=4, hour=9, minute=30)
    assert march["scheduledAt"] == NOW.replace(month=3, day=31, hour=9, minute=30)
    assert next(
        row
        for row in rows
        if row["code"] == "CPI_YOY" and row["referencePeriod"] == "2025-10"
    )["scheduledAt"] == NOW.replace(month=11, day=9, hour=9, minute=30)
    assert not any(row["code"] == "M2_YOY" for row in rows)


@pytest.mark.parametrize("kwargs", [{"exception": False}, {"malformed": True}])
def test_calendar_unexplained_multiple_dates_and_unknown_cell_fail_without_guess(
    kwargs,
):
    with pytest.raises(ValueError):
        _adapter().parse_nbs_calendar(
            _calendar_html(**kwargs),
            NBS + "/xw/tjxw/tzgg/202412/t20241225_100.html",
            NOW,
        )


def test_fed_rss_uses_gmt_publication_and_official_fact_classification():
    rows = _adapter().parse_fed_rss(_fixture("fed_monetary.xml"), NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row["publishedAt"].astimezone(ZoneInfo("Asia/Shanghai")) == NOW.replace(
        hour=2
    )
    assert row["category"] == "MONETARY_POLICY" and row["sourceType"] == "OFFICIAL_FACT"
    assert row["sourceKey"] == "FED_RSS" and row["publisher"] == "Federal Reserve"
    assert "gold:physical" in row["directionIds"] and "us:broad" in row["directionIds"]
    assert len(row["summary"]) <= 300 and "<p>" not in row["summary"]


def test_future_rss_events_are_excluded_and_external_links_rejected():
    adapter = _adapter()
    assert (
        adapter.parse_fed_rss(_fixture("fed_monetary.xml"), NOW.replace(day=15, hour=9))
        == []
    )
    xml = _fixture("fed_monetary.xml").replace(
        "https://www.federalreserve.gov/newsevents", "https://example.test/newsevents"
    )
    with pytest.raises(ValueError):
        adapter.parse_fed_rss(xml, NOW)


def test_rss_entity_declarations_are_not_accepted():
    with pytest.raises(ValueError):
        _adapter().parse_fed_rss(
            '<!DOCTYPE rss [<!ENTITY data SYSTEM "file:///private">]><rss><channel><item><title>&data;</title></item></channel></rss>',
            NOW,
        )


def _responses(adapter):
    nbs_index = '<a href="/sj/zxfb/202510/t20251015_100.html">2025年9月份居民消费价格同比下降0.2%</a><a href="/sj/zxfb/202510/t20251015_101.html">2025年9月份工业生产者出厂价格同比下降2.1%</a><a href="/sj/zxfb/202509/t20250930_102.html">2025年9月中国采购经理指数运行情况</a>'
    return {
        adapter.NBS_RELEASE_INDEX: nbs_index,
        NBS + "/sj/zxfb/202510/t20251015_100.html": _fixture("nbs_release.html"),
        NBS + "/sj/zxfb/202510/t20251015_101.html": _nbs_html(
            "2025年9月份工业生产者出厂价格同比下降2.1%",
            "2025年9月份，全国工业生产者出厂价格同比下降2.1%，环比上涨0.1%。",
        ),
        NBS + "/sj/zxfb/202509/t20250930_102.html": _nbs_html(
            "2025年9月中国采购经理指数运行情况",
            "9月份，制造业采购经理指数（PMI）为49.8%。",
            "2025/09/30 09:30",
        ),
        adapter.NBS_CALENDAR_INDEX: '<a href="/xw/tjxw/tzgg/202412/t20241225_100.html">2025年国家统计局主要统计信息发布日程表</a>',
        NBS + "/xw/tjxw/tzgg/202412/t20241225_100.html": _calendar_html(),
        adapter.PBOC_RELEASE_INDEX: '<a href="/diaochatongjisi/116219/116225/report/index.html">2025年前三季度金融统计数据报告</a>',
        PBC + "/diaochatongjisi/116219/116225/report/index.html": _fixture(
            "pboc_release.html"
        ),
        adapter.FED_RSS_URL: _fixture("fed_monetary.xml"),
    }


@pytest.mark.asyncio
async def test_fetch_returns_independent_release_schedule_event_and_source_results(
    monkeypatch,
):
    adapter = _adapter()
    pages = _responses(adapter)

    async def fetch(client, url):
        return pages[url]

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    result = await adapter.fetch_macro_evidence(NOW)
    assert {row["code"] for row in result["releases"]} == {
        "CPI_YOY",
        "PPI_YOY",
        "PMI",
        "M2_YOY",
    }
    assert set(result["sourceResults"]) == {
        "NBS_CPI",
        "NBS_PPI",
        "NBS_PMI",
        "NBS_CALENDAR",
        "PBOC_M2",
        "FED_RSS",
    }
    assert all(row["status"] == "SUCCESS" for row in result["sourceResults"].values())
    assert len(result["events"]) == 5
    assert len({row["eventKey"] for row in result["events"]}) == 5


@pytest.mark.asyncio
async def test_provider_failure_isolated_from_other_official_sources(monkeypatch):
    adapter = _adapter()
    pages = _responses(adapter)

    async def fetch(client, url):
        if url == adapter.PBOC_RELEASE_INDEX:
            raise httpx.ReadTimeout("unavailable")
        return pages[url]

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    result = await adapter.fetch_macro_evidence(NOW)
    assert result["sourceResults"]["PBOC_M2"] == {
        "status": "FAILED",
        "errorClass": "ReadTimeout",
    }
    assert all(row["code"] != "M2_YOY" for row in result["releases"])
    assert result["sourceResults"]["NBS_CPI"]["status"] == "SUCCESS"
    assert result["sourceResults"]["FED_RSS"]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_missing_latest_statistic_does_not_look_like_success(monkeypatch):
    adapter = _adapter()
    pages = _responses(adapter)
    pages[NBS + "/sj/zxfb/202510/t20251015_100.html"] = "<html>maintenance</html>"
    monkeypatch.setattr(
        adapter, "_fetch_text", AsyncMock(side_effect=lambda client, url: pages[url])
    )
    result = await adapter.fetch_macro_evidence(NOW)
    assert result["sourceResults"]["NBS_CPI"]["status"] == "FAILED"
    assert result["sourceResults"]["NBS_PPI"]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_redirect_to_nonofficial_host_is_rejected_before_second_request():
    adapter = _adapter()
    visited = []

    def response(request):
        visited.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "https://example.test/untrusted"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(ValueError):
            await adapter._fetch_text(client, NBS + "/sj/zxfb/")
    assert visited == [NBS + "/sj/zxfb/"]


@pytest.mark.asyncio
async def test_whole_source_deadline_isolates_slow_source_instead_of_only_per_request_timeouts(
    monkeypatch,
):
    import asyncio

    adapter = _adapter()
    pages = _responses(adapter)
    monkeypatch.setattr(adapter, "_SOURCE_TIMEOUT_SECONDS", 0.01, raising=False)

    async def fetch(client, url):
        if url == adapter.PBOC_RELEASE_INDEX:
            await asyncio.Future()
        return pages[url]

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    result = await asyncio.wait_for(adapter.fetch_macro_evidence(NOW), 0.3)
    assert result["sourceResults"]["PBOC_M2"] == {
        "status": "FAILED",
        "errorClass": "TimeoutError",
    }
    assert result["sourceResults"]["NBS_CPI"]["status"] == "SUCCESS"
