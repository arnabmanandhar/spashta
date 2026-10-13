import json

import pytest

from spashta.cli import main
from spashta.dataset import agreement as AG
from spashta.dataset import labels as LB
from spashta.dataset import labeltool


def test_cohen_kappa_known_value():
    assert AG.cohen_kappa([0, 1, 1, 0, 1], [0, 1, 0, 0, 1]) == pytest.approx(0.6154, abs=1e-4)
    assert AG.cohen_kappa(list("abcab"), list("abcab")) == 1.0
    assert AG.cohen_kappa([], []) is None


def test_weighted_kappa_penalises_near_misses_less():
    a = [0, 1, 2, 0, 1, 2, 0, 1, 2]
    near = [0, 1, 1, 0, 1, 2, 1, 1, 2]
    far = [0, 1, 0, 0, 1, 2, 2, 1, 2]
    assert AG.cohen_kappa(a, near, "quadratic", [0, 1, 2]) > AG.cohen_kappa(a, far, "quadratic", [0, 1, 2])
    assert AG.cohen_kappa(a, near) == AG.cohen_kappa(a, far)  # unweighted can't tell them apart


def test_token_labels_and_span_f1():
    body = "मन्त्रीले भने कि मूल्य बढ्छ। Sources said prices rise."
    spans = [{"start": 0, "end": 13, "sourcing": "named"}, {"start": 28, "end": 40, "sourcing": "anonymous"}]
    toks = AG.token_labels(body, spans, "sourcing")
    assert toks[:2] == ["named", "named"] and "anonymous" in toks and toks[-1] == "none"
    assert AG.span_f1([{"start": 0, "end": 5}], [{"start": 3, "end": 9}]) == 1.0
    assert AG.span_f1([{"start": 0, "end": 5}], [{"start": 6, "end": 9}]) == 0.0


def _art(aid, who, h, s, claims=(), loaded=()):
    return {"task": "article", "article_id": aid, "annotator": who, "headline": h, "sensationalism": s,
            "claims": list(claims), "loaded": list(loaded)}


def test_article_agreement_report():
    bodies = {f"a{i}": "word " * 40 for i in range(6)}
    first = [_art("a0", "x", "consistent", 0), _art("a1", "x", "partly", 1, loaded=[{"start": 0, "end": 4}]),
             _art("a2", "x", "inconsistent", 2), _art("a3", "x", "consistent", 0),
             _art("a4", "x", "consistent", 1, claims=[{"start": 0, "end": 9, "sourcing": "named"}]), _art("a5", "x", "partly", 0)]
    second = [_art("a0", "y", "consistent", 0), _art("a1", "y", "partly", 1, loaded=[{"start": 0, "end": 9}]),
              _art("a2", "y", "partly", 2), _art("a3", "y", "consistent", 1),
              _art("a4", "y", "consistent", 1, claims=[{"start": 0, "end": 9, "sourcing": "anonymous"}]), _art("a5", "y", "partly", 0)]
    rep = AG.article_agreement(first, second, bodies)
    assert rep["articles_compared"] == 6
    assert 0 < rep["headline"]["kappa"] < 1 and rep["headline"]["weighted_kappa"] > rep["headline"]["kappa"]
    assert rep["headline"]["confusion"]["inconsistent|partly"] == 1
    assert rep["framing"]["span_f1"] == 1.0
    assert rep["headline"]["band"] in ("moderate", "substantial", "fair")
    md = AG.to_markdown({"articles": rep, "pairs": {"n": 0}, "qrels": {"n": 0}})
    assert "| headline |" in md and "| sensationalism |" in md


def test_gold_overrides_by_field_and_drops_skips():
    primary = [_art("a1", "x", "partly", 1), _art("a2", "x", "consistent", 0), {**_art("a3", "x", None, None), "skip": "not news"}]
    adjudicated = [{"task": "article", "article_id": "a1", "headline": "inconsistent"}]
    gold = {r["article_id"]: r for r in LB.gold(primary, adjudicated)}
    assert set(gold) == {"a1", "a2"}
    assert gold["a1"]["headline"] == "inconsistent" and gold["a1"]["sensationalism"] == 1
    assert LB.poorly_sourced({"claims": [{"sourcing": "named"}, {"sourcing": "unattributed"}, {"sourcing": "anonymous"}]})
    assert LB.poorly_sourced({"claims": []}) is None


def test_validate():
    assert LB.validate(_art("a", "x", "partly", 1)) == []
    assert LB.validate(_art("a", "x", "maybe", 4))
    assert LB.validate(_art("a", "x", "partly", 1, claims=[{"start": 5, "end": 50, "sourcing": "named"}]), body_chars=20)


def test_labeltool_page_embeds_items_safely(tmp_path):
    items = [{"id": "x1", "url": "https://n.example/1", "source": "n.example", "lang": "en", "topic": "other",
              "title": "</script><script>alert(1)</script>", "body": "Body text."}]
    html = labeltool.render("article", items, "pilot")
    assert "</script><script>alert(1)" not in html and "<\\/script>" in html
    assert '"mode": "article"' in html


def test_cli_labeltool_check_agreement_gold(tmp_path, capsys):
    from test_sampling import _records
    recs = _records(tmp_path / "snap")
    main(["dataset", "sample", str(tmp_path / "snap"), "-o", str(tmp_path / "s.jsonl"), "--n", "6", "--pilot", "2",
          "--double", "2", "--minimum", "1"])
    assert main(["dataset", "labeltool", str(tmp_path / "snap"), str(tmp_path / "s.jsonl"), "--batch", "pilot",
                 "-o", str(tmp_path / "pilot.html")]) == 0
    assert (tmp_path / "pilot.html").stat().st_size > 5000
    ids = [r["id"] for r in recs[:3]]
    a = [_art(i, "x", "consistent", 0) for i in ids]
    b = [_art(i, "y", "consistent", 1 if n else 0) for n, i in enumerate(ids)]
    for name, rows in (("a", a), ("b", b)):
        (tmp_path / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert main(["dataset", "check", str(tmp_path / "snap"), str(tmp_path / "a.jsonl")]) == 0
    assert main(["dataset", "agreement", str(tmp_path / "snap"), "--a", str(tmp_path / "a.jsonl"), "--b", str(tmp_path / "b.jsonl"),
                 "--markdown"]) == 0
    assert main(["dataset", "gold", str(tmp_path / "a.jsonl"), "-o", str(tmp_path / "gold.jsonl")]) == 0
    assert "| sensationalism |" in capsys.readouterr().out
