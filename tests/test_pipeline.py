"""The whole evidence pipeline end to end on the bundled samples, through the CLI."""

import json

from spashta.cli import main
from spashta.embed import HashingEmbedder
from spashta.retrieve import Index
from spashta.samples import sample_articles, sample_questions_path
from spashta.store import Store


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_snapshot_to_results(tmp_path, capsys):
    live = str(tmp_path / "live.db")
    Index(Store(live), HashingEmbedder()).add_many(sample_articles())
    snap, ev = str(tmp_path / "snap"), str(tmp_path / "eval.db")
    assert main(["--db", live, "dataset", "freeze", snap, "--note", "test"]) == 0
    assert main(["--db", ev, "dataset", "index", snap]) == 0
    assert main(["dataset", "sample", snap, "-o", str(tmp_path / "s.jsonl"), "--n", "12", "--pilot", "3",
                 "--double", "6", "--minimum", "1"]) == 0
    sample = [json.loads(x) for x in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()]
    first = [{"task": "article", "article_id": s["id"], "headline": ["consistent", "partly"][i % 2], "sensationalism": i % 3,
              "claims": [{"start": 0, "end": 10, "sourcing": "named"}], "loaded": [], "topic": s["topic"]}
             for i, s in enumerate(sample)]
    second = [{**r, "sensationalism": min(2, r["sensationalism"] + (i % 4 == 0))} for i, r in enumerate(first) if sample[i]["double"]]
    _jsonl(tmp_path / "a.jsonl", first)
    _jsonl(tmp_path / "b.jsonl", second)
    assert main(["dataset", "agreement", snap, "--a", str(tmp_path / "a.jsonl"), "--b", str(tmp_path / "b.jsonl"),
                 "--out", str(tmp_path / "agr.json")]) == 0
    assert main(["dataset", "gold", str(tmp_path / "a.jsonl"), "-o", str(tmp_path / "gold.jsonl")]) == 0
    assert main(["dataset", "release", snap, str(tmp_path / "rel"), "--labels", str(tmp_path / "gold.jsonl"),
                 "--sample", str(tmp_path / "s.jsonl"), "--queries", str(sample_questions_path())]) == 0
    assert main(["dataset", "card", str(tmp_path / "rel"), "--agreement", str(tmp_path / "agr.json"),
                 "-o", str(tmp_path / "rel" / "README.md")]) == 0
    card = (tmp_path / "rel" / "README.md").read_text(encoding="utf-8")
    assert "{{" not in card and "| sensationalism |" in card
    q = str(sample_questions_path())
    assert main(["--db", ev, "eval", q, "--compare", "-k", "5", "--out", str(tmp_path / "cmp.json")]) == 0
    assert main(["--db", ev, "eval", q, "--ablate-xling", "-k", "5", "--out", str(tmp_path / "abl.json")]) == 0
    assert main(["eval", "--gold", str(tmp_path / "gold.jsonl"), "--snapshot", snap, "--out", str(tmp_path / "sig.json")]) == 0
    assert main(["results", "--manifest", f"{snap}/manifest.json", "--compare", str(tmp_path / "cmp.json"),
                 "--ablation", str(tmp_path / "abl.json"), "--signals", str(tmp_path / "sig.json"),
                 "--agreement", str(tmp_path / "agr.json"), "-o", str(tmp_path / "RESULTS.md")]) == 0
    md = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    for heading in ("## Corpus", "## Retrieval", "## Cross-language ablation", "## Integrity signals", "## Annotator agreement",
                    "Romanised Nepali", "One line for a resume"):
        assert heading in md
    assert "real articles from" in md
