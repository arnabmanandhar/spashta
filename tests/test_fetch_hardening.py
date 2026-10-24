import pytest
from conftest import fixture

from spashta.fetch import Fetcher, FetchError, Response
from spashta.ingest import boilerplate, ingest_sources
from spashta.models import Article, Source
from spashta.store import Store


class Scripted:
    """Transport answering from a list of responses per URL, in order."""

    def __init__(self, script):
        self.script = {u: list(rs) for u, rs in script.items()}
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if url.endswith("/robots.txt"):
            return Response(url, 404, {}, b"")
        rs = self.script.get(url)
        if not rs:
            return Response(url, 404, {}, b"")
        return rs.pop(0) if len(rs) > 1 else rs[0]


def test_redirect_to_private_address_is_refused_before_request():
    web = Scripted({"https://93.184.216.34/a": [Response("https://93.184.216.34/a", 302, {"location": "http://127.0.0.1/admin"}, b"")]})
    with pytest.raises(FetchError) as e:
        Fetcher(transport=web, delay_s=0).get("https://93.184.216.34/a")
    assert e.value.kind == "blocked"
    assert not any("127.0.0.1" in u for u, _ in web.calls)


def test_redirects_are_followed_and_robots_rechecked():
    web = Scripted({
        "https://a.example/old": [Response("https://a.example/old", 301, {"location": "/new"}, b"")],
        "https://a.example/new": [Response("https://a.example/new", 200, {"content-type": "text/html"}, b"ok")],
    })
    r = Fetcher(transport=web, delay_s=0, allow_private=True).get("https://a.example/old")
    assert r.url == "https://a.example/new" and r.text == "ok"


def test_retry_after_on_429(monkeypatch):
    sleeps = []
    monkeypatch.setattr("spashta.fetch.time.sleep", sleeps.append)
    web = Scripted({"https://a.example/x": [Response("https://a.example/x", 429, {"retry-after": "2"}, b""),
                                            Response("https://a.example/x", 200, {}, b"fine")]})
    r = Fetcher(transport=web, delay_s=0, allow_private=True).get("https://a.example/x")
    assert r.text == "fine" and 2.0 in sleeps


def test_conditional_get_uses_cache(tmp_path):
    store = Store(tmp_path / "s.db")
    web = Scripted({"https://a.example/feed": [
        Response("https://a.example/feed", 200, {"etag": '"v1"', "content-type": "application/rss+xml"}, b"<rss/>"),
        Response("https://a.example/feed", 304, {}, b""),
    ]})
    f = Fetcher(transport=web, delay_s=0, allow_private=True, cache=store)
    assert f.get("https://a.example/feed", conditional=True).text == "<rss/>"
    second = f.get("https://a.example/feed", conditional=True)
    assert second.text == "<rss/>" and second.headers.get("x-spashta-cache") == "hit"
    assert web.calls[-1][1]["If-None-Match"] == '"v1"'


def test_boilerplate_learned_per_site():
    tail = "Follow us on Facebook and Twitter for the latest news updates."
    bodies = [f"Story number {i} has its own unique opening paragraph here.\n\n{tail}" for i in range(4)]
    bp = boilerplate.learn(bodies)
    assert len(bp) == 1
    a = boilerplate.strip(Article(url="https://x.example/1", title="t", body=bodies[0]), bp)
    assert a.body == "Story number 0 has its own unique opening paragraph here."
    assert boilerplate.learn(bodies[:2]) == set()  # too few articles to tell


def test_parallel_ingest(fake_web, sample_index):
    web, fetcher = fake_web({
        "https://news.example/feed": ("application/rss+xml", fixture("feed_rss.xml")),
        "https://news.example/2083/06/101180": ("text/html", fixture("article_ne.html")),
        "https://blog.example/": ("text/html", fixture("home.html")),
    })
    results = ingest_sources([Source(url="https://news.example/feed", kind="feed", endpoint="https://news.example/feed"),
                              Source(url="https://blog.example/")], fetcher, sample_index, workers=2)
    assert results[0].added == 1
    assert results[1].kind == "links"


