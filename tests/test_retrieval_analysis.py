import json

from spashta import lang as L
from spashta.analysis import llm_review
from spashta.analysis.signals import find_terms, headline_consistency, sensationalism, sourcing
from spashta.chunk import chunk_article
from spashta.cli import main
from spashta.evaluate import evaluate_retrieval
from spashta.models import Article
from spashta.report import analyze
from spashta.samples import sample_articles, sample_questions_path

BY_URL = {a.url: a for a in sample_articles()}
MEASURED_NE = BY_URL["https://khabar-sample.example/samachar/2083/06/101234"]
SENSATIONAL_NE = BY_URL["https://sanoportal.example/2083/06/10/bhayanak-pahiro-50-janako-mrityu"]
LOADED_EN = BY_URL["https://daily-sample.example/opinion-news/corrupt-regime-slams-citizens-with-shocking-fuel-hike"]
MEASURED_EN = BY_URL["https://himal-times.example/news/noc-raises-petrol-price-by-rs-5-per-litre-from-tonight"]


def test_chunks_start_with_headline_and_lead():
    chunks = chunk_article(MEASURED_NE)
    assert chunks[0].text.startswith(MEASURED_NE.title)
    assert all(c.article_id == MEASURED_NE.id for c in chunks)
    assert chunks[0].lang == "ne"


def test_nepali_morphology_matches_in_search(sample_index):
    hits = sample_index.search("पेट्रोलको मूल्यमा", k=3, mode="lexical")
    assert hits[0].chunk.article_id == BY_URL["https://khabar-sample.example/samachar/2083/06/101180"].id
    assert "पेट्रोल" in hits[0].matched_terms


def test_search_excludes_own_outlet(sample_index):
    hits = sample_index.search("पहिरो", k=10, exclude_sources=["khabar-sample.example"])
    assert all(sample_index.article_source[h.chunk.article_id] != "khabar-sample.example" for h in hits)


def test_related_coverage_skips_unrelated_stories(sample_index):
    related = dict(sample_index.related_articles(MEASURED_NE))
    assert SENSATIONAL_NE.id in related
    assert BY_URL["https://himal-times.example/sports/nepal-beat-uae-by-six-wickets-in-tri-series-opener"].id not in related


def test_lexicon_matches_inflected_nepali():
    assert "सनसनीपूर्ण" in find_terms("यो सनसनीपूर्ण खबर हो", __import__("spashta.analysis.lexicons", fromlist=["x"]).SENSATIONAL, "ne")


def test_sensational_scores_higher_than_measured():
    assert sensationalism(SENSATIONAL_NE).concern > 0.6 > sensationalism(MEASURED_NE).concern
    assert sensationalism(LOADED_EN).concern > 0.6 > sensationalism(MEASURED_EN).concern


def test_headline_number_not_in_body_is_flagged():
    s = headline_consistency(SENSATIONAL_NE)
    assert "50" in s.details["missing_numbers"]
    assert headline_consistency(MEASURED_NE).concern < 0.25


def test_sourcing_rewards_named_attribution():
    assert sourcing(MEASURED_EN).concern < 0.3


def test_reports_rank_measured_above_sensational(sample_index):
    good = analyze(MEASURED_NE, sample_index)
    bad = analyze(SENSATIONAL_NE, sample_index)
    worse = analyze(LOADED_EN, sample_index)
    assert good.score > bad.score and good.score > worse.score
    d = bad.to_dict()
    assert d["signals"] and d["disclaimer"]
    assert "Integrity report" in bad.to_markdown()


def test_report_without_index_still_works():
    r = analyze(Article(url="text:", title="Shocking!", body="Nothing else.", lang=L.detect_language("Shocking! Nothing else.")))
    assert 0 <= r.score <= 100


def test_llm_review_audits_citations(monkeypatch, sample_index):
    from anchor import llm

    hits = sample_index.search(SENSATIONAL_NE.title, k=3, exclude_articles=[SENSATIONAL_NE.id])
    real_id = hits[0].chunk.id

    def fake_complete_json(conn, system, user, schema, name="result", **kw):
        assert real_id in user
        return {"claims": [
            {"claim": "50 people died", "verdict": "contradicted", "passage_id": real_id, "note": "other outlet says 12"},
            {"claim": "the whole village was swept away", "verdict": "supported", "passage_id": "made-up:9", "note": None},
        ], "headline_fair": False, "headline_note": "The headline number is not in the story."}, None

    monkeypatch.setattr(llm, "complete_json", fake_complete_json)
    conn = llm.Connection("groq", "test-model", llm.Secret("gsk_test_key_123456"))
    r = llm_review.review(SENSATIONAL_NE, hits, conn)
    assert r.claims[0].verdict == "contradicted"
    assert r.claims[1].verdict == "unverified"  # cited a passage it was never given
    assert r.headline_fair is False and r.signal.concern > 0.5
    assert "gsk_test" not in json.dumps(r.signal.evidence)


def test_llm_review_skips_without_key(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN", "MISTRAL_API_KEY",
              "OPENAI_API_KEY", "XAI_API_KEY", "ANCHOR_LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    assert llm_review.review(MEASURED_NE, []).error == "no connection"


def test_retrieval_eval_uses_anchor_metrics(sample_index, tmp_path):
    out = tmp_path / "records.jsonl"
    result = evaluate_retrieval(sample_index, sample_questions_path(), k=5, records_out=out)
    assert result["n"] == 21
    assert result["mean"]["recall_at_k"] >= 0.7
    assert set(result["by_tag"]) >= {"ne", "en", "xl", "rom"}
    assert out.read_text().count("\n") == 21


def test_mode_ablation_reports_significance(sample_index):
    from spashta.evaluate import compare_modes
    r = compare_modes(sample_index, sample_questions_path())
    assert set(r["mean"]) == {"hybrid", "lexical", "dense", "xling"}
    assert r["mean"]["hybrid"]["ndcg_at_k"] >= r["mean"]["lexical"]["ndcg_at_k"]
    assert "p_value" in r["significance"]["hybrid vs lexical"]


def test_cli_demo_and_eval(capsys):
    assert main(["demo", "--json"]) == 0
    assert '"score"' in capsys.readouterr().out
    assert main(["eval"]) == 0
    assert "recall_at_k" in capsys.readouterr().out


def test_cli_sources_and_text_analysis(tmp_path, capsys):
    db = str(tmp_path / "s.db")
    assert main(["--db", db, "sources", "add", "https://blog.example/"]) == 0
    assert main(["--db", db, "sources", "seed"]) == 0
    assert main(["--db", db, "sources", "list"]) == 0
    assert "blog.example" in capsys.readouterr().out
    assert main(["--db", db, "analyze", "--text", "SHOCKING news!\nYou won't believe what happened next."]) == 0
    assert "Sensationalism" in capsys.readouterr().out
