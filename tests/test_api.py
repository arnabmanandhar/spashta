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
