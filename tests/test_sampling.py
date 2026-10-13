from collections import Counter

from spashta.dataset import sample, snapshot
from spashta.dataset.topics import section_topic, topic_of
from spashta.samples import sample_articles
from spashta.store import Store


def _records(tmp_path):
    store = Store(":memory:")
    from spashta.chunk import chunk_article
    for a in sample_articles():
        store.upsert_article(a, chunk_article(a), "none", [[0.0]] * len(chunk_article(a)))
    snapshot.freeze(store.iter_articles(), tmp_path)
    return snapshot.read_records(tmp_path)


def test_topics_from_section_and_keywords():
    assert section_topic("https://kathmandupost.com/politics/2026/10/01/x") == "politics"
    assert section_topic("https://ekantipur.com/khelkud/2026/10/01/123") == "sports"
    assert topic_of("https://x.example/news/1", "सिन्धुपाल्चोकमा पहिरो, १२ जनाको मृत्यु", "पहिरोले घर बगायो। उद्धार जारी छ।") == "disaster"
    assert topic_of("https://x.example/news/2", "Nepal beat UAE by five wickets", "The cricket team won the match.") == "sports"
    assert topic_of("https://x.example/news/3", "A quiet day", "Nothing much happened.") == "other"


def test_clusters_join_same_event_across_outlets(tmp_path):
    recs = _records(tmp_path)
    cluster, outlets = sample.story_clusters(recs)
    multi = [c for c, n in outlets.items() if n >= 2]
    assert multi, "the bundled samples cover the landslide in several outlets"
    biggest = Counter(cluster).most_common(1)[0][0]
    langs = {r["lang"] for r, c in zip(recs, cluster) if c == biggest}
    assert langs == {"ne", "en"}


def test_stratified_sample_is_deterministic_and_marks_batches(tmp_path):
    recs = _records(tmp_path)
    a = sample.stratified_sample(recs, n=10, pilot=3, double=4, minimum=1)
    b = sample.stratified_sample(recs, n=10, pilot=3, double=4, minimum=1)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert len(a) == 10 and len({r["id"] for r in a}) == 10
    assert [r["batch"] for r in a[:3]] == ["pilot"] * 3 and all(r["batch"] == "main" for r in a[3:])
    assert sum(r["double"] for r in a) == 7
    s = sample.summary(a)
    assert set(s["by_lang"]) == {"ne", "en"} and s["multi_outlet_stories"] >= 1


def test_candidate_pairs_cross_outlets(tmp_path):
    recs = _records(tmp_path)
    chosen = sample.stratified_sample(recs, n=8, pilot=2, double=2, minimum=1)
    pairs = sample.candidate_pairs(recs, chosen)
    by_id = {r["id"]: r for r in recs}
    assert pairs and {"cluster"} <= {p["reason"] for p in pairs}
    for p in pairs:
        assert by_id[p["a"]]["outlet_group"] != by_id[p["b"]]["outlet_group"]


def test_cli_sample_and_pairs(tmp_path, capsys):
    import json

    from spashta.cli import main
    _records(tmp_path / "snap")
    assert main(["dataset", "sample", str(tmp_path / "snap"), "-o", str(tmp_path / "s.jsonl"), "--n", "8",
                 "--pilot", "2", "--double", "2", "--minimum", "1"]) == 0
    assert main(["dataset", "pairs", str(tmp_path / "snap"), str(tmp_path / "s.jsonl"), "-o", str(tmp_path / "p.jsonl")]) == 0
    assert len((tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()) == 8
    assert json.loads((tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines()[0])["a"]
