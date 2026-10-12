import json

from spashta.analysis.corroboration import corroborate
from spashta.samples import DATA, sample_articles
from spashta.story import genre, near_duplicate, outlet_group, same_story
from spashta.xling import name_keys

ARTICLES = sample_articles()
EVENTS = {a["url"]: a["event"] for a in json.loads((DATA / "sample_articles.json").read_text(encoding="utf-8"))["articles"]}
BY_END = lambda end: next(a for a in ARTICLES if a.url.endswith(end))  # noqa: E731


def test_same_story_matches_labelled_events():
    names = set().union(*(name_keys(a.text) for a in ARTICLES))
    tp = fp = fn = 0
    for i, a in enumerate(ARTICLES):
        for b in ARTICLES[i + 1:]:
            if genre(a) == "opinion" or genre(b) == "opinion":
                continue
            ok, _ = same_story(a, b, names)
            gold = EVENTS[a.url] == EVENTS[b.url]
            tp, fp, fn = tp + (ok and gold), fp + (ok and not gold), fn + (gold and not ok)
    assert fp == 0 and fn == 0, (tp, fp, fn)


def test_last_years_landslide_is_not_the_same_story():
    names = set().union(*(name_keys(a.text) for a in ARTICLES))
    ok, why = same_story(BY_END("101234"), BY_END("090112"), names)
    assert not ok and "apart" in why


def test_opinion_detection():
    assert genre(BY_END("systems-now")) == "opinion"
    assert genre(BY_END("101234")) == "news"


def test_wire_copy_is_near_duplicate():
    assert near_duplicate(BY_END("rescue-continues"), BY_END("kills-12-five-missing")) > 0.5
    assert near_duplicate(BY_END("rescue-continues"), BY_END("two-houses-buried")) < 0.2


def test_outlet_groups():
    assert outlet_group("ekantipur.com") == outlet_group("https://kathmandupost.com/x") == "kantipur-media"
    assert outlet_group("english.onlinekhabar.com") == "onlinekhabar"
    assert outlet_group("unknown.example") == "unknown.example"


def test_corroboration_counts_independent_sources_only(sample_index):
    c = corroborate(BY_END("101234"), sample_index)
    roles = {r.source: r.role for r in c.related}
    assert roles["wire-sample.example"] == "copy"
    assert roles["blog-sample.example"] == "commentary"
    assert roles["himal-times.example"] == "independent"
    assert len(c.signal.details["independent_sources"]) == 2
    assert all(r.reason for r in c.related)
