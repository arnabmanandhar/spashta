from spashta.analysis.corroboration import corroborate
from spashta.facts import compare_facts, extract_facts
from spashta.samples import sample_articles

BY_END = lambda end: next(a for a in sample_articles() if a.url.endswith(end))  # noqa: E731


def kinds(text):
    return {(f.kind, f.value) for f in extract_facts(text)}


def test_digits_and_words_in_both_languages():
    assert kinds("Landslide kills 12, five missing") == {("death", 12), ("missing", 5)}
    assert kinds("पहिरोमा १२ जनाको मृत्यु, पाँच जना बेपत्ता") == {("death", 12), ("missing", 5)}
    assert ("injured", 7) in kinds("Seven injured people were taken to hospital.")
    assert ("house", 8) in kinds("पहिरोले ८ घर पुरिएका छन्।")


def test_scales_lakh_crore():
    assert ("rupee", 320_000_000) in kinds("३२ करोड रुपैयाँ बजेट विनियोजन भएको छ।")
    assert ("people", 120_000) in kinds("1,20,000 people were affected.")


def test_hedged_figures_are_marked():
    (f,) = [f for f in extract_facts("५० जनाको मृत्युको आशंका छ।") if f.kind == "death"]
    assert f.hedged


def test_years_and_articles_are_not_facts():
    assert kinds("In 2026 a man said one thing.") == set()
    assert kinds("एक स्थानीयले भने।") == set()


def test_compare_majority_decides():
    ours = extract_facts("50 people died")
    checks = compare_facts(ours, {"a": extract_facts("12 people died"), "b": extract_facts("12 dead"),
                                  "c": extract_facts("50 died")})
    (c,) = [c for c in checks if c.fact.kind == "death"]
    assert c.status == "conflict" and c.support == ["c"] and set(c.dissent) == {"a", "b"}


def test_corroboration_flags_inflated_toll(sample_index):
    c = corroborate(BY_END("50-janako-mrityu"), sample_index)
    statuses = {(f["kind"], f["value"]): f["status"] for f in c.signal.details["facts"]}
    assert statuses[("death", 50)] == "conflict"
    assert any("conflict: death 50" in e for e in c.signal.evidence)
    measured = corroborate(BY_END("101234"), sample_index)
    assert all(f["status"] == "confirmed" for f in measured.signal.details["facts"])