def test_bikram_sambat_dates():
    from spashta.bsdate import find_bs_date, parse_bs_date
    assert find_bs_date("काठमाडौं, २०८३ असोज २४ ।") == (2083, 6, 24)
    assert find_bs_date("/samachar/2083-06-24/x") == (2083, 6, 24)
    assert find_bs_date("published 2026-10-10") is None  # Gregorian, not BS
    pytest.importorskip("nepali_datetime")
    assert parse_bs_date("२०८३ असोज २४").date().isoformat() == "2026-10-10"


@pytest.mark.parametrize("url", ["http://[::ffff:127.0.0.1]/", "http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/",
                                 "http://0.0.0.0/", "http://[::1]/", "http://10.1.2.3/", "http://[2002:7f00:1::]/"])
def test_non_public_addresses_are_blocked(url):
    from spashta.fetch import check_url
    with pytest.raises(FetchError) as e:
        check_url(url)
    assert e.value.kind == "blocked"


def test_non_web_ports_are_blocked():
    from spashta.fetch import check_url
    with pytest.raises(FetchError, match="port 22"):
        check_url("http://93.184.216.34:22/")
    assert check_url("https://93.184.216.34:8443/") == ["93.184.216.34"]


def test_connection_is_pinned_to_the_checked_address(monkeypatch):
    """DNS rebinding: the name resolves to a public address for the check and
    to 127.0.0.1 afterwards. The request must go to the checked address."""
    import socket

    import spashta.fetch as F
    answers = iter(["93.184.216.34", "127.0.0.1", "127.0.0.1"])
    lookups = []

    def fake_getaddrinfo(host, port, *a, **k):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    sent = []
    monkeypatch.setattr(F.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(F, "pinned_transport", lambda url, headers, ips, timeout=0: sent.append(ips) or
                        Response(url, 200, {"content-type": "text/html"}, b"ok"))
    r = Fetcher(delay_s=0, respect_robots=False).get("http://rebind.example/a")
    assert r.text == "ok" and sent == [["93.184.216.34"]] and lookups == ["rebind.example"]


def test_pinned_transport_sends_the_hostname(tmp_path):
    import http.server
    import threading

    from spashta.fetch import pinned_transport
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["host"], seen["path"] = self.headers["Host"], self.path
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    port = srv.server_address[1]
    r = pinned_transport(f"http://news.example:{port}/a/b?x=1", {"User-Agent": "SpashtaBot"}, ["127.0.0.1"], 5)
    srv.server_close()
    assert r.status == 200 and r.body == b"hello"
    assert seen == {"host": f"news.example:{port}", "path": "/a/b?x=1"}


def test_deadline_caps_total_time():
    import time
    web = Scripted({"https://a.example/slow": [Response("https://a.example/slow", 503, {"retry-after": "5"}, b"")]})
    t = time.monotonic()
    with pytest.raises(FetchError) as e:
        Fetcher(transport=web, delay_s=0, allow_private=True, deadline_s=0.3, retries=3).get("https://a.example/slow")
    assert e.value.kind == "timeout" and time.monotonic() - t < 2


def test_one_broken_outlet_does_not_stop_the_others(fake_web, sample_index, monkeypatch):
    import spashta.ingest as ingest
    web, fetcher = fake_web({
        "https://news.example/feed": ("application/rss+xml", fixture("feed_rss.xml")),
        "https://news.example/2083/06/101180": ("text/html", fixture("article_ne.html")),
    })

    def broken(source, fetcher):
        raise ValueError("unexpected page")

    monkeypatch.setattr(ingest, "resolve", broken)
    results = ingest_sources([Source(url="https://broken.example/"),
                              Source(url="https://news.example/feed", kind="feed", endpoint="https://news.example/feed")],
                             fetcher, sample_index, workers=2)
    assert results[0].errors == ["ValueError: unexpected page"]
    assert results[1].added == 1
