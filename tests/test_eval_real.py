import json

from spashta.cli import main
from spashta.dataset import qrels
from spashta.embed import HashingEmbedder
from spashta.evaluate import ablate_xling, compare_modes, evaluate_retrieval
from spashta.retrieve import Index
from spashta.samples import sample_articles, sample_questions_path
from spashta.store import Store
from spashta.xling import XlingConfig, xling_terms


def _index():
    index = Index(Store(":memory:"), HashingEmbedder())
    index.add_many(sample_articles())
    return index


def test_eval_reports_language_breakdown_and_intervals():
    res = evaluate_retrieval(_index(), sample_questions_path(), k=5)
    assert {"lang:ne", "lang:en", "lang:rom", "cross-language"} <= set(res["by_tag"])
    lo, hi = res["ci95"]["ndcg_at_k"]
    assert lo <= res["mean"]["ndcg_at_k"] <= hi
    cmp = compare_modes(_index(), sample_questions_path(), k=5)
    assert "hybrid vs lexical" in cmp["significance_by_tag"]["lang:rom"]
    assert cmp["ci95"]["hybrid"]["mrr"]


def test_xling_config_switches_bridges():
    text = "सिन्धुपाल्चोकमा पहिरो"
    full = xling_terms(text)
    assert any(t.startswith("c:") for t in full)
    assert not any(t.startswith("c:") for t in xling_terms(text, XlingConfig(glossary=False)))
    assert all("सिन्धु" in t or t.startswith("c:") or not t.startswith("p:s") for t in xling_terms(text, XlingConfig(romanize=False)))
    assert xling_terms(text, XlingConfig(enabled=False)) == []


def test_ablation_reports_each_variant_against_full():
    index = _index()
    res = ablate_xling(index.store, index.embedder, sample_questions_path(), k=5)
    assert list(res["variants"]) == ["full", "without romanisation", "without phonetic keys", "without glossary",
                                     "no cross-language index"]
    assert "vs_full" in res["variants"]["without glossary"]["xling"]
    rom_full = res["variants"]["full"]["xling"]["by_tag"]["lang:rom"]["ndcg_at_k"]
    rom_noroman = res["variants"]["without romanisation"]["xling"]["by_tag"]["lang:rom"]["ndcg_at_k"]
    assert rom_full > rom_noroman  # romanised queries need the romanisation bridge


def test_pool_and_build_questions(tmp_path):
    index = _index()
    qfile = tmp_path / "queries.jsonl"
    qfile.write_text("\n".join(json.dumps(q, ensure_ascii=False) for q in [
        {"qid": "q1", "query": "Sindhupalchok landslide deaths", "lang": "en"},
        {"qid": "q2", "query": "petrol ko mulya", "lang": "rom"}]) + "\n", encoding="utf-8")
    rows = qrels.pool(index, str(qfile), depth=3)
    assert {r["qid"] for r in rows} == {"q1", "q2"}
    assert all(r["found_by"] for r in rows) and len({(r["qid"], r["article_id"]) for r in rows}) == len(rows)
    judgments = [{"task": "qrel", "qid": r["qid"], "article_id": r["article_id"], "grade": 2 if i % 2 == 0 else 0}
                 for i, r in enumerate(rows)]
    data = qrels.build_questions(str(qfile), judgments, rows)
    assert all(q["relevant_ids"] for q in data["questions"])
    stats = qrels.pool_stats(rows, judgments)
    assert stats["judged"] == stats["pooled"] and stats["relevant"] == sum(j["grade"] for j in judgments) // 2
    out = tmp_path / "questions.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    assert evaluate_retrieval(index, out, k=3)["n"] == 2


def test_cli_qrels_and_ablation(tmp_path, capsys):
    db = str(tmp_path / "e.db")
    Index(Store(db), HashingEmbedder()).add_many(sample_articles())
    q = tmp_path / "q.jsonl"
    q.write_text(json.dumps({"query": "पहिरो", "lang": "ne"}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert main(["--db", db, "qrels", "pool", str(q), "-o", str(tmp_path / "pool.jsonl"), "--depth", "2"]) == 0
    pool_rows = [json.loads(x) for x in (tmp_path / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    (tmp_path / "j.jsonl").write_text("".join(json.dumps({"task": "qrel", "qid": r["qid"], "article_id": r["article_id"],
                                                           "grade": 1}) + "\n" for r in pool_rows), encoding="utf-8")
    assert main(["qrels", "build", str(q), str(tmp_path / "j.jsonl"), "--pool", str(tmp_path / "pool.jsonl"),
                 "-o", str(tmp_path / "qs.json")]) == 0
    assert main(["--db", db, "eval", str(tmp_path / "qs.json"), "--ablate-xling", "-k", "3",
                 "--out", str(tmp_path / "abl.json")]) == 0
    assert "variants" in json.loads((tmp_path / "abl.json").read_text(encoding="utf-8"))
