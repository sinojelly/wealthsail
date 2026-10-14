"""Public official protocols; synthetic values are never production seeds."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from importlib import import_module, util
from pathlib import Path

import httpx
import pytest

NOW = datetime(2025, 10, 16, 1, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "opportunity_sources"
PBC = "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/20251015171000/index.html"
UST = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"


def _adapter():
    name = "backend.integrations.opportunity_sources"
    assert util.find_spec(name), "opportunity official source adapter is missing"
    return import_module(name)


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _ust_uri(kind, year=2025):
    return (
        f"{UST}?data=daily_treasury_{kind}_yield_curve&field_tdr_date_value={year}"
        if kind == "real"
        else f"{UST}?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
    )


def test_nominal_date_is_new_york_observation_not_publication():
    rows = _adapter().parse_treasury_xml(
        _fixture("treasury_nominal.xml"), _ust_uri("nominal"), "US_T10_NOMINAL", NOW
    )
    assert [r["referenceDate"] for r in rows] == ["2025-10-13", "2025-10-14"]
    latest = rows[-1]
    assert latest["value"] == Decimal("4.20")
    assert latest["frequency"] == "DAILY"
    assert latest["unit"] == "%"
    assert latest["publishedAt"] is None
    assert latest["availableAt"] == latest["retrievedAt"] == NOW
    assert latest["timeBasis"] == "RETRIEVAL_ONLY"
    assert latest["metadata"]["referenceTimezone"] == "America/New_York"


def test_real_negative_and_zero_are_valid_and_definition_differs_from_nominal():
    adapter = _adapter()
    real = adapter.parse_treasury_xml(
        _fixture("treasury_real.xml"), _ust_uri("real"), "US_T10_REAL", NOW
    )
    nominal = adapter.parse_treasury_xml(
        _fixture("treasury_nominal.xml"), _ust_uri("nominal"), "US_T10_NOMINAL", NOW
    )
    assert [r["value"] for r in real] == [Decimal("-0.10"), Decimal("0.00")]
    assert (
        real[-1]["metadata"]["definitionKey"]
        != nominal[-1]["metadata"]["definitionKey"]
    )


def test_annual_feed_uri_does_not_change_definition_identity():
    adapter = _adapter()
    first = adapter.parse_treasury_xml(
        _fixture("treasury_real.xml").replace("2025-10", "2024-10"),
        _ust_uri("real", 2024),
        "US_T10_REAL",
        NOW,
    )[-1]
    latest = adapter.parse_treasury_xml(
        _fixture("treasury_real.xml"), _ust_uri("real"), "US_T10_REAL", NOW
    )[-1]
    assert first["sourceUri"] != latest["sourceUri"]
    assert first["metadata"]["definitionKey"] == latest["metadata"]["definitionKey"]
    assert (
        first["metadata"]["definitionVersion"]
        == latest["metadata"]["definitionVersion"]
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "", "-0.01", "null"])
def test_latest_bad_nominal_value_never_falls_back_to_old_point(value):
    with pytest.raises(ValueError):
        _adapter().parse_treasury_xml(
            _fixture("treasury_nominal.xml").replace(">4.20<", f">{value}<"),
            _ust_uri("nominal"),
            "US_T10_NOMINAL",
            NOW,
        )


def test_null_marker_is_not_zero():
    with pytest.raises(ValueError):
        _adapter().parse_treasury_xml(
            _fixture("treasury_nominal.xml").replace(
                '<d:BC_10YEAR m:type="Edm.Double">4.20',
                '<d:BC_10YEAR m:null="true" m:type="Edm.Double">4.20',
            ),
            _ust_uri("nominal"),
            "US_T10_NOMINAL",
            NOW,
        )


def test_future_new_york_day_is_rejected_even_when_shanghai_day_is_current():
    with pytest.raises(ValueError):
        _adapter().parse_treasury_xml(
            _fixture("treasury_nominal.xml"),
            _ust_uri("nominal"),
            "US_T10_NOMINAL",
            datetime(2025, 10, 14, 0, 30, tzinfo=timezone.utc),
        )


def test_conflicting_duplicate_date_and_wrong_feed_are_rejected():
    adapter = _adapter()
    xml = _fixture("treasury_nominal.xml")
    with pytest.raises(ValueError):
        adapter.parse_treasury_xml(
            xml.replace(
                "2025-10-14T00:00:00</d:NEW_DATE>", "2025-10-13T00:00:00</d:NEW_DATE>"
            ),
            _ust_uri("nominal"),
            "US_T10_NOMINAL",
            NOW,
        )
    with pytest.raises(ValueError):
        adapter.parse_treasury_xml(xml, _ust_uri("nominal"), "US_T10_REAL", NOW)


@pytest.mark.parametrize(
    "prefix", ['<!DOCTYPE feed [<!ENTITY x "1">]>', '<!ENTITY x "1">']
)
def test_xml_external_entities_are_not_allowed(prefix):
    with pytest.raises(ValueError):
        _adapter().parse_treasury_xml(
            prefix + _fixture("treasury_nominal.xml"),
            _ust_uri("nominal"),
            "US_T10_NOMINAL",
            NOW,
        )


def test_m1_current_and_explicit_2024_comparable_backcast_keep_actual_availability():
    rows = _adapter().parse_pboc_series(
        _fixture("pboc_combined.html"), PBC, "M1_YOY", NOW
    )
    assert len(rows) == 13
    assert rows[0]["referenceDate"] == "2024-01-01"
    assert rows[-1]["referenceDate"] == "2025-09-01"
    assert rows[4]["value"] == Decimal("-1.0")
    assert rows[-1]["value"] == Decimal("3.2")
    for row in rows:
        assert row["publishedAt"] == datetime(
            2025, 10, 15, 9, 0, 4, tzinfo=timezone.utc
        )
        assert row["availableAt"] == row["publishedAt"]
        assert row["timeBasis"] == "OFFICIAL_PUBLICATION"
        assert row["metadata"]["definitionVersion"] == "PBOC_M1_2025"
    assert rows[0]["metadata"]["isComparableBackcast"] is True
    assert rows[-1]["metadata"]["isComparableBackcast"] is False


def test_no_backcast_table_does_not_fabricate_2024_values():
    text = _fixture("pboc_combined.html")
    text = text[: text.index("<table>")] + "</div>"
    rows = _adapter().parse_pboc_series(text, PBC, "M1_YOY", NOW)
    assert len(rows) == 1
    assert rows[0]["referenceDate"] == "2025-09-01"


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace("2025年前三季度金融", "2023年前三季度金融").replace(
            "9月末，广义", "9月末，广义"
        ),
        lambda s: s.replace("启用新修订的狭义货币（M1）统计口径", "沿用旧口径"),
        lambda s: s.replace("2024年1月", "2023年1月"),
        lambda s: s.replace("<td>-1.0%</td>", "<td>NaN%</td>"),
    ],
)
def test_old_or_bad_m1_definition_never_mixes_with_new_series(change):
    with pytest.raises(ValueError):
        _adapter().parse_pboc_series(
            change(_fixture("pboc_combined.html")), PBC, "M1_YOY", NOW
        )


def test_stock_growth_is_not_cumulative_tsf_increment():
    row = _adapter().parse_pboc_series(
        _fixture("pboc_combined.html"), PBC, "TSF_STOCK_YOY", NOW
    )[0]
    assert row["value"] == Decimal("8.2")
    assert row["frequency"] == "MONTHLY"
    assert row["unit"] == "%"
    assert row["metadata"]["sourceKey"] == "PBOC_TSF"


def test_separate_stock_report_has_same_definition_as_combined_report():
    adapter = _adapter()
    text = _fixture("pboc_combined.html").replace(
        "2025年前三季度金融统计数据报告", "2025年9月社会融资规模存量统计数据报告"
    )
    standalone = adapter.parse_pboc_series(text, PBC, "TSF_STOCK_YOY", NOW)[0]
    combined = adapter.parse_pboc_series(
        _fixture("pboc_combined.html"), PBC, "TSF_STOCK_YOY", NOW
    )[0]
    assert (
        standalone["metadata"]["definitionKey"] == combined["metadata"]["definitionKey"]
    )
    assert (
        standalone["metadata"]["definitionVersion"]
        == combined["metadata"]["definitionVersion"]
    )


def test_corporate_medium_long_ytd_is_not_households_and_yi_converts_to_wan_yi():
    row = _adapter().parse_pboc_series(
        _fixture("pboc_combined.html"), PBC, "CORP_MEDLONG_LOANS_YTD", NOW
    )[0]
    assert row["value"] == Decimal("6.5")
    assert row["unit"] == "万亿元"
    assert row["frequency"] == "YTD"
    assert row["metadata"]["accumulationStart"] == "2025-01-01"
    assert row["metadata"]["accumulationEnd"] == "2025-09-30"
    assert row["metadata"]["statistic"] == "corporateMediumLongLoansNetIncreaseYTD"


def test_negative_ytd_is_valid_but_single_month_cannot_be_called_ytd():
    adapter = _adapter()
    text = _fixture("pboc_combined.html").replace(
        "中长期贷款增加65000亿元", "中长期贷款减少65000亿元"
    )
    assert adapter.parse_pboc_series(text, PBC, "CORP_MEDLONG_LOANS_YTD", NOW)[0][
        "value"
    ] == Decimal("-6.5")
    text = text.replace("前三季度人民币贷款增加", "9月份人民币贷款增加")
    with pytest.raises(ValueError):
        adapter.parse_pboc_series(text, PBC, "CORP_MEDLONG_LOANS_YTD", NOW)


@pytest.mark.parametrize("code", ["M1_YOY", "TSF_STOCK_YOY", "CORP_MEDLONG_LOANS_YTD"])
def test_date_only_publication_has_retrieval_availability_and_future_day_is_rejected(
    code,
):
    adapter = _adapter()
    text = _fixture("pboc_combined.html").replace("2025-10-15 17:00:04", "2025-10-15")
    row = adapter.parse_pboc_series(text, PBC, code, NOW)[-1]
    assert row["publishedAt"] is None
    assert row["availableAt"] == NOW
    assert row["timeBasis"] == "RETRIEVAL_ONLY"
    with pytest.raises(ValueError):
        adapter.parse_pboc_series(
            text.replace("2025-10-15", "2025-10-17"), PBC, code, NOW
        )


def test_conflicting_or_missing_latest_stock_value_rejected():
    adapter = _adapter()
    text = _fixture("pboc_combined.html")
    with pytest.raises(ValueError):
        adapter.parse_pboc_series(
            text.replace("同比增长8.2%", "同比增长NaN%"), PBC, "TSF_STOCK_YOY", NOW
        )
    with pytest.raises(ValueError):
        adapter.parse_pboc_series(
            text.replace(
                "</div>",
                "<p>2025年9月末社会融资规模存量为420万亿元，同比增长9.0%。</p></div>",
            ),
            PBC,
            "TSF_STOCK_YOY",
            NOW,
        )


@pytest.mark.parametrize(
    "uri",
    [
        "https://home.treasury.gov.evil.test/x",
        "https://user@home.treasury.gov/x",
        "https://home.treasury.gov:444/x",
        "https://www.pbc.gov.cn.evil.test/x",
        "file:///x",
    ],
)
def test_official_source_url_rejects_impostors(uri):
    with pytest.raises(ValueError):
        _adapter().parse_treasury_xml(
            _fixture("treasury_nominal.xml"), uri, "US_T10_NOMINAL", NOW
        )


def test_naive_collection_time_is_rejected():
    with pytest.raises(ValueError):
        _adapter().parse_treasury_xml(
            _fixture("treasury_nominal.xml"),
            _ust_uri("nominal"),
            "US_T10_NOMINAL",
            NOW.replace(tzinfo=None),
        )


@pytest.mark.asyncio
async def test_collection_three_years_isolated_sources_and_restricted_zero_http(
    monkeypatch,
):
    adapter = _adapter()
    requested = []

    async def fetch(client, uri):
        requested.append(uri)
        if "treasury.gov" in uri:
            year = uri.split("field_tdr_date_value=")[1]
            return _fixture(
                "treasury_real.xml" if "real_yield" in uri else "treasury_nominal.xml"
            ).replace("2025-10", f"{year}-10")
        if uri == adapter.PBOC_RELEASE_INDEX:
            return f'<a href="{PBC}">2025年前三季度金融统计数据报告</a>'
        return _fixture("pboc_combined.html")

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    result = await adapter.fetch_opportunity_series(
        NOW,
        permissions={
            "CHINABOND": {"status": "APPROVED"},
            "CFETS": {"status": "APPROVED"},
        },
    )
    assert result["sourceResults"]["CHINABOND"]["status"] == "PERMISSION_REQUIRED"
    assert result["sourceResults"]["CFETS"]["status"] == "PERMISSION_REQUIRED"
    assert all("chinabond" not in uri and "chinamoney" not in uri for uri in requested)
    assert set(result["sourceResults"]) == {
        "UST_NOMINAL",
        "UST_REAL",
        "PBOC_M1",
        "PBOC_TSF",
        "PBOC_LOANS",
        "CHINABOND",
        "CFETS",
    }
    assert all(
        result["sourceResults"][key]["status"] == "SUCCESS"
        for key in ["UST_NOMINAL", "UST_REAL", "PBOC_M1", "PBOC_TSF", "PBOC_LOANS"]
    )
    years = {
        uri.split("field_tdr_date_value=")[1]
        for uri in requested
        if "treasury.gov" in uri
    }
    assert years == {"2022", "2023", "2024", "2025"}
    assert {r["code"] for r in result["observations"]} == {
        "US_T10_NOMINAL",
        "US_T10_REAL",
        "M1_YOY",
        "TSF_STOCK_YOY",
        "CORP_MEDLONG_LOANS_YTD",
    }


@pytest.mark.asyncio
async def test_latest_bad_source_does_not_emit_older_fallback_but_other_sources_survive(
    monkeypatch,
):
    adapter = _adapter()
    old_uri = PBC.replace("20251015171000", "20250915171000")

    async def fetch(client, uri):
        if "treasury.gov" in uri:
            raise httpx.ConnectError("offline")
        if uri == adapter.PBOC_RELEASE_INDEX:
            return f'<a href="{PBC}">2025年前三季度金融统计数据报告</a><a href="{old_uri}">2025年8月金融统计数据报告</a>'
        text = _fixture("pboc_combined.html")
        if uri == PBC:
            return text.replace(
                "狭义货币（M1）余额110.00万亿元，同比增长3.2%",
                "狭义货币（M1）余额110.00万亿元，同比增长NaN%",
            )
        return (
            text.replace("2025年前三季度", "2025年8月")
            .replace("2025年9月末", "2025年8月末")
            .replace("9月末", "8月末")
            .replace("前三季度", "前八个月")
        )

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    result = await adapter.fetch_opportunity_series(NOW)
    assert result["sourceResults"]["PBOC_M1"]["status"] == "FAILED"
    assert not any(r["code"] == "M1_YOY" for r in result["observations"])
    assert result["sourceResults"]["PBOC_TSF"]["status"] == "SUCCESS"
    assert result["sourceResults"]["PBOC_LOANS"]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_per_source_timeout_is_bounded_and_does_not_cancel_healthy_publishers(
    monkeypatch,
):
    adapter = _adapter()
    cancelled = asyncio.Event()

    async def fetch(client, uri):
        if "treasury.gov" in uri:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        if uri == adapter.PBOC_RELEASE_INDEX:
            return f'<a href="{PBC}">2025年前三季度金融统计数据报告</a>'
        return _fixture("pboc_combined.html")

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    monkeypatch.setattr(adapter, "_SOURCE_TIMEOUT_SECONDS", 0.1)
    result = await asyncio.wait_for(adapter.fetch_opportunity_series(NOW), 1)
    assert result["sourceResults"]["UST_REAL"]["status"] == "FAILED"
    assert result["sourceResults"]["UST_REAL"]["errorClass"] == "TimeoutError"
    assert result["sourceResults"]["PBOC_M1"]["status"] == "SUCCESS"
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_http_redirect_cannot_escape_official_publisher():
    adapter = _adapter()

    def handler(request):
        return httpx.Response(302, headers={"location": "https://example.org/data"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await adapter._fetch_text(client, _ust_uri("real"))


@pytest.mark.parametrize("label", ["前八个月", "上半年", "1—8月"])
def test_ytd_label_must_end_at_report_month(label):
    text = _fixture("pboc_combined.html").replace(
        "前三季度人民币贷款", f"{label}人民币贷款"
    )
    with pytest.raises(ValueError):
        _adapter().parse_pboc_series(text, PBC, "CORP_MEDLONG_LOANS_YTD", NOW)


def test_monthly_corporate_clause_cannot_borrow_an_unrelated_ytd_heading():
    text = _fixture("pboc_combined.html").replace(
        "前三季度人民币贷款增加12.00万亿元。分部门看，",
        "前三季度人民币贷款增加12.00万亿元。</p><p>9月份人民币贷款增加1.00万亿元。分部门看，",
    )
    with pytest.raises(ValueError):
        _adapter().parse_pboc_series(text, PBC, "CORP_MEDLONG_LOANS_YTD", NOW)


@pytest.mark.parametrize("code", ["M1_YOY", "TSF_STOCK_YOY", "CORP_MEDLONG_LOANS_YTD"])
def test_unfinished_reference_month_is_not_a_complete_financial_fact(code):
    text = (
        _fixture("pboc_combined.html")
        .replace("2025年前三季度金融", "2025年10月金融")
        .replace("2025年9月末", "2025年10月末")
        .replace("9月末", "10月末")
        .replace("前三季度人民币贷款", "前十个月人民币贷款")
    )
    with pytest.raises(ValueError):
        _adapter().parse_pboc_series(text, PBC, code, NOW)


def test_future_publication_date_is_rejected_even_if_time_format_is_unknown():
    text = (
        _fixture("pboc_combined.html")
        .replace("2025-10-15 17:00:04", "2025-10-17T17:00:04+08:00")
        .replace('content="2025-10-15"', 'content="2025-10-17T17:00:04+08:00"')
    )
    with pytest.raises(ValueError):
        _adapter().parse_pboc_series(text, PBC, "M1_YOY", NOW)


@pytest.mark.asyncio
async def test_m1_source_excludes_old_definition_annual_report_before_selecting_documents(
    monkeypatch,
):
    adapter = _adapter()
    old_uri = PBC.replace("20251015171000", "20250115171000")

    async def fetch(client, uri):
        if "treasury.gov" in uri:
            raise httpx.ConnectError("offline")
        if uri == adapter.PBOC_RELEASE_INDEX:
            return f'<a href="{PBC}">2025年前三季度金融统计数据报告</a><a href="{old_uri}">2024年金融统计数据报告</a>'
        text = _fixture("pboc_combined.html")
        return (
            text.replace("2025年前三季度金融", "2024年金融") if uri == old_uri else text
        )

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    result = await adapter.fetch_opportunity_series(NOW)
    assert result["sourceResults"]["PBOC_M1"]["status"] == "SUCCESS"
    assert len([r for r in result["observations"] if r["code"] == "M1_YOY"]) == 13


@pytest.mark.asyncio
async def test_standalone_stock_report_is_preferred_to_same_period_financial_report_without_stock(
    monkeypatch,
):
    adapter = _adapter()
    stock_uri = PBC.replace("20251015171000", "20251014171000")

    async def fetch(client, uri):
        if "treasury.gov" in uri:
            raise httpx.ConnectError("offline")
        if uri == adapter.PBOC_RELEASE_INDEX:
            return f'<a href="{PBC}">2025年前三季度金融统计数据报告</a><a href="{stock_uri}">2025年9月社会融资规模存量统计数据报告</a>'
        text = _fixture("pboc_combined.html")
        if uri == stock_uri:
            return text.replace(
                "2025年前三季度金融统计数据报告",
                "2025年9月社会融资规模存量统计数据报告",
            )
        return "\n".join(
            line
            for line in text.splitlines()
            if "初步统计，2025年9月末社会融资规模存量" not in line
        )

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    result = await adapter.fetch_opportunity_series(NOW)
    assert result["sourceResults"]["PBOC_TSF"]["status"] == "SUCCESS"
    rows = [r for r in result["observations"] if r["code"] == "TSF_STOCK_YOY"]
    assert len(rows) == 1
    assert rows[0]["sourceUri"] == stock_uri


@pytest.mark.asyncio
async def test_two_same_period_official_stock_claims_cannot_hide_conflict(monkeypatch):
    adapter = _adapter()
    stock_uri = PBC.replace("20251015171000", "20251014171000")

    async def fetch(client, uri):
        if "treasury.gov" in uri:
            raise httpx.ConnectError("offline")
        if uri == adapter.PBOC_RELEASE_INDEX:
            return f'<a href="{PBC}">2025年前三季度金融统计数据报告</a><a href="{stock_uri}">2025年9月社会融资规模存量统计数据报告</a>'
        text = _fixture("pboc_combined.html")
        if uri == stock_uri:
            return text.replace(
                "2025年前三季度金融统计数据报告",
                "2025年9月社会融资规模存量统计数据报告",
            )
        return text.replace("同比增长8.2%", "同比增长9.0%")

    monkeypatch.setattr(adapter, "_fetch_text", fetch)
    result = await adapter.fetch_opportunity_series(NOW)
    assert result["sourceResults"]["PBOC_TSF"]["status"] == "FAILED"
    assert not any(r["code"] == "TSF_STOCK_YOY" for r in result["observations"])
