from spashta.ingest.feeds import parse_feed
from spashta.ingest.sitemap import parse_sitemap

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>T</title>
<item><title>One</title><link>https://news.example/1</link></item>
</channel></rss>"""


def test_feed_with_junk_after_the_root_still_parses():
    items = parse_feed(RSS + "\n<br /><b>Warning</b>: something in functions.php", "https://news.example/")
    assert [i.url for i in items] == ["https://news.example/1"]


def test_not_xml_is_no_feed_rather_than_an_error():
    assert parse_feed("<html><body>nope", "https://news.example/") == []


def test_sitemap_with_junk_after_the_root_still_parses():
    sm = parse_sitemap('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                       "<url><loc>https://news.example/a</loc></url></urlset><!-- cache --><div>x</div>")
    assert [e.url for e in sm.entries] == ["https://news.example/a"]
