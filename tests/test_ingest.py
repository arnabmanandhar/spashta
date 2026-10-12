import pytest
from conftest import fixture

from spashta.fetch import Fetcher, FetchError, check_url
from spashta.ingest import extract_article, ingest_source, list_articles, parse_feed, parse_sitemap, resolve
from spashta.ingest.discover import looks_like_article
from spashta.models import Source, canonical_url


def test_rss_parse():
    items = parse_feed(fixture("feed_rss.xml"), "https://news.example/feed")
    assert len(items) == 2
    assert items[0].title == "पेट्रोलको मूल्य बढ्यो"
    assert items[0].author == "नमुना संवाददाता"
    assert items[0].published.year == 2026
    assert items[0].summary.startswith("नेपाल आयल")
    assert items[0].lang == "ne"


def test_atom_parse_resolves_relative_links():
    (item,) = parse_feed(fixture("feed_atom.xml"), "https://blog.example/atom.xml")
    assert item.url == "https://blog.example/posts/hello-and-welcome-to-the-sample-blog"
    assert item.title == "Hello & welcome"
    assert item.author == "A. Writer"


def test_json_feed_parse():
    items = parse_feed('{"version":"https://jsonfeed.org/version/1.1","items":[{"url":"https://b.example/p/1","title":"T"}]}')
    assert items[0].url == "https://b.example/p/1"


def test_sitemap_parse():
    sm = parse_sitemap(fixture("sitemap_news.xml"))
    assert len(sm.entries) == 2
    news = sm.entries[1]
    assert news.title.startswith("पहिरो") and news.lang == "ne"
    assert parse_sitemap(fixture("sitemap_index.xml")).children == ["https://news.example/sitemap-news.xml"]


def test_extract_article_nepali():
    a = extract_article(fixture("article_ne.html"), "https://news.example/2083/06/101234?utm_source=fb", use_trafilatura=False)
    assert a.url == "https://news.example/2083/06/101234"
    assert a.title.startswith("सिन्धुपाल्चोकमा")
    assert "१२ जनाको" in a.body  # the publisher's digits are kept for display
    from spashta import lang as L
    assert "12" in L.numbers(a.body)  # and folded for analysis
    assert "विज्ञापन" not in a.body and "फुटर" not in a.body
    assert a.lang == "ne"
    assert a.author == "नमुना संवाददाता"
    assert a.published.year == 2026


def test_extract_with_trafilatura_when_installed():
    pytest.importorskip("trafilatura")
    a = extract_article(fixture("article_ne.html"), "https://news.example/2083/06/101234")
    assert "बेपत्ता" in a.body


def test_canonical_url_drops_tracking():
    assert canonical_url("HTTPS://News.Example/a/?utm_source=x&id=3#top") == "https://news.example/a?id=3"


def test_looks_like_article():
    assert looks_like_article("https://blog.example/2026/10/fuel-prices-rise-again-across-the-valley", "blog.example")
    assert looks_like_article("https://blog.example/news/123456", "blog.example")
    assert not looks_like_article("https://blog.example/category/politics", "blog.example")
    assert not looks_like_article("https://blog.example/images/photo.jpg", "blog.example")
    assert not looks_like_article("https://other.example/2026/10/some-other-site-story-here", "blog.example")


def test_resolve_finds_advertised_feed(fake_web):
    web, fetcher = fake_web({
        "https://news.example/": ("text/html", fixture("article_ne.html")),
        "https://news.example/feed": ("application/rss+xml", fixture("feed_rss.xml")),
    })
    s = resolve(Source(url="https://news.example/"), fetcher)
    assert s.kind == "feed" and s.endpoint == "https://news.example/feed"


def test_resolve_falls_back_to_sitemap_from_robots(fake_web):
    web, fetcher = fake_web({
        "https://news.example/robots.txt": ("text/plain", "User-agent: *\nAllow: /\nSitemap: https://news.example/sitemap-news.xml\n"),
        "https://news.example/": ("text/html", "<html><body>no feed here</body></html>"),
        "https://news.example/sitemap-news.xml": ("application/xml", fixture("sitemap_news.xml")),
    })
    s = resolve(Source(url="https://news.example/"), fetcher)
    assert s.kind == "sitemap"
    items = list_articles(s, fetcher)
    assert items[0].url == "https://news.example/2083/06/100002"  # newest first


def test_resolve_falls_back_to_links(fake_web):
    web, fetcher = fake_web({"https://blog.example/": ("text/html", fixture("home.html"))})
    s = resolve(Source(url="https://blog.example/"), fetcher)
    assert s.kind == "links"
    urls = {i.url for i in list_articles(s, fetcher)}
    assert urls == {"https://blog.example/2026/10/fuel-prices-rise-again-across-the-valley", "https://blog.example/news/123456"}


def test_robots_disallow_is_honoured(fake_web):
    web, fetcher = fake_web({
        "https://news.example/robots.txt": ("text/plain", "User-agent: *\nDisallow: /private\n"),
        "https://news.example/private/x": ("text/html", "secret"),
    })
    with pytest.raises(FetchError) as e:
        fetcher.get("https://news.example/private/x")
    assert e.value.kind == "robots"
    assert "https://news.example/private/x" not in web.requests


def test_private_addresses_blocked_by_default():
    with pytest.raises(FetchError) as e:
        check_url("http://127.0.0.1/admin")
    assert e.value.kind == "blocked"
    with pytest.raises(FetchError):
        check_url("file:///etc/passwd")
    with pytest.raises(FetchError):
        Fetcher().get("http://user:pass@example.com/")


def test_ingest_source_end_to_end(fake_web, sample_index):
    body = fixture("article_ne.html")
    web, fetcher = fake_web({
        "https://news.example/feed": ("application/rss+xml", fixture("feed_rss.xml")),
        "https://news.example/2083/06/101180": ("text/html", body),
        "https://news.example/2083/06/101181": ("text/html", "<html><body><p>too short</p></body></html>"),
    })
    before = sample_index.store.count()["articles"]
    src = Source(url="https://news.example/feed", kind="feed", endpoint="https://news.example/feed")
    r = ingest_source(src, fetcher, sample_index)
    assert r.found == 2 and r.added == 1 and r.skipped == 1
    assert sample_index.store.count()["articles"] == before + 1
    again = ingest_source(src, fetcher, sample_index)
    assert again.added == 0 and again.skipped == 2
