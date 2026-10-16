"""Public daily evidence: explicit identity, bounded HTTP and honest units."""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.integrations import daily_sources as sources

NOW = datetime(2026, 10, 16, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize(
    "uri",
    [
        "http://ofac.treasury.gov/recent-actions/20261009_33",
        "https://ofac.treasury.gov.evil.test/story",
        "https://ofac.treasury.gov@127.0.0.1/story",
        "https://127.0.0.1/story",
        "https://localhost/story",
        "https://ofac.treasury.gov:8443/story",
        "file:///secrets",
    ],
)
def test_only_named_public_official_https_sources_are_fetchable(uri):
    with pytest.raises(ValueError):
        sources.official_url(uri)


@pytest.mark.asyncio
async def test_redirect_to_untrusted_host_is_never_fetched():
    requests = []

    def route(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(ValueError):
            await sources.fetch_text(client, "https://ofac.treasury.gov/story")
    assert requests == ["https://ofac.treasury.gov/story"]


@pytest.mark.asyncio
async def test_document_size_limit_is_enforced_before_parsing():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-length": "9000000"}, text="small"
            )
        )
    ) as client:
        with pytest.raises(ValueError, match="size"):
            await sources.fetch_text(client, "https://ofac.treasury.gov/story")


@pytest.mark.asyncio
async def test_original_quote_check_does_not_invent_publication_hour_or_delivery():
    quote = (
        "Authorizing transactions related to diesel fuel of Russian Federation origin."
    )
    original = (
        f"<h1>Issuance of a general license</h1><time>10/09/2026</time><p>{quote}</p>"
    )
    source = {
        "id": "license",
        "url": "https://ofac.treasury.gov/recent-actions/20261009_33",
        "title": "Model title",
        "quote": quote,
        "publishedDate": "2026-10-09",
        "publishedAt": None,
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=original)
        )
    ) as client:
        checked = (
            await sources.verify_research_sources(
                [source], now=NOW, client=client, clock=lambda: NOW
            )
        )["license"]
    assert checked["verification"] == "SOURCE_CHECKED"
    assert checked["title"] == "Issuance of a general license"
    assert checked["publishedDate"] == "2026-10-09" and checked["publishedAt"] is None
    assert checked["quoteMatched"] is True
    assert "delivered" not in str(checked).lower()


@pytest.mark.parametrize(
    "change",
    [
        {"quote": "Unsupported delivery of one million tons."},
        {"publishedDate": "2026-10-17"},
    ],
)
@pytest.mark.asyncio
async def test_unmatched_quote_or_future_source_is_not_verified(change):
    source = {
        "id": "x",
        "url": "https://ofac.treasury.gov/story",
        "title": "Original",
        "quote": "A public policy license was issued.",
        "publishedDate": "2026-10-09",
        **change,
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="<h1>Original</h1><p>10/09/2026 A public policy license was issued.</p>",
            )
        )
    ) as client:
        checked = (
            await sources.verify_research_sources(
                [source], now=NOW, client=client, clock=lambda: NOW
            )
        )["x"]
    assert checked["verification"] == "UNVERIFIED"


@pytest.mark.asyncio
async def test_unapproved_media_link_is_retained_as_unverified_without_http():
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: seen.append(request))
    ) as client:
        checked = await sources.verify_research_sources(
            [
                {
                    "id": "media",
                    "url": "https://news.example/story",
                    "quote": "A report claims something happened.",
                }
            ],
            now=NOW,
            client=client,
            clock=lambda: NOW,
        )
    assert not seen
    assert checked["media"]["verification"] == "UNVERIFIED"


def test_energy_units_frequency_missing_values_and_actual_observation_changes():
    brent = sources.parse_energy_csv(
        "observation_date,DCOILBRENTEU\n2026-10-12,125\n2026-10-13,.\n2026-10-14,126\n",
        "DCOILBRENTEU",
        NOW,
    )
    diesel = sources.parse_energy_csv(
        "observation_date,GASDESW\n2026-10-05,6.2\n2026-10-12,6.1\n", "GASDESW", NOW
    )
    assert (brent["unit"], brent["frequency"], brent["sampleCount"]) == (
        "USD/barrel",
        "DAILY",
        2,
    )
    assert brent["previousDate"] == "2026-10-12" and brent["change"] == 1
    assert brent["changePct"] == pytest.approx(0.8)
    assert (diesel["unit"], diesel["frequency"], diesel["value"]) == (
        "USD/gallon",
        "WEEKLY",
        6.1,
    )
    assert diesel["change"] == pytest.approx(-0.1)
    assert brent["publishedAt"] is None and brent["timeBasis"] == "OBSERVATION_ONLY"
    assert "causal" not in brent


@pytest.mark.parametrize(
    "rows", ["2026-10-17,126", "2026-10-14,NaN", "2026-10-14,126\n2026-10-14,128"]
)
def test_future_nonfinite_or_conflicting_energy_observations_are_rejected(rows):
    with pytest.raises(ValueError):
        sources.parse_energy_csv(
            "observation_date,DCOILBRENTEU\n" + rows, "DCOILBRENTEU", NOW
        )


def test_old_energy_observation_is_stale_and_has_no_fabricated_current_date():
    result = sources.parse_energy_csv(
        "observation_date,DCOILBRENTEU\n2026-09-01,99\n", "DCOILBRENTEU", NOW
    )
    assert result["state"] == "STALE"
    assert result["asOf"] == "2026-09-01" and result["change"] is None


@pytest.mark.asyncio
async def test_each_public_source_failure_remains_independent_and_errors_are_safe():
    def route(request):
        if "fredgraph" in str(request.url):
            code = request.url.params["id"]
            return httpx.Response(
                200, text=f"observation_date,{code}\n2026-10-12,100\n2026-10-14,101\n"
            )
        return httpx.Response(403, text="private error credential must not escape")

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await sources.collect_daily_sources(
            NOW, client=client, clock=lambda: NOW
        )
    assert len(result["energy"]) == 2
    assert result["sourceResults"]["OFAC"]["status"] == "FAILED"
    assert result["sourceResults"]["DCOILBRENTEU"]["status"] == "SUCCESS"
    assert "private error" not in str(result)


def test_official_title_and_publication_day_use_page_metadata_and_explicit_date():
    nbs = sources._document(
        '<title></title><meta name="ArticleTitle" content="2026年9月经济运行"><meta name="PubDate" content="2026-10-16 10:00"><h1></h1><div class="TRS_Editor"><p>真实公布的国内宏观经济统计信息及原始口径说明。</p></div>',
        "https://www.stats.gov.cn/sj/zxfb/202610/t20261016_1.html",
        NOW,
    )
    fed = sources._document(
        "<title>FOMC minutes</title><time>October 07, 2026</time><p>The Federal Reserve issued minutes of its recent meeting.</p>",
        "https://www.federalreserve.gov/story",
        NOW,
    )
    assert nbs["title"] == "2026年9月经济运行"
    assert fed["publishedDate"] == "2026-10-07"
    assert nbs["publishedAt"] is None and fed["publishedAt"] is None
