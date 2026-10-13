import pytest

pytest.importorskip("fastapi")
from conftest import fixture  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from spashta.api import create_app  # noqa: E402


@pytest.fixture
def client(sample_index, fake_web):
    web, fetcher = fake_web({"https://news.example/2083/06/101234": ("text/html", fixture("article_ne.html"))})
    return TestClient(create_app(index=sample_index, fetcher=fetcher, admin_token="s3cret", rate_per_min=5))


def test_health_and_page(client):
    assert client.get("/api/health").json()["articles"] == 17
    page = client.get("/")
    assert page.status_code == 200 and "स्पष्ट" in page.text


def test_analyze_text_and_url(client):
    r = client.post("/api/analyze", json={"title": "SHOCKING! You won't believe this", "body": "Sources say something happened."})
    assert r.status_code == 200 and r.json()["score"] < 75
    r = client.post("/api/analyze", json={"url": "https://news.example/2083/06/101234"})
    assert r.status_code == 200 and r.json()["lang"] == "ne"
    assert client.post("/api/analyze", json={}).status_code == 422


def test_private_urls_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("SPASHTA_DB", str(tmp_path / "api.db"))
    app = create_app(index=None, admin_token="x")
    c = TestClient(app)
    r = c.post("/api/analyze", json={"url": "http://127.0.0.1:8080/admin"})
    assert r.status_code == 400


def test_search(client):
    hits = client.get("/api/search", params={"q": "पेट्रोल मूल्य", "k": 3}).json()
    assert len(hits) == 3 and hits[0]["title"]


def test_admin_requires_token(client):
    assert client.post("/api/sources", json={"url": "https://blog.example/"}).status_code == 401
    ok = client.post("/api/sources", json={"url": "https://blog.example/"}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert any(s["url"] == "https://blog.example/" for s in client.get("/api/sources").json())


def test_rate_limit(client):
    codes = [client.get("/api/search", params={"q": "x"}).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429


def test_llm_review_gated(sample_index):
    app = create_app(index=sample_index, admin_token="s3cret", rate_per_min=50, public_llm=False, llm_daily_limit=1)
    c = TestClient(app)
    body = {"title": "Petrol price up", "body": "Prices rose.", "llm": True}
    assert c.post("/api/analyze", json=body).status_code == 403
    auth = {"Authorization": "Bearer s3cret"}
    assert c.post("/api/analyze", json=body, headers=auth).status_code == 200
    assert c.post("/api/analyze", json=body, headers=auth).status_code == 429  # daily budget of 1


def test_rate_limit_behind_proxy(sample_index, monkeypatch):
    monkeypatch.setenv("SPASHTA_TRUST_PROXY", "1")
    c = TestClient(create_app(index=sample_index, rate_per_min=2))
    a, b = {"X-Forwarded-For": "1.1.1.1"}, {"X-Forwarded-For": "2.2.2.2"}
    assert [c.get("/api/search", params={"q": "x"}, headers=a).status_code for _ in range(3)] == [200, 200, 429]
    assert c.get("/api/search", params={"q": "x"}, headers=b).status_code == 200


def test_body_limits_and_security_headers(client):
    r = client.post("/api/analyze", content=b'{"title": "x", "body": "' + b"a" * 300_000 + b'"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff" and "default-src 'self'" in r.headers["content-security-policy"]

    def chunks():
        yield b'{"title": "x", "body": "y"}'
    r = client.post("/api/analyze", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 411


def test_logs_never_contain_the_client_address(sample_index, caplog, monkeypatch):
    import logging
    monkeypatch.setenv("SPASHTA_TRUST_PROXY", "1")
    c = TestClient(create_app(index=sample_index, rate_per_min=50))
    with caplog.at_level(logging.INFO, logger="spashta.api"):
        c.get("/api/search", params={"q": "पहिरो"}, headers={"X-Forwarded-For": "203.0.113.77"})
        c.get("/api/search", params={"q": "landslide"}, headers={"X-Forwarded-For": "203.0.113.77"})
    lines = [r.getMessage() for r in caplog.records if r.name == "spashta.api"]
    assert len(lines) == 2 and not any("203.0.113" in x for x in lines)
    assert lines[0].split("client=")[1] == lines[1].split("client=")[1]  # same client, same daily tag
    assert "q=" not in lines[0]  # no query text either


def test_slow_analysis_times_out_and_frees_its_slot(sample_index, monkeypatch):
    import time

    import spashta.api as api
    monkeypatch.setenv("SPASHTA_REQUEST_TIMEOUT", "0.2")
    monkeypatch.setenv("SPASHTA_MAX_CONCURRENT", "1")
    real = api.analyze
    monkeypatch.setattr(api, "analyze", lambda *a, **k: (time.sleep(0.5), real(*a, **k))[1])
    c = TestClient(create_app(index=sample_index, rate_per_min=50))
    r = c.post("/api/analyze", json={"title": "शीर्षक", "body": "पहिरोले घर बगायो।"})
    assert r.status_code == 504
    assert c.post("/api/analyze", json={"title": "t", "body": "b"}).status_code == 503  # the slow one still holds the slot
    time.sleep(0.5)
    monkeypatch.setattr(api, "analyze", real)
    assert c.post("/api/analyze", json={"title": "t", "body": "b c d"}).status_code == 200
