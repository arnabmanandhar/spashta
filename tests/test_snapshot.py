import json
from datetime import datetime, timezone

from spashta.cli import main
from spashta.dataset import snapshot
from spashta.ingest import backfill_items
from spashta.models import Source
from spashta.samples import sample_articles
from spashta.store import Store


def _store_with_samples(path):
    store = Store(path)
    from spashta.chunk import chunk_article
    for a in sample_articles():
        store.upsert_article(a, chunk_article(a), "none", [[0.0]] * len(chunk_article(a)))
    return store


def test_freeze_is_reproducible_and_verifiable(tmp_path):
    store = _store_with_samples(":memory:")
    m1 = snapshot.freeze(store.iter_articles(), tmp_path / "a")
    m2 = snapshot.freeze(reversed(list(store.iter_articles())), tmp_path / "b")
    assert m1["articles"] == len(sample_articles()) > 10
    assert m1["snapshot_id"] == m2["snapshot_id"]  # order of input doesn't matter
    assert snapshot.verify(tmp_path / "a") == []
    assert sum(m1["by_lang"].values()) == m1["articles"]
    loaded = snapshot.load(tmp_path / "a")
    assert {a.id for a in loaded} == {a.id for a in sample_articles()}


def test_verify_catches_an_edited_article(tmp_path):
    store = _store_with_samples(":memory:")
    snapshot.freeze(store.iter_articles(), tmp_path)
    lines = (tmp_path / "articles.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[3])
    row["body"] = row["body"].replace("।", ".", 1) + " "
    lines[3] = json.dumps(row, ensure_ascii=False)
    (tmp_path / "articles.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    problems = snapshot.verify(tmp_path)
    assert any("does not match its hash" in p for p in problems)


def test_freeze_window_filters_by_publication_date(tmp_path):
    store = _store_with_samples(":memory:")
    dates = sorted(a.published for a in sample_articles() if a.published)
    cut = dates[len(dates) // 2]
    m = snapshot.freeze(store.iter_articles(), tmp_path, since=cut)
    assert 0 < m["articles"] < len(sample_articles())


def test_cli_freeze_verify_index(tmp_path, capsys):
    db = tmp_path / "live.db"
    _store_with_samples(str(db)).close()
    assert main(["--db", str(db), "dataset", "freeze", str(tmp_path / "snap")]) == 0
    assert main(["dataset", "verify", str(tmp_path / "snap")]) == 0
    assert main(["--db", str(tmp_path / "eval.db"), "dataset", "index", str(tmp_path / "snap")]) == 0
    out = capsys.readouterr().out
    assert "OK" in out and '"articles"' in out


INDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://news.example/sitemap-2026-07.xml</loc><lastmod>2026-07-31T10:00:00Z</lastmod></sitemap>
<sitemap><loc>https://news.example/sitemap-2026-09.xml</loc></sitemap>
<sitemap><loc>https://news.example/sitemap-2026-10.xml</loc><lastmod>2026-10-12T10:00:00Z</lastmod></sitemap>
</sitemapindex>"""


def _urlset(*entries):
    rows = "".join(f"<url><loc>{u}</loc>{f'<lastmod>{d}</lastmod>' if d else ''}</url>" for u, d in entries)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'


def test_backfill_walks_only_sitemaps_in_the_window(fake_web):
    web, fetcher = fake_web({
        "https://news.example/robots.txt": ("text/plain", "Sitemap: https://news.example/sitemap.xml\n"),
        "https://news.example/sitemap.xml": ("application/xml", INDEX),
        "https://news.example/sitemap-2026-09.xml": ("application/xml", _urlset(
            ("https://news.example/news/1001", "2026-09-10T08:00:00+05:45"),
            ("https://news.example/news/1002", "2026-09-20T08:00:00+05:45"))),
        "https://news.example/sitemap-2026-10.xml": ("application/xml", _urlset(
            ("https://news.example/news/1003", "2026-10-02T08:00:00+05:45"),
            ("https://news.example/2026/10/05/undated-story-slug", None))),
    })
    since = datetime(2026, 9, 15, tzinfo=timezone.utc)
    until = datetime(2026, 10, 10, tzinfo=timezone.utc)
    items = backfill_items(Source(url="https://news.example"), fetcher, since, until)
    assert [i.url for i in items] == ["https://news.example/2026/10/05/undated-story-slug",
                                      "https://news.example/news/1002", "https://news.example/news/1003"]
    assert "https://news.example/sitemap-2026-07.xml" not in web.requests  # lastmod before the window


def _snap(tmp_path):
    store = _store_with_samples(":memory:")
    snapshot.freeze(store.iter_articles(), tmp_path / "snap")
    return {r["id"]: r for r in snapshot.read_records(tmp_path / "snap")}


def test_release_has_no_text_and_rehydrate_reanchors_spans(tmp_path):
    from spashta.dataset import release
    from spashta.models import Article
    recs = _snap(tmp_path)
    rid, r = next((k, v) for k, v in recs.items() if v["lang"] == "ne")
    phrase = r["body"][40:80]
    labels = tmp_path / "gold.jsonl"
    labels.write_text(json.dumps({"article_id": rid, "headline": "consistent", "sensationalism": 0,
                                  "claims": [{"start": 40, "end": 80, "sourcing": "named", "text": phrase}],
                                  "loaded": []}, ensure_ascii=False) + "\n", encoding="utf-8")
    release.build_release(tmp_path / "snap", tmp_path / "rel", labels=labels)
    blob = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "rel").glob("*.jsonl"))
    assert phrase not in blob and r["title"] not in blob
    assert release.check_no_text(tmp_path / "rel") == []

    # The outlet has since added a paragraph at the top: the hash fails, the span moves but is found.
    def fetch(url):
        x = recs[next(k for k, v in recs.items() if v["url"] == url)]
        body = ("Editor's note: updated.\n\n" + x["body"]) if x["id"] == rid else x["body"]
        return Article(url=url, title=x["title"], body=body, source=x["source"], lang=x["lang"])

    rep = release.rehydrate(tmp_path / "rel", tmp_path / "full", fetch=fetch)
    assert rep["articles"] == {"exact": len(recs) - 1, "changed": 1, "failed": 0}
    assert rep["spans"] == {"kept": 0, "moved": 1, "lost": 0}
    (lab,) = [json.loads(x) for x in (tmp_path / "full" / "labels.full.jsonl").read_text(encoding="utf-8").splitlines()]
    assert lab["claims"][0]["text"] == phrase


def test_release_refuses_text(tmp_path):
    import pytest

    from spashta.dataset import release
    _snap(tmp_path)
    pairs = tmp_path / "pairs.jsonl"
    pairs.write_text(json.dumps({"a": "x", "b": "y", "same_event": True, "note": "z" * 400}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="article text"):
        release.build_release(tmp_path / "snap", tmp_path / "rel", pairs=pairs)
