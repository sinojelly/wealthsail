"""Public daily evidence: explicit identity, bounded HTTP and honest units."""

import asyncio
from collections import Counter
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


@pytest.mark.asyncio
async def test_exchange_html_original_can_verify_profit_and_cash_without_relaxing_host_checks():
    quote = "The current quarter reported profit growth and cash collected from paid customer orders."
    source = {
        "id": "earnings",
        "url": "https://www.sse.com.cn/disclosure/earnings.html",
        "quote": quote,
        "publishedDate": "2026-10-15",
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=(
                    '<h1>Financial disclosure</h1><meta name="pubdate" content="2026-10-15">'
                    f"<p>{quote}</p>"
                ),
            )
        )
    ) as client:
        checked = await sources.verify_research_sources(
            [source], now=NOW, client=client, clock=lambda: NOW
        )
    assert checked["earnings"]["verification"] == "SOURCE_CHECKED"
    with pytest.raises(ValueError):
        sources.official_url(
            "https://www.sse.com.cn.evil.test/disclosure/earnings.html"
        )


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


@pytest.mark.asyncio
async def test_verification_keeps_the_bounded_24_source_budget_without_fetching_a_25th():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(
            200, text="<h1>Original</h1><p>A public policy license was issued.</p>"
        )

    candidates = [
        {
            "id": f"source{i}",
            "url": f"https://ofac.treasury.gov/source-{i}",
            "quote": "A public policy license was issued.",
            "publishedDate": None,
        }
        for i in range(25)
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        checked = await sources.verify_research_sources(
            candidates, now=NOW, client=client, clock=lambda: NOW
        )
    assert len(checked) == len(seen) == 24
    assert "source23" in checked and "source24" not in checked


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


@pytest.mark.asyncio
async def test_license_expiry_in_body_cannot_become_the_publication_day():
    original = "<h1>Policy license</h1><time>10/09/2026</time><p>Authorizing public transactions until 10/16/2026 under the license.</p>"
    source = {
        "id": "x",
        "url": "https://ofac.treasury.gov/policy",
        "title": "Policy license",
        "quote": "Authorizing public transactions until 10/16/2026 under the license.",
        "publishedDate": "2026-10-16",
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=original)
        )
    ) as client:
        checked = await sources.verify_research_sources(
            [source], now=NOW, client=client, clock=lambda: NOW
        )
    assert checked["x"]["verification"] == "UNVERIFIED"
    assert checked["x"]["publishedDate"] is None


def rss(links):
    return (
        "<rss><channel>"
        + "".join(
            f"<item><title>September observation</title><link>{link.replace('&', '&amp;')}</link>"
            "<pubDate>Thu, 15 Oct 2026 09:00:00 EST</pubDate></item>"
            for link in links
        )
        + "</channel></rss>"
    )


def page(title, day="2026-10-15"):
    return f'<title>{title}</title><meta name="pubdate" content="{day}"><main><p>{title} reports public original observations with the published statistical definitions.</p></main>'


