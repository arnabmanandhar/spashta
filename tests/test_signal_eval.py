import json

from spashta.cli import main
from spashta.dataset import signal_eval as SE
from spashta.dataset import snapshot
from spashta.samples import sample_articles


def _setup(tmp_path):
    from test_sampling import _records
    recs = {r["id"]: r for r in _records(tmp_path / "snap")}
    raw = json.loads((__import__("spashta").samples.sample_articles_path()).read_text(encoding="utf-8"))["articles"]
    from spashta.models import article_id
    gold = []
    for a in raw:  # turn the synthetic labels into v2 gold labels
        lab = a.get("labels", {})
        aid = article_id(a["url"])
        if aid not in recs:
            continue
        body = recs[aid]["body"]
        gold.append({"task": "article", "article_id": aid, "headline": "partly" if lab.get("headline_unsupported") else "consistent",
                     "sensationalism": 2 if lab.get("sensational") else 0,
                     "claims": [{"start": 0, "end": min(20, len(body)), "sourcing": "unattributed" if lab.get("poorly_sourced") else "named"}],
                     "loaded": [{"start": 0, "end": 5}] if lab.get("loaded") else []})
    return recs, gold


def test_prf_and_threshold_helpers():
    scores, gold = [0.1, 0.2, 0.7, 0.9], [False, False, True, True]
    assert SE.prf(scores, gold, 0.5) == {"threshold": 0.5, "precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 2, "fp": 0, "fn": 0}
    assert SE.best_threshold(scores, gold) == 0.7
    assert SE.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0


def test_evaluate_against_gold(tmp_path):
    recs, gold = _setup(tmp_path)
    rep = SE.evaluate(recs, gold)
    assert rep["articles"] == len(gold) > 10
    sens = rep["signals"]["sensationalism"]["targets"]["level 1 or 2"]
    assert sens["auc"] > 0.7 and sens["shipped_some"]["threshold"] == 0.25
    assert set(SE.suggested_thresholds(rep)) <= {"headline", "sensationalism", "sourcing", "framing"}
    cases = SE.error_cases(recs, gold)
    assert all(c["kind"] in ("false alarm", "miss") for c in cases)


def test_evaluate_pairs(tmp_path):
    recs, _ = _setup(tmp_path)
    by_url = {r["url"]: r["id"] for r in recs.values()}
    ne = by_url["https://khabar-sample.example/samachar/2083/06/101234"]
    en = by_url["https://himal-times.example/news/landslide-sindhupalchok-12-dead-5-missing-rescue-continues"]
    other = next(i for u, i in by_url.items() if "petrol" in u or "mulya" in u)
    pairs = [{"task": "pair", "pair_id": "p1", "a": ne, "b": en, "same_event": "yes", "conflicting_figures": []},
             {"task": "pair", "pair_id": "p2", "a": ne, "b": other, "same_event": "no"}]
    rep = SE.evaluate_pairs(recs, pairs)
    assert rep["pairs"] == 2 and rep["matcher"]["tp"] == 1 and rep["matcher"]["fp"] == 0


def test_cli_eval_gold_and_errors(tmp_path, capsys):
    recs, gold = _setup(tmp_path)
    g = tmp_path / "gold.jsonl"
    g.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in gold), encoding="utf-8")
    assert main(["eval", "--gold", str(g), "--snapshot", str(tmp_path / "snap"), "--out", str(tmp_path / "sig.json"),
                 "--write-thresholds", str(tmp_path / "thr.json")]) == 0
    assert "signals" in json.loads((tmp_path / "sig.json").read_text(encoding="utf-8"))
    assert main(["eval", "--out", str(tmp_path / "ret.json")]) == 0
    assert main(["dataset", "errors", str(tmp_path / "snap"), str(g), "--retrieval", str(tmp_path / "ret.json"),
                 "-o", str(tmp_path / "errors.md")]) == 0
    text = (tmp_path / "errors.md").read_text(encoding="utf-8")
    assert "# Retrieval failures" in text
    assert snapshot.verify(tmp_path / "snap") == [] and sample_articles()
