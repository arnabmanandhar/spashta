from spashta.embed import HashingEmbedder
from spashta.retrieve import BM25, Index
from spashta.samples import sample_articles
from spashta.store import Store


def test_incremental_index_matches_fresh_load(tmp_path):
    store = Store(tmp_path / "s.db")
    inc = Index(store, HashingEmbedder())
    arts = sample_articles()
    inc.add_many(arts[:9])
    inc.add_many(arts[9:])
    fresh = Index(store, HashingEmbedder())
    for q in ("पहिरो मृत्यु", "petrol price", "SEE results"):
        assert [h.chunk.id for h in inc.search(q, k=8)] == [h.chunk.id for h in fresh.search(q, k=8)]
    assert inc.names == fresh.names


def test_readding_an_article_replaces_its_chunks():
    index = Index(Store(":memory:"), HashingEmbedder())
    a = sample_articles()[0]
    index.add_many([a])
    changed = a.model_copy(update={"body": a.body + "\n\nएउटा नयाँ अनुच्छेद थपिएको छ जसमा नयाँ जानकारी छ।"})
    index.add_many([changed])
    live = [c for c, ok in zip(index.chunks, index.alive) if ok]
    assert all(c.article_id == a.id for c in live)
    assert any("नयाँ अनुच्छेद" in c.text for c in live)
    hits = index.search("पहिरो नयाँ अनुच्छेद", k=10)
    assert len({h.chunk.id for h in hits}) == len(hits)  # no stale duplicate of a chunk id
    assert any("नयाँ अनुच्छेद" in h.chunk.text for h in hits)


def test_bm25_scores_only_matching_docs():
    bm = BM25()
    bm.add(["a", "b"])
    bm.add(["c"])
    s = bm.scores(["a"])
    assert s[0] > 0 and s[1] == 0