def test_bls_and_eia_registry_uses_their_official_published_feeds():
    assert sources.INDEXES["BLS"] == "https://www.bls.gov/feed/bls_latest.rss"
    assert sources.INDEXES["EIA"] == "https://www.eia.gov/rss/todayinenergy.xml"
    assert "BEA" not in sources.INDEXES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,feed,article",
    [
        (
            "BLS",
            "https://www.bls.gov/feed/bls_latest.rss",
            "https://www.bls.gov/news.release/cpi.nr0.htm",
        ),
        (
            "EIA",
            "https://www.eia.gov/rss/todayinenergy.xml",
            "https://www.eia.gov/todayinenergy/detail.php?id=123",
        ),
    ],
)
async def test_official_rss_collects_bounded_original_quotes_and_real_page_day(
    monkeypatch, key, feed, article
):
    monkeypatch.setattr(sources, "INDEXES", {key: feed})
    monkeypatch.setattr(sources, "ENERGY", {})

    def route(request):
        return httpx.Response(
            200,
            text=rss([article])
            if str(request.url) == feed
            else page("September reference period"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await sources.collect_daily_sources(
            NOW, client=client, clock=lambda: NOW
        )
    doc = result["documents"][0]
    assert doc["sourceKey"] == key and doc["url"] == article
    assert doc["publishedDate"] == "2026-10-15" and doc["publishedAt"] is None
    assert len(doc["quote"]) <= 300 and "<main>" not in str(result)
    assert result["sourceResults"][key] == {"status": "SUCCESS"}
    assert "quote" not in result["candidateIndex"][0]


@pytest.mark.asyncio
async def test_five_publishers_get_fair_documents_and_larger_metadata_only_index():
    def route(request):
        uri = str(request.url)
        if "fredgraph" in uri:
            code = request.url.params["id"]
            return httpx.Response(
                200, text=f"observation_date,{code}\n2026-10-14,100\n"
            )
        for key, feed in sources.INDEXES.items():
            if uri != feed:
                continue
            if key == "OFAC":
                links = [
                    f"https://ofac.treasury.gov/recent-actions/20261015_{n}"
                    for n in range(8)
                ]
            elif key == "NBS":
                links = [
                    f"https://www.stats.gov.cn/sj/zxfb/202610/t20261015_{n}.html"
                    for n in range(8)
                ]
            else:
                host = {
                    "BLS": "www.bls.gov",
                    "EIA": "www.eia.gov",
                    "FED": "www.federalreserve.gov",
                }[key]
                links = [f"https://{host}/story/{n}" for n in range(8)]
            raw = (
                "".join(
                    f'<a href="{link}">Official release {n}</a>'
                    for n, link in enumerate(links)
                )
                if key in {"OFAC", "NBS"}
                else rss(links)
            )
            return httpx.Response(200, text=raw)
        return httpx.Response(200, text=page(uri))

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await sources.collect_daily_sources(
            NOW, client=client, clock=lambda: NOW
        )
    counts = Counter(row["sourceKey"] for row in result["documents"])
    assert counts == {"OFAC": 2, "FED": 2, "NBS": 2, "BLS": 2, "EIA": 2}
    assert len(result["documents"]) <= 12 and len(result["candidateIndex"]) == 40
    assert all(
        set(row)
        <= {
            "id",
            "url",
            "title",
            "publishedDate",
            "retrievedAt",
            "contentHash",
            "sourceKey",
        }
        for row in result["candidateIndex"]
    )


@pytest.mark.asyncio
async def test_normalized_url_and_exact_content_hash_remove_duplicate_candidates(
    monkeypatch,
):
    feed = "https://www.eia.gov/rss/todayinenergy.xml"
    monkeypatch.setattr(sources, "INDEXES", {"EIA": feed})
    monkeypatch.setattr(sources, "ENERGY", {})
    links = [
        "https://www.eia.gov/story?id=1&utm_source=feed#top",
        "https://www.eia.gov/story?id=1",
        "https://www.eia.gov/mirror?id=1",
        "https://www.eia.gov/story?id=2",
    ]
    requests = []

    def route(request):
        uri = str(request.url)
        requests.append(uri)
        return httpx.Response(
            200,
            text=rss(links)
            if uri == feed
            else page("Unique second" if uri.endswith("id=2") else "Same original"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await sources.collect_daily_sources(
            NOW, client=client, clock=lambda: NOW
        )
    assert requests.count("https://www.eia.gov/story?id=1") == 1
    assert len(result["candidateIndex"]) == len(result["documents"]) == 2


@pytest.mark.asyncio
async def test_source_total_timeout_preserves_already_collected_documents(monkeypatch):
    feed = "https://www.eia.gov/rss/todayinenergy.xml"
    monkeypatch.setattr(sources, "INDEXES", {"EIA": feed})
    monkeypatch.setattr(sources, "ENERGY", {})
    monkeypatch.setattr(sources, "SOURCE_TIMEOUT_SECONDS", 0.02, raising=False)

    async def route(request):
        uri = str(request.url)
        if uri == feed:
            return httpx.Response(
                200, text=rss(["https://www.eia.gov/fast", "https://www.eia.gov/slow"])
            )
        if uri.endswith("slow"):
            await asyncio.Event().wait()
        return httpx.Response(200, text=page("Already collected official fact"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await asyncio.wait_for(
            sources.collect_daily_sources(NOW, client=client, clock=lambda: NOW), 0.5
        )
    assert len(result["documents"]) == len(result["candidateIndex"]) == 1
    assert result["sourceResults"]["EIA"] == {
        "status": "FAILED",
        "errorClass": "TimeoutError",
    }


@pytest.mark.asyncio
async def test_future_feed_publication_hour_is_not_fetched_even_when_same_local_day(
    monkeypatch,
):
    feed = "https://www.eia.gov/rss/todayinenergy.xml"
    monkeypatch.setattr(sources, "INDEXES", {"EIA": feed})
    monkeypatch.setattr(sources, "ENERGY", {})
    requested = []

    def route(request):
        requested.append(str(request.url))
        return httpx.Response(
            200,
            text=rss(["https://www.eia.gov/future"]).replace(
                "Thu, 15 Oct 2026 09:00:00 EST", "Fri, 16 Oct 2026 09:00:00 EST"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await sources.collect_daily_sources(
            NOW, client=client, clock=lambda: NOW
        )
    assert requested == [feed]
    assert not result["documents"] and not result["candidateIndex"]
    assert result["sourceResults"]["EIA"]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_partial_article_http_failure_keeps_other_originals_with_safe_source_failure(
    monkeypatch,
):
    feed = "https://www.eia.gov/rss/todayinenergy.xml"
    monkeypatch.setattr(sources, "INDEXES", {"EIA": feed})
    monkeypatch.setattr(sources, "ENERGY", {})

    def route(request):
        uri = str(request.url)
        if uri == feed:
            return httpx.Response(
                200, text=rss(["https://www.eia.gov/ok", "https://www.eia.gov/denied"])
            )
        return (
            httpx.Response(403, text="SECRET provider error")
            if uri.endswith("denied")
            else httpx.Response(200, text=page("Legitimate original"))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await sources.collect_daily_sources(
            NOW, client=client, clock=lambda: NOW
        )
    assert len(result["documents"]) == 1 and "SECRET" not in str(result)
    assert result["sourceResults"]["EIA"] == {
        "status": "FAILED",
        "errorClass": "HTTP_403",
    }


@pytest.mark.asyncio
async def test_same_day_future_publication_metadata_cannot_certify_original_quote(
    monkeypatch,
):
    original = page("Published energy statistics").replace(
        '<meta name="pubdate" content="2026-10-15">',
        '<meta property="article:published_time" content="2026-10-16T20:00:00+00:00">',
    )
    source = {
        "id": "eia",
        "url": "https://www.eia.gov/story",
        "quote": "Published energy statistics reports public original observations",
        "publishedDate": None,
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=original)
        )
    ) as client:
        proof = await sources.verify_research_sources(
            [source], now=NOW, client=client, clock=lambda: NOW
        )
    assert proof["eia"]["verification"] == "UNVERIFIED"
